"""Compact text renderings of a :class:`~pitwall.model.Session`.

Everything here is written for an LLM reader at a keyboard: fixed-width columns,
three-decimal times, no JSON punctuation, no URLs, no fields the pit wall does
not act on.  A full 22-car order costs about 1.2 KB of text -- roughly 20x less
than the 75 KB live-timing document it summarises.
"""

from __future__ import annotations

import time

from .model import Driver, Session
from .timefmt import fmt_age, fmt_clock, fmt_gap, fmt_lap, fmt_sector

MAX_ROWS = 40


def _header(session: Session) -> str:
    part = f" Q{session.part}" if session.part else ""
    bits = [f"{session.kind or 'SESSION'}{part}"]
    if session.mode == "race" and session.current_lap:
        lap = f"LAP {session.current_lap}" + (f"/{session.total_laps}" if session.total_laps else "")
        bits.append(lap)
    bits.append(f"{session.meeting} / {session.circuit}".strip(" /"))
    bits.append(f"{fmt_clock(session.remaining)} LEFT")
    bits.append(session.status.upper() if session.status else "STATUS ?")
    return "  ".join(b for b in bits if b)


def _conditions(session: Session) -> str:
    bits = [f"track {session.track}"]
    spoken = session.track_message.replace(" ", "").upper()
    if session.track_message and spoken not in session.track.replace(" ", "").upper():
        bits.append(session.track_message)
    if session.air is not None:
        bits.append(f"air {session.air:.1f}C")
    if session.track_temp is not None:
        bits.append(f"track {session.track_temp:.1f}C")
    if session.wind is not None:
        bits.append(f"wind {session.wind:.1f}m/s")
    if session.rain:
        bits.append(f"rain {session.rain:g}")
    return "  ".join(bits)


def _age(session: Session) -> str:
    if not session.fetched_at:
        return ""
    return f" [{fmt_age(time.time() - session.fetched_at)}]"


def _sector_letters(driver: Driver) -> str:
    return " ".join(s.letter for s in driver.sectors)


#: A projection built on a car's best sectors is meaningless when those sectors
#: came from an out-lap or a lap with a stop. Anything this far off pole is noise.
PROJECTION_WINDOW = 3.0


def _projection(driver: Driver, session: Session) -> float | None:
    if not driver.proj or not session.pole:
        return driver.proj
    return driver.proj if driver.proj - session.pole <= PROJECTION_WINDOW else None


def _sector_phase(driver: Driver) -> str:
    """Sector values describe the live lap only while the car is running one."""
    return "now" if driver.run in {"PUSH", "BUILD", "COOL"} else "last lap"


def _flags(driver: Driver, session: Session) -> str:
    marks = []
    if driver.knocked_out:
        marks.append("X")
    elif driver.cutoff:
        marks.append("!")
    if driver.retired:
        marks.append("R")
    elif driver.stopped:
        marks.append("S")
    return "".join(marks)


# ---------------------------------------------------------------------------


def overview(session: Session) -> str:
    """One-screen state: session, clock, pole, weather, who is where, latest flags."""
    counts = (
        f"{len(session.drivers)} cars: {len(session.on_track())} on track, "
        f"{len(session.flyers())} on flyers, "
        f"{sum(1 for d in session.drivers if d.in_pit)} in pits"
    )
    lines = [
        _header(session) + _age(session),
        f"pole {fmt_lap(session.pole)} {session.pole_holder or '-'}"
        f"   theoretical {fmt_lap(session.theo_session)}",
        _conditions(session),
        counts,
    ]
    for message in session.messages[-3:]:
        stamp = str(message.get("Utc") or "")[11:16]
        lines.append(f"rc {stamp} {message.get('Message') or ''}".strip())
    if session.archive and session.archive.lower() != "complete":
        lines.append(f"replay archive: {session.archive}")
    return "\n".join(lines)


def board(session: Session, *, limit: int = 25, gap_tol: float | None = None) -> str:
    """The order, one line per car, with run state and live sector colours."""
    rows = session.sorted_by_pace()
    if gap_tol is not None and session.pole:
        rows = [d for d in rows if d.best_lap and d.best_lap - session.pole <= gap_tol]
    rows = rows[: max(1, min(limit, MAX_ROWS))]

    lines = [
        f"{_header(session)}{_age(session)}",
        f"pole {fmt_lap(session.pole)} {session.pole_holder or '-'}",
        "P  DRV  BEST      GAP     LAPS  RUN    S1 S2 S3",
    ]
    marked = False
    for driver in rows:
        gap = (driver.best_lap - session.pole) if driver.best_lap and session.pole else None
        flags = _flags(driver, session)
        marked = marked or bool(flags)
        position = str(driver.position) if driver.position else "-"
        lines.append(
            f"{position:<2} {driver.label:<4} {fmt_lap(driver.best_lap):<9} "
            f"{fmt_gap(gap) if driver.best_lap else '-':<8} "
            f"L{driver.laps:<4} {driver.run:<6} {_sector_letters(driver):<7}{flags}"
        )
    if marked:
        lines.append("marks: X eliminated, ! in drop zone, R retired, S stopped")
    return "\n".join(lines)


