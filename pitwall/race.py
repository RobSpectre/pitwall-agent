"""Race-pace models: tyre life, fuel-corrected pace, overtakes, pit plans.

Every number here is an estimate built from observed laps, and every constant
that cannot be observed is named, documented and overridable from the tool call.
The point is not precision to the millisecond -- it is to say "this tyre is
losing two tenths a lap" and "that stop costs 21 seconds" with the evidence next
to it, and to say nothing at all when there are not enough laps to fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import history
from .model import Driver, Session

# --- constants the feed cannot tell us --------------------------------------

#: A full race fuel load. Cars start on the permitted maximum, not on a measured
#: value, so this is the honest default.
FUEL_START_KG = 110.0
#: Seconds lost per kilogram of fuel carried, per lap. The paddock figure has sat
#: around 0.03-0.035 s/kg for years; 0.033 splits it.
FUEL_EFFECT = 0.033
#: Seconds a stop costs: pit-lane transit plus the stationary time. ~21 s at most
#: circuits; measured stops in PitLaneTimeCollection are stationary time only.
PIT_LOSS = 21.0
#: Below this pace advantage a car cannot close and pass; it just sits there.
MIN_DELTA = 0.12
#: Fallbacks when a compound has no observed laps yet (s/lap, and seconds off the
#: medium at zero age).
DEFAULT_DEG = {"SOFT": 0.075, "MEDIUM": 0.050, "HARD": 0.035, "INTERMEDIATE": 0.090, "WET": 0.110}
DEFAULT_OFFSET = {"SOFT": -0.35, "MEDIUM": 0.0, "HARD": 0.45, "INTERMEDIATE": 3.5, "WET": 9.0}
#: Laps kept for the pace medians: enough to smooth one mistake, short enough to
#: still be about the tyres the car is on now.
RECENT = 5


#: Laps a set can realistically reach on a dry track before it is scrap. Used
#: only to decide whether "stay out to the flag" is an option at all.
TYRE_LIFE = {"SOFT": 28, "MEDIUM": 38, "HARD": 48, "INTERMEDIATE": 32, "WET": 32}


#: Laps on a set before its degradation fit is trusted for planning. Below this
#: the fit is dominated by track evolution (rubber going down), not tyre wear.
MIN_FIT_LAPS = 6

#: How much of the variance a fit has to explain before it may be called a wear
#: rate. A slope with an R² of 0.03 is a line through noise -- usually the fuel
#: correction and a track still rubbering in -- and pricing a stop off it is
#: worse than using the book figure and saying so.
MIN_FIT_R2 = 0.20


@dataclass(slots=True)
class Lap:
    lap: int
    raw: float
    adjusted: float        # fuel-corrected to an empty tank
    age: int               # laps on this set of tyres when the lap started
    compound: str


@dataclass(slots=True)
class DegRow:
    label: str
    position: int | None
    compound: str
    age: int
    slope: float | None        # s/lap, positive = losing time
    samples: int
    r2: float | None
    five_lap_cost: float | None    # what the next five laps should cost
    trusted: bool = False          # enough laps to mean anything


@dataclass(slots=True)
class FuelRow:
    label: str
    position: int | None
    raw: float | None
    corrected: float | None
    delta: float | None        # to the best corrected lap


@dataclass(slots=True)
class OvertakeRow:
    ahead: str
    behind: str
    gap: float
    delta: float | None        # s/lap the chasing car is quicker by
    drs: bool
    laps_to_drs: float | None
    pass_lap: int | None
    note: str


@dataclass(slots=True)
class Plan:
    stops: int
    sequence: str              # e.g. "M14->H"
    pit_laps: list[int]
    total: float               # projected seconds to the flag
    vs_best: float
    vs_stay_out: float
    basis: str = ""            # which wear rates were measured, which are book


# --- shared maths -----------------------------------------------------------


def fuel_kg(lap: int, total: int | None, start_kg: float = FUEL_START_KG) -> float:
    """Kilograms still on board at the start of ``lap`` (1-based)."""
    if not total or total < 2:
        return 0.0
    burned = min(max(lap - 1, 0), total)
    return max(0.0, start_kg * (1.0 - burned / total))


def _fit(points: list[tuple[float, float]]) -> tuple[float, float, float, int]:
    """Least squares ``y = a + b*x``. Returns (slope, intercept, r2, n)."""
    n = len(points)
    if n < 2:
        return 0.0, (points[0][1] if points else 0.0), 0.0, n
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx <= 0:
        return 0.0, my, 0.0, n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((p[1] - my) ** 2 for p in points)
    ss_res = sum((p[1] - (intercept + slope * p[0])) ** 2 for p in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2, n


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


# --- lap extraction ---------------------------------------------------------


def laps(session: Session, *, fuel_effect: float = FUEL_EFFECT,
         start_kg: float = FUEL_START_KG) -> dict[str, list[Lap]]:
    """Every recorded lap, with the compound and tyre age it was driven on."""
    stored = history.read(session.key).get("laps") or {}
    total = session.total_laps
    out: dict[str, list[Lap]] = {}
    for driver in session.drivers:
        if not driver.stints:
            continue
        own = stored.get(driver.num) or []
        if not own:
            continue
        windows: list[tuple[int, int, str]] = []
        for index, stint in enumerate(driver.stints):
            start = stint.start_laps
            if index == len(driver.stints) - 1 and driver.laps:
                # The open stint's age is authoritative: the feed counts it live.
                start = max(start, driver.laps - stint.age)
            end = start + max(stint.age, 0)
            windows.append((start, end, stint.compound))
        rows: list[Lap] = []
        for entry in own:
            try:
                lap_no = int(entry["lap"])
                raw = float(entry["t"])
            except (KeyError, TypeError, ValueError):
                continue
            compound, age = "", 0
            for start, end, name in windows:
                if start < lap_no <= max(end, start + 1):
                    compound, age = name, lap_no - start
            if not compound:
                compound, age = windows[-1][2], max(1, lap_no - windows[-1][0])
            corrected = raw - fuel_effect * fuel_kg(lap_no, total, start_kg)
            rows.append(Lap(lap=lap_no, raw=raw, adjusted=corrected, age=max(0, age), compound=compound))
        if rows:
            out[driver.num] = rows
    return out


def recent_pace(session: Session, driver: Driver, laps_by_num: dict[str, list[Lap]] | None = None,
                keep: int = RECENT) -> float | None:
    """Median of the driver's last ``keep`` laps, out-laps and outliers dropped."""
    rows = (laps_by_num or {}).get(driver.num) or []
    if not rows:
        return None
    tail = [row.raw for row in rows if row.age >= 1][-keep:]
    if len(tail) < 2:
        return None
    med = _median(tail)
    if med is None:
        return None
    clean = [t for t in tail if t <= med * 1.06]
    return _median(clean) or med


