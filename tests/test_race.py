from types import SimpleNamespace

import pytest

from pitwall import history, race
from pitwall.model import build

TOTAL = 57
NOW = 32
EFFECT = race.FUEL_EFFECT
START_KG = race.FUEL_START_KG


def _state(*, lap=NOW, total=TOTAL) -> dict:
    return {
        "SessionInfo": {
            "Key": 7, "Type": "Race", "Name": "Race",
            "Meeting": {"Key": 1, "Name": "Test", "Circuit": {"ShortName": "Test"}},
        },
        "LapCount": {"CurrentLap": str(lap), "TotalLaps": str(total)},
        "TimingData": {
            "Lines": {
                "1": {
                    "RacingNumber": "1", "Position": "1", "NumberOfLaps": lap,
                    "TimeDiffToFastest": "", "TimeDiffToPositionAhead": "",
                    "LastLapTime": {"Value": "1:35.000"},
                    "MVStatus": {"InPit": False, "PitOut": False, "Stopped": False},
                },
                "2": {
                    "RacingNumber": "2", "Position": "2", "NumberOfLaps": lap,
                    "TimeDiffToFastest": "+0.500", "TimeDiffToPositionAhead": "0.500",
                    "LastLapTime": {"Value": "1:34.750"},
                    "MVStatus": {"InPit": False, "PitOut": False, "Stopped": False},
                },
            }
        },
        "DriverList": {
            "1": {"Tla": "AAA", "FullName": "A Driver"},
            "2": {"Tla": "BBB", "FullName": "B Driver"},
        },
        "TimingAppData": {
            "Lines": {
                "1": {"Stints": [{"Compound": "MEDIUM", "StartLaps": 18, "TotalLaps": lap, "New": "true"}]},
                "2": {"Stints": [{"Compound": "MEDIUM", "StartLaps": 18, "TotalLaps": lap, "New": "true"}]},
            }
        },
    }


def _laps(nums: dict[str, float], *, slope: float = 0.06, start_stint: int = 18,
          stop: int = NOW, total: int = TOTAL) -> dict:
    """History with a known degradation slope and the standard fuel burn."""
    laps = {}
    for num, base in nums.items():
        rows = []
        for lap in range(start_stint + 1, stop + 1):
            fuel = race.fuel_kg(lap, total, START_KG) * EFFECT
            rows.append({"lap": lap, "t": round(base + fuel + slope * (lap - start_stint), 3)})
        laps[num] = rows
    return {"session": 7, "laps": laps}


@pytest.fixture
def clocked(monkeypatch):
    """Install a synthetic lap history for the race fixture."""
    def install(payload: dict):
        monkeypatch.setattr(history, "read", lambda session_key=None: payload)
        return payload
    return install


# --- fuel ------------------------------------------------------------------


def test_fuel_burn_falls_linearly_to_zero():
    assert race.fuel_kg(1, 57) == pytest.approx(110.0)
    assert race.fuel_kg(57, 57) == pytest.approx(110.0 * (1 - 56 / 57))
    assert race.fuel_kg(58, 57) == pytest.approx(0.0)
    assert race.fuel_kg(3, None) == 0.0


def test_fuel_correction_recovers_the_empty_tank_time(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.0))
    session = build(_state(), mode="race")
    rows = {row.label: row for row in race.fuel_pace(session)}
    # The generated lap is base + fuel penalty, so the corrected value is base.
    assert rows["AAA"].corrected == pytest.approx(90.0, abs=0.002)
    assert rows["AAA"].raw == pytest.approx(90.0 + EFFECT * race.fuel_kg(NOW, TOTAL), abs=0.002)
    assert rows["AAA"].delta == pytest.approx(0.0, abs=0.002)


# --- degradation -----------------------------------------------------------


def test_degradation_recovers_a_known_slope(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.08))
    session = build(_state(), mode="race")
    rows = {row.label: row for row in race.degradation(session)}
    assert rows["AAA"].slope == pytest.approx(0.08, abs=0.004)
    # The out-lap on the set (age 1) is not tyre pace and is not fitted.
    assert rows["AAA"].samples == 13
    assert rows["AAA"].five_lap_cost == pytest.approx(0.40, abs=0.03)
    assert rows["AAA"].r2 > 0.99


def test_a_flat_tyre_fits_flat(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.0))
    session = build(_state(), mode="race")
    assert race.degradation(session)[0].slope == pytest.approx(0.0, abs=0.004)


def _noisy_laps(nums: dict[str, float], *, noise: float = 0.7, slope: float = 0.02,
                start_stint: int = 18, stop: int = NOW, total: int = TOTAL) -> dict:
    """Same fuel burn, same nominal slope, but lap-to-lap noise that swamps it.

    This is the shape of an early-race "fit" on a car whose tyres are fine: a
    line through the noise, with an R² that explains almost nothing.
    """
    laps = {}
    for num, base in nums.items():
        rows = []
        for index, lap in enumerate(range(start_stint + 1, stop + 1)):
            fuel = race.fuel_kg(lap, total, START_KG) * EFFECT
            wobble = noise if index % 2 == 0 else -noise
            rows.append({"lap": lap, "t": round(base + fuel + slope * (lap - start_stint) + wobble, 3)})
        laps[num] = rows
    return {"session": 7, "laps": laps}


