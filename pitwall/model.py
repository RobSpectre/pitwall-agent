"""Turn the raw F1 live-timing document into a small, analysis-ready model.

MultiViewer mirrors the official F1 live-timing feed as a flat dict of sections
(``TimingData``, ``DriverList``, ``TimingStats``, ...).  The document is large
(~75 KB mid-session) and heavily redundant.  This module reads only the fields
the pit wall reasons about, normalises every duration to float seconds, and
derives the three broadcast questions:

* who is on a flying lap right now (and how good is it going),
* who owns the purple sectors,
* who can realistically take pole.

Nothing here talks to the network or prints anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .timefmt import parse_clock, parse_time

# --- broadcast constants ---------------------------------------------------
#
# A push lap is judged against the driver's own best sectors where possible: a
# slow car can still be on a flyer, and the question is intent + execution, not
# absolute pace.  Thresholds are cumulative seconds lost versus that reference.
PUSH_TOL = {1: 0.45, 2: 0.90, 3: 1.35}
COOL_TOL = {1: 1.60, 2: 2.40, 3: 3.20}
#: A lap can be "close to his own best" and still be an installation run: the
#: own-best reference is soft when the reference itself is slow.  Also require
#: the lap to be within sight of the session pace.
PUSH_SESSION_TOL = {1: 3.50, 2: 6.00, 3: 9.00}

TRACK_STATUS = {
    "1": "ALL CLEAR",
    "2": "YELLOW",
    "3": "SC DEPLOYED",
    "4": "SAFETY CAR",
    "5": "RED FLAG",
    "6": "VSC",
    "7": "VSC ENDING",
}

#: Grid slots eliminated at the end of each qualifying part.
CUTOFF_BY_PART = {"1": 15, "2": 10, "3": None}

#: Tyre compound -> the single letter the timing tower uses.
COMPOUNDS = {
    "SOFT": "S", "MEDIUM": "M", "HARD": "H",
    "INTERMEDIATE": "I", "WET": "W",
    "UNKNOWN": "?", "TEST_UNKNOWN": "?",
}

RUN_STATES = ("PUSH", "BUILD", "COOL", "LAP", "OUT", "PIT", "STOPPED", "RETIRED", "DONE", "NONE")


@dataclass(slots=True)
class Sector:
    t: float | None
    overall: bool
    personal: bool
    status: int
    segments: tuple[int, ...] = ()

    @property
    def letter(self) -> str:
        """P = fastest in session, G = personal best, - = neither."""
        if self.t is None:
            return "-"
        if self.overall:
            return "P"
        if self.personal:
            return "G"
        return "-"


@dataclass(slots=True)
class Stint:
    compound: str
    start_laps: int
    total_laps: int
    lap_time: float | None = None
    new: bool = True

    @property
    def age(self) -> int:
        """Laps completed on this set of tyres."""
        return max(0, self.total_laps - self.start_laps)

    @property
    def code(self) -> str:
        return COMPOUNDS.get(self.compound.upper(), (self.compound[:1] or "?").upper())

    @property
    def label(self) -> str:
        return f"{self.code}{self.age}" if self.compound else f"?{self.age}"


@dataclass(slots=True)
class Driver:
    num: str
    tla: str
    name: str
    team: str
    colour: str
    position: int | None
    sectors: list[Sector]
    sector_pb: list[float | None]
    speeds: dict[str, float | None]
    last_lap: float | None
    last_lap_overall: bool
    best_lap: float | None
    best_lap_no: int | None
    laps: int
    gap_leader: float | None
    gap_ahead: float | None
    in_pit: bool
    pit_out: bool
    stopped: bool
    retired: bool
    outlap: bool
    knocked_out: bool
    cutoff: bool
    taken_chequered: bool
    stints: list[Stint] = field(default_factory=list)
    never_started: bool = False
    theo_sectors: tuple[float | None, ...] = ()
    pit_time: float | None = None

    # derived
    run: str = "NONE"
    run_note: str = ""
    sectors_done: int = 0
    theo: float | None = None      # own best sectors summed
    proj: float | None = None      # current lap projected with own best sectors
    delta_pb: float | None = None  # current lap vs own best-sector pace, cumulative

    @property
    def stint(self) -> Stint | None:
        return self.stints[-1] if self.stints else None

    @property
    def tyre(self) -> str:
        stint = self.stint
        return stint.label if stint else "-"

    @property
    def stops(self) -> int:
        """Pit stops so far, inferred from the number of tyre runs."""
        return max(0, len(self.stints) - 1)

    @property
    def on_track(self) -> bool:
        return not (self.in_pit or self.retired or self.stopped)

    @property
    def flying(self) -> bool:
        return self.run == "PUSH"

    @property
    def label(self) -> str:
        return self.tla or self.num


@dataclass(slots=True)
class Session:
    key: int | None
    meeting: str
    circuit: str
    name: str
    kind: str
    part: str | None
    remaining: float | None
    status: str
    track: str
    track_message: str
    air: float | None
    track_temp: float | None
    humidity: float | None
    wind: float | None
    rain: float | None
    drivers: list[Driver]
    by_num: dict[str, Driver]
    best_sector: list[float | None]
    best_sector_holder: list[str | None]
    pole: float | None
    pole_holder: str | None
    theo_session: float | None
    messages: list[dict]
    radio: list[dict]
    fetched_at: float
    archive: str = ""
    total_laps: int | None = None
    current_lap: int | None = None
    mode: str = "unknown"          # practice | qualifying | race | unknown

    # --- convenience views -------------------------------------------------

    @property
    def is_qualifying(self) -> bool:
        return self.kind.lower().startswith("qualif")

    @property
    def cutoff_pos(self) -> int | None:
        return CUTOFF_BY_PART.get(self.part or "", None) if self.is_qualifying else None

    @property
    def lap_time_ref(self) -> float:
        """A representative lap time: pole, else the session median best."""
        if self.pole:
            return self.pole
        bests = sorted(d.best_lap for d in self.drivers if d.best_lap)
        if not bests:
            return 90.0
        return bests[len(bests) // 2]

    @property
    def laps_left(self) -> int:
        if self.remaining is None:
            return 0
        return int(self.remaining // max(self.lap_time_ref, 1.0))

    def flyers(self) -> list[Driver]:
        return [d for d in self.drivers if d.run == "PUSH"]

    def on_track(self) -> list[Driver]:
        return [d for d in self.drivers if d.on_track]

    def sorted_by_pace(self) -> list[Driver]:
        return sorted(
            self.drivers,
            key=lambda d: (d.best_lap is None, d.best_lap or 0.0, d.position or 99),
        )


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(mv: dict, raw: dict, name: str) -> bool:
    """MultiViewer's decoded flags win; fall back to the raw timing line."""
    if name in mv:
        return bool(mv.get(name))
    return bool(raw.get(name))


