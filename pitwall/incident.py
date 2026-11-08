"""Incident reaction: find the car that stopped, put its onboard up, rewind.

Deterministic, like the watcher: no model decides any of this. On a yellow or
red flag the routine ranks the cars that are clearly not running -- off track
first, then MultiViewer's own Stopped flag, then composite coordinates that
have not changed between two frames -- and frames the best candidate.

The player is unlocked to full screen and seeked back so the cause of the
incident is on the broadcast instead of the aftermath. When the track goes
green again the same player is returned to live and un-fullscreened, because
leaving the output on a replay is the one thing worse than missing the crash.
"""

from __future__ import annotations

import logging
import time

from . import feed, players as playerlib
from .model import Driver, Session

_LOG = logging.getLogger("mvf1.mvf1")

#: Track states that mean "something happened, find the car".
FLAGS = {"YELLOW", "SAFETY CAR", "SC DEPLOYED", "VSC", "VSC ENDING", "RED FLAG"}

#: How far back the replay starts. A crash is usually 3-8 s before the flag
#: appears; 30 s covers the run-up without burying the moment.
DEFAULT_BACK = 30

#: Drift tolerated before we re-align with the broadcast feed.
SYNC_TOLERANCE = 1.0


def is_flag(session: Session) -> bool:
    return session.track in FLAGS


def _broadcast_player_id() -> tuple[int | None, str]:
    """The world feed: the reference every other window should match."""
    for player in playerlib.list_players():
        if player.title.strip().upper() == "INTERNATIONAL":
            return player.id, player.title
    return None, ""


def ensure_synced(player_id: int) -> str:
    """Align the output with the broadcast before framing the incident.

    A full-screen onboard that is two seconds behind the world feed is worse
    than no onboard: the commentary is describing a different moment. Sync
    first, rewind second.
    """
    remote = feed._client()
    broadcast_id, title = _broadcast_player_id()

    if broadcast_id is None or broadcast_id == player_id:
        # No world feed open: fall back to the live-timing clock.
        sync = getattr(remote, "player_sync_to_live_timing", None)
        if not callable(sync):
            return "no broadcast player to sync against"
        try:
            sync()
            return "synced to live timing"
        except Exception as exc:      # noqa: BLE001
            return f"sync to live timing failed ({type(exc).__name__})"

    try:
        mine = remote.player(player_id).state.get("interpolatedCurrentTime")
        theirs = remote.player(broadcast_id).state.get("interpolatedCurrentTime")
    except Exception:                 # noqa: BLE001 - state may not be ready
        mine = theirs = None

    if mine and theirs and abs(float(mine) - float(theirs)) <= SYNC_TOLERANCE:
        return f"already in sync with {title} ({float(mine) - float(theirs):+.2f}s)"

    try:
        remote.player(broadcast_id).sync()   # every player jumps to the world feed
        return f"synced to {title}"
    except Exception as exc:          # noqa: BLE001
        return f"sync to {title} failed ({type(exc).__name__})"


def candidates(session: Session, already_stopped=()) -> list[Driver]:
    """Cars that are not running, strongest evidence first, front of the field first.

    Cars in the pit lane or garage are never candidates: the flag is about the
    circuit.  A car that was already parked before this pass is demoted rather
    than dropped -- it is evidence the feed is lagging on the car that actually
    stopped, not the incident itself.
    """
    parked = {str(num) for num in already_stopped}
    ranked: list[tuple[int, int, Driver]] = []
    for driver in session.drivers:
        if driver.pitted or driver.retired:
            continue          # a car in the garage or pit lane is not the incident
        score = 0
        if driver.off_track:
            score += 4        # Position says OffTrack: the clearest signal
        if driver.stopped:
            score += 3        # MultiViewer's decoded flag
        if driver.stationary:
            score += 2        # coordinates identical across two frames
        if score:
            if driver.num in parked:
                score -= 5    # stopped before the flag: someone else caused it
            ranked.append((score, driver.position or 99, driver))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [driver for _score, _pos, driver in ranked]


def pick(session: Session, already_stopped=()) -> Driver | None:
    found = candidates(session, already_stopped)
    return found[0] if found else None


def _await_player(player_id: int, tries: int = 12, delay: float = 0.25):
    """MultiViewer exposes a new player a beat after it is created."""
    last: Exception | None = None
    level = _LOG.level
    _LOG.setLevel(logging.CRITICAL)       # every retry raises, and mvf1 logs each one
    try:
        for _ in range(tries):
            try:
                return feed._client().player(player_id)
            except Exception as exc:      # noqa: BLE001 - any failure means "not yet"
                last = exc
                time.sleep(delay)
    finally:
        _LOG.setLevel(level)
    raise RuntimeError(f"player {player_id} did not appear ({last})")