def test_a_noisy_fit_is_not_trusted(clocked):
    clocked(_noisy_laps({"1": 90.0, "2": 90.0}))
    row = {r.label: r for r in race.degradation(build(_state(), mode="race"))}["AAA"]
    assert row.r2 is not None and row.r2 < race.MIN_FIT_R2
    assert row.trusted is False


def test_a_noisy_fit_is_priced_at_the_book_rate(clocked):
    clocked(_noisy_laps({"1": 90.0, "2": 90.0}))
    session = build(_state(), mode="race")
    rows = race.plans(session, session.by_num["1"], stops=1)
    assert rows
    assert rows[0].basis.startswith("M book")


def test_a_clean_fit_is_priced_as_measured(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.09))
    session = build(_state(), mode="race")
    rows = race.plans(session, session.by_num["1"], stops=1)
    assert rows
    assert rows[0].basis.startswith("M measured")


def test_degradation_uses_the_current_set_only(clocked):
    # Two stints on the same compound: pooling them would blend two different
    # tyre lives into one meaningless slope.
    state = _state(lap=15)
    state["TimingAppData"] = {
        "Lines": {"1": {"Stints": [
            {"Compound": "SOFT", "StartLaps": 0, "TotalLaps": 10, "New": "true"},
            {"Compound": "SOFT", "StartLaps": 10, "TotalLaps": 15, "New": "true"},
        ]}}
    }
    rows = {
        "1": [
            {
                "lap": lap,
                "t": 90.0
                + race.fuel_kg(lap, TOTAL, START_KG) * EFFECT
                + (0.02 if lap <= 10 else 0.10) * (lap - (0 if lap <= 10 else 10)),
            }
            for lap in range(1, 16)
        ]
    }
    clocked({"session": 7, "laps": rows})
    session = build(state, mode="race")
    row = race.degradation(session)[0]
    assert row.samples == 4
    assert row.slope == pytest.approx(0.10, abs=0.001)


def test_without_history_there_is_no_slope(clocked):
    clocked({"session": 7, "laps": {}})
    session = build(_state(), mode="race")
    rows = race.degradation(session)
    assert rows and all(row.slope is None for row in rows)


# --- overtakes -------------------------------------------------------------


def test_a_faster_car_gets_a_pass_lap(clocked):
    # 0.5 s behind at 0.25 s/lap quicker: caught in two laps.
    clocked(_laps({"1": 90.0, "2": 89.75}, slope=0.02))
    session = build(_state(), mode="race")
    rows = race.overtakes(session, window=2.0)
    assert rows, "the pair should be inside the window"
    row = rows[0]
    assert row.behind == "BBB" and row.ahead == "AAA"
    assert row.delta == pytest.approx(0.25, abs=0.02)
    assert row.drs is True
    assert row.pass_lap == NOW + 2


def test_a_slower_car_is_holding_station(clocked):
    clocked(_laps({"1": 89.75, "2": 90.0}, slope=0.02))
    session = build(_state(), mode="race")
    row = race.overtakes(session, window=2.0)[0]
    assert row.delta is not None and row.delta < 0
    assert row.pass_lap is None
    assert "pulled away" in row.note


def test_no_recent_laps_means_no_delta(clocked):
    clocked({"session": 7, "laps": {}})
    session = build(_state(), mode="race")
    row = race.overtakes(session, window=2.0)[0]
    assert row.delta is None
    assert row.pass_lap is None


# --- pit plans -------------------------------------------------------------


def test_a_negative_fit_is_clamped_instead_of_projected(clocked):
    # Early in a race the track rubbers in faster than the tyres wear, so a short
    # fit can slope downwards. Projecting that for a race distance is fantasy.
    laps = {"1": [{"lap": 30, "t": 90.30}, {"lap": 31, "t": 90.10}, {"lap": 32, "t": 89.90}]}
    clocked({"session": 7, "laps": laps})
    session = build(_state(), mode="race")
    rows = race.plans(session, session.by_num["1"], stops=1)
    assert rows
    per_lap = rows[0].total / 25
    assert 89.0 < per_lap < 96.0


def test_plans_are_ranked_and_positive(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.09))
    session = build(_state(), mode="race")
    driver = session.by_num["1"]
    rows = race.plans(session, driver, stops=1)
    assert rows
    assert all(plan.total > 0 for plan in rows)
    assert [plan.total for plan in rows] == sorted(plan.total for plan in rows)


def test_an_expensive_stop_makes_staying_out_better(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.09))
    session = build(_state(), mode="race")
    driver = session.by_num["1"]
    rows = race.plans(session, driver, stops=1, pit_loss=400.0)
    assert rows and rows[0].vs_stay_out > 0


