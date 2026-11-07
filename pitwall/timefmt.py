"""Duration parsing and formatting for F1 live-timing strings.

Every value the feed produces is a string and every one of them is either
``M:SS.mmm`` (lap/best times), ``SS.mmm`` (sector times), ``H:MM:SS`` (session
clock) or empty.  All arithmetic in this plugin happens in float seconds; these
helpers are the only place a time string is turned into a number.
"""

from __future__ import annotations

import re

#: ``1:14.933`` / ``74.933`` / ``14.9`` -- optional minutes, optional fraction.
_LAP_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}(?:\.\d+)?)$")
#: ``00:59:59`` -- the extrapolated session clock.
_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$")

_EMPTY = {"", "-", "--", "—"}


def parse_time(value: object) -> float | None:
    """Parse a lap or sector time into seconds. ``None`` for anything unusable."""
    if value is None:
        return None
    text = str(value).strip()
    if text in _EMPTY:
        return None
    if text.startswith("+"):
        text = text[1:]
    match = _LAP_RE.match(text)
    if match is None:
        return None
    minutes, seconds = match.groups()
    try:
        total = float(seconds)
    except ValueError:
        return None
    if minutes is not None:
        total += int(minutes) * 60
    return total


def parse_clock(value: object) -> float | None:
    """Parse the ``H:MM:SS`` session clock into seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if text in _EMPTY:
        return None
    match = _CLOCK_RE.match(text)
    if match is None:
        return parse_time(text)
    hours, minutes, seconds = match.groups()
    try:
        return (int(hours or 0) * 3600) + (int(minutes) * 60) + float(seconds)
    except ValueError:
        return None


def fmt_lap(seconds: float | None) -> str:
    """``74.933`` -> ``1:14.933``. Lap times always carry minutes."""
    if seconds is None or seconds <= 0:
        return "-"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:06.3f}"


def fmt_sector(seconds: float | None) -> str:
    """``19.412`` -> ``19.412``. Sector times are always under a minute."""
    if seconds is None or seconds <= 0:
        return "-"
    return f"{seconds:.3f}"


def fmt_gap(seconds: float | None) -> str:
    """Signed gap, always three decimals: a tenth is the unit of the sport."""
    if seconds is None:
        return "-"
    if abs(seconds) < 0.0005:
        return "+0.000"
    return f"{seconds:+.3f}"


def fmt_clock(seconds: float | None) -> str:
    """Seconds -> ``4:12`` for the remaining-session readout."""
    if seconds is None:
        return "-"
    if seconds < 0:
        seconds = 0
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{int(rest):02d}"


def fmt_age(seconds: float | None) -> str:
    """How stale the *data* is -- not how recently we asked for it."""
    if seconds is None:
        return "?"
    if seconds < 2:
        return "live"
    if seconds < 90:
        return f"unchanged {seconds:.0f}s"
    return f"STALLED {seconds / 60:.0f}m"
