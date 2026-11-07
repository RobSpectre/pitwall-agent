"""Session identity: which meeting, which session, which F1 TV content id.

MultiViewer needs a numeric ``contentId`` before it will create a video player,
and the only way to get one is two-legged:

1. **Live timing** carries ``SessionInfo.Meeting.Key`` and ``SessionInfo.Key``
   (the session key) plus a human name -- "Practice 3", "Qualifying".
2. **The MultiViewer meetings API** (``api.multiviewer.app``) returns every
   session of that meeting with its ``f1tv_content_id``.

The two are joined on ``session_key``, which is an exact match, so no guessing
at titles.  A title match is only a fallback, and it is reported as such: the
notebook's rule -- never issue a player command with an unverified id -- holds.

Everything here is cached per meeting for the life of the process, because the
meeting payload is ~8 KB and changes at most when a session starts.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.multiviewer.app/api/v1/meetings/{key}"
MEETING_TTL = 900.0

#: Live-timing ``SessionInfo.Type`` and MultiViewer ``session_type`` both land
#: in one of these.  The mode decides which tools the agent is offered.
PRACTICE_TYPES = {"practice", "fp1", "fp2", "fp3", "weekend_warmup", "warmup"}
QUALIFYING_TYPES = {"qualifying", "sprint_qualifying", "sprint_shootout", "shootout"}
RACE_TYPES = {"race", "sprint"}


class MeetingError(RuntimeError):
    """The meetings API could not answer."""


@dataclass(slots=True)
class MeetingSession:
    key: str
    title: str
    short_title: str
    session_type: str
    content_id: int | None
    start_at: str
    live_timing_available: bool
    archive_status: str


@dataclass(slots=True)
class Resolution:
    meeting_key: str
    meeting_name: str
    session_key: str
    title: str
    session_type: str
    content_id: int | None
    matched_on: str          # "session_key" | "title" | ""
    candidates: list[MeetingSession] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.content_id is not None


_CACHE: dict[str, tuple[float, list[MeetingSession]]] = {}


def mode_for(session_type: str) -> str:
    """Map any session-type spelling onto practice / qualifying / race."""
    key = (session_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in QUALIFYING_TYPES:
        return "qualifying"
    if key in RACE_TYPES:
        return "race"
    if key in PRACTICE_TYPES:
        return "practice"
    if "qualif" in key or "shootout" in key:
        return "qualifying"
    if "race" in key or "sprint" in key:
        return "race"
    if "practice" in key:
        return "practice"
    return "unknown"


def _get(url: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "pitwall-agent", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise MeetingError(f"MultiViewer meetings API unreachable ({type(exc).__name__}: {exc})") from exc


def meeting_sessions(meeting_key: object, *, refresh: bool = False) -> list[MeetingSession]:
    """Every session of a meeting, newest last. Cached per meeting key."""
    key = str(meeting_key or "").strip()
    if not key:
        raise MeetingError("no meeting key in live timing")
    cached = _CACHE.get(key)
    if cached and not refresh and time.time() - cached[0] < MEETING_TTL:
        return cached[1]

    payload = _get(API.format(key=key))
    sessions: list[MeetingSession] = []
    for item in payload.get("sessions") or ():
        raw = item.get("f1tv_content_id")
        try:
            content_id = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            content_id = None
        sessions.append(
            MeetingSession(
                key=str(item.get("session_key") or ""),
                title=str(item.get("title") or ""),
                short_title=str(item.get("short_title") or ""),
                session_type=str(item.get("session_type") or ""),
                content_id=content_id,
                start_at=str(item.get("start_at") or ""),
                live_timing_available=bool(item.get("live_timing_available")),
                archive_status=str(item.get("live_timing_archive_status") or ""),
            )
        )
    _CACHE[key] = (time.time(), sessions)
    return sessions


def resolve(state: dict, *, refresh: bool = False) -> Resolution:
    """Join live timing onto the meetings API and pick this session's content id.

    ``state`` is the ``F1LiveTimingState`` document (not the parsed session) so
    this can run before a Session is built.
    """
    info = (state or {}).get("SessionInfo") or {}
    meeting = info.get("Meeting") or {}
    meeting_key = str(meeting.get("Key") or "").strip()
    session_key = str(info.get("Key") or "").strip()
    title = str(info.get("Name") or "").strip()
    session_type = str(info.get("Type") or "").strip()

    resolution = Resolution(
        meeting_key=meeting_key,
        meeting_name=str(meeting.get("Name") or ""),
        session_key=session_key,
        title=title,
        session_type=session_type,
        content_id=None,
        matched_on="",
    )
    if not meeting_key:
        return resolution

    resolution.candidates = meeting_sessions(meeting_key, refresh=refresh)

    by_key = next((s for s in resolution.candidates if s.key and s.key == session_key), None)
    if by_key is not None and by_key.content_id:
        resolution.content_id = by_key.content_id
        resolution.matched_on = "session_key"
        return resolution

    # Fallback: the live-timing name ("Practice 3") against short_title/title.
    wanted = title.lower()
    for candidate in resolution.candidates:
        names = {candidate.short_title.lower(), candidate.title.lower()}
        if wanted and (wanted in names or any(wanted and wanted in n for n in names)):
            if candidate.content_id:
                resolution.content_id = candidate.content_id
                resolution.matched_on = "title"
                return resolution
    return resolution


def mode_of(state: dict) -> str:
    info = (state or {}).get("SessionInfo") or {}
    return mode_for(str(info.get("Type") or ""))
