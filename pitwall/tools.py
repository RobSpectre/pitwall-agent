"""Tool schemas and handlers.

Every handler answers one broadcast question with the smallest useful text.
Handlers return plain strings: no JSON punctuation, no URLs, no debug fields.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import feed, render
from .model import Session
from .timefmt import fmt_clock, fmt_lap

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _err(exc: Exception) -> str:
    return f"Error: {exc}"


def _session(args: dict) -> Session:
    session, _ = feed.snapshot(fresh=bool(args.get("fresh")))
    return session


def _driver(session: Session, needle: str):
    """Resolve a racing number or TLA/MSV reference to a Driver."""
    key = str(needle or "").strip().upper()
    if not key:
        return None
    if key in session.by_num:
        return session.by_num[key]
    for driver in session.drivers:
        if key in {driver.tla.upper(), driver.name.upper()}:
            return driver
    for driver in session.drivers:
        if key and key in driver.name.upper():
            return driver
    return None


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
# handlers
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


def _driver_tool(args: dict, **_kw: Any) -> str:
    try:
        session = _session(args)
        driver = _driver(session, str(args.get("driver") or ""))
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


def _show(args: dict, **_kw: Any) -> str:
    """Point an existing MultiViewer player at a car, the data channel, or sync."""
    try:
        session = _session(args)
        action = str(args.get("action") or "onboard").lower()
        remote = feed._client()

        players = remote.players

        if action == "sync":
            # mvf1 2.1 added a live-timing-aware sync; 2.0 only syncs to a player.
            sync = getattr(remote, "player_sync_to_live_timing", None)
            if callable(sync):
                return f"synced players to live timing: {json.dumps(sync())[:200]}"
            if not players:
                return "Error: no players open in MultiViewer. Open a stream first."
            players[0].sync()
            return f"synced {len(players)} player(s) to player {players[0].id}"

        if not players:
            return "Error: no players open in MultiViewer. Open a stream first."
        wanted = args.get("player")
        target = remote.player(int(wanted)) if wanted is not None else players[0]

        if action == "data":
            target.switch_stream("DATA")
            return f"player {target.id}: live timing data channel"

        driver = _driver(session, str(args.get("driver") or ""))
        if driver is None:
            return f"Error: no driver matching {args.get('driver')!r}"
        target.switch_stream(driver.tla or driver.num)
        return f"player {target.id}: {driver.label} onboard"
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

_FRESH = {
    "type": "boolean",
    "description": "Bypass the 1s cache (use only when you need the freshest possible read).",
}


def schemas() -> list[tuple[str, dict, Callable]]:
    """(name, schema, handler) triples, in the order the skill uses them."""
    return [
        (
            "pitwall_overview",
            {
                "name": "pitwall_overview",
                "description": (
                    "Session state in five lines: meeting, clock, pole time, track status, weather, "
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
        (
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
                        "limit": {"type": "integer", "description": "Rows to show (default 20)."},
                        "gap_tol": {"type": "number", "description": "Only cars within this many seconds of pole."},
                        "only_changes": {"type": "boolean", "description": "Return only deltas since the last call."},
                        "fresh": _FRESH,
                    },
                },
            },
            _board,
        ),
        (
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
        ),
        (
            "pitwall_purple",
            {
                "name": "pitwall_purple",
                "description": (
                    "Purple watch: session-best time and holder for each sector, who is purple on their "
                    "current lap, and how much time each driver has left on the table (best lap minus "
                    "their own best sectors)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"recent": {"type": "integer", "description": "Live purple rows (default 6)."}},
                },
            },
            _purple,
        ),
        (
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
        ),
        (
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
        (
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
                        "max_chars": {"type": "integer", "description": "Truncate the JSON at this size (default 2500)."},
                        "fresh": _FRESH,
                    },
                },
            },
            _raw,
        ),
        (
            "pitwall_show",
            {
                "name": "pitwall_show",
                "description": (
                    "Direct MultiViewer: switch a player to a driver's onboard, to the DATA channel, or sync "
                    "all open players to live timing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["onboard", "data", "sync"],
                            "description": "onboard (default) needs `driver`; sync takes no target.",
                        },
                        "driver": {"type": "string", "description": "Three-letter code or racing number."},
                        "player": {"type": "integer", "description": "Player id; defaults to the first open player."},
                    },
                },
            },
            _show,
        ),
    ]
