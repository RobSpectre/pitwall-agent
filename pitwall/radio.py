"""Driver radio transcriptions, as text.

MultiViewer's own backend publishes an AI transcription of the team-radio clips
it carries, so the pit wall never has to run speech-to-text at all: the words
are already there, keyed by meeting and session.

    GET /api/v2/f1/meetings/<meeting>/sessions/<session>/driver_radio_transcriptions

Two consequences shape this module.

The payload is a **whole-session snapshot**. On a finished race every radio from
lights out is in the list immediately, so on a replay a naive read spoils the
race. Everything here is cut at the playhead -- the last moment the timing feed
has actually reached -- and the cut is reported rather than hidden.

The text is **published, not generated here**: it is another pack's model, with
its own lag and its own mistakes. This module never edits a line; it cleans
whitespace and quotes what it was given.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .model import Session, find_driver
from .timefmt import parse_utc

API = (
    "https://api.multiviewer.app/api/v2/f1/meetings/{meeting}"
    "/sessions/{session}/driver_radio_transcriptions"
)
#: A whole-session list costs ~600 KB once the race is done and the payload is
#: CDN-cached for ten minutes, so a fetched list is held for half a minute and
#: every revalidation after that is a conditional GET.
CACHE_TTL = 30.0


class RadioError(RuntimeError):
    """The transcription endpoint could not answer."""


@dataclass(slots=True)
class Line:
    """One transcribed radio message."""

    at: str          # HH:MM:SS, the feed's own stamp
    ts: float        # the same instant as epoch seconds, for ordering and cuts
    label: str       # driver TLA where the session knows it
    num: str
    text: str
    duration: int    # milliseconds of audio


_CACHE: dict[tuple[str, str], tuple[float, list[dict], str]] = {}


def _get(url: str, timeout: float, headers: dict[str, str] | None = None) -> tuple[str, str]:
    """Fetch ``url``; returns ``(body, Last-Modified)``."""
    merged = {"User-Agent": "pitwall-agent", "Accept": "application/json"}
    merged.update(headers or {})
    request = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        return body, str(response.headers.get("Last-Modified") or "")


def fetch(
    meeting_key: object, session_key: object, *, refresh: bool = False, timeout: float = 8.0
) -> list[dict]:
    """Every transcription published for the session, oldest first."""
    key = (str(meeting_key), str(session_key))
    cached = _CACHE.get(key)
    if cached and not refresh and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    conditional = {"If-Modified-Since": cached[2]} if cached and cached[2] else {}
    url = API.format(meeting=key[0], session=key[1])
    try:
        raw, stamp = _get(url, timeout, conditional)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached:
            _CACHE[key] = (time.time(), cached[1], cached[2])
            return cached[1]
        raise RadioError(f"radio transcriptions unavailable (HTTP {exc.code})") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RadioError(
            f"radio transcriptions unavailable ({type(exc).__name__}: {exc})"
        ) from exc

    if not raw.strip():
        entries: object = []
    else:
        try:
            # The published JSON is not always strict: a clip can carry a
            # literal newline, which json.loads rejects without strict=False.
            entries = json.loads(raw, strict=False)
        except json.JSONDecodeError as exc:
            raise RadioError(f"radio transcriptions did not parse ({exc})") from exc
    if not isinstance(entries, list):
        entries = []
    _CACHE[key] = (time.time(), entries, stamp)
    return entries


def playhead(session: Session) -> float | None:
    """The last moment the timing feed has reached, in session time.

    The model already works this out from the freshest timestamped section. The
    fallback here is for hand-built sessions that carry only messages or radio
    captures. The heartbeat is useless for this: it is delivery time, not
    session time.
    """
    if session.playhead_utc is not None:
        return session.playhead_utc
    stamps = [
        parse_utc(item.get("Utc"))
        for item in list(session.messages) + list(session.radio)
        if isinstance(item, dict)
    ]
    known = [stamp for stamp in stamps if stamp is not None]
    return max(known) if known else None


def _clock(stamp: object) -> tuple[str, float | None]:
    """``2026-11-01T20:11:42.9Z`` -> (``20:11:42``, epoch seconds)."""
    text = str(stamp or "")
    when = parse_utc(text)
    if when is None:
        return "", None
    return time.strftime("%H:%M:%S", time.gmtime(when)), when


def resolve(session: Session, spec: object) -> list:
    """Turn a driver filter into drivers.

    Accepts one code (``NOR``), one number (``1``), a comma- or space-separated
    string (``NOR, HAM``), or a list of any of those. Unknown names raise rather
    than quietly narrowing the filter -- asking for three cars and being shown
    two is how a broadcast misses the one that matters.
    """
    if spec is None or spec == "":
        return []
    if isinstance(spec, (list, tuple, set)):
        # A repeated flag gives a list whose entries may each be comma lists.
        tokens = [piece for item in spec for piece in re.split(r"[,\s]+", str(item))]
    else:
        tokens = re.split(r"[,\s]+", str(spec))
    found = []
    missing = []
    for token in tokens:
        if not token.strip():
            continue
        driver = find_driver(session, token)
        if driver is None:
            missing.append(token.strip())
        elif all(other.num != driver.num for other in found):
            found.append(driver)
    if missing:
        known = " ".join(item.label for item in session.drivers)
        raise RadioError(f"no driver matching {', '.join(missing)}. Known: {known}")
    return found


def recent(
    session: Session, *, driver: object = None, limit: int = 6, refresh: bool = False
) -> tuple[list[Line], int, float | None]:
    """The last ``limit`` lines at or before the playhead.

    Returns ``(lines, published, cut)`` where ``published`` is how many lines
    survived the cut (before the limit) and ``cut`` is the playhead used, or
    ``None`` when the feed had no clock to cut against.
    """
    if not session.meeting_key or not session.key:
        raise RadioError("this session carries no meeting/session key, so radio cannot be looked up")

    wanted = {item.num for item in resolve(session, driver)}
    cut = playhead(session)
    rows: list[Line] = []
    for item in fetch(session.meeting_key, session.key, refresh=refresh):
        if not isinstance(item, dict):
            continue
        at, when = _clock(item.get("player_ts"))
        if when is None:
            continue
        # Everything past the playhead is the future on a replay.
        if cut is not None and when > cut:
            continue
        num = str(item.get("driver_number") or "").strip()
        if wanted and num not in wanted:
            continue
        known = session.by_num.get(num)
        rows.append(
            Line(
                at=at,
                ts=when,
                label=(known.label if known else num) or "?",
                num=num,
                text=" ".join(str(item.get("transcription") or "").split()),
                duration=int(item.get("duration_ms") or 0),
            )
        )
    rows.sort(key=lambda line: line.ts)
    return rows[-max(1, limit):], len(rows), cut


@dataclass(slots=True)
class DriverRadio:
    """One driver's radio inside a lap window."""

    label: str
    num: str
    position: int | None
    calls: int
    airtime: int                 # milliseconds of audio
    lines: list[Line]


