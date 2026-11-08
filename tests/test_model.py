from datetime import datetime, timezone

import pytest

from pitwall.model import RUN_STATES, build


# --- real capture ----------------------------------------------------------


def test_real_capture_parses_cleanly(real_state):
    session = build(real_state)
    assert len(session.drivers) == 22
    assert session.meeting == "São Paulo Grand Prix"
    assert session.circuit == "Interlagos"
    assert session.kind == "Practice"
    assert session.part is None
    assert session.pole == pytest.approx(74.933)
    assert session.pole_holder == "HUL"
    assert session.remaining == pytest.approx(3599)
    assert session.track == "ALL CLEAR"


def test_real_capture_run_states(real_state):
    session = build(real_state)
    # HUL has all three sectors on the board: the lap is done, not in progress.
    assert session.by_num["27"].run == "LAP"
    assert session.by_num["27"].sectors_done == 3
    # A car two sectors into a lap that matches its own best sectors is a flyer.
    assert session.by_num["11"].run == "PUSH"
    assert session.by_num["11"].sectors_done == 2
    # 23.833 in sector 1 is four seconds off the session best: not a flyer.
    assert session.by_num["63"].run == "BUILD"
    # MultiViewer's own outlap flag wins over any inference.
    assert session.by_num["16"].run == "OUT"


def test_real_capture_positions_are_ordered(real_state):
    session = build(real_state)
    positions = [d.position for d in session.drivers if d.position]
    assert positions == sorted(positions)


# --- qualifying (synthetic, real schema) -----------------------------------


def test_q3_header_and_clock(q3_state):
    session = build(q3_state)
    assert session.kind == "Qualifying"
    assert session.part == "3"
    assert session.is_qualifying
    assert session.cutoff_pos is None          # nobody is eliminated in Q3
    assert session.remaining == pytest.approx(200)


def test_q3_flyers_are_the_three_push_laps(q3_state):
    session = build(q3_state)
    assert [d.label for d in session.flyers()] == ["LEC", "PIA", "ALB"]
    lec = session.by_num["16"]
    assert lec.sectors_done == 2
    assert lec.proj == pytest.approx(69.858, abs=0.002)
    assert lec.delta_pb < 0                    # ahead of his own best sectors


def test_q3_purple_sector_comes_from_the_live_lap(q3_state):
    session = build(q3_state)
    # LEC's live S2 beats the standing best sector held in TimingStats.
    assert session.best_sector_holder[1] == "LEC"
    assert session.best_sector[1] == pytest.approx(34.373)
    assert session.best_sector_holder[0] == "VER"
    assert session.theo_session < session.pole


def test_q3_states_cover_every_run(q3_state):
    session = build(q3_state)
    assert session.by_num["63"].run == "OUT"
    assert session.by_num["10"].run == "COOL"
    assert session.by_num["12"].run == "LAP"
    assert session.by_num["3"].run == "PIT"
    for driver in session.drivers:
        assert driver.run in RUN_STATES


def test_every_best_lap_beats_the_sum_of_its_sectors(q3_state):
    session = build(q3_state)
    for driver in session.drivers:
        assert driver.theo is not None
        assert driver.best_lap + 1e-6 >= driver.theo


def test_q1_drop_zone_and_eliminations(q1_state):
    session = build(q1_state)
    assert session.part == "1"
    assert session.cutoff_pos == 15
    assert [d.label for d in session.drivers if d.cutoff] == ["CAM", "BEA", "COL", "BOR", "LIN", "BOT", "PER"]
    eliminated = [d.label for d in session.drivers if d.knocked_out]
    assert eliminated == ["BOR", "LIN", "BOT", "PER"]
    no_time = [d.label for d in session.drivers if not d.best_lap]
    assert no_time == ["BOT", "PER"]


def test_laps_left_uses_pole_lap(q3_state):
    session = build(q3_state)
    assert session.lap_time_ref == pytest.approx(69.876)
    assert session.laps_left == 2