# --- 1. degradation ---------------------------------------------------------


def degradation(session: Session, *, min_samples: int = 4) -> list[DegRow]:
    """Tyre degradation on each car's current set, from its own fuel-corrected laps."""
    laps_by_num = laps(session)
    rows: list[DegRow] = []
    for driver in session.drivers:
        if not driver.stints:
            continue
        stint = driver.stints[-1]
        # Only laps from *this* set: an earlier stint on the same compound would
        # otherwise be pooled in and flatten the fit.
        start_lap = max(1, (driver.laps or 0) - stint.age)
        own = [row for row in (laps_by_num.get(driver.num) or [])
               if row.compound == stint.compound and row.age >= 2 and row.lap > start_lap]
        points = [(float(row.age), row.adjusted) for row in own]
        slope, _base, r2, n = _fit(points)
        usable = n >= min_samples
        rows.append(
            DegRow(
                label=driver.label,
                position=driver.position,
                compound=stint.compound,
                age=stint.age,
                slope=round(slope, 3) if usable else None,
                samples=n,
                r2=round(r2, 2) if usable else None,
                five_lap_cost=round(slope * 5, 2) if usable else None,
                trusted=usable and _measured(n, slope, r2),
            )
        )
    rows.sort(key=lambda row: (row.position or 99))
    return rows


def _measured(samples: int, slope: float, r2: float) -> bool:
    """Is this fit a wear rate, or a line drawn through noise?

    Three ways to fail: too few laps, a slope the wrong way (or flat, which is
    track evolution not tyre life), and an R² too low for the line to explain
    anything. Any of them means the book figure is the honest input.
    """
    return samples >= MIN_FIT_LAPS and slope >= 0.005 and r2 >= MIN_FIT_R2