def _segments(sector: dict) -> tuple[int, ...]:
    return tuple(int(s.get("Status") or 0) for s in sector.get("Segments") or ())


def _sectors(line: dict) -> list[Sector]:
    out: list[Sector] = []
    raw = line.get("Sectors") or []
    for item in raw[:3]:
        out.append(
            Sector(
                t=parse_time(item.get("Value")),
                overall=bool(item.get("OverallFastest")),
                personal=bool(item.get("PersonalFastest")),
                status=int(item.get("Status") or 0),
                segments=_segments(item),
            )
        )
    while len(out) < 3:
        out.append(Sector(None, False, False, 0))
    return out


def _stints(entry: dict) -> list[Stint]:
    """Parse ``TimingAppData`` stints.

    The feed records one entry per run of tyres and keeps updating ``TotalLaps``
    as the run continues, so a stint's age is ``TotalLaps - StartLaps``.
    """
    out: list[Stint] = []
    for item in entry.get("Stints") or ():
        start = int(item.get("StartLaps") or 0)
        total = int(item.get("TotalLaps") or start)
        out.append(
            Stint(
                compound=str(item.get("Compound") or ""),
                start_laps=start,
                total_laps=total,
                lap_time=parse_time(item.get("LapTime")),
                new=str(item.get("New") or "true").strip().lower() not in {"false", "0"},
            )
        )
    return out


def _best_lap_no(stats_line: dict) -> int | None:
    value = (stats_line.get("PersonalBestLapTime") or {}).get("Lap")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _best_lap_number(line: dict) -> int | None:
    value = (line.get("BestLapTime") or {}).get("Lap")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# run-state classification -- the "is he on a flyer" question
# ---------------------------------------------------------------------------


