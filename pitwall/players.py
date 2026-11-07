"""Video players: create, place, sync and close MultiViewer windows.

MultiViewer player creation needs a content id (the session) plus optionally a
stream title -- a driver TLA, ``DATA`` for the live-timing channel, or
``INTERNATIONAL`` for the world feed.  The content id comes from
:mod:`pitwall.meeting`; this module never invents one.

Tiling is computed here rather than guessed by the model: a grid inside a
region of a stated screen size, each cell fitted to 16:9.  The notebook's
"tile the onboards on the right of a 3840x2160 screen" is one call.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from . import feed
from .meeting import MeetingError, Resolution, resolve

#: Streams that are not driver feeds.  Matched case-insensitively.
NAMED_STREAMS = {
    "international": "INTERNATIONAL",
    "world": "INTERNATIONAL",
    "data": "DATA",
    "timing": "DATA",
    "pit lane": "PIT LANE",
    "pitlane": "PIT LANE",
    "tracker": "TRACKER",
}


@dataclass(slots=True)
class PlayerInfo:
    id: int
    title: str
    content_id: int | None
    channel_id: int | None
    x: int
    y: int
    width: int
    height: int
    paused: bool
    live: bool


def _remote():
    return feed._client()


def current_resolution(*, refresh: bool = False) -> Resolution:
    """The session's content id, resolved from the feed and cached by meeting."""
    state = feed.raw_state(fresh=refresh)
    return resolve(state, refresh=refresh)


def _as_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def list_players() -> list[PlayerInfo]:
    out: list[PlayerInfo] = []
    for player in _remote().players:
        state = player.state if isinstance(player.state, dict) else {}
        out.append(
            PlayerInfo(
                id=int(player.id),
                title=str(player.title or ""),
                # MultiViewer returns the content id as a string; normalise it or
                # every player looks like it belongs to another session.
                content_id=_as_int(player.content_id),
                channel_id=_as_int(player.channel_id),
                x=int(player.x or 0),
                y=int(player.y or 0),
                width=int(player.width or 0),
                height=int(player.height or 0),
                paused=bool(state.get("paused")),
                live=bool(state.get("live")),
            )
        )
    return out


def _stream_title(*, driver: str | None, stream: str | None, session) -> str:
    if driver:
        known = feed.known_driver(driver, session)
        if known is None:
            names = ", ".join(d.tla for d in session.drivers)
            raise ValueError(f"no driver matching {driver!r}. Known: {names}")
        return known.tla or known.num
    key = (stream or "").strip().lower()
    if key in NAMED_STREAMS:
        return NAMED_STREAMS[key]
    if stream and stream.strip():
        return stream.strip().upper()
    return "INTERNATIONAL"

def create(
    *,
    driver: str | None = None,
    stream: str | None = None,
    bounds: tuple[int, int, int, int] | None = None,
    content_id: int | None = None,
) -> tuple[int, str]:
    """Create a player. Returns ``(player_id, what_it_shows)``."""
    session, _ = feed.snapshot()
    if content_id is None:
        resolution = current_resolution()
        if not resolution.ok:
            raise MeetingError(
                "no content id for this session yet: "
                + (", ".join(f"{s.short_title}={s.content_id}" for s in resolution.candidates[:6])
                   or "the meetings API returned no sessions")
            )
        content_id = resolution.content_id

    title = _stream_title(driver=driver, stream=stream, session=session)
    x, y, width, height = bounds if bounds else (0, 0, 960, 540)
    response = _remote().player_create(
        content_id=int(content_id),
        stream_title=title,
        x=int(x), y=int(y), width=int(width), height=int(height),
    )
    payload = (response or {}).get("data") or {}
    player_id = payload.get("playerCreate") if isinstance(payload, dict) else None
    try:
        player_id = int(player_id) if player_id is not None else None
    except (TypeError, ValueError):
        player_id = None
    if player_id is None:
        raise RuntimeError(f"MultiViewer did not return a player id: {str(response)[:160]}")
    return player_id, title


def set_bounds(player_id: int, x: int, y: int, width: int, height: int) -> None:
    _remote().player_set_bounds(id=player_id, x=x, y=y, width=width, height=height)


