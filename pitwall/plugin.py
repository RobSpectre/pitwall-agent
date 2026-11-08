"""Hermes registration entry point.

Exposes the tool set, the ``/pitwall`` command (including ``/pitwall init``),
and every skill under ``skills/`` as ``<plugin>:<name>``.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import feed, meeting, players as playerlib, render
from .tools import MODE_TOOLS, mode_check, radio_report, specs

TOOLSET = "pitwall"
EMOJI = "\U0001f3c1"  # chequered flag
ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)

SUGGESTIONS = {
    "practice": "/pitwall board   /pitwall pace   /pitwall flying   /pitwall players",
    "qualifying": "/pitwall board   /pitwall flying   /pitwall pole   /pitwall purple",
    "race": "/pitwall race   /pitwall battle   /pitwall strategy   /pitwall players",
    "unknown": "/pitwall board   /pitwall players",
}


def _description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        if line.lower().startswith("description:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


# ---------------------------------------------------------------------------
# /pitwall init
# ---------------------------------------------------------------------------


def init_report() -> str:
    """Confirm MultiViewer, identify the session, and say what is available."""
    try:
        version = feed._client().version
        label = ((version or {}).get("data") or {}).get("version") or "?"
    except Exception as exc:
        return (
            "MultiViewer  NOT REACHABLE\n"
            f"  {exc}\n"
            "  Start MultiViewer, open a session, then run /pitwall init again."
        )

    lines = [f"MultiViewer {label}  {feed.uri()}"]

    try:
        state = feed.raw_state()
    except Exception as exc:
        lines.append(f"live timing  NOT AVAILABLE: {exc}")
        lines.append("next  open a session (or start the replay) in MultiViewer, then retry")
        return "\n".join(lines)

    session, _ = feed.snapshot()
    kind = session.name or session.kind or "session"
    if session.part and f"Q{session.part}" not in kind:
        kind += f" Q{session.part}"
    lines.append(f"session   {kind}  {session.meeting} / {session.circuit}")
    clock = f"clock     {render.fmt_clock(session.remaining)} left  {session.status.upper()}  track {session.track}"
    if session.mode == "race" and session.current_lap:
        clock += f"  lap {session.current_lap}/{session.total_laps or '?'}"
    lines.append(clock)

    try:
        resolution = meeting.resolve(state)
    except meeting.MeetingError as exc:
        resolution = None
        lines.append(f"content   NOT RESOLVED: {exc}")
    if resolution is not None:
        if resolution.ok:
            lines.append(
                f"content   {resolution.content_id}  matched on {resolution.matched_on}  "
                f"({resolution.title}, meeting {resolution.meeting_key})"
            )
        else:
            titles = ", ".join(s.short_title or s.title for s in resolution.candidates[:8]) or "none"
            lines.append(f"content   NOT RESOLVED  (meeting {resolution.meeting_key}; sessions: {titles})")

    mode = feed.session_mode()
    tools = MODE_TOOLS.get(mode, MODE_TOOLS["unknown"])
    lines.append(f"mode      {mode}  ->  {tools}" + ("" if mode == "unknown" else "  (+ core tools)"))

    try:
        infos = playerlib.list_players()
        detail = ", ".join(f"{p.id} {p.title}" for p in sorted(infos, key=lambda p: p.id))
        lines.append(f"players   {len(infos)} open" + (f": {detail}" if detail else ""))
    except Exception as exc:
        lines.append(f"players   unavailable: {exc}")

    lines.append(f"next      {SUGGESTIONS.get(mode, SUGGESTIONS['unknown'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /pitwall
# ---------------------------------------------------------------------------


def _command(raw_args: str, **_kw) -> str:
    args = (raw_args or "").strip()
    word = args.split()[0].lower() if args else ""

    if word in {"init", "start", "connect"}:
        return init_report()

    try:
        session, _previous = feed.snapshot()
    except Exception as exc:
        return f"pitwall: {exc}"

    if not args:
        return render.board(session)
    if word in {"pole", "quali", "qualifying"}:
        return render.pole(session)
    if word in {"flying", "flyers"}:
        return render.flying(session)
    if word in {"purple", "sectors"}:
        return render.purple(session)
    if word == "pace":
        return render.pace(session)
    if word == "race":
        return render.race(session)
    if word in {"battle", "battles"}:
        return render.battle(session)
    if word in {"strategy", "tyres", "tires"}:
        return render.strategy(session)
    if word in {"players", "player"}:
        try:
            return render.players_table(
                playerlib.list_players(), playerlib.current_resolution(), session
            )
        except Exception as exc:
            return f"pitwall: {exc}"
    if word == "open":
        target = args[len(word):].strip()
        try:
            player_id, title = playerlib.create(
                driver=target or None, stream=None if target else "INTERNATIONAL"
            )
            layout = playerlib.tile()
            return f"player {player_id} -> {title} (tiled {len(layout)})"
        except Exception as exc:
            return f"pitwall: {exc}"
    if word in {"radio", "radios", "teamradio"}:
        rest = args[len(word):].split()
        summarise = False
        laps, limit = 4, 0
        who: list[str] = []
        if rest and rest[0].lower() in {"summary", "sum", "digest"}:
            summarise = True
            rest = rest[1:]
        for token in rest:
            if token.isdigit():
                if summarise:
                    laps = int(token)
                else:
                    limit = int(token)
            else:
                who.append(token)
        try:
            return radio_report(
                session,
                driver=who or None,
                limit=max(1, min(30, limit if limit else (10 if summarise else 6))),
                summarise=summarise,
                laps=max(1, min(20, laps)),
            )
        except Exception as exc:
            return f"pitwall: {exc}"
    if word == "tile":
        try:
            layout = playerlib.tile()
            return f"tiled {len(layout)} players: " + ", ".join(
                f"{pid}@{x},{y}" for pid, (x, y, _w, _h) in layout
            )
        except Exception as exc:
            return f"pitwall: {exc}"

    driver = feed.known_driver(args, session)
    if driver is None:
        return (
            f"pitwall: no driver {args!r}. Try /pitwall init, /pitwall race, /pitwall pace, "
            "/pitwall flying, /pitwall pole, /pitwall players, /pitwall VER"
        )
    return render.driver_detail(session, driver)


def register(ctx) -> None:
    for spec in specs():
        ctx.register_tool(
            name=spec.name,
            toolset=TOOLSET,
            schema=spec.schema,
            handler=spec.handler,
            description=spec.schema["description"],
            emoji=EMOJI,
            check_fn=mode_check(spec.modes),
        )

    ctx.register_command(
        "pitwall",
        _command,
        description="Pit wall: init, board, flying, pole, race, strategy, players",
        args_hint="[init|race|flying|pole|purple|pace|strategy|battle|players|DRIVER]",
    )

    if SKILLS_DIR.is_dir():
        for skill in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill / "SKILL.md"
            if skill_md.is_file():
                ctx.register_skill(
                    name=skill.name.replace("_", "-"),
                    path=skill_md,
                    description=_description(skill_md),
                )
