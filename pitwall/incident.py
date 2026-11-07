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


def candidates(session: Session) -> list[Driver]:
    """Cars that are not running, strongest evidence first, front of the field first."""
    ranked: list[tuple[int, int, Driver]] = []
    for driver in session.drivers:
        if driver.in_pit or driver.retired:
            continue          # a car in the garage is not the incident
        score = 0
        if driver.off_track:
            score += 4        # Position says OffTrack: the clearest signal
        if driver.stopped:
            score += 3        # MultiViewer's decoded flag
        if driver.stationary:
            score += 2        # coordinates identical across two frames
        if score:
            ranked.append((score, driver.position or 99, driver))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [driver for _score, _pos, driver in ranked]


def pick(session: Session) -> Driver | None:
    found = candidates(session)
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
    rewound = back
    try:
        player.seek(relative=-abs(int(back)))
    except Exception as exc:          # noqa: BLE001 - live players refuse
        rewound = 0
        message = str(exc).lower()
        if "live" in message:
            # MultiViewer will not seek a player that is on the live stream, and
            # there is no mutation in its API to leave live mode. Say so plainly
            # rather than implying a rewind happened.
            note = " (live stream: MultiViewer refuses to rewind, onboard only)"
        else:
            note = f" (rewind unavailable: {type(exc).__name__})"
    else:
        note = ""

    how = "new player" if created else "existing player"
    head = f"{driver.label} stopped -> player {player_id} fullscreen ({how}; {synced})"
    detail = f", rewound {rewound}s{note}" if rewound else note
    return f"{head}{detail}", player_id


def restore(player_id: int) -> str:
    """Give the output back: un-fullscreen and return the player to live."""
    try:
        player = feed._client().player(player_id)
        player.set_fullscreen(False)
        playerlib.control(player_id, "live")
        return f"player {player_id} back to live, full screen released"
    except Exception as exc:          # noqa: BLE001
        return f"player {player_id} could not be returned to live ({type(exc).__name__})"


def react(session: Session, previous: dict) -> tuple[str | None, dict]:
    """One flag episode. Returns (report line or None, updated marker)."""
    marker = dict(previous or {})
    if not is_flag(session):
        return None, {}
    if marker.get("player") is not None:
        return None, marker      # already framed this episode

    driver = pick(session)
    if driver is None:
        return None, marker      # nothing stopped yet; try again next pass

    try:
        report, player_id = frame(driver)
    except Exception as exc:     # noqa: BLE001 - never let the watcher die
        return f"flag on track, could not frame {driver.label}: {type(exc).__name__}", marker

    marker.update(player=player_id, flag=session.track, driver=driver.label)
    return report, marker
