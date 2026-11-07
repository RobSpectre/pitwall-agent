"""The one place that talks to MultiViewer.

Two jobs: hold a short-lived cache so several tool calls in one agent turn cost
one HTTP round-trip, and remember the previous snapshot so tools can report what
changed instead of re-reading everything.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

from .meeting import mode_of
from .model import Session, build

DEFAULT_URI = "http://localhost:10101/api/graphql"
#: MultiViewer recomputes the document on demand; below ~1 s everything is the
#: same data, so a burst of tool calls shares one fetch.
CACHE_TTL = 1.0


class PitwallError(RuntimeError):
    """A problem the agent can act on (MultiViewer down, no session, ...)."""


_LOCK = threading.Lock()
_CLIENT = None
_CACHE: dict = {"raw": None, "at": 0.0}
_PREVIOUS: Session | None = None
_PREVIOUS_AT = 0.0


def _client():
    global _CLIENT
    if _CLIENT is None:
        try:
            from mvf1 import MultiViewerForF1
        except ImportError as exc:  # pragma: no cover - depends on the host env
            # Report the real cause: an ImportError raised *inside* mvf1 (a
            # missing transitive dependency) reads nothing like "not installed".
            raise PitwallError(
                f"cannot import mvf1 in this process ({exc}). Install the plugin with "
                "`hermes plugins install <owner>/hermes-pitwall --yes-deps`, then restart "
                "the gateway (`hermes gateway restart`) so the new dependency environment "
                "is used — a running process never switches environments."
            ) from exc
        uri = os.environ.get("MVF1_URI") or DEFAULT_URI
        _CLIENT = MultiViewerForF1(uri=uri)
    return _CLIENT


def raw_state(*, fresh: bool = False) -> dict:
    """Return the ``F1LiveTimingState`` document, cached for ``CACHE_TTL``."""
    now = time.time()
    if not fresh:
        with _LOCK:
            cached = _CACHE["raw"]
            if cached is not None and now - _CACHE["at"] < CACHE_TTL:
                return cached
    try:
        response = _client().live_timing_state
    except PitwallError:
        raise
    except Exception as exc:
        raise PitwallError(
            f"MultiViewer is not reachable at {os.environ.get('MVF1_URI') or DEFAULT_URI} "
            f"({type(exc).__name__}: {exc}). Start MultiViewer and open a session."
        ) from exc

    data = (response or {}).get("data") or {}
    state = data.get("f1LiveTimingState")
    if not state:
        raise PitwallError("MultiViewer returned no F1 live timing state. Open a session in MultiViewer.")
    if not state.get("TimingData"):
        raise PitwallError(
            "Live timing is idle: no TimingData yet. Wait for the session to start "
            "(or start the replay in MultiViewer)."
        )
    # Freshness is a property of the *document*, not of our fetch. A paused
    # replay or a stalled generator returns the same bytes forever, and a tool
    # that calls that "live" is worse than useless during a red flag.
    digest = hashlib.blake2b(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode(), digest_size=16
    ).digest()
    with _LOCK:
        if _CACHE.get("digest") != digest:
            _CACHE["digest"] = digest
            _CACHE["changed_at"] = time.time()
        _CACHE["raw"] = state
        _CACHE["at"] = time.time()
    return state


def data_age() -> float:
    """Seconds since the live-timing document last changed."""
    changed = _CACHE.get("changed_at")
    return max(0.0, time.time() - changed) if changed else 0.0


def snapshot(*, fresh: bool = False) -> tuple[Session, Session | None]:
    """Parsed session plus the previous parse of the same session, if any.

    The pair is what makes cheap polling possible: the second element is only
    populated when the earlier call saw the same session, so a change report
    never compares two different sessions.
    """
    global _PREVIOUS, _PREVIOUS_AT
    state = raw_state(fresh=fresh)
    now = time.time()
    session = build(state, fetched_at=now, mode=mode_of(state), data_age=data_age())
    with _LOCK:
        previous = _PREVIOUS
        _PREVIOUS = session
        _PREVIOUS_AT = now
    if previous is None or previous.key != session.key:
        previous = None
    return session, previous


#: Tool gating is decided once per session, not per call: a verdict that flips
#: between turns would rewrite the tool list mid-conversation. A new session
#: key (Q3 -> race) is a new context and may legitimately change the set.
_MODE: dict = {"key": None, "mode": "unknown"}


def session_mode() -> str:
    """``practice`` / ``qualifying`` / ``race`` for the session open now."""
    try:
        state = raw_state()
    except PitwallError:
        return "unknown"
    key = (state.get("SessionInfo") or {}).get("Key")
    with _LOCK:
        if _MODE["key"] != key:
            _MODE["key"] = key
            _MODE["mode"] = mode_of(state)
        return _MODE["mode"]


def known_driver(needle: object, session: Session | None = None):
    """Resolve a driver code without forcing a parse the caller did not need."""
    from .model import find_driver

    if session is None:
        session, _ = snapshot()
    return find_driver(session, needle)


def previous() -> Session | None:
    return _PREVIOUS


def reset() -> None:
    """Drop caches (tests, or after MultiViewer restarts)."""
    global _PREVIOUS, _PREVIOUS_AT
    with _LOCK:
        _CACHE["raw"] = None
        _CACHE["at"] = 0.0
        _PREVIOUS = None
        _PREVIOUS_AT = 0.0
        _MODE["key"] = None
        _MODE["mode"] = "unknown"