def _curve_measured(curves: dict[str, tuple[float, float, int, float]], compound: str) -> bool:
    base, slope, samples, r2 = curves.get(compound, (0.0, 0.0, 0, 0.0))
    return _measured(samples, slope, r2)


def compound_curves(session: Session) -> dict[str, tuple[float, float, int, float]]:
    """Field-wide (base, slope, samples, r2) per compound, in fuel-corrected seconds.

    The first lap of a stint is not the tyre's pace: it is an out-lap, a standing
    start or a cold tyre.  Pooling those in shifts the intercept by seconds and
    makes one compound look free, so they are excluded -- as are laps more than
    a tenth off the field median, which are traffic or mistakes.
    """
    laps_by_num = laps(session)
    pooled: dict[str, list[tuple[float, float]]] = {}
    for rows in laps_by_num.values():
        for row in rows:
            if row.age >= 2 and row.compound:
                pooled.setdefault(row.compound.upper(), []).append((float(row.age), row.adjusted))
    curves: dict[str, tuple[float, float, int, float]] = {}
    for compound, points in pooled.items():
        median = _median([p[1] for p in points])
        if median:
            points = [p for p in points if p[1] <= median * 1.10]
        slope, base, r2, n = _fit(points)
        curves[compound] = (base, slope, n, r2)
    return curves


# --- 2. fuel-corrected pace -------------------------------------------------


def fuel_pace(session: Session, *, fuel_effect: float = FUEL_EFFECT,
              start_kg: float = FUEL_START_KG) -> list[FuelRow]:
    """Last lap, the same lap on an empty tank, and the gap to the best."""
    laps_by_num = laps(session, fuel_effect=fuel_effect, start_kg=start_kg)
    rows: list[FuelRow] = []
    for driver in session.drivers:
        own = laps_by_num.get(driver.num) or []
        if not own:
            rows.append(FuelRow(driver.label, driver.position, driver.last_lap, None, None))
            continue
        last = own[-1]
        rows.append(FuelRow(driver.label, driver.position, last.raw, round(last.adjusted, 3), None))
    best = min((row.corrected for row in rows if row.corrected), default=None)
    if best is not None:
        for row in rows:
            if row.corrected is not None:
                row.delta = round(row.corrected - best, 3)
    rows.sort(key=lambda row: (row.delta is None, row.delta or 0.0, row.position or 99))
    return rows


# --- 3. overtakes -----------------------------------------------------------


def overtakes(session: Session, *, window: float = 2.0, floor: float = MIN_DELTA) -> list[OvertakeRow]:
    """Pairs close enough to fight, and how many laps the chase should take."""
    laps_by_num = laps(session)
    order = [driver for driver in session.sorted_by_pace()
             if driver.position and not driver.retired and not driver.stopped]
    rows: list[OvertakeRow] = []
    current = session.current_lap or 0
    total = session.total_laps or 0
    for ahead, behind in zip(order, order[1:]):
        gap = behind.gap_ahead
        if gap is None or gap <= 0 or gap > window:
            continue
        if behind.in_pit or ahead.in_pit or behind.pit_out:
            continue
        pace_behind = recent_pace(session, behind, laps_by_num)
        pace_ahead = recent_pace(session, ahead, laps_by_num)
        delta = None if pace_behind is None or pace_ahead is None else pace_ahead - pace_behind
        drs = gap <= 1.0
        laps_to_drs = None
        pass_lap = None
        if delta is not None and delta > 0.005:
            if gap > 1.0:
                laps_to_drs = round((gap - 1.0) / delta, 1)
            else:
                laps_to_drs = 0.0
            if delta >= floor:
                pass_lap = current + max(1, int(round(gap / delta)))
        if delta is None:
            note = "no recent laps to compare"
        elif delta < 0.005:
            note = "being pulled away from"
        elif delta < floor:
            note = "holding station"
        elif pass_lap and total and pass_lap > total:
            note = "cannot close before the flag"
        else:
            note = f"closing {delta:.2f} s/lap"
        rows.append(
            OvertakeRow(
                ahead=ahead.label,
                behind=behind.label,
                gap=round(gap, 3),
                delta=None if delta is None else round(delta, 3),
                drs=drs,
                laps_to_drs=laps_to_drs,
                pass_lap=pass_lap,
                note=note,
            )
        )
    rows.sort(key=lambda row: row.gap)
    return rows


