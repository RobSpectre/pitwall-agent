"""Tool schemas and handlers.

Every tool answers one broadcast question with the smallest useful text, and
every tool declares which session modes it belongs to: lap-time tools for
practice and qualifying, race tools for sprints and grands prix.  A tool with
``modes=None`` is always offered.

Handlers return plain strings -- no JSON punctuation, no URLs, no debug fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from . import feed, players as playerlib, render
from .meeting import MeetingError
from .model import Session, find_driver
from .timefmt import fmt_lap

LAP = frozenset({"practice", "qualifying"})
QUALI = frozenset({"qualifying"})
RACE = frozenset({"race"})

#: What /pitwall init advertises for each mode.
MODE_TOOLS = {
    "practice": "flying, pace",
    "qualifying": "flying, purple, pole, pace",
    "race": "race, battle, strategy",
    "unknown": "every set (session not identified)",
}


def _err(exc: Exception) -> str:
    return f"Error: {exc}"


def _session(args: dict) -> Session:
    session, _ = feed.snapshot(fresh=bool(args.get("fresh")))
    return session


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _screen(value: Any) -> tuple[int, int]:
    text = str(value or "3840x2160").lower().replace(" ", "")
    try:
        width, height = text.split("x", 1)
        return max(320, int(width)), max(240, int(height))
    except (ValueError, TypeError):
        return 3840, 2160


# ---------------------------------------------------------------------------
# change detection
# ---------------------------------------------------------------------------


def _changes(current: Session, previous: Session | None, limit: int = 12) -> str:
    if previous is None:
        return "[first read this session -- full board]\n" + render.board(current)

    lines: list[str] = []
    for driver in current.drivers:
        before = previous.by_num.get(driver.num)
        if before is None:
            continue
        if driver.best_lap and (
            before.best_lap is None or driver.best_lap < before.best_lap - 0.0005
        ):
            gain = (
                f" ({before.best_lap - driver.best_lap:+.3f})" if before.best_lap else " (first time)"
            )
            lines.append(f"{driver.label} best {fmt_lap(driver.best_lap)}{gain}")
        if before.position != driver.position:
            lines.append(f"{driver.label} P{before.position or '-'} -> P{driver.position or '-'}")
        if before.run != driver.run:
            lines.append(f"{driver.label} {before.run} -> {driver.run}")
        if before.retired != driver.retired and driver.retired:
            lines.append(f"{driver.label} RETIRED")
    if previous.track != current.track:
        lines.append(f"track {previous.track} -> {current.track}")

    if not lines:
        return "no changes since the previous call"
    head = f"CHANGES ({min(len(lines), limit)} of {len(lines)}):"
    return "\n".join([head] + ["  " + line for line in lines[:limit]])


# ---------------------------------------------------------------------------
# session-state handlers
# ---------------------------------------------------------------------------


def _overview(args: dict, **_kw: Any) -> str:
    try:
        session = _session(args)
        messages = _int(args.get("messages"), 3, 0, 12)
        text = render.overview(session)
        if messages > 3:
            extra = session.messages[-(messages - 3):]
            text += "\n" + "\n".join(
                f"rc {str(m.get('Utc') or '')[11:16]} {m.get('Message') or ''}".strip()
                for m in extra
            )
        return text
    except Exception as exc:
        return _err(exc)


def _board(args: dict, **_kw: Any) -> str:
    try:
        session, previous = feed.snapshot(fresh=bool(args.get("fresh")))
        if args.get("only_changes"):
            return _changes(session, previous)
        tol = args.get("gap_tol")
        return render.board(
            session,
            limit=_int(args.get("limit"), 25, 1, 40),
            gap_tol=_float(tol, 0.0) if tol is not None else None,
        )
    except Exception as exc:
        return _err(exc)


def _flying(args: dict, **_kw: Any) -> str:
    try:
        return render.flying(_session(args), limit=_int(args.get("limit"), 8, 1, 20))
    except Exception as exc:
        return _err(exc)


def _purple(args: dict, **_kw: Any) -> str:
    try:
        return render.purple(_session(args), recent=_int(args.get("recent"), 6, 1, 10))
    except Exception as exc:
        return _err(exc)


def _pole(args: dict, **_kw: Any) -> str:
    try:
        return render.pole(
            _session(args),
            tol=_float(args.get("tol"), 0.25),
            limit=_int(args.get("limit"), 10, 1, 20),
        )
    except Exception as exc:
        return _err(exc)


def _pace(args: dict, **_kw: Any) -> str:
    try:
        return render.pace(_session(args), limit=_int(args.get("limit"), 10, 1, 22))
    except Exception as exc:
        return _err(exc)


def _race(args: dict, **_kw: Any) -> str:
    try:
        return render.race(_session(args), limit=_int(args.get("limit"), 25, 1, 40))
    except Exception as exc:
        return _err(exc)


def _battle(args: dict, **_kw: Any) -> str:
    try:
        return render.battle(
            _session(args),
            window=_float(args.get("window"), 1.0),
            limit=_int(args.get("limit"), 6, 1, 20),
        )
    except Exception as exc:
        return _err(exc)


def _strategy(args: dict, **_kw: Any) -> str:
    try:
        return render.strategy(_session(args), limit=_int(args.get("limit"), 10, 1, 22))
    except Exception as exc:
        return _err(exc)


def _driver_tool(args: dict, **_kw: Any) -> str:
    try:
        session = _session(args)
        driver = find_driver(session, args.get("driver"))
        if driver is None:
            known = ", ".join(d.label for d in session.drivers)
            return f"Error: no driver matching {args.get('driver')!r}. Known: {known}"
        return render.driver_detail(session, driver)
    except Exception as exc:
        return _err(exc)


def _raw(args: dict, **_kw: Any) -> str:
    """Escape hatch: an index of sections, or one section / one car as JSON."""
    try:
        state = feed.raw_state(fresh=bool(args.get("fresh")))
        driver = str(args.get("driver") or "").strip()
        # One car's four sections are ~4 KB of dense JSON; a whole section can be
        # tens of KB.  Default per intent, and let the caller override.
        budget = _int(args.get("max_chars"), 6000 if driver else 2500, 400, 20000)

        if driver:
            picked = {}
            for section in ("TimingData", "TimingStats", "TimingAppData", "DriverList"):
                container = state.get(section) or {}
                # DriverList is keyed by number directly; the rest wrap "Lines".
                entries = container.get("Lines") if "Lines" in container else container
                if driver in (entries or {}):
                    picked.setdefault(section, {})[driver] = entries[driver]
            if not picked:
                return f"Error: no live-timing entries for {driver!r}"
            return _dump(picked, budget)

        sections = args.get("sections") or []
        if isinstance(sections, str):
            sections = [sections]
        if sections:
            picked = {name: state.get(name) for name in sections if name in state}
            missing = [name for name in sections if name not in state]
            body = _dump(picked, budget)
            if missing:
                body += f"\n(absent: {', '.join(missing)})"
            return body

        index = {name: len(json.dumps(value)) for name, value in state.items()}
        ordered = sorted(index.items(), key=lambda kv: -kv[1])
        lines = [f"live-timing sections ({len(state)}), bytes:"]
        lines += [f"  {name:<26} {size:>6}" for name, size in ordered]
        lines.append("ask for one with sections=[...] or one car with driver='16'")
        return "\n".join(lines)
    except Exception as exc:
        return _err(exc)


def _dump(payload: dict, budget: int) -> str:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= budget:
        return text
    return text[:budget] + f"\n[truncated at {budget} chars of {len(text)}]"


# ---------------------------------------------------------------------------
# player handlers
# ---------------------------------------------------------------------------


def _players(args: dict, **_kw: Any) -> str:
    try:
        session = _session(args)
        return render.players_table(playerlib.list_players(), playerlib.current_resolution(), session)
    except Exception as exc:
        return _err(exc)


def _player_open(args: dict, **_kw: Any) -> str:
    try:
        session = _session(args)
        screen = _screen(args.get("screen"))
        side = str(args.get("side") or "right")

        driver = args.get("driver")
        if driver:
            known = find_driver(session, driver)
            if known is None:
                names = ", ".join(d.tla for d in session.drivers)
                return f"Error: no driver matching {driver!r}. Known: {names}"
            driver = known.tla or known.num

        override = args.get("content_id")
        content_id = _int(override, 0, 1, 10**12) if override else None

        player_id, title = playerlib.create(
            driver=driver,
            stream=None if driver else args.get("stream"),
            content_id=content_id,
        )

        if args.get("tile", True):
            layout = playerlib.tile(screen=screen, side=side)
            note = f"; tiled {len(layout)} players on the {side} of {screen[0]}x{screen[1]}"
        else:
            open_now = playerlib.list_players()
            layout = playerlib.tile_layout(len(open_now), screen, side)
            cell = layout[-1] if layout else (0, 0, 960, 540)
            playerlib.set_bounds(player_id, *cell)
            note = f" at {cell[0]},{cell[1]} {cell[2]}x{cell[3]}"

        resolution = playerlib.current_resolution()
        return f"player {player_id} -> {title} (content {resolution.content_id}){note}"
    except (MeetingError, ValueError, RuntimeError) as exc:
        return _err(exc)
    except Exception as exc:
        return _err(exc)


def _player_control(args: dict, **_kw: Any) -> str:
    try:
        action = str(args.get("action") or "").lower()
        if action == "tile":
            screen = _screen(args.get("screen"))
            side = str(args.get("side") or "right")
            layout = playerlib.tile(screen=screen, side=side)
            if not layout:
                return "no players to tile"
            return f"tiled {len(layout)} players on the {side} of {screen[0]}x{screen[1]}: " + ", ".join(
                f"{pid}@{x},{y}" for pid, (x, y, _w, _h) in layout
            )
        player_id = _int(args.get("player"), 0, 1, 10**9)
        if not player_id:
            return "Error: player id is required"
        return playerlib.control(player_id, action, value=args.get("value"))
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

_FRESH = {
    "type": "boolean",
    "description": "Bypass the 1s cache (use only when you need the freshest possible read).",
}


@dataclass(slots=True)
class ToolSpec:
    name: str
    schema: dict
    handler: Callable
    modes: frozenset[str] | None = None      # None = offered in every mode


def specs() -> list[ToolSpec]:
    """Every tool, in the order a broadcast reaches for them."""
    return [
        ToolSpec(
            "pitwall_overview",
            {
                "name": "pitwall_overview",
                "description": (
                    "Session state in five lines: meeting, clock, pole, track status, weather, "
                    "car counts, latest race-control messages. Call this first each turn."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "messages": {"type": "integer", "description": "Race-control messages to show (default 3)."},
                        "fresh": _FRESH,
                    },
                },
            },
            _overview,
        ),
        ToolSpec(
            "pitwall_board",
            {
                "name": "pitwall_board",
                "description": (
                    "Compact classification: position, driver, best lap, gap to pole, laps, run state "
                    "(PUSH/BUILD/COOL/LAP/OUT/PIT), live sector colours, drop-zone markers. "
                    "Set only_changes=true to get just what moved since your previous call."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Rows to show (default 25)."},
                        "gap_tol": {"type": "number", "description": "Only cars within this many seconds of pole."},
                        "only_changes": {"type": "boolean", "description": "Return only deltas since the last call."},
                        "fresh": _FRESH,
                    },
                },
            },
            _board,
        ),
        ToolSpec(
            "pitwall_flying",
            {
                "name": "pitwall_flying",
                "description": (
                    "Who is on a flying lap right now: completed sectors with delta vs that driver's "
                    "own best sectors, a projected lap time and the gap to pole. The cut-to camera list."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Flyers to show (default 8)."}},
                },
            },
            _flying,
            LAP,
        ),
        ToolSpec(
            "pitwall_purple",
            {
                "name": "pitwall_purple",
                "description": (
                    "Purple watch: session-best time and holder for each sector, who is purple on their "
                    "current lap, and how much time each driver has left on the table."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"recent": {"type": "integer", "description": "Live purple rows (default 6)."}},
                },
            },
            _purple,
            QUALI,
        ),
        ToolSpec(
            "pitwall_pole",
            {
                "name": "pitwall_pole",
                "description": (
                    "Pole contention: drivers within `tol` seconds of pole, their unrealised pace, who is "
                    "on a flyer now, and whether the remaining clock permits another outlap plus flyer."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tol": {"type": "number", "description": "Contention window in seconds (default 0.25)."},
                        "limit": {"type": "integer", "description": "Rows to show (default 10)."},
                    },
                },
            },
            _pole,
            QUALI,
        ),
        ToolSpec(
            "pitwall_pace",
            {
                "name": "pitwall_pace",
                "description": (
                    "Practice pace table: best lap and gap for each car, the best lap of the current tyre "
                    "run, laps on those tyres, and the theoretical best."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Rows to show (default 10)."}},
                },
            },
            _pace,
            LAP,
        ),
        ToolSpec(
            "pitwall_race",
            {
                "name": "pitwall_race",
                "description": (
                    "Race order: interval to the car ahead, last lap, tyre and age, stops, run state, and "
                    "the latest race-control messages. The race counterpart to pitwall_board."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Rows to show (default 25)."}},
                },
            },
            _race,
            RACE,
        ),
        ToolSpec(
            "pitwall_battle",
            {
                "name": "pitwall_battle",
                "description": (
                    "On-track fights: every pair inside `window` seconds, with tyres and the last-lap "
                    "pace trend. DRS range is marked."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window": {"type": "number", "description": "Maximum gap in seconds (default 1.0)."},
                        "limit": {"type": "integer", "description": "Pairs to show (default 6)."},
                    },
                },
            },
            _battle,
            RACE,
        ),
        ToolSpec(
            "pitwall_strategy",
            {
                "name": "pitwall_strategy",
                "description": (
                    "Tyres and stops: every run on each car, stop count, last stop time, laps remaining, "
                    "and who is on the oldest rubber."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Rows to show (default 10)."}},
                },
            },
            _strategy,
            RACE,
        ),
        ToolSpec(
            "pitwall_driver",
            {
                "name": "pitwall_driver",
                "description": (
                    "One car in detail: best/last lap, sector PBs and theoretical best, current run state "
                    "with evidence, live sector deltas, speeds and stints."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "driver": {"type": "string", "description": "Three-letter code (VER) or racing number (1)."},
                    },
                    "required": ["driver"],
                },
            },
            _driver_tool,
        ),
        ToolSpec(
            "pitwall_players",
            {
                "name": "pitwall_players",
                "description": (
                    "Open MultiViewer players: what each shows, its box on screen, playing state, and "
                    "whether it is on the current session's content id."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            _players,
        ),
        ToolSpec(
            "pitwall_player_open",
            {
                "name": "pitwall_player_open",
                "description": (
                    "Create a video player for a driver onboard, DATA (live timing) or INTERNATIONAL, "
                    "resolving the session content id itself, then tile the layout."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "driver": {"type": "string", "description": "Three-letter code or racing number."},
                        "stream": {
                            "type": "string",
                            "description": "Named stream instead of a driver: DATA, INTERNATIONAL, PIT LANE, TRACKER.",
                        },
                        "tile": {"type": "boolean", "description": "Re-tile every player afterwards (default true)."},
                        "screen": {"type": "string", "description": "Screen size for tiling, e.g. 3840x2160."},
                        "side": {
                            "type": "string",
                            "enum": ["left", "right", "top", "bottom", "full"],
                            "description": "Region to tile into (default right).",
                        },
                        "content_id": {"type": "integer", "description": "Override the resolved session content id."},
                    },
                },
            },
            _player_open,
        ),
        ToolSpec(
            "pitwall_player_control",
            {
                "name": "pitwall_player_control",
                "description": (
                    "Control one player: sync, live, pause, play, mute, unmute, seek, fullscreen, close. "
                    "action=tile lays every open player out in a grid instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "sync", "live", "pause", "play", "mute", "unmute",
                                "seek", "fullscreen", "close", "tile",
                            ],
                        },
                        "player": {"type": "integer", "description": "Player id (from pitwall_players)."},
                        "value": {"type": "number", "description": "Seconds for seek."},
                        "screen": {"type": "string", "description": "Screen size for action=tile."},
                        "side": {"type": "string", "description": "Region for action=tile."},
                    },
                    "required": ["action"],
                },
            },
            _player_control,
        ),
        ToolSpec(
            "pitwall_raw",
            {
                "name": "pitwall_raw",
                "description": (
                    "Escape hatch into the live-timing document. No arguments returns a section index with "
                    "byte sizes; pass sections=[...] or driver='16' to get exactly that JSON."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sections": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Top-level sections, e.g. ['RaceControlMessages','WeatherDataSeries'].",
                        },
                        "driver": {"type": "string", "description": "Racing number; returns that car's four sections."},
                        "max_chars": {"type": "integer", "description": "Truncate the JSON at this size."},
                        "fresh": _FRESH,
                    },
                },
            },
            _raw,
        ),
    ]


def schemas() -> list[tuple[str, dict, Callable]]:
    """Backwards-compatible view: (name, schema, handler)."""
    return [(spec.name, spec.schema, spec.handler) for spec in specs()]


def mode_check(modes: frozenset[str] | None):
    """A ``check_fn`` that hides a tool outside its session modes.

    ``unknown`` (MultiViewer down, or no session yet) admits everything: hiding
    tools because the app is closed would be worse than offering a tool that
    answers with an error.
    """
    if not modes:
        return None

    def check() -> bool:
        mode = feed.session_mode()
        return mode == "unknown" or mode in modes

    check.__name__ = "pitwall_mode_check"
    return check
