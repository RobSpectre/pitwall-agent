import json

import pytest

from pitwall import feed, tools


def handler(name: str):
    return next(h for n, _schema, h in tools.schemas() if n == name)


def test_manifest_declares_every_registered_tool():
    from pathlib import Path

    import yaml

    manifest = yaml.safe_load((Path(__file__).resolve().parent.parent / "plugin.yaml").read_text())
    declared = set(manifest["provides_tools"])
    registered = {name for name, _s, _h in tools.schemas()}
    assert declared == registered


def test_overview_handler(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_overview")({"messages": 5})
    assert "Qualifying Q3" in text
    assert "GREEN LIGHT" in text


def test_board_handler_and_gap_filter(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_board")({"gap_tol": 0.25})
    assert "LEC" in text
    assert "PER" not in text


def test_board_changes_after_a_new_lap(monkeypatch, q3_state):
    feed.reset()
    live = {"state": q3_state}
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: live["state"])
    board = handler("pitwall_board")
    assert "first read" in board({"only_changes": True})

    improved = json.loads(json.dumps(q3_state))
    improved["TimingData"]["Lines"]["16"]["BestLapTime"]["Value"] = "1:09.990"
    improved["TimingData"]["Lines"]["16"]["MVStatus"]["InPit"] = True
    live["state"] = improved
    text = board({"only_changes": True})
    assert "LEC best 1:09.990" in text
    assert "PUSH -> PIT" in text


def test_board_changes_reports_nothing_when_idle(offline, q3_state):
    offline(q3_state)
    board = handler("pitwall_board")
    board({"only_changes": True})
    assert "no changes" in board({"only_changes": True})


def test_driver_lookup_by_number_and_code(offline, q3_state):
    offline(q3_state)
    by_num = handler("pitwall_driver")({"driver": "16"})
    by_code = handler("pitwall_driver")({"driver": "lec"})
    assert "Charles LECLERC" in by_num
    assert by_num.splitlines()[0] == by_code.splitlines()[0]


def test_driver_lookup_failure_lists_the_grid(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_driver")({"driver": "XYZ"})
    assert text.startswith("Error: no driver matching")
    assert "VER" in text


def test_raw_index_then_section(offline, q3_state):
    offline(q3_state)
    raw = handler("pitwall_raw")
    index = raw({})
    assert "TimingData" in index and "bytes" in index

    body = raw({"sections": ["RaceControlMessages"], "max_chars": 400})
    assert "GREEN LIGHT" in body

    missing = raw({"sections": ["Nope"]})
    assert "absent: Nope" in missing


def test_raw_one_driver(offline, q3_state):
    offline(q3_state)
    body = handler("pitwall_raw")({"driver": "16"})
    payload = json.loads(body)
    assert payload["DriverList"]["16"]["Tla"] == "LEC"
    assert "Sectors" in payload["TimingData"]["16"]


def test_raw_truncates_to_budget(offline, q3_state):
    offline(q3_state)
    body = handler("pitwall_raw")({"sections": ["TimingData"], "max_chars": 400})
    assert body.endswith("chars]") or "truncated at 400" in body


def test_friendly_error_when_multiviewer_is_down(offline, monkeypatch):
    def boom(*, fresh=False):
        raise feed.PitwallError("MultiViewer is not reachable")

    feed.reset()
    monkeypatch.setattr(feed, "raw_state", boom)
    text = handler("pitwall_board")({})
    assert text == "Error: MultiViewer is not reachable"


class FakePlayer:
    def __init__(self, pid, title="INTERNATIONAL", content=1000010425, box=(0, 0, 960, 540), paused=False):
        self.id = pid
        self.title = title
        self.content_id = content
        self.channel_id = 1025
        self.x, self.y, self.width, self.height = box
        self.state = {"paused": paused, "live": True}
        self.actions: list[str] = []

    def sync(self):
        self.actions.append("sync")

    def delete(self):
        self.actions.append("delete")

    def mute(self, value=True):
        self.actions.append(f"mute={value}")

    def pause(self, value=True):
        self.actions.append(f"pause={value}")

    def seek(self, absolute=None, relative=None):
        self.actions.append(f"seek={absolute or relative}")

    def set_fullscreen(self, value=True):
        self.actions.append(f"fullscreen={value}")


class FakeRemote:
    def __init__(self, players=None, new_id=42):
        self.players = players or []
        self.new_id = new_id
        self.created: dict = {}
        self.bounds: list[tuple] = []
        self.deleted: list[int] = []

    def player_create(self, **kwargs):
        self.created = kwargs
        # MultiViewer exposes the new player a beat later; mirror that so tiling
        # sees it, exactly like the real app.
        self.players.append(
            FakePlayer(self.new_id, kwargs.get("stream_title") or "INTERNATIONAL",
                       content=kwargs.get("content_id"),
                       box=(kwargs.get("x", 0), kwargs.get("y", 0),
                            kwargs.get("width", 960), kwargs.get("height", 540)))
        )
        return {"data": {"playerCreate": self.new_id}}

    def player_set_bounds(self, id, x, y, width, height):
        self.bounds.append((id, x, y, width, height))

    def player_delete(self, id):
        self.deleted.append(id)

    def player(self, pid):
        return next(p for p in self.players if p.id == pid)


def test_players_lists_open_windows(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting()
    remote = FakeRemote([FakePlayer(5, "INTERNATIONAL"), FakePlayer(6, "ALB", box=(1920, 0, 960, 540))])
    monkeypatch.setattr(feed, "_client", lambda: remote)
    text = handler("pitwall_players")({})
    assert "PLAYERS (2)" in text
    assert "session content 1000010425" in text
    assert "INTERNATIONAL" in text and "ALB" in text


def test_players_flags_a_player_on_another_session(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting()
    remote = FakeRemote([FakePlayer(5, "INTERNATIONAL", content=999)])
    monkeypatch.setattr(feed, "_client", lambda: remote)
    text = handler("pitwall_players")({})
    assert "other session" in text


def test_players_accepts_string_content_ids(offline, q3_state, fake_meeting, monkeypatch):
    # MultiViewer serialises contentId as a string; a string must not look stale.
    offline(q3_state)
    fake_meeting(content_id=1000010425)
    remote = FakeRemote([FakePlayer(5, "INTERNATIONAL", content="1000010425")])
    monkeypatch.setattr(feed, "_client", lambda: remote)
    text = handler("pitwall_players")({})
    assert "other session" not in text


def test_player_open_resolves_content_and_tiles(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting(content_id=1000010425)
    remote = FakeRemote([FakePlayer(5, "INTERNATIONAL")])
    monkeypatch.setattr(feed, "_client", lambda: remote)

    text = handler("pitwall_player_open")({"driver": "LEC", "screen": "3840x2160"})
    assert remote.created["content_id"] == 1000010425
    assert remote.created["stream_title"] == "LEC"
    assert "player 42 -> LEC" in text
    assert "tiled 2 players on the right of 3840x2160" in text
    # the new player got bounds inside the right half of the screen
    assert remote.bounds and remote.bounds[0][1] >= 1920


def test_player_open_without_a_driver_uses_a_named_stream(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting()
    remote = FakeRemote()
    monkeypatch.setattr(feed, "_client", lambda: remote)
    handler("pitwall_player_open")({"stream": "data", "tile": False})
    assert remote.created["stream_title"] == "DATA"


def test_player_open_unknown_driver(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting()
    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    text = handler("pitwall_player_open")({"driver": "XYZ"})
    assert text.startswith("Error: no driver matching")
    assert "VER" in text


def test_player_control_close_and_mute(offline, q3_state, monkeypatch):
    player = FakePlayer(7, "LEC")
    monkeypatch.setattr(feed, "_client", lambda: FakeRemote([player]))
    offline(q3_state)
    assert "closed" in handler("pitwall_player_control")({"player": 7, "action": "close"})
    assert "muted" in handler("pitwall_player_control")({"player": 7, "action": "mute"})
    assert player.actions == ["delete", "mute=True"]


def test_player_control_tiles_every_player(offline, q3_state, monkeypatch):
    offline(q3_state)
    remote = FakeRemote([FakePlayer(1), FakePlayer(2), FakePlayer(3)])
    monkeypatch.setattr(feed, "_client", lambda: remote)
    text = handler("pitwall_player_control")({"action": "tile", "screen": "3840x2160"})
    assert "tiled 3 players" in text
    assert len(remote.bounds) == 3


# --- session-mode gating ---------------------------------------------------


def test_mode_check_hides_race_tools_in_practice(offline, q3_state, monkeypatch):
    offline(q3_state)
    monkeypatch.setattr(feed, "session_mode", lambda: "practice")
    assert tools.mode_check(tools.LAP)() is True
    assert tools.mode_check(tools.RACE)() is False
    assert tools.mode_check(None) is None


def test_mode_check_fails_open_when_the_session_is_unknown(monkeypatch):
    monkeypatch.setattr(feed, "session_mode", lambda: "unknown")
    assert tools.mode_check(tools.RACE)() is True


def test_race_tools_are_absent_from_practice(offline, q3_state):
    offline(q3_state)
    by_mode = {spec.name: spec.modes for spec in tools.specs()}
    assert by_mode["pitwall_pole"] == tools.QUALI
    assert by_mode["pitwall_flying"] == tools.LAP
    assert by_mode["pitwall_race"] == tools.RACE
    assert by_mode["pitwall_overview"] is None


def test_every_schema_is_well_formed():
    for name, schema, _handler in tools.schemas():
        assert schema["name"] == name
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"


def test_screen_comes_from_the_flag_then_the_environment(monkeypatch):
    monkeypatch.delenv("PITWALL_SCREEN", raising=False)
    assert tools._screen(None) == (3840, 2160)          # the documented fallback
    monkeypatch.setenv("PITWALL_SCREEN", "1728x1117")
    assert tools._screen(None) == (1728, 1117)
    assert tools._screen("2560x1440") == (2560, 1440)   # the flag wins
    assert tools._screen("nonsense") == (3840, 2160)
