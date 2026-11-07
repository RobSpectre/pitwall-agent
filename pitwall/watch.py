"""Deterministic change detection: what happened since the last look.

No model, no network beyond the feed, no clock of its own. One pass:

    python -m pitwall.cli watch --once

reads the live timing state, compares it against the state it stored last
time, prints only the events that are new, and stores what it saw. Run it as
often as you like; running it twice reports nothing the second time.

The events are the ones a broadcast actually reacts to -- flags, a deleted
lap, a new pole, a purple sector, someone entering the drop zone, the clock
crossing two minutes.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import feed
from .model import Session
from .timefmt import fmt_clock, fmt_gap, fmt_lap, fmt_sector

CRITICAL = "CRIT"
HIGH = "HIGH"
MEDIUM = "MED"
_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2}

#: Clock marks that change what the broadcast can say.
CLOCK_STAGES = (300, 120, 30)

#: A driver this close to pole, or this high up the order, is worth a line.
CONTENDER_GAP = 0.5
CONTENDER_POSITIONS = 5

#: Cap the seen-signature list so the state file cannot grow forever.
MAX_SEEN = 500


@dataclass(slots=True)
class Event:
    level: str
    text: str
    signature: str

    def line(self, stamp: str) -> str:
        return f"{stamp}  {self.level:<4}  {self.text}"


def state_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "state" / "pitwall-watch.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _sig(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def _contenders(session: Session) -> set[str]:
    """Cars worth a line: the front of the order and anyone close to pole."""
    out: set[str] = set()
    for driver in session.drivers:
        if driver.position and driver.position <= CONTENDER_POSITIONS:
            out.add(driver.num)
        if driver.best_lap and session.pole and driver.best_lap - session.pole <= CONTENDER_GAP:
            out.add(driver.num)
    return out


def _drop_zone(session: Session) -> set[str]:
    """Cars at risk of elimination — the bubble, not the whole field."""
    cutoff = session.cutoff_pos
    if not cutoff:
        return set()
    out: set[str] = set()
    for driver in session.drivers:
        if driver.position and driver.position >= cutoff - 1:
            out.add(driver.num)
    return out


def _classify_message(message: dict) -> str | None:
    """Race-control messages that change a call, in priority order."""
    text = str(message.get("Message") or "")
    upper = text.upper()
    # A session that is stopped and may or may not come back: the single most
    # useful thing to know during rain. Check the negative form first --
    # "WILL NOT RESUME" contains "RESUME".
    if "WILL NOT RESUME" in upper or "NOT RESTART" in upper or "ABANDON" in upper:
        return CRITICAL
    if "WILL RESUME" in upper or "RESUME AT" in upper:
        return CRITICAL
    if "DELAYED" in upper or "SUSPEND" in upper:
        return HIGH
    # Recovery chatter. "CLEAR IN TRACK SECTOR 16", "TRACK CLEAR" and "GREEN
    # LIGHT - PIT EXIT OPEN" all carry Category=Flag, but the track-status rule
    # above already reports the recovery; repeating it here just fills the
    # critical channel.
    if "CLEAR IN TRACK SECTOR" in upper or upper.startswith("TRACK CLEAR"):
        return None
    if "PIT EXIT OPEN" in upper:
        return None
    if message.get("Category") == "Flag" or message.get("Flag"):
        return CRITICAL
    if "DELETED" in upper:
        return HIGH
    if "PENALTY" in upper or "INVESTIGATION" in upper or "UNDER INVESTIGATION" in upper:
        return HIGH
    if "TRACK LIMITS" in upper:
        return HIGH
    return None


def detect(session: Session, previous: dict) -> tuple[list[Event], dict]:
    """Compare a session against stored state. Returns (events, new_state)."""
    events: list[Event] = []
    seen: set[str] = set(previous.get("seen") or ())

    def add(level: str, text: str, *sig_parts: object) -> None:
        signature = _sig(*sig_parts)
        if signature in seen:
            return
        seen.add(signature)
        events.append(Event(level, text, signature))

    same_session = previous.get("session_key") == session.key
    baseline = not previous or not same_session

    # --- flags and session state (always worth saying) --------------------
    if same_session and previous.get("track") != session.track:
        if session.track != "ALL CLEAR":
            add(CRITICAL, f"{session.track} — {session.track_message or 'track status'}", "track", session.track)
        else:
            add(HIGH, "track clear", "track", "clear", session.key)
    if same_session and previous.get("status") != session.status and session.status:
        add(
            CRITICAL if session.status.upper() not in {"STARTED"} else HIGH,
            f"session {session.status.upper()}",
            "status",
            session.status,
        )

    # --- new race-control messages ---------------------------------------
    known_messages = set(previous.get("rc") or ())
    for message in session.messages:
        key = _sig(message.get("Utc"), message.get("Message"))
        if baseline or key in known_messages:
            continue
        level = _classify_message(message)
        if level:
            stamp = str(message.get("Utc") or "")[11:16]
            add(level, f"{(message.get('Message') or '').strip()} ({stamp})", "rc", key)

    # --- pole and purple sectors -----------------------------------------
    if session.pole and previous.get("pole") and session.pole < previous["pole"] - 0.0005:
        gain = previous["pole"] - session.pole
        add(
            HIGH,
            f"new pole {session.pole_holder} {fmt_lap(session.pole)} (-{gain:.3f})",
            "pole",
            round(session.pole, 3),
        )
    for i, best in enumerate(session.best_sector):
        was = (previous.get("sectors") or [None, None, None])[i]
        if best and was and best < was - 0.0005:
            holder = session.best_sector_holder[i] or "?"
            add(
                HIGH,
                f"purple S{i + 1} {holder} {fmt_sector(best)} (-{was - best:.3f})",
                "sector",
                i,
                round(best, 3),
            )

    # --- personal bests for the cars that matter -------------------------
    contenders = _contenders(session)
    previous_best = previous.get("best") or {}
    for driver in session.drivers:
        if driver.num not in contenders or not driver.best_lap:
            continue
        was = previous_best.get(driver.num)
        if was is None:
            if not baseline:
                gap = f" {fmt_gap(driver.best_lap - session.pole)}" if session.pole else ""
                add(MEDIUM, f"{driver.label} first lap {fmt_lap(driver.best_lap)}{gap}", "first", driver.num)
            continue
        if driver.best_lap < was - 0.0005:
            gap = f" {fmt_gap(driver.best_lap - session.pole)}" if session.pole else ""
            add(
                MEDIUM,
                f"{driver.label} {fmt_lap(driver.best_lap)} (-{was - driver.best_lap:.3f}){gap}",
                "best",
                driver.num,
                round(driver.best_lap, 3),
            )

    # --- drop zone and elimination ---------------------------------------
    zone_now = _drop_zone(session)
    if same_session and session.cutoff_pos:
        zone_before = set(previous.get("zone") or ())
        for num in sorted(zone_now - zone_before):
            driver = session.by_num.get(num)
            if driver:
                add(HIGH, f"{driver.label} into the drop zone (P{driver.position})", "zone", num, "in", session.key)
        for num in sorted(zone_before - zone_now):
            driver = session.by_num.get(num)
            if driver:
                add(MEDIUM, f"{driver.label} out of the drop zone (P{driver.position})", "zone", num, "out", session.key)

    knocked_before = set(previous.get("knocked") or ())
    for driver in session.drivers:
        if driver.knocked_out and driver.num not in knocked_before and not baseline:
            add(CRITICAL, f"{driver.label} eliminated", "knocked", driver.num, session.key)

    # --- clock stages -----------------------------------------------------
    # The most recent mark we have passed: 4:30 left has passed the 5:00 mark only.
    passed = [mark for mark in CLOCK_STAGES if session.remaining is not None and session.remaining <= mark]
    stage = min(passed) if passed else 0
    if same_session and stage and stage != previous.get("stage"):
        add(CRITICAL, f"{fmt_clock(session.remaining)} left", "clock", stage)

    new_state = {
        "session_key": session.key,
        "track": session.track,
        "status": session.status,
        "pole": round(session.pole, 3) if session.pole else None,
        "sectors": [round(s, 3) if s else None for s in session.best_sector],
        "best": {d.num: round(d.best_lap, 3) for d in session.drivers if d.best_lap},
        "zone": sorted(zone_now),
        "knocked": [d.num for d in session.drivers if d.knocked_out],
        "rc": sorted(
            {_sig(m.get("Utc"), m.get("Message")) for m in session.messages if _classify_message(m)}
        )[-60:],
        "stage": stage,
        "seen": sorted(seen)[-MAX_SEEN:],
        "updated": time.time(),
    }
    return events, new_state


def scan(*, path: Path | None = None, react: bool = False) -> list[Event]:
    """One detection pass against the live feed. Empty when nothing is new.

    ``react`` opts into the side effect: on a flag, frame the stopped car and
    rewind it; on green, give the screen back. Off by default, because a
    read-only pass must never move an operator's output.
    """
    path = path or state_path()
    session, _ = feed.snapshot(fresh=True)
    previous = _load(path)
    events, state = detect(session, previous)

    marker = previous.get("incident") or {}
    if react:
        from . import incident

        if incident.is_flag(session):
            line, marker = incident.react(session, marker)
            if line:
                events.append(Event(HIGH, line, _sig("incident", session.key, session.track, line)))
        elif marker.get("player"):
            line = incident.restore(int(marker["player"]))
            events.append(Event(MEDIUM, line, _sig("restore", marker["player"], session.key)))
            marker = {}
    state["incident"] = marker

    _save(path, state)
    return events


def render(events: list[Event], stamp: str | None = None) -> str:
    stamp = stamp or time.strftime("%H:%M:%S")
    return "\n".join(event.line(stamp) for event in sorted(events, key=lambda e: _ORDER[e.level]))