# --- 4. pit plans -----------------------------------------------------------


def plans(session: Session, driver: Driver, *, stops: int = 1, pit_loss: float = PIT_LOSS,
          step: int = 2, limit: int = 3) -> list[Plan]:
    """Enumerate stop plans from here to the flag and rank them by total time."""
    if not session.total_laps or not session.current_lap or not driver.stints:
        return []
    total = session.total_laps
    current = session.current_lap
    remaining = total - current
    if remaining <= 3:
        return []

    curves = compound_curves(session)
    laps_by_num = laps(session)
    own = laps_by_num.get(driver.num) or []
    now_compound = driver.stints[-1].compound.upper()
    now_age = driver.stints[-1].age

    points = [(float(row.age), row.adjusted) for row in own
              if row.compound.upper() == now_compound and row.age >= 2
              and row.lap > max(1, current - now_age)]
    slope_now, base_now, r2_now, n_now = _fit(points)
    # Decide "measured" from the fit itself, before the fallback overwrites the
    # slope -- otherwise every car with enough laps claims a measured rate.
    measured_now = _measured(n_now, slope_now, r2_now)
    if not measured_now:
        # Not measurable yet, measured flat or wrong-way, or a line through
        # noise: wear it at the book rate rather than not at all.
        base_now = base_now if n_now >= 2 else (_median([row.adjusted for row in own]) or 0.0)
        slope_now = DEFAULT_DEG.get(now_compound, 0.05)
    else:
        slope_now = min(0.25, slope_now)
    if base_now <= 0:
        return []          # no usable pace: a plan built on nothing is worse than none

    def curve(compound: str) -> tuple[float, float]:
        base, slope, samples, r2 = curves.get(compound, (0.0, 0.0, 0, 0.0))
        if not _measured(samples, slope, r2):
            # Offsets are relative to the compound the car is on: a medium is not
            # free just because it is the reference compound.
            offset = DEFAULT_OFFSET.get(compound, 0.0) - DEFAULT_OFFSET.get(now_compound, 0.0)
            return offset, DEFAULT_DEG.get(compound, 0.05)
        reference = curves.get(now_compound)
        offset = (base - reference[0]) if reference and _curve_measured(curves, now_compound) else 0.0
        return offset, min(0.25, max(0.0, slope))

    options = [c for c in curves if _curve_measured(curves, c)] or ["MEDIUM", "HARD"]
    options = [c for c in options if c != now_compound] or options

    basis_bits = [f"{now_compound[:1]} {'measured' if measured_now else 'book'}"]
    for compound in options:
        base_c, slope_c, samples_c, r2_c = curves.get(compound, (0.0, 0.0, 0, 0.0))
        known = _measured(samples_c, slope_c, r2_c)
        basis_bits.append(f"{compound[:1]} {'measured' if known else 'book'}")
    basis = " ".join(dict.fromkeys(basis_bits))

    def simulate(first_pit: int, second_pit: int | None, first: str, second: str) -> float:
        clock = 0.0
        age = now_age
        compound = now_compound
        for lap in range(current + 1, total + 1):
            age += 1
            offset, slope = (0.0, slope_now) if compound == now_compound else curve(compound)
            clock += base_now + offset + slope * age + fuel_kg(lap, total) * FUEL_EFFECT
            if lap == first_pit or (second_pit is not None and lap == second_pit):
                clock += pit_loss
                compound = first if lap == first_pit else second
                age = 1
        return clock

    candidates: list[Plan] = []
    stay_out = simulate(total + 1, None, now_compound, now_compound)
    first_grid = list(range(current + 2, total - 2, max(1, step)))
    if stops <= 1:
        for pit_lap in first_grid:
            for compound in options:
                total_time = simulate(pit_lap, None, compound, compound)
                sequence = f"{now_compound[:1]}{now_age}->{compound[:1]}"
                candidates.append(
                    Plan(1, sequence, [pit_lap], total_time, 0.0, total_time - stay_out, basis)
                )
    else:
        for first_pit in first_grid:
            for second_pit in range(first_pit + 2, total - 1, max(1, step)):
                for first in options:
                    for second in options:
                        total_time = simulate(first_pit, second_pit, first, second)
                        sequence = (
                            f"{now_compound[:1]}{now_age}->{first[:1]}->{second[:1]}"
                        )
                        candidates.append(
                            Plan(2, sequence, [first_pit, second_pit], total_time, 0.0,
                                 total_time - stay_out, basis)
                        )
    if not candidates:
        return []
    candidates.sort(key=lambda plan: plan.total)
    best = candidates[0].total
    for plan in candidates:
        plan.vs_best = round(plan.total - best, 1)
    return candidates[:limit]