def flying(session: Session, *, limit: int = 8) -> str:
    """Who is on a flyer right now, with per-sector deltas and a projected lap."""
    flyers = sorted(session.flyers(), key=lambda d: (_projection(d, session) or 999.0))[:limit]
    head = f"ON A FLYER NOW ({len(session.flyers())})  {fmt_clock(session.remaining)} LEFT{_age(session)}"

    if not flyers:
        upcoming = [d for d in session.drivers if d.run == "OUT"]
        tail = f"  next out: {', '.join(d.label for d in upcoming[:6])}" if upcoming else ""
        return f"{head}\nnone.{tail}"

    lines = [head + "   (delta vs own best sector)"]
    for driver in flyers:
        pieces = [f"{driver.label:<4} P{driver.position or '-':<3} lap {driver.laps + 1:<3}"]
        for i, sector in enumerate(driver.sectors):
            if sector.t is None:
                pieces.append(f"S{i + 1} --")
                continue
            delta = sector.t - driver.sector_pb[i] if driver.sector_pb[i] else None
            pieces.append(f"S{i + 1} {fmt_sector(sector.t)}" + (f" ({fmt_gap(delta)})" if delta is not None else ""))
        proj = _projection(driver, session)
        if proj:
            vs_pole = f" ({fmt_gap(proj - session.pole)})" if session.pole else ""
            pieces.append(f"proj {fmt_lap(proj)}{vs_pole} vs pole")
        else:
            pieces.append("proj -  (no clean reference lap)")
        lines.append("  ".join(pieces))
    return "\n".join(lines)


def purple(session: Session, *, recent: int = 6) -> str:
    """Session-best sectors, who holds them, and each driver's theoretical best."""
    head = f"PURPLE  theoretical {fmt_lap(session.theo_session)}  {fmt_clock(session.remaining)} LEFT{_age(session)}"
    best = "  ".join(
        f"S{i + 1} {fmt_sector(session.best_sector[i])} {session.best_sector_holder[i] or '-'}"
        for i in range(3)
    )
    lines = [head, best]

    # A live purple flag is authoritative only if the time really beats the
    # standing session best; stale or inconsistent flags are dropped rather
    # than reported as something the viewer cannot see on screen.
    live = []
    for driver in session.drivers:
        held = [
            (i, s) for i, s in enumerate(driver.sectors)
            if s.overall and s.t and session.best_sector[i] and s.t <= session.best_sector[i] + 0.0005
        ]
        if held:
            live.append((driver, held))
    if live:
        rows = [
            f"{driver.label} " + " ".join(f"S{i + 1} {fmt_sector(s.t)}" for i, s in held)
            + f" ({'just completed' if driver.run == 'LAP' else driver.run.lower()})"
            for driver, held in live[:recent]
        ]
        lines.append("purple on the live lap: " + "; ".join(rows))

    targets = []
    for driver in session.drivers:
        if driver.theo and driver.best_lap:
            gain = driver.best_lap - driver.theo
            if gain > 0.001:
                targets.append((gain, driver))
    if targets:
        targets.sort(key=lambda item: -item[0])
        lines.append(
            "time left on the table (own best sectors): "
            + "; ".join(f"{d.label} {gain:.3f}" for gain, d in targets[:6])
        )
    return "\n".join(lines)


