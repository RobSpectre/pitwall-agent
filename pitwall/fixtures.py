"""Build synthetic but schema-accurate qualifying snapshots.

The document shape comes from a real FP3 live-timing capture; only the timing
values are authored.  That keeps fixture tests honest about the wire format
(which is the part that breaks) while letting us exercise qualifying-only
fields -- ``QualifyingPart``, ``KnockedOut``, ``Cutoff`` -- that no offline
session can produce on demand.

Used by ``tests/`` and by ``python -m pitwall.fixtures`` to regenerate the
committed JSON files.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE.parent / "tests" / "fixtures"
REAL = FIXTURE_DIR / "fp3_interlagos_real.json"


#: (racing number, TLA) in the 2026 grid order used by the real capture.
GRID = [
    ("1", "NOR"), ("3", "VER"), ("5", "BOR"), ("6", "HAD"), ("10", "GAS"), ("11", "PER"),
    ("12", "ANT"), ("14", "ALO"), ("16", "LEC"), ("18", "STR"), ("23", "ALB"), ("27", "HUL"),
    ("30", "LAW"), ("41", "LIN"), ("43", "COL"), ("44", "HAM"), ("51", "CAM"), ("55", "SAI"),
    ("63", "RUS"), ("77", "BOT"), ("81", "PIA"), ("87", "BEA"),
]


def _sec(value: float) -> str:
    return f"{value:.3f}"


def _lap(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes}:{seconds - minutes * 60:06.3f}"


def _segments(status: int, count: int = 7) -> list[dict]:
    return [{"Status": status} for _ in range(count)]


def _timing_line(num: str, tla: str, position: int, *,
                 best_lap: float | None,
                 sectors: tuple[float | None, float | None, float | None],
                 laps: int,
                 in_pit: bool = False,
                 outlap: bool = False,
                 cutoff: bool = False,
                 knocked_out: bool = False,
                 purple_flags: tuple[bool, bool, bool] = (False, False, False),
                 pb_flags: tuple[bool, bool, bool] = (False, False, False),
                 status: int = 64,
                 last_lap: float | None = None) -> dict:
    sector_rows = []
    for i in range(3):
        value = sectors[i]
        sector_rows.append({
            "Stopped": False,
            "Value": _sec(value) if value else "",
            "Status": 2051 if purple_flags[i] else (2049 if pb_flags[i] else (2048 if value else 0)),
            "OverallFastest": purple_flags[i],
            "PersonalFastest": pb_flags[i] or purple_flags[i],
            "Segments": _segments(2051 if purple_flags[i] else (2049 if value else 0)),
        })
    return {
        "RacingNumber": num,
        "Line": position,
        "Position": str(position),
        "ShowPosition": True,
        "Retired": False,
        "InPit": in_pit,
        "PitOut": False,
        "Stopped": False,
        "Status": status,
        "Sectors": sector_rows,
        "Speeds": {
            "I1": {"Value": "320", "Status": 0, "OverallFastest": False, "PersonalFastest": False},
            "I2": {"Value": "240", "Status": 0, "OverallFastest": False, "PersonalFastest": False},
            "FL": {"Value": "310", "Status": 0, "OverallFastest": False, "PersonalFastest": False},
            "ST": {"Value": "295", "Status": 0, "OverallFastest": False, "PersonalFastest": False},
        },
        "BestLapTime": {"Value": _lap(best_lap) if best_lap else "", "Lap": laps - 1 if best_lap else 0},
        "LastLapTime": (
            {"Value": _lap(last_lap), "Status": 0, "OverallFastest": False, "PersonalFastest": False}
            if last_lap else {"Value": "", "Status": 0, "OverallFastest": False, "PersonalFastest": False}
        ),
        "NumberOfLaps": laps,
        "MVStatus": {
            "Stopped": False,
            "Retired": False,
            "InPit": in_pit,
            "PitOut": False,
            "ShowPosition": True,
            "KnockedOut": knocked_out,
            "Cutoff": cutoff,
            "Outlap": outlap,
            "TakenChequered": False,
        },
    }


def _stats_line(num: str, position: int, sectors: tuple[float, float, float], best_lap: float) -> dict:
    return {
        "Line": position,
        "RacingNumber": num,
        "PersonalBestLapTime": {"Value": _lap(best_lap), "Lap": 11, "Position": position},
        "BestSectors": [{"Value": _sec(s), "Position": position} for s in sectors],
        "BestSpeeds": {
            "I1": {"Value": "320"}, "I2": {"Value": "240"},
            "FL": {"Value": "310"}, "ST": {"Value": "295"},
        },
    }


def _stint(start: int, total: int, compound: str = "SOFT", lap_time: float = 70.0) -> dict:
    """One tyre run in the shape the real feed uses."""
    return {
        "Stint": 1,
        "Compound": compound,
        "New": "true",
        "TyresNotChanged": "0",
        "StartLaps": start,
        "TotalLaps": total,
        "LapNumber": total + 1,
        "LapTime": _lap(lap_time),
    }


def _base() -> dict:
    if not REAL.is_file():
        raise FileNotFoundError(
            f"{REAL} is missing. It is the real FP3 capture the fixtures are shaped from; "
            "regenerate it with `python -m pitwall.fixtures --capture` from a running MultiViewer."
        )
    return json.loads(REAL.read_text(encoding="utf-8"))


def build_q3() -> dict:
    """Q3 with 3:20 on the clock: two cars on flyers, one purple, one in the garage."""
    state = copy.deepcopy(_base())
    info = state["SessionInfo"]
    info["Type"] = "Qualifying"
    info["Name"] = "Qualifying"
    info["Number"] = 1
    state["SessionData"]["StatusSeries"] = [
        {"Utc": "2026-11-07T15:00:00.000Z", "SessionStatus": "Started"},
        {"Utc": "2026-11-07T15:18:00.000Z", "QualifyingPart": 3},
    ]
    state["SessionStatus"] = {"Status": "Started", "Started": "Started"}
    state["ExtrapolatedClock"] = {"Utc": "2026-11-07T15:24:40.000Z", "Remaining": "00:03:20", "Extrapolating": True}
    state["TimingStats"]["SessionType"] = "Qualifying"

    order = [
        # num, gap to pole, run state
        ("3",  0.000, "PIT"),
        ("1",  0.105, "PIT"),
        ("16", 0.168, "PUSH2"),
        ("81", 0.226, "PUSH1"),
        ("63", 0.274, "OUT"),
        ("44", 0.329, "PIT"),
        ("14", 0.401, "PIT"),
        ("12", 0.452, "LAP"),
        ("55", 0.511, "PIT"),
        ("23", 0.558, "PUSH1"),
        ("6",  0.607, "PIT"),
        ("27", 0.664, "LAP"),
        ("10", 0.702, "COOL"),
        ("30", 0.755, "PIT"),
        ("18", 0.801, "PIT"),
        ("51", 0.849, "PIT"),
        ("87", 0.902, "PIT"),
        ("43", 0.958, "PIT"),
        ("5",  1.014, "PIT"),
        ("41", 1.077, "PIT"),
        ("77", 1.135, "PIT"),
        ("11", 1.190, "PIT"),
    ]
    pole = 69.876

    def sectors_for(best: float, deficit: float) -> tuple[float, float, float]:
        """Split one lap into sectors whose sum is ``best - deficit``.

        A driver's best lap is always slower than the sum of his own best
        sectors, so the deficit is what "time on the table" measures.
        """
        total = best - deficit
        first = round(total * 0.2785, 3)
        second = round(total * 0.4926, 3)
        return first, second, round(total - first - second, 3)
    timing: dict = {}
    stats: dict = {}
    app: dict = {}
    for position, (num, gap, run) in enumerate(order, start=1):
        tla = dict(GRID)[num]
        best = round(pole + gap, 3)
        deficit = 0.031 + (position % 5) * 0.017
        sectors = sectors_for(best, deficit)
        in_pit = run == "PIT"
        outlap = run == "OUT"
        laps = 14 + (position % 4)

        if run == "PUSH1":
            # S1 complete, on a flyer slightly up on his own PB
            live = (sectors[0] - 0.021, None, None)
            purple = (False, False, False)
            pb = (True, False, False)
        elif run == "PUSH2":
            # S1 personal best, S2 faster than the standing session best
            live = (sectors[0] - 0.014, round(sectors[1] - 0.090, 3), None)
            purple = (False, True, False)
            pb = (True, True, False)
        elif outlap:
            live = (None, None, None)
            purple = (False, False, False)
            pb = (False, False, False)
        elif run == "LAP":
            live = (None, None, None)
            purple = (False, False, False)
            pb = (False, False, False)
        elif run == "COOL":
            live = (sectors[0] + 1.9, None, None)
            purple = (False, False, False)
            pb = (False, False, False)
        else:
            live = (None, None, None)
            purple = (False, False, False)
            pb = (False, False, False)

        timing[num] = _timing_line(
            num, tla, position, best_lap=best, sectors=live, laps=laps,
            in_pit=in_pit, outlap=outlap, purple_flags=purple, pb_flags=pb,
        )
        stats[num] = _stats_line(num, position, sectors, best)
        app[num] = {"RacingNumber": num, "Line": position, "Stints": [_stint(0, 8)]}

    state["TimingData"]["Lines"] = timing
    state["TimingStats"]["Lines"] = stats
    state["TimingAppData"]["Lines"] = app
    state["RaceControlMessages"]["Messages"] = [
        {"Utc": "2026-11-07T15:18:02", "Category": "Flag", "Flag": "GREEN",
         "Scope": "Track", "Message": "GREEN LIGHT - PIT EXIT OPEN"},
        {"Utc": "2026-11-07T15:21:44", "Category": "Other", "Message": "CAR 41 TIME DELETED - TRACK LIMITS TURN 4"},
    ]
    return state


def build_q1() -> dict:
    """Q1 with 2:00 left: the drop zone matters and two cars are already out."""
    state = build_q3()
    info = state["SessionInfo"]
    info["Name"] = "Qualifying"
    state["SessionData"]["StatusSeries"] = [
        {"Utc": "2026-11-07T14:00:00.000Z", "SessionStatus": "Started"},
        {"Utc": "2026-11-07T14:02:00.000Z", "QualifyingPart": 1},
    ]
    state["ExtrapolatedClock"] = {"Utc": "2026-11-07T14:16:00.000Z", "Remaining": "00:02:00", "Extrapolating": True}

    lines = state["TimingData"]["Lines"]
    stats = state["TimingStats"]["Lines"]
    # P16..P22 are in the drop zone or eliminated; the last two never set a time.
    for position in range(16, 23):
        num = [n for n, d in lines.items() if int(d["Position"]) == position][0]
        lines[num]["MVStatus"]["Cutoff"] = True
        if position >= 19:
            lines[num]["MVStatus"]["KnockedOut"] = True
    for position in (21, 22):
        num = [n for n, d in lines.items() if int(d["Position"]) == position][0]
        lines[num]["BestLapTime"] = {"Value": ""}
        lines[num]["LastLapTime"] = {"Value": ""}
        lines[num]["Sectors"] = [dict(s, Value="") for s in lines[num]["Sectors"]]
        lines[num]["MVStatus"]["KnockedOut"] = True
        stats.pop(num, None)
    return state


def build_race() -> dict:
    """Lap 32 of 57: a two-stop race with a fight for P3 and a penalty pending."""
    state = build_q3()
    info = state["SessionInfo"]
    info["Type"] = "Race"
    info["Name"] = "Race"
    info["Number"] = 1
    state["SessionData"]["StatusSeries"] = [
        {"Utc": "2026-11-08T17:00:00.000Z", "SessionStatus": "Started"},
    ]
    state["ExtrapolatedClock"] = {"Utc": "2026-11-07T16:32:00.000Z", "Remaining": "00:52:10", "Extrapolating": True}
    state["LapCount"] = {"CurrentLap": "32", "TotalLaps": "57"}
    state["TimingStats"]["SessionType"] = "Race"

    lines = state["TimingData"]["Lines"]
    stats = state["TimingStats"]["Lines"]
    app = state["TimingAppData"]["Lines"]

    # (position, number, interval to the car ahead, last lap, compound, stint start lap)
    plan = [
        (1, "3", 0.0, 72.410, "MEDIUM", 18),
        (2, "1", 4.212, 72.388, "MEDIUM", 18),
        (3, "16", 6.901, 72.201, "HARD", 12),
        (4, "81", 0.412, 72.150, "HARD", 12),
        (5, "63", 0.884, 72.334, "SOFT", 25),
        (6, "44", 2.310, 72.502, "SOFT", 25),
        (7, "14", 8.774, 72.640, "MEDIUM", 18),
        (8, "12", 11.205, 72.811, "HARD", 12),
        (9, "55", 14.902, 72.944, "MEDIUM", 18),
        (10, "23", 18.338, 73.010, "HARD", 12),
        (11, "6", 21.774, 73.220, "MEDIUM", 18),
        (12, "27", 25.101, 73.388, "SOFT", 25),
        (13, "10", 28.554, 73.401, "HARD", 12),
        (14, "30", 31.902, 73.512, "MEDIUM", 18),
        (15, "18", 35.220, 73.640, "HARD", 12),
        (16, "51", 38.771, 73.802, "SOFT", 25),
        (17, "87", 42.115, 73.910, "MEDIUM", 18),
        (18, "43", 45.660, 74.020, "HARD", 12),
        (19, "5", 49.202, 74.101, "MEDIUM", 18),
        (20, "41", 53.884, 74.220, "SOFT", 25),
        (21, "77", 58.102, 74.388, "HARD", 12),
        (22, "11", 62.771, 74.502, "MEDIUM", 18),
    ]
    pit_times = {}
    for position, num, interval, last_lap, compound, start in plan:
        line = lines[num]
        line["Position"] = str(position)
        line["Line"] = position
        line["TimeDiffToFastest"] = "" if position == 1 else f"{sum(p[2] for p in plan[:position]):.3f}"
        line["TimeDiffToPositionAhead"] = "" if position == 1 else f"{interval:.3f}"
        line["LastLapTime"] = {
            "Value": _lap(last_lap), "Status": 0,
            "OverallFastest": position == 3, "PersonalFastest": position == 3,
        }
        line["NumberOfLaps"] = 31
        line["Sectors"] = [
            {"Stopped": False, "Value": "", "Status": 0, "OverallFastest": False,
             "PersonalFastest": False, "Segments": _segments(0)}
            for _ in range(3)
        ]
        line["MVStatus"]["InPit"] = False
        line["MVStatus"]["Outlap"] = False

        first_end = 17 if start >= 18 else 11
        app[num] = {
            "RacingNumber": num,
            "Line": position,
            "Stints": [
                _stint(0, first_end, "SOFT" if start >= 18 else "MEDIUM", last_lap + 0.9),
                _stint(start, 32, compound, last_lap),
            ],
        }
        stats[num]["BestSectors"] = [
            {"Value": _sec(19.9), "Position": position},
            {"Value": _sec(36.1), "Position": position},
            {"Value": _sec(16.2), "Position": position},
        ]
        if position % 7 == 0:
            pit_times[num] = {"RacingNumber": num, "Duration": "21.904", "Lap": "18"}

    state["PitLaneTimeCollection"] = {"PitTimes": pit_times}
    state["TrackStatus"] = {"Status": "1", "Message": "AllClear"}
    state["RaceControlMessages"]["Messages"] = [
        {"Utc": "2026-11-08T16:58:02", "Category": "Flag", "Flag": "GREEN",
         "Scope": "Track", "Message": "GREEN LIGHT - PIT EXIT OPEN"},
        {"Utc": "2026-11-08T17:28:44", "Category": "Other",
         "Message": "CAR 63 (RUS) 5 SECOND TIME PENALTY - TRACK LIMITS"},
    ]
    state["TeamRadio"] = {"Captures": [
        {"Utc": "2026-11-08T17:29:30.000Z", "RacingNumber": "16", "Path": "TeamRadio/LEC_16_20261108_172930.mp3"},
    ]}
    return state


def main(argv: list[str] | None = None) -> None:
    import sys

    argv = argv if argv is not None else sys.argv[1:]
    if "--capture" in argv:
        from . import feed
        state = feed.raw_state(fresh=True)
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        REAL.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        print(f"captured {len(json.dumps(state))} bytes -> {REAL}")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    builders = (
        ("q3_interlagos.json", build_q3),
        ("q1_interlagos.json", build_q1),
        ("race_interlagos.json", build_race),
    )
    for name, builder in builders:
        path = FIXTURE_DIR / name
        payload = _trim(builder())
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes)")


#: Sections the plugin never reads; dropped so the committed fixtures stay small.
#: ``LapCount`` and ``PitLaneTimeCollection`` are kept -- race reads need both.
_DROPPED = {"CarData", "Position", "AudioStreams", "ContentStreams", "WeatherDataSeries",
            "LapSeries", "TopThree", "ChampionshipPrediction"}


def _trim(state: dict) -> dict:
    return {key: value for key, value in state.items() if key not in _DROPPED}


if __name__ == "__main__":
    main()