# --- 5. gaps ----------------------------------------------------------------


@dataclass(slots=True)
class GapInfo:
    """Seconds between cars, whatever the feed is willing to publish."""

    source: str                     # feed | laps | none
    ahead: dict[str, float]         # racing number -> seconds to the car in front
    leader: dict[str, float]        # racing number -> seconds to the leader
    note: str = ""


def _order(session: Session) -> list[Driver]:
    """Classified cars in running order. Never sort a race by best lap."""
    cars = [driver for driver in session.drivers if driver.position and not driver.retired]
    cars.sort(key=lambda driver: driver.position or 99)
    return cars


def gap_info(session: Session, laps_by_num: dict[str, list[Lap]] | None = None) -> GapInfo:
    """Intervals from the feed, or rebuilt from lap history when it is silent.

    The official feed does not always publish intervals -- some replays carry
    ``Position`` and nothing else -- so the gaps are rebuilt by summing each
    car's recorded laps from a common start.  That is exact arithmetic, not an
    estimate, but it needs one recorded lap per lap number: a car with a hole in
    its history (a deleted lap) is left out rather than given a made-up gap.
    """
    laps_by_num = laps_by_num if laps_by_num is not None else laps(session)
    order = _order(session)
    if not order:
        return GapInfo("none", {}, {}, "no classified cars")

    if any(driver.gap_ahead is not None for driver in order):
        ahead: dict[str, float] = {}
        leader: dict[str, float] = {}
        running = 0.0
        known = True
        for index, driver in enumerate(order):
            if index:
                gap = driver.gap_ahead
                if gap is None:
                    known = False
                else:
                    running += gap
            if index == 0:
                ahead[driver.num] = 0.0
                leader[driver.num] = 0.0
            elif known:
                ahead[driver.num] = float(driver.gap_ahead)
                leader[driver.num] = running
        return GapInfo("feed", ahead, leader)

    current = session.current_lap or 0
    series: dict[str, list[Lap]] = {}
    for driver in order:
        rows = sorted((row for row in (laps_by_num.get(driver.num) or []) if row.lap <= current),
                      key=lambda row: row.lap)
        if len(rows) >= 2:
            series[driver.num] = rows
    if len(series) < 2:
        return GapInfo("none", {}, {}, "no lap history to rebuild gaps")

    # Sum every car over the *same* laps. Cars cross the line at different
    # moments, so one may have a lap the others do not: comparing raw totals
    # then reads as a free lap of advantage. A car with a hole (a deleted lap)
    # is dropped rather than given a made-up gap.
    start = max(rows[0].lap for rows in series.values())
    end = min(rows[-1].lap for rows in series.values())
    if end - start < 2:
        return GapInfo("none", {}, {}, "lap history is too short to compare")
    span = end - start + 1
    usable: dict[str, float] = {}
    for num, rows in series.items():
        window = [row for row in rows if start <= row.lap <= end]
        if len(window) == span:
            usable[num] = sum(row.raw for row in window)
    if len(usable) < 2:
        return GapInfo("none", {}, {}, "lap history does not line up")

    base = min(usable.values())
    leader = {num: total - base for num, total in usable.items()}
    ahead = {}
    previous: str | None = None
    for driver in order:
        if driver.num not in leader:
            continue
        ahead[driver.num] = 0.0 if previous is None else leader[driver.num] - leader[previous]
        previous = driver.num
    return GapInfo("laps", ahead, leader, f"rebuilt from {span} laps of history")