def pole(session: Session, *, tol: float = 0.25, limit: int = 10) -> str:
    """Who can take pole: current order, unrealised pace, and clock feasibility."""
    lines = [
        f"POLE WATCH  pole {fmt_lap(session.pole)} {session.pole_holder or '-'}"
        f"  {fmt_clock(session.remaining)} LEFT{_age(session)}"
    ]
    if not session.pole:
        return lines[0] + "\nno timed laps yet this session."

    ranked = session.sorted_by_pace()
    lines.append(f"CONTENDERS (within {tol:.3f} of pole)")
    shown = 0
    for driver in ranked:
        if not driver.best_lap:
            continue
        gap = driver.best_lap - session.pole
        if gap > tol:
            continue
        shown += 1
        extra = ""
        if driver.theo and driver.best_lap - driver.theo > 0.001:
            extra = f"  theo {fmt_lap(driver.theo)} ({-(driver.best_lap - driver.theo):+.3f} unrealised)"
        mark = "  X" if driver.knocked_out else ("  !" if driver.cutoff else "")
        lines.append(f"{driver.position or '-':<2} {driver.label:<4} {fmt_lap(driver.best_lap)} {fmt_gap(gap)}{extra}{mark}")
        if shown >= limit:
            break
    if not shown:
        lines.append("none inside the window.")

    flyers = [(d, _projection(d, session)) for d in session.flyers()]
    flyers = [(d, p) for d, p in flyers if p]
    if flyers:
        flyers.sort(key=lambda item: item[1])
        lines.append(
            "on a flyer now: "
            + "; ".join(
                f"{d.label} proj {fmt_lap(p)} ({fmt_gap(p - session.pole)})" for d, p in flyers[:5]
            )
        )

    lap_ref = session.lap_time_ref
    if session.remaining is not None:
        need = 2.05 * lap_ref
        if session.remaining < lap_ref * 0.75:
            lines.append(f"clock: {session.remaining:.0f}s -- only an in-progress lap can count")
        elif session.remaining < need:
            lines.append(
                f"clock: {session.remaining:.0f}s vs {need:.0f}s needed for outlap+flyer -- "
                "anyone in the pits is out of time"
            )
    return "\n".join(lines)


def players_table(infos, resolution, session) -> str:
    """Open players, what they show, and whether they are on this session."""
    head = f"PLAYERS ({len(infos)})   session content {resolution.content_id or '-'}"
    if resolution.matched_on:
        head += f" ({resolution.title}, matched on {resolution.matched_on})"
    lines = [head + _age(session)]
    if not infos:
        lines.append("none open")
        return "\n".join(lines)
    lines.append("ID  STREAM          CONTENT      BOX                 STATE")
    for player in sorted(infos, key=lambda p: (p.y, p.x)):
        stale = "" if player.content_id == resolution.content_id else "  <- other session"
        lines.append(
            f"{player.id:<3} {player.title[:14]:<15} {str(player.content_id or '-'):<12} "
            f"{player.x:>5},{player.y:<5} {player.width:>4}x{player.height:<5} "
            f"{'paused' if player.paused else 'playing'}{stale}"
        )
    return "\n".join(lines)


def race(session: Session, *, limit: int = 25) -> str:
    """Race order: interval, last lap, tyres, stops, live flags."""
    lines = [
        f"{_header(session)}{_age(session)}",
        _conditions(session),
        "P  DRV  INT      LAST       TYRE  STOPS  STATUS",
    ]
    marked = False
    for driver in session.sorted_by_pace()[: max(1, min(limit, MAX_ROWS))]:
        interval = "-" if driver.position == 1 else fmt_gap(driver.gap_ahead)
        drs = "*" if (driver.gap_ahead is not None and 0 < driver.gap_ahead <= 1.0) else " "
        tyre = driver.tyre
        stops = str(driver.stops) if driver.stints else "-"
        flags = _flags(driver, session)
        marked = marked or bool(flags)
        # "LAP" is every car's normal state in a race; only the exceptions are news.
        status = "" if driver.run in {"LAP", "PUSH", "BUILD"} else driver.run
        lines.append(
            f"{driver.position or '-':<2} {driver.label:<4} {interval:<8} "
            f"{fmt_lap(driver.last_lap):<10} {tyre:<5} {stops:<6} {status:<5}{drs}{flags}"
        )
    if marked:
        lines.append("marks: X eliminated, ! drop zone, R retired, S stopped")
    lines.append("INT = to the car ahead, * = inside DRS range")
    for message in session.messages[-2:]:
        lines.append(f"rc {str(message.get('Utc') or '')[11:16]} {message.get('Message') or ''}".strip())
    return "\n".join(lines)


def battle(session: Session, *, window: float = 1.0, limit: int = 6) -> str:
    """Pairs inside a second, with the pace each is showing on the last lap."""
    order = [d for d in session.sorted_by_pace() if d.position]
    pairs = []
    for ahead, behind in zip(order, order[1:]):
        gap = behind.gap_ahead
        if gap is None or gap > window or behind.retired or ahead.retired:
            continue
        trend = None
        if ahead.last_lap and behind.last_lap:
            trend = behind.last_lap - ahead.last_lap
        pairs.append((gap, ahead, behind, trend))
    head = f"BATTLES (<= {window:.1f}s)   {_header(session)}{_age(session)}"
    if not pairs:
        return head + f"\nnone inside {window:.1f}s."
    pairs.sort(key=lambda item: item[0])
    lines = [head]
    for gap, ahead, behind, trend in pairs[:limit]:
        drs = " DRS" if gap <= 1.0 else ""
        pace = ""
        if trend is not None:
            who = behind.label if trend < 0 else ahead.label
            pace = f"  {who} faster on the last lap ({abs(trend):.3f})"
        lines.append(
            f"P{ahead.position} {ahead.label} {ahead.tyre} vs P{behind.position} {behind.label} "
            f"{behind.tyre}  gap {fmt_gap(gap)}{drs}{pace}"
        )
    return "\n".join(lines)