def _classify_run(driver: Driver, best_sector: list[float | None]) -> None:
    """Set ``run``/``run_note``/``proj``/``delta_pb`` for one driver.

    Sectors in the feed always describe the lap being run: on the lap they
    reset, then fill in S1, S2, S3.  So "one sector has a value" means the car
    is somewhere in sector 2 of a live lap.
    """
    done = [s.t for s in driver.sectors if s.t is not None]
    driver.sectors_done = len(done)

    if driver.retired:
        driver.run, driver.run_note = "RETIRED", "retired"
        return
    if driver.stopped:
        driver.run, driver.run_note = "STOPPED", "stopped on track"
        return
    if driver.in_pit:
        driver.run, driver.run_note = "PIT", "in the pits"
        return
    if driver.pit_out or driver.outlap:
        driver.run, driver.run_note = "OUT", "outlap"
        return
    if driver.taken_chequered and not done:
        driver.run, driver.run_note = "DONE", "took the chequered flag"
        return

    reference = driver.sector_pb if any(driver.sector_pb) else best_sector
    if not done:
        if driver.laps == 0 and not driver.best_lap:
            driver.run, driver.run_note = "NONE", "no timed lap yet"
        else:
            driver.run, driver.run_note = "LAP", "on a lap, S1 not yet complete"
        if driver.theo:
            driver.proj = driver.theo
        return

    reference_total = sum(x for x in reference[: len(done)] if x)
    if not reference_total:
        driver.run, driver.run_note = "LAP", "on a lap"
        return

    delta_own = sum(done) - reference_total
    driver.delta_pb = delta_own
    if driver.theo:
        driver.proj = sum(done) + sum(x for x in driver.theo_sectors[len(done):] if x)

    session_slice = [x for x in best_sector[: len(done)] if x]
    delta_session = sum(done) - sum(session_slice) if len(session_slice) == len(done) else None

    if len(done) >= 3:
        driver.run = "LAP"
        driver.run_note = f"just completed a lap ({delta_own:+.3f})"
        return

    within_reference = delta_own <= PUSH_TOL.get(len(done), 1.35)
    within_session = delta_session is None or delta_session <= PUSH_SESSION_TOL.get(len(done), 9.0)
    if within_reference and within_session:
        driver.run = "PUSH"
        driver.run_note = f"on a flyer ({delta_own:+.3f} vs own best sectors)"
    elif delta_own >= COOL_TOL.get(len(done), 3.20):
        driver.run = "COOL"
        driver.run_note = f"cool-down lap ({delta_own:+.3f} vs own best sectors)"
    elif not within_session:
        driver.run = "BUILD"
        driver.run_note = f"not on the pace ({delta_session:+.3f} vs session best)"
    else:
        driver.run = "BUILD"
        driver.run_note = f"building ({delta_own:+.3f} vs own best sectors)"



# ---------------------------------------------------------------------------
# document -> Session
# ---------------------------------------------------------------------------


def _session_part(state: dict) -> str | None:
    data = state.get("SessionData") or {}
    for series in (data.get("StatusSeries"), data.get("Series")):
        parts = [s.get("QualifyingPart") for s in series or () if s.get("QualifyingPart")]
        if parts:
            return str(int(parts[-1]))
    info = state.get("SessionInfo") or {}
    for key in ("CurrentSessionPart", "QualifyingPart"):
        if info.get(key):
            return str(int(info[key]))
    return None


def _lap_count(state: dict) -> tuple[int | None, int | None]:
    count = state.get("LapCount") or {}

    def as_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return as_int(count.get("CurrentLap")), as_int(count.get("TotalLaps"))


def find_driver(session: Session, needle: object) -> Driver | None:
    """Resolve a racing number, TLA, or partial name to a Driver."""
    key = str(needle or "").strip().upper()
    if not key:
        return None
    if key in session.by_num:
        return session.by_num[key]
    for driver in session.drivers:
        if key in {driver.tla.upper(), driver.name.upper()}:
            return driver
    for driver in session.drivers:
        if key in driver.name.upper():
            return driver
    return None


