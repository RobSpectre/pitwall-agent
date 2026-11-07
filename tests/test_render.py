import pytest

from pitwall import render
from pitwall.model import build


@pytest.fixture
def q3(q3_state):
    return build(q3_state)


@pytest.fixture
def q1(q1_state):
    return build(q1_state)


def test_overview_is_short_and_complete(q3):
    text = render.overview(q3)
    assert len(text) < 520
    assert "Qualifying Q3" in text
    assert "3:20 LEFT" in text
    assert "pole 1:09.876 VER" in text
    assert "3 on flyers" in text
    assert text.count("\n") <= 7                 # header, pole, weather, counts, flags


def test_board_one_line_per_car_with_states(q3):
    text = render.board(q3)
    assert text.count("\n") == 22 + 2          # header, pole line, 22 rows
    assert "LEC  1:10.044" in text
    assert "PUSH" in text and "OUT" in text and "COOL" in text
    assert len(text) < 1500                     # ~1.2 KB for a full grid


def test_board_marks_the_drop_zone(q1):
    text = render.board(q1)
    assert "!" in text and "X" in text
    assert "marks: X eliminated" in text
    assert "BOT  -" in text                     # no time set


def test_board_respects_gap_tolerance(q3):
    text = render.board(q3, gap_tol=0.25)
    assert "LEC" in text and "PIA" in text
    assert "VER" in text
    assert "ALB" not in text                    # +0.558


def test_flying_lists_only_push_laps(q3):
    text = render.flying(q3)
    assert "ON A FLYER NOW (3)" in text
    for label in ("LEC", "PIA", "ALB"):
        assert label in text
    assert "VER" not in text                    # in the pits, not on a flyer
    assert "proj" in text
    # LEC is 18 thousandths under pole on projection
    assert "-0.018" in text


def test_flying_empty_state_is_a_sentence(q3):
    for driver in q3.drivers:
        driver.run = "PIT"
    text = render.flying(q3)
    assert "ON A FLYER NOW (0)" in text
    assert "none." in text


def test_projection_is_dropped_when_the_reference_lap_is_junk(q3):
    # A car whose "best sectors" came from an out-lap must not get a projection.
    slow = q3.by_num["16"]
    slow.proj = (q3.pole or 0) + 12.0
    text = render.flying(q3)
    assert "proj -  (no clean reference lap)" in text
    assert "1:2" not in text.split("proj -")[1][:12]


def test_driver_in_the_pits_reports_last_lap_sectors_without_deltas(q3):
    ver = q3.by_num["3"]
    ver.sectors[0].t = 34.284
    ver.sectors[1].t = 61.038
    ver.sectors[2].t = 26.405
    text = render.driver_detail(q3, ver)
    assert "last lap S1 34.284" in text
    assert "+" not in text.split("last lap")[1].split("speeds")[0]


def test_purple_reports_holder_and_live_lap(q3):
    text = render.purple(q3)
    assert "S2 34.373 LEC" in text
    assert "S1 19.447 VER" in text
    assert "purple on the live lap: LEC S2 34.373 (push)" in text


def test_purple_ignores_a_stale_purple_flag(q3):
    # A flag that does not actually beat the session best is not purple.
    lec = q3.by_num["16"]
    lec.sectors[1].overall = True
    lec.sectors[1].t = 35.5
    text = render.purple(q3)
    assert "35.500" not in text


def test_pole_flags_the_clock_problem(q1):
    text = render.pole(q1)
    assert "CONTENDERS (within 0.250 of pole)" in text
    assert "out of time" in text


def test_pole_without_times(q3):
    for driver in q3.drivers:
        driver.best_lap = None
    q3.pole = None
    assert "no timed laps yet" in render.pole(q3)


def test_driver_detail(q3):
    text = render.driver_detail(q3, q3.by_num["16"])
    assert "LEC  Charles LECLERC  Ferrari  P3  #16" in text
    assert "best 1:10.044" in text
    assert "PUSH" in text
    assert "SOFT8" in text
    assert len(text) < 400
