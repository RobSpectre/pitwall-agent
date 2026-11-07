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


def _stint(laps: int, compound: str = "SOFT") -> dict:
    return {"Stint": 1, "Compound": compound, "StartLaps": 0,
            "Laps": [{"Lap": i + 1, "LapTime": _lap(70.0 + i * 0.1)} for i in range(laps)]}


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
        app[num] = {"RacingNumber": num, "Line": position, "Stints": [_stint(8)]}

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
    for name, builder in (("q3_interlagos.json", build_q3), ("q1_interlagos.json", build_q1)):
        path = FIXTURE_DIR / name
        payload = _trim(builder())
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes)")


#: Sections the plugin never reads; dropped so the committed fixtures stay small.
_DROPPED = {"CarData", "Position", "AudioStreams", "ContentStreams", "WeatherDataSeries",
            "LapSeries", "LapCount", "TopThree", "PitLaneTimeCollection", "ChampionshipPrediction"}


def _trim(state: dict) -> dict:
    return {key: value for key, value in state.items() if key not in _DROPPED}


if __name__ == "__main__":
    main()