def strategy(session: Session, *, limit: int = 10) -> str:
    """Tyre runs, stop counts and how much race is left."""
    laps_left = ""
    if session.current_lap and session.total_laps:
        laps_left = f"  {session.total_laps - session.current_lap} laps remaining"
    lines = [f"STRATEGY   {_header(session)}{laps_left}{_age(session)}"]
    rows = [d for d in session.sorted_by_pace() if d.stints]
    if not rows:
        return lines[0] + "\nno stint data yet"
    lines.append("P  DRV  NOW    STOPS  RUNS")
    for driver in rows[:limit]:
        runs = " ".join(s.label for s in driver.stints)
        pit = f"  last stop {driver.pit_time:.1f}s" if driver.pit_time else ""
        lines.append(f"{driver.position or '-':<2} {driver.label:<4} {driver.tyre:<6} {driver.stops:<6} {runs}{pit}")
    oldest = max(rows, key=lambda d: (d.stint.age if d.stint else 0))
    if oldest.stint:
        lines.append(
            f"oldest tyres: {oldest.label} on {oldest.stint.age} laps ({oldest.stint.compound.lower()})"
        )
    return "\n".join(lines)


def pace(session: Session, *, limit: int = 10) -> str:
    """Practice pace: best lap, the best lap of the current run, and run length."""
    lines = [f"{_header(session)}{_age(session)}",
             f"fastest {fmt_lap(session.pole)} {session.pole_holder or '-'}"]
    rows = session.sorted_by_pace()[: max(1, min(limit, MAX_ROWS))]
    lines.append("P  DRV  BEST      GAP     RUN BEST   LAPS ON TYRE  THEO")
    for driver in rows:
        gap = (driver.best_lap - session.pole) if driver.best_lap and session.pole else None
        stint = driver.stint
        run_best = fmt_lap(stint.lap_time) if stint and stint.lap_time else "-"
        tyre = f"{driver.tyre:<10}" if stint else f"{'-':<10}"
        lines.append(
            f"{driver.position or '-':<2} {driver.label:<4} {fmt_lap(driver.best_lap):<9} "
            f"{fmt_gap(gap) if driver.best_lap else '-':<7} {run_best:<10} {tyre}"
            f"{fmt_lap(driver.theo)}"
        )
    return "\n".join(lines)


def driver_detail(session: Session, driver: Driver) -> str:
    """Everything about one car, in about 400 characters."""
    gap = f" ({fmt_gap(driver.best_lap - session.pole)} vs pole)" if driver.best_lap and session.pole else ""
    best = (
        f"best {fmt_lap(driver.best_lap)} (lap {driver.best_lap_no or '-'}){gap}"
        if driver.best_lap
        else "best none yet"
    )
    lines = [
        f"{driver.label}  {driver.name}  {driver.team}  P{driver.position or '-'}  #{driver.num}",
        f"{best}  last {fmt_lap(driver.last_lap)}  laps {driver.laps}  {driver.run}: {driver.run_note}",
        f"sector PB {fmt_sector(driver.sector_pb[0])} {fmt_sector(driver.sector_pb[1])} "
        f"{fmt_sector(driver.sector_pb[2])}  theo {fmt_lap(driver.theo)}",
    ]
    phase = _sector_phase(driver)
    now = []
    for i, sector in enumerate(driver.sectors):
        if sector.t is None:
            now.append(f"S{i + 1} --")
            continue
        delta = None
        if phase == "now" and driver.sector_pb[i]:
            delta = sector.t - driver.sector_pb[i]
        now.append(f"S{i + 1} {fmt_sector(sector.t)}" + (f" ({fmt_gap(delta)})" if delta is not None else ""))
    lines.append(f"{phase} " + "  ".join(now))
    speeds = " ".join(f"{k} {v:.0f}" if v else f"{k} -" for k, v in driver.speeds.items())
    if speeds:
        lines.append("speeds " + speeds)
    if driver.stints:
        lines.append("stints " + " ".join(s.label for s in driver.stints))
    return "\n".join(lines)
