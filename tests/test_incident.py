import time

import pytest

from pitwall import feed, incident, watch
from pitwall.model import build


# --- model signals ---------------------------------------------------------


def _state(*frames) -> dict:
    return {
        "SessionInfo": {"Key": 1, "Type": "Qualifying", "Name": "Qualifying",
                        "Meeting": {"Key": 1299, "Name": "Test"}},
        "TimingData": {
            "Lines": {
                "16": {"RacingNumber": "16", "Position": "3", "Line": 3,
                       "Sectors": [], "MVStatus": {"Stopped": False, "Retired": False,
                                                   "InPit": False, "PitOut": False}},
                "81": {"RacingNumber": "81", "Position": "4", "Line": 4,
                       "Sectors": [], "MVStatus": {"Stopped": False, "Retired": False,
                                                   "InPit": False, "PitOut": False}},
            }
        },
        "DriverList": {"16": {"Tla": "LEC", "FullName": "Charles LECLERC"},
                       "81": {"Tla": "PIA", "FullName": "Oscar PIASTRI"}},
        "Position": {"Position": list(frames)},
    }


def test_off_track_and_stationary_come_from_position_frames():
    frame = {"Timestamp": "2026-11-07T18:00:00Z",
             "Entries": {"16": {"Status": "OffTrack", "X": 5, "Y": 5, "Z": 0},
                         "81": {"Status": "OnTrack", "X": 9, "Y": 9, "Z": 0}}}
    moved = {"Timestamp": "2026-11-07T18:00:02Z",
             "Entries": {"16": {"Status": "OffTrack", "X": 5, "Y": 5, "Z": 0},
                         "81": {"Status": "OnTrack", "X": 40, "Y": 60, "Z": 0}}}
    session = build(_state(frame, moved), mode="qualifying")
    assert session.by_num["16"].off_track is True
    assert session.by_num["16"].stationary is True
    assert session.by_num["81"].off_track is False
    assert session.by_num["81"].stationary is False


def test_garage_coordinates_are_not_mistaken_for_stopped():
    frame = {"Timestamp": "a", "Entries": {"16": {"Status": "OnTrack", "X": 0, "Y": 0, "Z": 0}}}
    other = {"Timestamp": "b", "Entries": {"16": {"Status": "OnTrack", "X": 0, "Y": 0, "Z": 0}}}
    session = build(_state(frame, other), mode="qualifying")
    assert session.by_num["16"].stationary is False


# --- candidate selection ---------------------------------------------------


class FakePlayer:
    def __init__(self, pid=42):
        self.id = pid
        self.calls: list[tuple] = []
        self.state = {"paused": False, "interpolatedCurrentTime": 1000.0}

    def set_fullscreen(self, value=True):
        self.calls.append(("fullscreen", value))

    def pause(self, value=True):
        self.calls.append(("pause", value))
        self.state["paused"] = bool(value)

    def seek(self, absolute=None, relative=None):
        self.calls.append(("seek", relative))


def session_with(**flags):
    """A session whose drivers carry the given incident evidence."""
    state = _state({"Timestamp": "a", "Entries": {
        "16": {"Status": "OnTrack", "X": 1, "Y": 1, "Z": 0},
        "81": {"Status": "OnTrack", "X": 2, "Y": 2, "Z": 0}}})
    session = build(state, mode="qualifying")
    for num, attrs in flags.items():
        for key, value in attrs.items():
            setattr(session.by_num[num], key, value)
    return session


def test_off_track_outranks_stopped_outranks_stationary():
    session = session_with(**{
        "16": {"stationary": True},
        "81": {"off_track": True},
    })
    assert incident.pick(session).label == "PIA"


def test_stopped_beats_a_stationary_car():
    session = session_with(**{
        "16": {"stopped": True},
        "81": {"stationary": True},
    })
    assert incident.pick(session).label == "LEC"


def test_a_car_in_the_pits_is_never_the_incident():
    session = session_with(**{"16": {"stopped": True, "in_pit": True}})
    assert incident.pick(session) is None


def test_nothing_stopped_means_no_pick():
    assert incident.pick(session_with()) is None


