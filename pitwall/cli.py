"""``pitwall-agent``: the same compact reads, for any agent that can run a command.

Every subcommand calls the identical handler the Hermes tools call, so the text
an MCP client, a shell agent or Hermes sees is the same.

    pitwall-agent init
    pitwall-agent board --changes
    pitwall-agent flying
    pitwall-agent race --limit 10
    pitwall-agent open LEC --side right --screen 3840x2160
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import feed, meeting, players as playerlib, render
from .model import find_driver
from .plugin import init_report
from .tools import specs

LEVELS = {"CRIT": 0, "HIGH": 1, "MED": 2}


def _log_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "state" / "pitwall-events.log"


def _append_log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


def _tail(path: Path, count: int) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return lines[-count:]


def _notify(events) -> None:
    """Desktop notification -- the point is to be seen without asking a model."""
    binary = shutil.which("notify-send")
    if not binary:
        return
    worst = "normal" if any(e.level == "HIGH" for e in events) else "low"
    if any(e.level == "CRIT" for e in events):
        worst = "critical"
    head = events[0].text if len(events) == 1 else f"{len(events)} events"
    body = "\n".join(e.text for e in events[:4])
    try:
        subprocess.run(
            [binary, "-u", worst, "-a", "pitwall", f"pitwall: {head}", body],
            check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _watch(args) -> int:
    from . import watch as watcher

    log_path = _log_path(args.log)

    if args.recent:
        for line in _tail(log_path, args.recent):
            print(line)
        return 0

    events = watcher.scan()
    keep = [e for e in events if LEVELS[e.level] <= LEVELS[args.min_level]]
    if not keep:
        return 0

    stamp = time.strftime("%H:%M:%S")
    if args.json:
        print(json.dumps([{"at": stamp, "level": e.level, "text": e.text} for e in keep]))
    else:
        print(watcher.render(keep, stamp))
    _append_log(log_path, watcher.render(keep, stamp))
    if args.notify:
        _notify(keep)
    return 0



def _handler(name: str):
    return next(spec.handler for spec in specs() if spec.name == name)


def _out(text: str) -> int:
    print(text)
    return 1 if text.startswith("Error:") else 0


def _session():
    session, _ = feed.snapshot()
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pitwall-agent",
        description="Compact MultiViewer live-timing reads for a broadcast agent.",
    )
    parser.add_argument("--version", action="version", version="pitwall-agent 0.2.0")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="check MultiViewer, identify the session and content id")

    p = sub.add_parser("overview", help="session, clock, pole, track, latest flags")
    p.add_argument("--messages", type=int, default=3)

    p = sub.add_parser("board", help="classification with run states and sector colours")
    p.add_argument("--changes", action="store_true", help="only what moved since the last call")
    p.add_argument("--tol", type=float, help="only cars within this many seconds of pole")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("flying", help="who is on a flying lap now")

    p = sub.add_parser("purple", help="session-best sectors and live purples")

    p = sub.add_parser("pole", help="pole contention and clock feasibility")
    p.add_argument("--tol", type=float, default=0.25)

    p = sub.add_parser("pace", help="practice pace table")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("race", help="race order, intervals, tyres, stops")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("battle", help="pairs inside a second")
    p.add_argument("--window", type=float, default=1.0)

    p = sub.add_parser("strategy", help="tyre runs, stops, laps remaining")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("driver", help="one car in detail")
    p.add_argument("driver")

    sub.add_parser("players", help="open players and the resolved content id")

    p = sub.add_parser("open", help="create a player")
    p.add_argument("target", help="driver code, or DATA / INTERNATIONAL / PIT LANE")
    p.add_argument("--side", default="right", choices=["left", "right", "top", "bottom", "full"])
    p.add_argument("--screen", default="3840x2160")
    p.add_argument("--no-tile", action="store_true")

    p = sub.add_parser("control", help="control one player")
    p.add_argument("player", type=int)
    p.add_argument(
        "action",
        choices=["sync", "live", "pause", "play", "mute", "unmute", "seek", "fullscreen", "close"],
    )
    p.add_argument("--value", type=float)

    p = sub.add_parser("tile", help="lay every open player out in a grid")
    p.add_argument("--side", default="right", choices=["left", "right", "top", "bottom", "full"])
    p.add_argument("--screen", default="3840x2160")

    p = sub.add_parser("raw", help="live-timing document, whole or in slices")
    p.add_argument("sections", nargs="*")
    p.add_argument("--driver")

    sub.add_parser("mcp", help="run the MCP server on stdio (for other agents)")

    p = sub.add_parser("watch", help="deterministic change detection - no model involved")
    p.add_argument("--once", action="store_true", default=True, help="one pass (default)")
    p.add_argument("--json", action="store_true", help="machine-readable events")
    p.add_argument("--notify", action="store_true", help="desktop notification when something happens")
    p.add_argument("--recent", type=int, metavar="N", help="print the last N logged events instead")
    p.add_argument("--log", default=None, help="event log path (default ~/.hermes/state/pitwall-events.log)")
    p.add_argument("--min-level", choices=["CRIT", "HIGH", "MED"], default="MED")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "init":
            return _out(init_report())
        if args.command == "mcp":
            from .mcp import run

            run()
            return 0
        if args.command == "watch":
            return _watch(args)
        if args.command == "overview":
            return _out(_handler("pitwall_overview")({"messages": args.messages}))
        if args.command == "board":
            payload = {"only_changes": args.changes, "limit": args.limit}
            if args.tol is not None:
                payload["gap_tol"] = args.tol
            return _out(_handler("pitwall_board")(payload))
        if args.command == "flying":
            return _out(_handler("pitwall_flying")({}))
        if args.command == "purple":
            return _out(_handler("pitwall_purple")({}))
        if args.command == "pole":
            return _out(_handler("pitwall_pole")({"tol": args.tol}))
        if args.command == "pace":
            return _out(_handler("pitwall_pace")({"limit": args.limit}))
        if args.command == "race":
            return _out(_handler("pitwall_race")({"limit": args.limit}))
        if args.command == "battle":
            return _out(_handler("pitwall_battle")({"window": args.window}))
        if args.command == "strategy":
            return _out(_handler("pitwall_strategy")({"limit": args.limit}))
        if args.command == "driver":
            return _out(_handler("pitwall_driver")({"driver": args.driver}))
        if args.command == "players":
            return _out(_handler("pitwall_players")({}))
        if args.command == "open":
            return _out(
                _handler("pitwall_player_open")(
                    {
                        "driver": args.target if len(args.target) <= 3 and args.target.upper() != "DATA" else None,
                        "stream": None if len(args.target) <= 3 and args.target.upper() != "DATA" else args.target,
                        "side": args.side,
                        "screen": args.screen,
                        "tile": not args.no_tile,
                    }
                )
            )
        if args.command == "control":
            return _out(
                _handler("pitwall_player_control")(
                    {
                        "player": args.player,
                        "action": args.action,
                        "value": args.value,
                        "screen": "3840x2160",
                        "side": "right",
                    }
                )
            )
        if args.command == "tile":
            return _out(
                _handler("pitwall_player_control")(
                    {"action": "tile", "screen": args.screen, "side": args.side}
                )
            )
        if args.command == "raw":
            payload: dict = {"sections": args.sections} if args.sections else {}
            if args.driver:
                payload["driver"] = args.driver
            return _out(_handler("pitwall_raw")(payload))
    except Exception as exc:  # never dump a traceback at a broadcast operator
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
