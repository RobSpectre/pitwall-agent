import pytest
import time

from pitwall import watch
from pitwall.model import build


@pytest.fixture
def q3(q3_state):
    return build(q3_state, mode="qualifying")


def run(session, previous):
    return watch.detect(session, previous)


def test_first_pass_is_a_baseline(q3):
    events, state = run(q3, {})
    assert events == []
    assert state["session_key"] == q3.key
    assert state["pole"] == pytest.approx(69.876)
    assert state["best"]


def test_second_identical_pass_says_nothing(q3):
    _, state = run(q3, {})
    events, _ = run(q3, state)
    assert events == []


def test_red_flag_is_critical(q3):
    _, state = run(q3, {})
    q3.track = "RED FLAG"
    q3.track_message = "RED FLAG"
    events, _ = run(q3, state)
    flagged = [e for e in events if e.level == watch.CRITICAL]
    assert flagged and "RED FLAG" in flagged[0].text


def test_new_pole_is_high(q3):
    _, state = run(q3, {})
    q3.by_num["16"].best_lap = 69.500
    q3.pole = 69.500
    q3.pole_holder = "LEC"
    events, _ = run(q3, state)
    pole_events = [e for e in events if "new pole" in e.text]
    assert pole_events and pole_events[0].level == watch.HIGH
    assert "LEC" in pole_events[0].text


def test_purple_sector_is_high(q3):
    _, state = run(q3, {})
    q3.best_sector[0] = 18.900
    q3.best_sector_holder[0] = "VER"
    events, _ = run(q3, state)
    sectors = [e for e in events if "purple S1" in e.text]
    assert sectors and "18.900" in sectors[0].text


def test_deleted_lap_is_high(q3):
    _, state = run(q3, {})
    q3.messages = list(q3.messages) + [
        {"Utc": "2026-11-07T15:30:00", "Category": "Other",
         "Message": "CAR 16 (LEC) TIME 1:10.044 DELETED - TRACK LIMITS TURN 4"}
    ]
    events, _ = run(q3, state)
    deleted = [e for e in events if "DELETED" in e.text]
    assert deleted and deleted[0].level == watch.HIGH


def test_clock_crossing_two_minutes_is_critical(q3):
    _, state = run(q3, {})
    q3.remaining = 110
    events, _ = run(q3, state)
    clock = [e for e in events if "left" in e.text]
    assert clock and clock[0].level == watch.CRITICAL
    assert "1:50" in clock[0].text


def test_contender_personal_best_is_reported(q3):
    _, state = run(q3, {})
    q3.by_num["16"].best_lap = 70.000          # LEC is P3, inside the window
    events, _ = run(q3, state)
    assert any("LEC 1:10.000" in e.text for e in events)


def test_a_backmarker_personal_best_is_not_reported(q3):
    _, state = run(q3, {})
    q3.by_num["11"].best_lap = 71.500          # PER, P22, more than a second off
    events, _ = run(q3, state)
    assert not any(e.text.startswith("PER") for e in events)


def test_drop_zone_entry_uses_the_q1_cutoff(q1_state):
    q1 = build(q1_state, mode="qualifying")
    _, state = run(q1, {})
    # P10 drops into the bottom of the order, inside the P15 cutoff
    q1.by_num["23"].position = 22
    events, _ = run(q1, state)
    assert any("into the drop zone" in e.text for e in events)


def test_elimination_is_critical(q3):
    _, state = run(q3, {})
    q3.by_num["11"].knocked_out = True
    events, _ = run(q3, state)
    eliminated = [e for e in events if "eliminated" in e.text]
    assert eliminated and eliminated[0].level == watch.CRITICAL


def test_an_event_only_fires_once(q3):
    _, state = run(q3, {})
    q3.track = "YELLOW"
    events, state = run(q3, state)
    assert events
    again, _ = run(q3, state)
    assert again == []


def test_render_puts_critical_first(q3):
    _, state = run(q3, {})
    q3.track = "SAFETY CAR"
    q3.by_num["16"].best_lap = 70.000
    events, _ = run(q3, state)
    text = watch.render(events, "18:00:00")
    assert text.splitlines()[0].startswith("18:00:00  CRIT")
    assert "SAFETY CAR" in text.splitlines()[0]


def test_scan_persists_state_between_runs(q3_state, tmp_path, monkeypatch):
    path = tmp_path / "pitwall-watch.json"
    monkeypatch.setattr(watch, "state_path", lambda: path)
    from pitwall import feed
    from tests.conftest import load

    state = load("q3_interlagos.json")
    feed.reset()
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: state)

    assert watch.scan(path=path) == []
    assert path.exists()
    assert watch.scan(path=path) == []


def test_a_resume_announcement_is_critical():
    assert watch._classify_message({"Message": "SESSION WILL RESUME AT 15:20"}) == watch.CRITICAL
    assert watch._classify_message({"Message": "QUALIFYING WILL RESUME AT 14:05"}) == watch.CRITICAL


def test_an_abandonment_is_critical_and_not_read_as_a_resume():
    # "WILL NOT RESUME" contains "RESUME" -- ordering matters.
    assert watch._classify_message({"Message": "SESSION WILL NOT RESUME"}) == watch.CRITICAL
    assert watch._classify_message({"Message": "SESSION START DELAYED"}) == watch.HIGH


def test_recovery_chatter_is_not_critical():
    for text in ("CLEAR IN TRACK SECTOR 16", "TRACK CLEAR", "GREEN LIGHT - PIT EXIT OPEN"):
        assert watch._classify_message({"Message": text, "Category": "Flag"}) is None, text
    assert watch._classify_message({"Message": "RED FLAG", "Category": "Flag"}) == watch.CRITICAL