def test_is_flag_covers_every_stoppage():
    session = session_with()
    for track in ("ALL CLEAR", "YELLOW", "RED FLAG", "SAFETY CAR", "VSC"):
        session.track = track
        assert incident.is_flag(session) is (track != "ALL CLEAR")


# --- framing ---------------------------------------------------------------


@pytest.fixture
def player_env(monkeypatch):
    """Wire incident's player layer to a fake remote and one fake player."""
    player = FakePlayer()

    class Remote:
        def player(self, pid):
            return player

        def player_sync_to_live_timing(self):
            return {"ok": True}

    class Res:
        content_id = 1000010425
        title = "Qualifying"
        matched_on = "session_key"

    monkeypatch.setattr(feed, "_client", lambda: Remote())
    monkeypatch.setattr(incident.playerlib, "current_resolution", lambda: Res())
    monkeypatch.setattr(incident.playerlib, "list_players", lambda: [])
    monkeypatch.setattr(incident, "_await_player", lambda pid, **kw: player)
    return player


def test_frame_full_screens_and_rewinds(monkeypatch, player_env):
    session = session_with(**{"16": {"off_track": True}})
    monkeypatch.setattr(incident, "_existing_player", lambda driver, content: 7)
    report, pid = incident.frame(session.by_num["16"], back=30)
    assert pid == 7
    assert player_env.calls == [("fullscreen", True), ("seek", -30)]
    assert "LEC" in report and "30s" in report


def test_frame_creates_a_player_when_none_shows_the_driver(monkeypatch, player_env):
    session = session_with(**{"16": {"off_track": True}})
    created = {}

    monkeypatch.setattr(incident, "_existing_player", lambda driver, content: None)

    def fake_create(**kwargs):
        created.update(kwargs)
        return (99, "LEC")

    monkeypatch.setattr(incident.playerlib, "create", fake_create)
    report, pid = incident.frame(session.by_num["16"])
    assert pid == 99
    assert created["driver"] == "LEC"
    assert "new player" in report


def test_frame_pauses_when_the_stream_cannot_be_rewound(monkeypatch, player_env):
    """Pause works even though seek does not; leave the frame stopped to scrub."""
    session = session_with(**{"16": {"off_track": True}})
    monkeypatch.setattr(incident, "_existing_player", lambda driver, content: 7)

    def refuse(absolute=None, relative=None):
        raise RuntimeError("Cannot seek in live mode")

    player_env.seek = refuse
    report, _pid = incident.frame(session.by_num["16"], back=30)
    assert "fullscreen" in report
    assert "paused to scrub" in report
    assert ("pause", True) in player_env.calls
    assert "rewound 30s" not in report


# --- sync with the broadcast ----------------------------------------------


class SyncPlayer(FakePlayer):
    def __init__(self, pid, title, t):
        super().__init__(pid)
        self.title = title
        self.state = {"interpolatedCurrentTime": t}
        self.synced = False

    def sync(self):
        self.synced = True


class Info:
    def __init__(self, pid, title):
        self.id = pid
        self.title = title


@pytest.fixture
def sync_env(monkeypatch):
    def install(broadcast_t=1000.0, mine_t=1000.0, with_broadcast=True, has_live_sync=True):
        broadcast = SyncPlayer(5, "INTERNATIONAL", broadcast_t)
        mine = SyncPlayer(7, "LEC", mine_t)
        table = {5: broadcast, 7: mine}

        class Remote:
            def player(self, pid):
                return table[pid]

        class Res:
            content_id = 1000010425
            title = "Qualifying"
            matched_on = "session_key"

        monkeypatch.setattr(feed, "_client", lambda: Remote())
        monkeypatch.setattr(incident.playerlib, "current_resolution", lambda: Res())
        infos = [Info(7, "LEC")] + ([Info(5, "INTERNATIONAL")] if with_broadcast else [])
        monkeypatch.setattr(incident.playerlib, "list_players", lambda: infos)
        if has_live_sync:
            Remote.player_sync_to_live_timing = lambda self: {"ok": True}
        return broadcast, mine
    return install


def test_ensure_synced_leaves_an_aligned_player_alone(sync_env):
    broadcast, _mine = sync_env(broadcast_t=1000.0, mine_t=1000.4)
    note = incident.ensure_synced(7)
    assert "already in sync" in note
    assert broadcast.synced is False


