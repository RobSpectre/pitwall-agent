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
import sys

from . import feed, meeting, players as playerlib, render
from .model import find_driver
from .plugin import init_report
from .tools import specs


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