def build(state: dict, *, fetched_at: float = 0.0, mode: str | None = None) -> Session:
    """Build a :class:`Session` from an ``F1LiveTimingState`` document."""
    info = state.get("SessionInfo") or {}
    meeting = (info.get("Meeting") or {}).get("Name") or "Unknown meeting"
    circuit = ((info.get("Meeting") or {}).get("Circuit") or {}).get("ShortName") or ""

    timing = ((state.get("TimingData") or {}).get("Lines")) or {}
    drivers_raw = (state.get("DriverList")) or {}
    stats = ((state.get("TimingStats") or {}).get("Lines")) or {}
    app = ((state.get("TimingAppData") or {}).get("Lines")) or {}
    pit_times = {
        str(num): parse_time((entry or {}).get("Duration"))
        for num, entry in (((state.get("PitLaneTimeCollection") or {}).get("PitTimes")) or {}).items()
    }

    drivers: list[Driver] = []
    for num, line in timing.items():
        meta = drivers_raw.get(num) or {}
        stat = stats.get(num) or {}
        mv = line.get("MVStatus") or {}
        sector_pb = [
            parse_time((s or {}).get("Value")) for s in (stat.get("BestSectors") or [])[:3]
        ]
        while len(sector_pb) < 3:
            sector_pb.append(None)

        position = None
        try:
            position = int(line.get("Position"))
        except (TypeError, ValueError):
            position = None

        driver = Driver(
            num=str(num),
            tla=str(meta.get("Tla") or "").strip(),
            name=str(meta.get("FullName") or meta.get("BroadcastName") or num).strip(),
            team=str(meta.get("TeamName") or "").strip(),
            colour=str(meta.get("TeamColour") or "").strip(),
            position=position,
            sectors=_sectors(line),
            sector_pb=sector_pb,
            speeds={
                key: _num((val or {}).get("Value"))
                for key, val in (line.get("Speeds") or {}).items()
            },
            last_lap=parse_time((line.get("LastLapTime") or {}).get("Value")),
            last_lap_overall=bool((line.get("LastLapTime") or {}).get("OverallFastest")),
            best_lap=parse_time((line.get("BestLapTime") or {}).get("Value")),
            best_lap_no=_best_lap_number(line),
            laps=int(_num(line.get("NumberOfLaps")) or 0),
            gap_leader=parse_time(line.get("TimeDiffToFastest")),
            gap_ahead=parse_time(line.get("TimeDiffToPositionAhead")),
            in_pit=_flag(mv, line, "InPit"),
            pit_out=_flag(mv, line, "PitOut"),
            stopped=_flag(mv, line, "Stopped"),
            retired=_flag(mv, line, "Retired"),
            outlap=_flag(mv, line, "Outlap"),
            knocked_out=_flag(mv, line, "KnockedOut"),
            cutoff=_flag(mv, line, "Cutoff"),
            taken_chequered=_flag(mv, line, "TakenChequered"),
            stints=_stints(app.get(num) or {}),
            never_started=bool(mv.get("NeverStarted")) if mv else False,
            theo_sectors=tuple(sector_pb),
            pit_time=pit_times.get(str(num)),
        )
        total = [x for x in sector_pb if x]
        driver.theo = sum(total) if len(total) == 3 else None
        drivers.append(driver)

    drivers.sort(key=lambda d: (d.position is None, d.position or 99, d.num))
    by_num = {d.num: d for d in drivers}

    # session-best sector, from TimingStats (best ever per driver this session)
    best_sector: list[float | None] = [None, None, None]
    best_holder: list[str | None] = [None, None, None]
    for driver in drivers:
        for i, value in enumerate(driver.sector_pb):
            if value and (best_sector[i] is None or value < best_sector[i]):
                best_sector[i] = value
                best_holder[i] = driver.label
    # fall back to the live purple flags when no stats have arrived yet
    for driver in drivers:
        for i, sector in enumerate(driver.sectors):
            if sector.t and sector.overall and (
                best_sector[i] is None or sector.t <= best_sector[i]
            ):
                best_sector[i] = sector.t
                best_holder[i] = driver.label

    pole = None
    pole_holder = None
    for driver in drivers:
        if driver.best_lap and (pole is None or driver.best_lap < pole):
            pole = driver.best_lap
            pole_holder = driver.label

    for driver in drivers:
        _classify_run(driver, best_sector)

    weather = state.get("WeatherData") or {}
    track = state.get("TrackStatus") or {}
    clock = state.get("ExtrapolatedClock") or {}
    status = state.get("SessionStatus") or {}
    archive = ((state.get("ArchiveStatus") or {}).get("Status")) or ""
    current_lap, total_laps = _lap_count(state)

    return Session(
        key=info.get("Key"),
        meeting=str(meeting),
        circuit=str(circuit),
        name=str(info.get("Name") or ""),
        kind=str(info.get("Type") or ""),
        part=_session_part(state),
        remaining=parse_clock(clock.get("Remaining")),
        status=str(status.get("Status") or ""),
        track=TRACK_STATUS.get(str(track.get("Status") or ""), str(track.get("Message") or "")),
        track_message=str(track.get("Message") or ""),
        air=_num(weather.get("AirTemp")),
        track_temp=_num(weather.get("TrackTemp")),
        humidity=_num(weather.get("Humidity")),
        wind=_num(weather.get("WindSpeed")),
        rain=_num(weather.get("Rainfall")),
        drivers=drivers,
        by_num=by_num,
        best_sector=best_sector,
        best_sector_holder=best_holder,
        pole=pole,
        pole_holder=pole_holder,
        theo_session=sum(best_sector) if all(best_sector) else None,
        messages=list((state.get("RaceControlMessages") or {}).get("Messages") or ()),
        radio=list((state.get("TeamRadio") or {}).get("Captures") or ()),
        fetched_at=fetched_at,
        archive=archive,
        total_laps=total_laps,
        current_lap=current_lap,
        mode=mode or "unknown",
    )