def _existing_player(driver: Driver, content_id: int | None) -> int | None:
    for player in playerlib.list_players():
        if player.title.upper() != (driver.tla or "").upper():
            continue
        if content_id and player.content_id and player.content_id != content_id:
            continue
        return player.id
    return None


def _pause(player_id: int, tries: int = 3, delay: float = 0.4) -> bool:
    """Pause and confirm it took.

    A sync in flight can resume the player a beat after the mutation returns, so
    read the state back rather than trusting the call.
    """
    for _ in range(tries):
        try:
            feed._client().player(player_id).pause(True)
        except Exception:             # noqa: BLE001
            pass
        time.sleep(delay)
        try:
            if feed._client().player(player_id).state.get("paused"):
                return True
        except Exception:             # noqa: BLE001
            pass
    return False


def _raise_window(player) -> None:
    """Full screen is not enough if another window is sitting on top of it."""
    try:
        player.set_always_on_top(True, level="FLOATING")   # mvf1 >= 2.2
    except TypeError:                                      # older signature
        try:
            player.set_always_on_top(True)
        except Exception:      # noqa: BLE001
            pass
    except Exception:          # noqa: BLE001
        pass


def frame(driver: Driver, *, back: int = DEFAULT_BACK) -> tuple[str, int]:
    """Full-screen this driver's onboard and rewind it. Returns (report, player id)."""
    resolution = playerlib.current_resolution()
    player_id = _existing_player(driver, resolution.content_id)
    created = False
    if player_id is None:
        player_id, _title = playerlib.create(driver=driver.tla or driver.num)
        created = True

    player = _await_player(player_id)
    synced = ensure_synced(player_id)
    player.set_fullscreen(True)
    _raise_window(player)
    rewound = back
    try:
        player.seek(relative=-abs(float(back)))
    except Exception as exc:          # noqa: BLE001 - live content refuses seeks
        rewound = 0
        message = str(exc).lower()
        if "live" in message:
            # MultiViewer blocks playerSeekTo for live content -- it refuses even
            # when the player is paused and reports live=false, so the guard is on
            # the stream, not the player. The UI scrubs through its own timeshift
            # buffer, which is not in the GraphQL surface. Pausing does work, so
            # leave the frame stopped at the incident for a manual scrub.
            if _pause(player_id):
                note = " (live stream: rewind blocked by MultiViewer; player paused to scrub)"
            else:
                note = " (live stream: rewind blocked by MultiViewer; onboard only)"
        else:
            note = f" (rewind unavailable: {type(exc).__name__})"
    else:
        note = ""

    how = "new player" if created else "existing player"
    head = f"{driver.label} stopped -> player {player_id} fullscreen ({how}; {synced})"
    detail = f", rewound {rewound}s{note}" if rewound else note
    return f"{head}{detail}", player_id


def restore(player_id: int) -> str:
    """Give the output back: un-fullscreen, resume, and return the player to live."""
    parts: list[str] = []
    try:
        player = feed._client().player(player_id)
    except Exception as exc:          # noqa: BLE001
        return f"player {player_id} is gone ({type(exc).__name__})"

    try:
        player.set_fullscreen(False)
        parts.append("full screen released")
    except Exception:                 # noqa: BLE001
        pass
    try:
        player.set_always_on_top(False)
        parts.append("window released")
    except Exception:                 # noqa: BLE001
        pass
    try:
        player.pause(False)           # we may have paused it for a manual scrub
        parts.append("playing")
    except Exception:                 # noqa: BLE001
        pass
    try:
        playerlib.control(player_id, "live")
        parts.append("back to live")
    except Exception:                 # noqa: BLE001
        parts.append("not returned to live")

    return f"player {player_id}: " + ", ".join(parts or ["nothing to do"])


def react(session: Session, previous: dict, already_stopped=()) -> tuple[str | None, dict]:
    """One flag episode. Returns (report line or None, updated marker)."""
    marker = dict(previous or {})
    if not is_flag(session):
        return None, {}
    if marker.get("player") is not None:
        return None, marker      # already framed this episode

    driver = pick(session, already_stopped)
    if driver is None:
        return None, marker      # nothing stopped yet; try again next pass

    try:
        report, player_id = frame(driver)
    except Exception as exc:     # noqa: BLE001 - never let the watcher die
        return f"flag on track, could not frame {driver.label}: {type(exc).__name__}", marker

    marker.update(player=player_id, flag=session.track, driver=driver.label)
    return report, marker