# --- 6. undercut ------------------------------------------------------------


@dataclass(slots=True)
class UndercutRow:
    ahead: str
    behind: str
    gap: float
    gain: float | None         # s/lap the chasing car takes out of the gap on fresh tyres
    laps: float | None         # laps before the stop has paid for itself
    verdict: str


@dataclass(slots=True)
class Undercut:
    source: str
    rows: list[UndercutRow] = field(default_factory=list)
    note: str = ""


def undercut(session: Session, *, pit_loss: float = PIT_LOSS, window: float = 25.0) -> Undercut:
    """Who can win a place in the pit lane, and how long the cover lasts."""
    lap_map = laps(session)
    info = gap_info(session, lap_map)
    order = _order(session)
    wear = {row.label: row for row in degradation(session)}
    rows: list[UndercutRow] = []

    for ahead, behind in zip(order, order[1:]):
        gap = info.ahead.get(behind.num)
        if gap is None or gap <= 0 or gap > window:
            continue
        defender = wear.get(ahead.label)
        if defender is None:
            continue
        if defender.slope is not None:
            gain = defender.slope
        elif defender.compound:
            gain = DEFAULT_DEG.get(defender.compound.upper(), 0.05)
        else:
            gain = None

        stint = behind.stints[-1] if behind.stints else None
        fresh = stint is not None and stint.age >= 3
        # An undercut wins when the chaser stops first, spends (gap + pit loss)
        # behind, and claws it back on fresh tyres before the car ahead stops
        # and pays the same pit loss. He needs to erase `gap` at `gain` a lap,
        # so the car ahead has gap/gain laps of cover -- not pit-loss/gain.
        remaining = (session.total_laps or 0) - (session.current_lap or 0)
        if gain is None or gain <= 0.02:
            rows.append(UndercutRow(ahead.label, behind.label, round(gap, 3), gain, None,
                                    "no undercut: the car ahead is not losing pace"))
        elif not fresh:
            rows.append(UndercutRow(ahead.label, behind.label, round(gap, 3), round(gain, 3), None,
                                    "too early in the stint to stop"))
        else:
            cover = gap / gain
            if cover > remaining:
                verdict = "cannot be undercut before the flag"
            else:
                verdict = "pit before then or lose the place"
            rows.append(UndercutRow(ahead.label, behind.label, round(gap, 3), round(gain, 3),
                                    round(cover, 1), verdict))

    rows.sort(key=lambda row: row.gap)
    return Undercut(info.source, rows, info.note)


# --- 7. tyre windows --------------------------------------------------------


@dataclass(slots=True)
class WindowRow:
    label: str
    position: int | None
    compound: str
    age: int
    life: int
    left: int                 # laps the set can still do
    must_stop_by: int | None  # lap number, None when the set reaches the flag
    reach_flag: bool


def _pace_model(driver: Driver, rows: list[Lap], compound: str) -> tuple[float | None, float]:
    """Zero-age pace and wear rate, anchored on what the car is doing *now*.

    Fitting an intercept over a two-lap stint and projecting it 30 laps is how
    a projection ends up two seconds a lap out. Peg the intercept to the recent
    median instead, and let the slope say what happens next.
    """
    recent = [row for row in rows if row.compound.upper() == compound and row.age >= 2][-RECENT:]
    if len(recent) < 2:
        return None, DEFAULT_DEG.get(compound, 0.05)
    fitted, _base, _r2, n = _fit([(float(row.age), row.adjusted) for row in recent])
    pace = _median([row.adjusted for row in recent])
    age = _median([float(row.age) for row in recent]) or 0.0
    if pace is None:
        return None, DEFAULT_DEG.get(compound, 0.05)
    if n < MIN_FIT_LAPS or fitted < 0.005:
        slope = DEFAULT_DEG.get(compound, 0.05)
    else:
        slope = min(0.25, fitted)
    return pace - slope * age, slope


