import pytest

from pitwall import feed, meeting, plugin, render
from pitwall.model import build


# --- session-type mapping --------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Practice", "practice"),
        ("fp1", "practice"),
        ("FP3", "practice"),
        ("weekend_warmup", "practice"),
        ("Qualifying", "qualifying"),
        ("Sprint Qualifying", "qualifying"),
        ("sprint_shootout", "qualifying"),
        ("Race", "race"),
        ("Sprint", "race"),
        ("", "unknown"),
        ("something new", "unknown"),
    ],
)
def test_mode_for_covers_every_spelling(raw, expected):
    assert meeting.mode_for(raw) == expected


# --- race model ------------------------------------------------------------


def test_race_session_parses_lap_count_and_stints(race_state):
    session = build(race_state, mode="race")
    assert session.mode == "race"
    assert session.current_lap == 32
    assert session.total_laps == 57

    ver = session.by_num["3"]
    assert ver.tyre == "M14"              # medium, 32 - 18 laps old
    assert ver.stops == 1                 # two runs so far
    assert len(ver.stints) == 2
    assert ver.stints[0].compound == "SOFT"    # opened on the soft
    assert ver.stints[0].age == 17
    assert ver.stints[1].compound == "MEDIUM"


def test_race_intervals_and_pit_times(race_state):
    session = build(race_state, mode="race")
    p4 = session.by_num["81"]             # 0.412 behind the car ahead
    assert p4.position == 4
    assert p4.gap_ahead == pytest.approx(0.412)
    assert session.by_num["14"].pit_time is None or session.by_num["14"].pit_time > 0


def test_race_render_shows_lap_tyres_and_drs(race_state):
    session = build(race_state, mode="race")
    text = render.race(session)
    assert "LAP 32/57" in text
    assert "TYRE" in text and "STOPS" in text
    assert "M14" in text
    assert "inside DRS range" in text


def test_battle_finds_the_fight_for_p3(race_state):
    session = build(race_state, mode="race")
    text = render.battle(session)
    assert "P3 LEC" in text and "P4 PIA" in text
    assert "gap +0.412" in text
    assert "DRS" in text


def test_battle_empty_when_nothing_is_close(race_state):
    session = build(race_state, mode="race")
    assert "none inside 0.1s" in render.battle(session, window=0.1)


def test_strategy_lists_runs_and_oldest_tyres(race_state):
    session = build(race_state, mode="race")
    text = render.strategy(session)
    assert "STRATEGY" in text
    assert "M14" in text and "H20" in text
    assert "oldest tyres" in text
    assert "laps remaining" in text


def test_pace_table_uses_the_current_run(race_state):
    session = build(race_state, mode="race")
    text = render.pace(session)
    assert "RUN BEST" in text
    assert "M14" in text


# --- meeting resolution ----------------------------------------------------


def test_resolve_joins_on_session_key(q3_state, fake_meeting):
    fake_meeting(session_key="11407", content_id=1000010425)
    resolution = meeting.resolve(q3_state)
    assert resolution.ok
    assert resolution.content_id == 1000010425
    assert resolution.matched_on == "session_key"


def test_resolve_falls_back_to_the_title(q3_state, fake_meeting):
    fake_meeting(session_key="nope", title="Qualifying", content_id=555)
    resolution = meeting.resolve(q3_state)
    assert resolution.content_id == 555
    assert resolution.matched_on == "title"


def test_resolve_reports_missing_content_without_inventing_it(q3_state, fake_meeting):
    fake_meeting(session_key="nope", title="Practice 1", content_id=None)
    resolution = meeting.resolve(q3_state)
    assert resolution.content_id is None
    assert resolution.matched_on == ""


def test_resolve_without_a_meeting_key_is_empty():
    resolution = meeting.resolve({"SessionInfo": {}})
    assert resolution.meeting_key == ""
    assert resolution.content_id is None


# --- /pitwall init ---------------------------------------------------------


class FakeInitRemote:
    version = {"data": {"version": "2.15.2"}}
    players: list = []


def test_init_report_when_multiviewer_is_down(monkeypatch):
    def boom():
        raise RuntimeError("Connection refused")

    monkeypatch.setattr(feed, "_client", boom)
    text = plugin.init_report()
    assert "NOT REACHABLE" in text
    assert "Connection refused" in text


def test_init_report_identifies_the_session(offline, q3_state, fake_meeting, monkeypatch):
    offline(q3_state)
    fake_meeting()
    monkeypatch.setattr(feed, "_client", lambda: FakeInitRemote())
    monkeypatch.setattr(feed, "session_mode", lambda: "qualifying")
    text = plugin.init_report()
    assert "MultiViewer 2.15.2" in text
    assert "session   Qualifying Q3" in text
    assert "content   1000010425  matched on session_key" in text
    assert "mode      qualifying" in text


def test_init_report_names_practice_by_its_number(offline, real_state, fake_meeting, monkeypatch):
    offline(real_state)
    fake_meeting(session_key="11407", content_id=1000010425)
    monkeypatch.setattr(feed, "_client", lambda: FakeInitRemote())
    monkeypatch.setattr(feed, "session_mode", lambda: "practice")
    text = plugin.init_report()
    assert "session   Practice 3" in text
    assert "mode      practice" in text
    assert "flying, pace" in text


def test_init_report_says_so_when_live_timing_is_idle(monkeypatch, offline):
    offline({"SessionInfo": {}})
    monkeypatch.setattr(feed, "_client", lambda: FakeInitRemote())
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: (_ for _ in ()).throw(
        feed.PitwallError("Live timing is idle")
    ))
    text = plugin.init_report()
    assert "NOT AVAILABLE" in text
    assert "Live timing is idle" in text


# --- command routing -------------------------------------------------------


def test_command_routes_init(monkeypatch):
    monkeypatch.setattr(plugin, "init_report", lambda: "INIT OK")
    assert plugin._command("init") == "INIT OK"


def test_command_routes_race(offline, race_state):
    offline(race_state)
    text = plugin._command("race")
    assert "STRATEGY" not in text and "LAP 32/57" in text


def test_command_unknown_driver_lists_help(offline, q3_state):
    offline(q3_state)
    assert "no driver" in plugin._command("XYZ")
