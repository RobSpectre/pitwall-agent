"""Compact text renderings of a :class:`~pitwall.model.Session`.

Everything here is written for an LLM reader at a keyboard: fixed-width columns,
three-decimal times, no JSON punctuation, no URLs, no fields the pit wall does
not act on.  A full 22-car order costs about 1.2 KB of text -- roughly 20x less
than the 75 KB live-timing document it summarises.
"""

from __future__ import annotations

import time

from .model import Driver, Session
from .radio import DriverRadio, Line
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
    if session.data_age is None:
        return ""
    return f" [{fmt_age(session.data_age)}]"


def _sector_letters(driver: Driver) -> str:
    return " ".join(s.letter for s in driver.sectors)


def _order(session: Session) -> list[Driver]:
    """Running order. A race is ordered by position, never by best lap."""
    cars = [driver for driver in session.drivers if driver.position and not driver.retired]
    cars.sort(key=lambda driver: driver.position or 99)
    return cars


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
    """The order, one line per car, with run state and live sector colours.

    In a race the order is the classification and ``GAP`` is the gap to the
    leader -- the number the broadcast needs and the race table does not carry.
    In practice and qualifying this stays a pace board: ordered by best lap,
    ``GAP`` measured off pole.
    """
    race = session.mode == "race"
    rows = _order(session) if race else session.sorted_by_pace()
    if gap_tol is not None and session.pole and not race:
        rows = [d for d in rows if d.best_lap and d.best_lap - session.pole <= gap_tol]
    rows = rows[: max(1, min(limit, MAX_ROWS))]

    lines = [
        f"{_header(session)}{_age(session)}",
        f"fastest {fmt_lap(session.pole)} {session.pole_holder or '-'}" if race
        else f"pole {fmt_lap(session.pole)} {session.pole_holder or '-'}",
        "P  DRV  BEST      GAP     LAPS  RUN    S1 S2 S3",
    ]
    marked = False
    for driver in rows:
        if race:
            gap = driver.gap_leader
        else:
            gap = (driver.best_lap - session.pole) if driver.best_lap and session.pole else None
        flags = _flags(driver, session)
        marked = marked or bool(flags)
        position = str(driver.position) if driver.position else "-"
        shown = gap if race else (gap if driver.best_lap else None)
        lines.append(
            f"{position:<2} {driver.label:<4} {fmt_lap(driver.best_lap):<9} "
            f"{fmt_gap(shown):<8} "
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
    for driver in _order(session)[: max(1, min(limit, MAX_ROWS))]:
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
    order = _order(session)
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
    rows = [d for d in _order(session) if d.stints]
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


# ---------------------------------------------------------------------------
# race pace models
# ---------------------------------------------------------------------------


def _fit_text(slope: float | None) -> str:
    return f"{slope:+.3f}" if slope is not None else "  -   "


def tyres(session: Session, rows, *, limit: int = 12) -> str:
    """Degradation on each car's current tyres, worst first."""
    lines = [
        f"TYRE DEGRADATION  {_header(session)}{_age(session)}",
        "s/lap fitted to that car's own laps on this set, fuel already removed",
        "P  DRV  TYRE  FIT     NEXT5  N  R2",
    ]
    known = [row for row in rows if row.slope is not None]
    unknown = [row.label for row in rows if row.slope is None]
    trusted = [row for row in known if row.trusted]
    provisional = [row for row in known if not row.trusted]
    trusted.sort(key=lambda row: -(row.slope or 0.0))
    provisional.sort(key=lambda row: -(row.slope or 0.0))
    for row in (trusted + provisional)[:limit]:
        tyre = f"{row.compound[:1]}{row.age}" if row.compound else f"?{row.age}"
        mark = "" if row.trusted else "~"
        lines.append(
            f"{row.position or '-':<2} {row.label:<4} {tyre:<5} {mark}{_fit_text(row.slope)} "
            f"{row.five_lap_cost:+.2f}   {row.samples:<2} {row.r2:.2f}"
        )
    if not known:
        lines.append("no car has enough clean laps on its current set yet.")
    if provisional:
        lines.append("~ provisional: under 6 laps, a slope the wrong way, or R2 below 0.20")
    if unknown:
        lines.append(f"not enough laps at all: {' '.join(unknown[:10])}")
    return "\n".join(lines)


def fuel(session: Session, rows, *, limit: int = 12, start_kg: float = 110.0,
         effect: float = 0.033) -> str:
    """Last lap and the same lap on an empty tank."""
    lines = [
        f"FUEL-CORRECTED PACE  {_header(session)}{_age(session)}",
        f"{start_kg:.0f} kg at the start, {effect:.3f} s/kg per lap removed",
        "P  DRV  LAST      EMPTY TANK   GAP",
    ]
    for row in rows[:limit]:
        if row.corrected is None:
            lines.append(f"{row.position or '-':<2} {row.label:<4} {fmt_lap(row.raw):<9} {'-':<12} -")
            continue
        lines.append(
            f"{row.position or '-':<2} {row.label:<4} {fmt_lap(row.raw):<9} "
            f"{fmt_lap(row.corrected):<12} {fmt_gap(row.delta) if row.delta is not None else '-'}"
        )
    return "\n".join(lines)


def overtakes(session: Session, rows, *, window: float = 2.0, floor: float = 0.12) -> str:
    """Who is close enough to fight, and how long the chase should take."""
    current = session.current_lap or 0
    lines = [
        f"OVERTAKE WATCH  {_header(session)}{_age(session)}",
        f"pairs inside {window:.1f}s; a pass needs {floor:.2f} s/lap of advantage",
    ]
    if not rows:
        return "\n".join(lines + ["nobody inside the window."])
    for row in rows:
        head = f"{row.behind} chasing {row.ahead}  {fmt_gap(row.gap)}"
        if row.delta is not None:
            head += f"  {row.delta:+.2f} s/lap"
        if row.drs:
            head += "  DRS"
        if row.pass_lap and row.note.startswith("closing"):
            head += f"  pass lap {row.pass_lap}"
        elif row.laps_to_drs is not None and row.laps_to_drs > 0:
            head += f"  DRS in {row.laps_to_drs:.0f} laps"
        head += f"  ({row.note})"
        lines.append(head)
    if current:
        lines.append(f"projections run from lap {current}; tyre age is in the pace, not assumed")
    return "\n".join(lines)


def pitplan(session: Session, driver: Driver, rows, *, pit_loss: float = 21.0) -> str:
    """Ranked stop plans for one car."""
    remaining = (session.total_laps or 0) - (session.current_lap or 0)
    lines = [
        f"PIT PLAN  {driver.label}  {_header(session)}{_age(session)}",
        f"pit loss {pit_loss:.1f}s  {remaining} laps to run",
    ]
    if not rows:
        return "\n".join(lines + ["not enough laps to model a plan yet."])
    from . import race

    stint = driver.stints[-1] if driver.stints else None
    compound = (stint.compound.upper() if stint else "")
    life = race.TYRE_LIFE.get(compound, 38)
    reachable = bool(stint) and stint.age + remaining <= life
    if not reachable:
        lines.append(
            f"a stop is mandatory: {stint.age if stint else '?'} laps on this set "
            f"plus {remaining} to run is beyond a {life}-lap life"
        )
    best = rows[0]
    if reachable:
        if best.vs_stay_out > 1.0:
            lines.append(f"staying out is quicker by {best.vs_stay_out:.1f}s -- no stop needed yet")
        elif best.vs_stay_out < -1.0:
            lines.append(f"the stop is worth {-best.vs_stay_out:.1f}s against staying out")
        else:
            lines.append("stop and stay-out are within a second: track position decides")
    for index, plan in enumerate(rows):
        order = "best stop" if index == 0 else f"{plan.vs_best:+.1f}s"
        pits = ", ".join(str(lap) for lap in plan.pit_laps)
        suffix = f"  ({plan.vs_stay_out:+.1f}s vs staying out)" if reachable else ""
        lines.append(f"{plan.stops} stop  {plan.sequence:<13} pit lap {pits:<7} {order}{suffix}")
    lines.append(
        f"estimate: wear {rows[0].basis or 'book'}; pit loss {pit_loss:.0f}s; fuel 0.033 s/kg"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# race strategy and outcome
# ---------------------------------------------------------------------------


def undercut(session: Session, data, *, pit_loss: float = 21.0, limit: int = 8) -> str:
    """Who can win a place in the pit lane, and how long the cover lasts."""
    lines = [
        f"UNDERCUT WATCH  {_header(session)}{_age(session)}",
        f"pit loss {pit_loss:.0f}s  intervals from {data.source}",
    ]
    if data.source == "none":
        return "\n".join(
            lines + ["no intervals published and no lap history to rebuild them: an undercut cannot be priced."]
        )
    if not data.rows:
        return "\n".join(lines + ["nobody inside the pit-loss window."])
    for row in data.rows[:limit]:
        head = f"{row.behind} on {row.ahead}  {fmt_gap(row.gap)}"
        if row.gain is not None:
            head += f"  ahead losing {row.gain:.3f}/lap"
        if row.laps:
            head += f"  {row.laps:.0f} laps of cover"
        head += f"  -- {row.verdict}"
        lines.append(head)
    if data.source == "laps":
        lines.append("intervals rebuilt from lap history: the feed is publishing none")
    return "\n".join(lines)


def windows(session: Session, rows, *, limit: int = 12) -> str:
    """Laps left on each set against laps left in the race."""
    lines = [
        f"TYRE WINDOWS  {_header(session)}{_age(session)}",
        "LEFT = laps this set can still do; STOP BY = the last lap to change it",
        "P  DRV  TYRE  LEFT  STOP BY  FLAG",
    ]
    if not rows:
        return "\n".join(lines + ["no stint data yet."])
    for row in rows[:limit]:
        tyre = f"{row.compound[:1]}{row.age}" if row.compound else f"?{row.age}"
        stop = f"lap {row.must_stop_by}" if row.must_stop_by else "-"
        lines.append(
            f"{row.position or '-':<2} {row.label:<4} {tyre:<5} {row.left:<5} {stop:<8} "
            f"{'yes' if row.reach_flag else 'NO'}"
        )
    must = [row.label for row in rows if not row.reach_flag]
    if must:
        lines.append("must stop to reach the flag: " + " ".join(must[:12]))
    return "\n".join(lines)


def outlook(session: Session, data, *, limit: int = 10) -> str:
    """Projected finishing order if everyone runs clean from here."""
    lines = [
        f"OUTLOOK  {_header(session)}{_age(session)}",
        "clean-air projection on current tyres, mandatory stops priced in",
    ]
    if data.source == "none":
        return "\n".join(lines + ["no gaps to project from: the feed publishes no intervals and the lap history is incomplete."])
    if not data.rows:
        return "\n".join(lines + [data.note or "not enough data to project an order."])
    lines.append("P  DRV  NOW  PROJECTED  MOVE  TYRE")
    for index, row in enumerate(data.rows[:limit], start=1):
        move = f"{row.move:+d}" if row.move else "0"
        lines.append(
            f"{index:<2} {row.label:<4} P{str(row.position or '-'):<3} "
            f"{fmt_gap(row.projected):<10} {move:<5} {row.tyre}"
        )
    if data.source == "laps":
        lines.append("gaps rebuilt from lap history (the feed publishes none)")
    lines.append("no traffic, safety car, team orders or defending in this number")
    return "\n".join(lines)


def radio(session: Session, lines: list["Line"], *, total: int = 0, cut: float | None = None) -> str:
    """Driver radio transcriptions as published, newest last.

    ``cut`` is the playhead the list was filtered to. Saying it out loud is the
    point: on a replay the endpoint holds the whole race, and a line the timing
    feed has not reached yet is a spoiler, not a scoop.
    """
    head = f"RADIO  {_header(session)}"
    if total:
        head += f"  last {len(lines)} of {total}"
    if cut is not None:
        head += f"  cut at {time.strftime('%H:%M:%S', time.gmtime(cut))}"
    if not lines:
        return head + "\nno radio transcribed up to this point"
    return "\n".join([head] + [f'{line.at}  {line.label:<4} "{line.text}"' for line in lines])


#: A summary is a briefing, not a transcript: one long radio call should not
#: push the next driver off the screen.
SUMMARY_QUOTE = 110


def _short(text: str, limit: int = SUMMARY_QUOTE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def radio_summary(
    session: Session,
    groups: list["DriverRadio"],
    *,
    window: tuple[float | None, int | None, int | None, float | None],
    per_driver: int = 3,
    wanted: list[str] | None = None,
) -> str:
    """Radio per driver over a lap window, busiest car first.

    The tool selects and counts; it does not paraphrase. A summary of what was
    *meant* needs a reader, and this plugin deliberately has none -- so the
    wording of the broadcast stays with whoever is on air.

    ``wanted`` is the filter the caller asked for: a car that was asked about
    and said nothing is worth stating, because silence is data too.
    """
    start, first, last, cut = window
    head = f"RADIO SUMMARY  {_header(session)}"
    head += f"  laps {first}-{last}" if first and last else "  whole session so far"
    if wanted:
        head += f"  for {' '.join(wanted)}"
    if cut is not None:
        head += f"  cut at {time.strftime('%H:%M:%S', time.gmtime(cut))}"

    silent = [label for label in (wanted or []) if label not in {group.label for group in groups}]
    if not groups:
        if wanted:
            return "\n".join([head, f"no radio from {', '.join(wanted)} in this window"])
        return "\n".join([head, "nobody spoke in this window"])

    calls = sum(group.calls for group in groups)
    airtime = sum(group.airtime for group in groups) / 1000.0
    who = "driver" if len(groups) == 1 else "drivers"
    lines = [head, f"{len(groups)} {who}, {calls} calls, {airtime:.1f}s of radio, busiest first"]

    for group in groups:
        plural = "" if group.calls == 1 else "s"
        lines.append(f"{group.label:<4} {group.calls} call{plural}  {group.airtime / 1000:.1f}s")
        shown = group.lines[-max(1, per_driver):]
        for line in shown:
            lines.append(f'  {line.at}  "{_short(line.text)}"')
        hidden = group.calls - len(shown)
        if hidden > 0:
            lines.append(f"  +{hidden} earlier")
    if silent:
        lines.append(f"no radio from {', '.join(silent)} in this window")
    return "\n".join(lines)