# --- defensive -------------------------------------------------------------


@pytest.mark.parametrize("state", [{}, {"TimingData": None}, {"SessionInfo": {}}])
def test_build_survives_an_empty_document(state):
    session = build(state)
    assert session.drivers == []
    assert session.pole is None
    assert session.lap_time_ref == 90.0
    assert session.laps_left == 0


def test_missing_stats_do_not_break_classification(real_state):
    stripped = dict(real_state)
    stripped["TimingStats"] = {"Lines": {}}
    session = build(stripped)
    assert len(session.drivers) == 22
    assert all(d.theo is None for d in session.drivers)
    assert all(d.run in RUN_STATES for d in session.drivers)


def test_missing_mvstatus_falls_back_to_raw_flags(real_state):
    stripped = dict(real_state)
    stripped["TimingData"] = {
        "Lines": {
            num: {k: v for k, v in line.items() if k != "MVStatus"}
            for num, line in real_state["TimingData"]["Lines"].items()
        }
    }
    session = build(stripped)
    assert session.by_num["1"].in_pit is True


# --- session clock ---------------------------------------------------------

STAMP = "2026-11-07T19:26:01.01Z"
#: The feed's clock lags its heartbeat by a few seconds in a healthy document.
BEAT = "2026-11-07T19:26:30Z"
STAMP_AT = datetime(2026, 11, 7, 19, 26, 1, 10000, tzinfo=timezone.utc).timestamp()
BEAT_AT = datetime(2026, 11, 7, 19, 26, 30, tzinfo=timezone.utc).timestamp()
#: Elapsed time the document itself claims, heartbeat against clock stamp.
FEED_ELAPSED = BEAT_AT - STAMP_AT


def clock_state(*, remaining="00:15:36", stamp=STAMP, beat: str | None = BEAT, extrapolating=True):
    state = {
        "SessionInfo": {},
        "ExtrapolatedClock": {"Remaining": remaining, "Utc": stamp, "Extrapolating": extrapolating},
    }
    if beat:
        state["Heartbeat"] = {"Utc": beat}
    return state


def test_clock_runs_on_from_the_feed_snapshot():
    # 15:36 was stamped at the green light and never re-sent; a minute has passed.
    session = build(clock_state(), fetched_at=STAMP_AT + 60)
    assert session.remaining == pytest.approx(936 - 60)


def test_clock_ignores_the_wall_clock_when_it_is_hours_out():
    # A mis-set local clock must not be allowed to zero the session.
    session = build(clock_state(), fetched_at=STAMP_AT + 4 * 3600)
    assert session.remaining == pytest.approx(936 - FEED_ELAPSED)


def test_clock_uses_the_wall_clock_when_there_is_no_heartbeat():
    session = build(clock_state(beat=None), fetched_at=STAMP_AT + 60)
    assert session.remaining == pytest.approx(936 - 60)


def test_clock_does_not_run_when_the_feed_stops_extrapolating():
    # A red flag freezes the clock deliberately; the raw value is the truth.
    session = build(clock_state(extrapolating=False), fetched_at=STAMP_AT + 300)
    assert session.remaining == pytest.approx(936)


def test_clock_is_raw_without_a_fetch_time():
    assert build(clock_state()).remaining == pytest.approx(936)


def test_clock_never_goes_negative():
    session = build(clock_state(remaining="00:00:20"), fetched_at=STAMP_AT + 600)
    assert session.remaining == pytest.approx(0)


def test_clock_falls_back_to_the_heartbeat_before_the_stamp():
    # The document itself says 29 s have passed; a fetch predating its own
    # timestamp is a clock problem, not a reason to rewind the session.
    session = build(clock_state(), fetched_at=STAMP_AT - 5)
    assert session.remaining == pytest.approx(936 - FEED_ELAPSED)