def test_no_plans_without_a_race_distance_or_laps(clocked):
    clocked({"session": 7, "laps": {}})
    session = build(_state(), mode="race")
    assert race.plans(session, session.by_num["1"], stops=1) == []
    qual = build({"SessionInfo": {"Key": 1, "Type": "Qualifying"}}, mode="qualifying")
    assert race.plans(qual, SimpleNamespace(stints=[], num="1", label="AAA"), stops=1) == []

# --- gaps, undercut, windows, outlook --------------------------------------


def _no_intervals(state: dict) -> dict:
    for line in state["TimingData"]["Lines"].values():
        line["TimeDiffToPositionAhead"] = ""
        line["TimeDiffToFastest"] = ""
    return state


def test_gaps_are_rebuilt_from_lap_history(clocked):
    clocked(_laps({"1": 90.0, "2": 90.5}, slope=0.0))
    session = build(_no_intervals(_state()), mode="race")
    info = race.gap_info(session)
    assert info.source == "laps"
    # 14 recorded laps, half a second a lap slower: seven seconds back.
    assert info.leader["2"] == pytest.approx(7.0, abs=0.05)
    assert info.ahead["2"] == pytest.approx(7.0, abs=0.05)


def test_gaps_prefer_the_feed_when_it_publishes_them(clocked):
    clocked(_laps({"1": 90.0, "2": 90.5}, slope=0.0))
    info = race.gap_info(build(_state(), mode="race"))
    assert info.source == "feed"
    assert info.leader["2"] == pytest.approx(0.5, abs=0.001)


def test_a_hole_in_the_history_removes_that_car(clocked):
    laps = _laps({"1": 90.0, "2": 90.5}, slope=0.0)["laps"]
    laps["2"] = [row for row in laps["2"] if row["lap"] != 25]   # deleted lap
    clocked({"session": 7, "laps": laps})
    info = race.gap_info(build(_no_intervals(_state()), mode="race"))
    assert "2" not in info.leader, "a car with a missing lap must not get a gap"
    assert info.source == "none"


def test_undercut_cover_is_the_gap_over_the_wear_rate(clocked):
    # Car 2 is 0.05 s/lap slower over 14 laps: a 0.7 s gap. Car 1 loses
    # 0.06 s/lap, so the cover is 0.7 / 0.06 ≈ 12 laps.
    clocked(_laps({"1": 90.0, "2": 90.05}, slope=0.06))
    session = build(_no_intervals(_state()), mode="race")
    data = race.undercut(session, window=10.0)
    assert data.source == "laps"
    row = next(row for row in data.rows if row.behind == "BBB")
    assert row.laps == pytest.approx(11.7, abs=0.6)
    assert "lose the place" in row.verdict


def test_undercut_says_so_when_the_chase_is_hopeless(clocked):
    clocked(_laps({"1": 90.0, "2": 90.6}, slope=0.06))
    session = build(_no_intervals(_state()), mode="race")
    data = race.undercut(session, window=10.0)
    row = next(row for row in data.rows if row.behind == "BBB")
    assert "cannot be undercut" in row.verdict


def test_windows_flag_who_cannot_reach_the_flag(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.06))
    session = build(_state(), mode="race")
    rows = {row.label: row for row in race.windows(session)}
    # 14 laps old on a 38-lap medium with 25 to run: the set runs out first.
    assert rows["AAA"].reach_flag is False
    assert rows["AAA"].must_stop_by == 32 + 24


def test_observed_tyre_age_raises_the_book_life(clocked):
    clocked(_laps({"1": 90.0, "2": 90.0}, slope=0.06))
    session = build(_state(), mode="race")
    session.by_num["1"].stints[-1].total_laps = 60      # a 42-lap-old set
    life = race.tyre_life(session)
    assert life["MEDIUM"] >= 47


def test_outlook_projects_an_order(clocked):
    clocked(_laps({"1": 90.0, "2": 90.5}, slope=0.02))
    session = build(_no_intervals(_state()), mode="race")
    data = race.outlook(session)
    assert data.source == "laps"
    assert data.rows and data.rows[0].projected == pytest.approx(0.0)
    assert [row.projected for row in data.rows] == sorted(row.projected for row in data.rows)


def test_outlook_refuses_without_gaps(clocked):
    clocked({"session": 7, "laps": {}})
    data = race.outlook(build(_no_intervals(_state()), mode="race"))
    assert data.source == "none"
    assert data.rows == []


# --- the race table is ordered by position --------------------------------


def test_race_table_is_in_running_order_not_best_lap_order():
    from pitwall import render

    state = _state()
    state["TimingData"]["Lines"]["1"]["BestLapTime"] = {"Value": "1:40.000"}
    state["TimingData"]["Lines"]["2"]["BestLapTime"] = {"Value": "1:30.000"}
    session = build(state, mode="race")
    lines = render.race(session).splitlines()
    rows = [line for line in lines if line[:2].strip().isdigit()]
    assert rows[0].split()[1] == "AAA", "P1 must come first whatever the best lap says"
    assert rows[1].split()[1] == "BBB"