def close(player_id: int) -> None:
    _remote().player_delete(id=player_id)


def control(player_id: int, action: str, *, value=None) -> str:
    """Small non-destructive action surface for one player."""
    player = _remote().player(player_id)
    action = action.lower()
    if action == "sync":
        player.sync()
        return f"player {player_id} synced"
    if action == "live":
        remote = _remote()
        sync = getattr(remote, "player_sync_to_live_timing", None)
        if callable(sync):
            sync()
            return "players synced to live timing"
        player.seek(relative=10000)
        player.pause(False)
        return f"player {player_id} seeked to the live edge"
    if action == "pause":
        player.pause(True)
        return f"player {player_id} paused"
    if action == "play":
        player.pause(False)
        return f"player {player_id} playing"
    if action == "mute":
        player.mute(True)
        return f"player {player_id} muted"
    if action == "unmute":
        player.mute(False)
        return f"player {player_id} unmuted"
    if action == "close":
        player.delete()
        return f"player {player_id} closed"
    if action == "seek":
        seconds = float(value or 0)
        player.seek(relative=seconds)
        return f"player {player_id} seeked {seconds:+.1f}s"
    if action == "fullscreen":
        player.set_fullscreen(True)
        return f"player {player_id} fullscreen"
    if action == "always_on_top":
        level = value if isinstance(value, str) and value else None
        try:
            player.set_always_on_top(True, level=level)   # mvf1 >= 2.2
        except TypeError:
            player.set_always_on_top(True)
        return f"player {player_id} always on top" + (f" ({level})" if level else "")
    if action == "always_on_bottom":
        player.set_always_on_top(False)
        return f"player {player_id} no longer always on top"
    return f"unknown action {action!r}"


def _region(screen: tuple[int, int], side: str) -> tuple[int, int, int, int]:
    width, height = screen
    side = (side or "right").lower()
    if side == "left":
        return 0, 0, width // 2, height
    if side == "full":
        return 0, 0, width, height
    if side == "top":
        return 0, 0, width, height // 2
    if side == "bottom":
        return 0, height // 2, width, height // 2
    return width // 2, 0, width // 2, height          # right


def tile_layout(count: int, screen: tuple[int, int] = (3840, 2160), side: str = "right") -> list[tuple[int, int, int, int]]:
    """Cells for *count* players inside a region, each fitted to 16:9."""
    if count <= 0:
        return []
    rx, ry, rw, rh = _region(screen, side)
    cols = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / cols))
    cell_w, cell_h = rw / cols, rh / rows
    width = int(min(cell_w, cell_h * 16 / 9))
    height = int(width * 9 / 16)
    cells = []
    for index in range(count):
        col, row = index % cols, index // cols
        x = int(rx + col * cell_w + (cell_w - width) / 2)
        y = int(ry + row * cell_h + (cell_h - height) / 2)
        cells.append((x, y, width, height))
    return cells


def tile(
    *, screen: tuple[int, int] = (3840, 2160), side: str = "right", ids: list[int] | None = None
) -> list[tuple[int, tuple[int, int, int, int]]]:
    players = list_players()
    if ids:
        wanted = set(ids)
        players = [p for p in players if p.id in wanted]
    if not players:
        return []
    out = []
    for player, bounds in zip(players, tile_layout(len(players), screen, side)):
        set_bounds(player.id, *bounds)
        out.append((player.id, bounds))
    return out


def summary(players: list[PlayerInfo]) -> str:
    """One-line health readout used by /pitwall init and the players tool."""
    if not players:
        return "no players open"
    paused = sum(1 for p in players if p.paused)
    off_edge = sum(1 for p in players if not p.paused)
    bits = [f"{len(players)} open"]
    if paused:
        bits.append(f"{paused} paused")
    if off_edge:
        bits.append(f"{off_edge} playing")
    return ", ".join(bits)


__all__ = [
    "PlayerInfo", "current_resolution", "list_players", "create", "set_bounds",
    "close", "control", "tile", "tile_layout", "summary",
]