def tyre_life(session: Session) -> dict[str, int]:
    """Book tyre life, raised to whatever this session has already proven.

    The defaults are paddock averages. If a car is out there on a 37-lap-old
    soft, the soft's life in this session is not 28 -- saying "he must stop"
    about the car that just proved otherwise is the fastest way to lose the room.
    """
    seen: dict[str, int] = {}
    for driver in session.drivers:
        for stint in driver.stints:
            compound = stint.compound.upper()
            if compound:
                seen[compound] = max(seen.get(compound, 0), stint.age)
    life = dict(TYRE_LIFE)
    for compound, age in seen.items():
        life[compound] = max(life.get(compound, 38), age + 5)
    return life


def windows(session: Session) -> list[WindowRow]:
    """How long each set has left, and who cannot reach the flag without a stop."""
    total = session.total_laps or 0
    current = session.current_lap or 0
    if not total or not current:
        return []
    remaining = total - current
    life_table = tyre_life(session)
    rows: list[WindowRow] = []
    for driver in _order(session):
        stint = driver.stints[-1] if driver.stints else None
        if stint is None:
            continue
        compound = stint.compound.upper()
        life = life_table.get(compound, 38)
        age = stint.age
        left = max(0, life - age)
        rows.append(
            WindowRow(
                label=driver.label,
                position=driver.position,
                compound=compound,
                age=age,
                life=life,
                left=left,
                must_stop_by=current + left if left < remaining else None,
                reach_flag=age + remaining <= life,
            )
        )
    rows.sort(key=lambda row: (row.reach_flag, row.left, row.position or 99))
    return rows


# --- 8. likely outcome ------------------------------------------------------


@dataclass(slots=True)
class OutlookRow:
    label: str
    position: int | None
    projected: float          # seconds to the projected leader
    move: int                 # places gained (+) or lost (-)
    tyre: str


@dataclass(slots=True)
class Outlook:
    source: str
    rows: list[OutlookRow] = field(default_factory=list)
    note: str = ""


def outlook(session: Session, *, pit_loss: float = PIT_LOSS, limit: int = 10) -> Outlook:
    """Projected finishing order if everyone runs clean from here.

    Every car is run to the flag on its current tyres, with a stop forced when
    the set reaches its life, priced with this session's compound curves and the
    fuel burn.  Gaps are the starting point, so the projection is a clean-air
    one: no traffic, no safety car, no team orders, no defending.
    """
    lap_map = laps(session)
    info = gap_info(session, lap_map)
    total = session.total_laps or 0
    current = session.current_lap or 0
    if info.source == "none":
        return Outlook("none", [], info.note or "no gaps to project from")
    if not total or not current or current >= total:
        return Outlook(info.source, [], "no laps left to project")

    life_table = tyre_life(session)
    projections: dict[str, float] = {}
    tyres: dict[str, str] = {}
    for driver in _order(session):
        stint = driver.stints[-1] if driver.stints else None
        if stint is None:
            continue
        compound = stint.compound.upper()
        base, slope = _pace_model(driver, lap_map.get(driver.num) or [], compound)
        if base is None or base <= 0:
            continue

        life = life_table.get(compound, 38)
        age = stint.age
        clock = 0.0
        stopped = False
        moved_to = compound
        for lap in range(current + 1, total + 1):
            if age >= life and not stopped:
                clock += pit_loss
                stopped = True
                moved_to = "MEDIUM" if compound != "MEDIUM" else "HARD"
                slope = DEFAULT_DEG.get(moved_to, 0.05)
                age = 1
            age += 1
            clock += base + slope * age + fuel_kg(lap, total) * FUEL_EFFECT
        gap = info.leader.get(driver.num)
        if gap is None:
            continue
        projections[driver.num] = gap + clock
        tyres[driver.num] = f"{compound[:1]}{stint.age}" + ("" if not stopped else f">{moved_to[:1]}")

    if len(projections) < 2:
        return Outlook(info.source, [], "not enough data to project an order")

    best = min(projections.values())
    ordered = sorted(projections.items(), key=lambda item: item[1])
    by_num = {driver.num: driver for driver in session.drivers}
    rows: list[OutlookRow] = []
    for index, (num, projected) in enumerate(ordered[:limit], start=1):
        driver = by_num[num]
        rows.append(
            OutlookRow(
                label=driver.label,
                position=driver.position,
                projected=projected - best,
                move=(driver.position or index) - index,
                tyre=tyres.get(num, ""),
            )
        )
    return Outlook(info.source, rows, info.note)