def lap_window(
    session: Session, laps: int = 4
) -> tuple[float | None, int | None, int | None, float | None]:
    """``(start, first_lap, last_lap, cut)`` for the last ``laps`` laps.

    A race at lap 7 with ``laps=4`` is laps 4-7: the lap being run counts, so
    four laps back is ``current - laps + 1``. The start is the feed's own stamp
    for that lap; when the feed publishes no lap stamps the window opens at the
    beginning of what is known, and ``first``/``last`` come back ``None`` so the
    caller can say so rather than invent a boundary.
    """
    cut = playhead(session)
    stamps = list(session.lap_stamps)
    current = session.current_lap or (stamps[-1][1] if stamps else None)
    if current is None:
        return None, None, None, cut
    want = max(1, laps)
    first = max(1, current - want + 1)
    if not stamps:
        return None, None, current, cut
    start = next((when for when, lap in stamps if lap >= first), stamps[0][0])
    return start, first, current, cut


def summary(
    session: Session,
    *,
    driver: object = None,
    laps: int = 4,
    refresh: bool = False,
) -> tuple[list[DriverRadio], tuple[float | None, int | None, int | None, float | None]]:
    """Every driver who spoke in the last ``laps`` laps, busiest first.

    This is selection and counting, not paraphrasing: the plugin has no model,
    so it hands back what was said and how much of it there was. Turning that
    into sentences is the caller's job, and a quote stays a quote.
    """
    if not session.meeting_key or not session.key:
        raise RadioError("this session carries no meeting/session key, so radio cannot be looked up")

    wanted = {item.num for item in resolve(session, driver)}
    start, first, last, cut = lap_window(session, laps)
    groups: dict[str, DriverRadio] = {}
    for item in fetch(session.meeting_key, session.key, refresh=refresh):
        if not isinstance(item, dict):
            continue
        at, when = _clock(item.get("player_ts"))
        if when is None:
            continue
        if cut is not None and when > cut:
            continue                      # not yet happened on a replay
        if start is not None and when < start:
            continue                      # older than the window
        num = str(item.get("driver_number") or "").strip()
        if wanted and num not in wanted:
            continue
        known = session.by_num.get(num)
        group = groups.get(num)
        if group is None:
            group = groups[num] = DriverRadio(
                label=(known.label if known else num) or "?",
                num=num,
                position=known.position if known else None,
                calls=0,
                airtime=0,
                lines=[],
            )
        group.lines.append(
            Line(
                at=at,
                ts=when,
                label=group.label,
                num=num,
                text=" ".join(str(item.get("transcription") or "").split()),
                duration=int(item.get("duration_ms") or 0),
            )
        )
        group.calls += 1
        group.airtime += int(item.get("duration_ms") or 0)

    rows = list(groups.values())
    for group in rows:
        group.lines.sort(key=lambda line: line.ts)
    # The busiest car is the story; position breaks a tie.
    rows.sort(key=lambda group: (-group.calls, group.position or 99, group.label))
    return rows, (start, first, last, cut)
