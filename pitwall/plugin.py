"""Hermes registration entry point.

Exposes eight tools, the ``/pitwall`` slash command, and every skill under
``skills/`` as ``pitwall:<name>``.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import feed, render
from .tools import schemas

TOOLSET = "pitwall"
EMOJI = "\U0001f3c1"  # chequered flag
ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


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


def _command(raw_args: str) -> str:
    """``/pitwall`` -> the classification, or a one-liner for a named driver."""
    args = (raw_args or "").strip()
    try:
        session, previous = feed.snapshot()
    except Exception as exc:
        return f"pitwall: {exc}"
    if not args:
        return render.board(session)
    if args.lower() in {"pole", "quali", "qualifying"}:
        return render.pole(session)
    if args.lower() in {"flying", "flyers"}:
        return render.flying(session)
    if args.lower() in {"purple", "sectors"}:
        return render.purple(session)
    driver = session.by_num.get(args.upper()) or next(
        (d for d in session.drivers if d.tla.upper() == args.upper()), None
    )
    if driver is None:
        return f"pitwall: no driver {args!r}. Try /pitwall, /pitwall pole, /pitwall flying, /pitwall VER"
    return render.driver_detail(session, driver)


def register(ctx) -> None:
    for name, schema, handler in schemas():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"],
            emoji=EMOJI,
        )

    ctx.register_command(
        "pitwall",
        lambda raw_args, **_kw: _command(raw_args),
        description="Pit wall: session board, pole watch, flyers, purple sectors",
        args_hint="[pole|flying|purple|DRIVER]",
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