def test_ensure_synced_pulls_the_output_to_the_broadcast(sync_env):
    broadcast, _mine = sync_env(broadcast_t=1000.0, mine_t=1008.5)
    note = incident.ensure_synced(7)
    assert broadcast.synced is True
    assert "synced to INTERNATIONAL" in note


def test_ensure_synced_falls_back_to_live_timing_without_a_world_feed(sync_env):
    sync_env(with_broadcast=False)
    assert incident.ensure_synced(7) == "synced to live timing"


def test_ensure_synced_says_so_when_there_is_nothing_to_sync_to(sync_env):
    sync_env(with_broadcast=False, has_live_sync=False)
    assert "no broadcast player" in incident.ensure_synced(7)


def test_frame_syncs_before_it_rewinds(monkeypatch, sync_env):
    session = session_with(**{"16": {"off_track": True}})
    broadcast, mine = sync_env(broadcast_t=1000.0, mine_t=1009.0)
    monkeypatch.setattr(incident, "_existing_player", lambda driver, content: 7)
    monkeypatch.setattr(incident, "_await_player", lambda pid, **kw: mine)

    report, _pid = incident.frame(session.by_num["16"], back=30)
    assert broadcast.synced is True
    assert "synced to INTERNATIONAL" in report
    assert ("seek", -30) in mine.calls


def test_react_does_nothing_on_green():
    session = session_with()
    session.track = "ALL CLEAR"
    line, marker = incident.react(session, {})
    assert line is None and marker == {}


def test_react_frames_once_then_stays_quiet(monkeypatch, player_env):
    session = session_with(**{"16": {"off_track": True}})
    session.track = "RED FLAG"
    monkeypatch.setattr(incident, "_existing_player", lambda driver, content: 7)

    line, marker = incident.react(session, {})
    assert line and "fullscreen" in line
    assert marker["player"] == 7

    again, _ = incident.react(session, marker)
    assert again is None


def test_react_waits_when_nothing_is_stopped_yet():
    session = session_with()
    session.track = "YELLOW"
    line, marker = incident.react(session, {})
    assert line is None and marker == {}


# --- watcher integration ---------------------------------------------------


def test_scan_with_react_frames_then_restores(q3_state, tmp_path, monkeypatch):
    path = tmp_path / "watch.json"
    monkeypatch.setattr(watch, "state_path", lambda: path)
    state = dict(q3_state)
    feed.reset()
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: state)

    # Baseline pass, still green.
    assert watch.scan(path=path, react=True) == []

    # A car stops; the flag goes red.
    state["TrackStatus"] = {"Status": "5", "Message": "RedFlag"}
    state["Position"] = {"Position": [
        {"Timestamp": "a", "Entries": {"16": {"Status": "OffTrack", "X": 3, "Y": 3, "Z": 0}}},
        {"Timestamp": "b", "Entries": {"16": {"Status": "OffTrack", "X": 3, "Y": 3, "Z": 0}}},
    ]}
    framed = {}

    def fake_frame(driver, back=30):
        framed["driver"] = driver.label
        return (f"{driver.label} stopped -> player 7 fullscreen, rewound 30s", 7)

    monkeypatch.setattr(incident, "frame", fake_frame)
    events = watch.scan(path=path, react=True)
    assert framed["driver"] == "LEC"
    assert any("rewound 30s" in e.text for e in events)

    # Green again: the screen is handed back.
    state["TrackStatus"] = {"Status": "1", "Message": "AllClear"}
    restored = {}
    monkeypatch.setattr(incident, "restore", lambda pid: f"player {pid} back to live")
    events = watch.scan(path=path, react=True)
    assert any("back to live" in e.text for e in events)


def test_scan_without_react_never_touches_players(q3_state, tmp_path, monkeypatch):
    path = tmp_path / "watch.json"
    monkeypatch.setattr(watch, "state_path", lambda: path)
    state = dict(q3_state)
    state["TrackStatus"] = {"Status": "5", "Message": "RedFlag"}
    feed.reset()
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: state)

    def explode(*_a, **_kw):
        raise AssertionError("read-only pass must not frame anything")

    monkeypatch.setattr(incident, "react", explode)
    watch.scan(path=path, react=False)
