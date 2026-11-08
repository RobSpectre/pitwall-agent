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
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, feed, meeting, players as playerlib, render
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


def _applescript(text: str) -> str:
    """An AppleScript string literal: escaping only backslash and quote."""
    return '"' + " ".join(str(text).split()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _powershell(text: str) -> str:
    return "'" + " ".join(str(text).split()).replace("'", "''") + "'"


def _notify_commands(urgency: str, title: str, body: str):
    """The notification command this platform understands, if any.

    Every branch is optional: a machine with no notifier is a normal machine,
    and the event log is the source of truth either way.
    """
    if sys.platform.startswith("linux"):
        binary = shutil.which("notify-send")
        if binary:
            yield [binary, "-u", urgency, "-a", "pitwall", title, body]
    elif sys.platform == "darwin":
        script = (
            f"display notification {_applescript(body)} "
            f"with title {_applescript(title)}"
        )
        yield ["osascript", "-e", script]
    elif os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            f"$n.ShowBalloonTip(4000, {_powershell(title)}, {_powershell(body)}, "
            "[System.Windows.Forms.ToolTipIcon]::Info);"
            "Start-Sleep -Seconds 4; $n.Dispose()"
        )
        yield ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def _notify(events) -> None:
    """Desktop notification -- the point is to be seen without asking a model."""
    worst = "normal" if any(e.level == "HIGH" for e in events) else "low"
    if any(e.level == "CRIT" for e in events):
        worst = "critical"
    head = events[0].text if len(events) == 1 else f"{len(events)} events"
    body = "\n".join(e.text for e in events[:4])
    for command in _notify_commands(worst, f"pitwall: {head}", body):
        try:
            subprocess.run(command, check=False, timeout=6)
            return
        except (OSError, subprocess.SubprocessError):
            continue


def _watch(args) -> int:
    log_path = _log_path(args.log)

    if args.recent:
        for line in _tail(log_path, args.recent):
            print(line)
        return 0

    if getattr(args, "loop", False):
        return _watch_loop(args, log_path)

    # A timer tick that lands while a loop is running must not double-log;
    # skipping is the correct outcome for a scheduled pass, not a failure.
    from .lock import SingleInstance, path as lock_path

    lock = SingleInstance(lock_path())
    if not lock.acquire():
        print("pitwall: another watcher is running; skipping this pass", file=sys.stderr)
        return 0
    try:
        return _watch_pass(args, log_path)
    finally:
        lock.release()


def _watch_pass(args, log_path: Path) -> int:
    """One detection pass: prints and logs only when something happened."""
    from . import watch as watcher

    events = watcher.scan(react=args.react)
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



def _watch_loop(args, log_path: Path) -> int:
    """Poll forever at a fixed cadence, one pass at a time.

    This is the macOS and Windows shape: launchd and Task Scheduler are both
    poor places to hold a 20-second beat, so the process owns the clock instead.
    The lock keeps a second copy from double-logging every event.
    """
    from .lock import SingleInstance, path as lock_path

    interval = max(5, int(getattr(args, "interval", 20) or 20))
    lock = SingleInstance(lock_path())
    if not lock.acquire():
        print("pitwall: another watcher already holds the lock", file=sys.stderr)
        return 1

    stopping = False

    def _stop(*_signal) -> None:
        nonlocal stopping
        stopping = True

    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(number, _stop)
        except (ValueError, OSError):
            pass                      # not settable on this platform/thread

    try:
        tick = time.monotonic()
        while not stopping:
            try:
                _watch_pass(args, log_path)
            except Exception as exc:   # a dead feed is a skipped pass, not a dead watcher
                print(f"pitwall: pass failed ({type(exc).__name__}: {exc})", file=sys.stderr)
            if stopping:
                break
            tick += interval
            remaining = tick - time.monotonic()
            if remaining <= 0:
                tick = time.monotonic()    # fell behind: resync instead of sprinting
                continue
            end = time.monotonic() + remaining
            while not stopping and time.monotonic() < end:
                time.sleep(min(1.0, max(0.0, end - time.monotonic())))
    finally:
        lock.release()
    return 0


def _incident(args) -> int:
    """Manual version of what the watcher does on its own."""
    from . import incident

    session, _ = feed.snapshot()
    if not incident.is_flag(session):
        print(f"no flag active (track: {session.track})")
        return 0

    found = incident.candidates(session)
    if not found:
        print("flag active, but no car is stopped on track in the feed")
        return 0

    driver = found[0]
    if args.dry_run:
        names = ", ".join(f"{d.label} (P{d.position or '-'})" for d in found[:4])
        print(f"would frame {driver.label} and rewind {args.back}s; candidates: {names}")
        return 0

    report, _player_id = incident.frame(driver, back=args.back)
    print(report)
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
    parser.add_argument("--version", action="version", version=f"pitwall-agent {__version__}")
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

    p = sub.add_parser("tyres", help="fitted tyre degradation per car")
    p.add_argument("--limit", type=int, default=12)

    p = sub.add_parser("fuel", help="last lap corrected to an empty tank")
    p.add_argument("--start-kg", dest="start_kg", type=float, default=None)
    p.add_argument("--effect", type=float, default=None)
    p.add_argument("--limit", type=int, default=12)

    p = sub.add_parser("overtake", help="pairs in range, pace delta, projected pass lap")
    p.add_argument("--window", type=float, default=2.0)
    p.add_argument("--floor", type=float, default=None)

    p = sub.add_parser("pitplan", help="ranked stop plans for one car")
    p.add_argument("--driver", help="three-letter code or racing number (default: the leader)")
    p.add_argument("--stops", type=int, choices=(1, 2))
    p.add_argument("--pit-loss", dest="pit_loss", type=float, default=None)
    p.add_argument("--limit", type=int, default=3)

    p = sub.add_parser("undercut", help="who can win a place in the pit lane")
    p.add_argument("--window", type=float, default=25.0)
    p.add_argument("--pit-loss", dest="pit_loss", type=float, default=None)
    p.add_argument("--limit", type=int, default=8)

    p = sub.add_parser("windows", help="laps left on each set against laps to the flag")
    p.add_argument("--limit", type=int, default=12)

    p = sub.add_parser("outlook", help="projected finishing order from here")
    p.add_argument("--pit-loss", dest="pit_loss", type=float, default=None)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("radio", help="driver radio transcriptions, cut at the playhead")
    p.add_argument("--driver", action="append", default=None, metavar="DRV",
                   help="one car or several: repeat the flag, or comma-separate (--driver NOR,HAM)")
    p.add_argument("--limit", type=int, default=None, help="lines (6) or, with --summary, drivers (10)")
    p.add_argument("--summary", action="store_true", help="per-driver digest of the last laps")
    p.add_argument("--laps", type=int, default=4, help="summary window in laps (default 4)")

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
    p.add_argument("--loop", action="store_true", help="poll forever at --interval instead of one pass")
    p.add_argument("--interval", type=int, default=20, help="seconds between passes with --loop (default 20)")
    p.add_argument("--once", action="store_true", default=True, help="one pass (default)")
    p.add_argument("--json", action="store_true", help="machine-readable events")
    p.add_argument("--notify", action="store_true", help="desktop notification when something happens")
    p.add_argument("--recent", type=int, metavar="N", help="print the last N logged events instead")
    p.add_argument("--log", default=None, help="event log path (default ~/.hermes/state/pitwall-events.log)")
    p.add_argument("--min-level", choices=["CRIT", "HIGH", "MED"], default="MED")
    p.add_argument(
        "--react",
        action="store_true",
        help="on a flag: full-screen the stopped car's onboard and rewind it; on green: restore",
    )

    p = sub.add_parser("service", help="install the 20s watcher in the platform scheduler")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--kind", choices=["systemd", "launchd", "taskscheduler"], default=None,
                   help="target scheduler (default: this platform)")
    p.add_argument("--interval", type=int, default=20, help="seconds between passes (default 20)")
    p.add_argument("--notify", action="store_true", help="also raise a desktop notification")
    p.add_argument("--dry-run", action="store_true", help="print the unit and commands, change nothing")

    p = sub.add_parser("incident", help="frame the stopped car now, manually")
    p.add_argument("--back", type=int, default=30, help="seconds to rewind (default 30)")
    p.add_argument("--dry-run", action="store_true", help="say what would be framed, touch nothing")

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
        if args.command == "service":
            from . import service as servicelib

            try:
                if args.action == "install":
                    return _out(servicelib.install(
                        kind=args.kind, interval=args.interval,
                        notify=args.notify, dry_run=args.dry_run,
                    ))
                if args.action == "uninstall":
                    return _out(servicelib.uninstall(kind=args.kind, dry_run=args.dry_run))
                return _out(servicelib.status(kind=args.kind))
            except servicelib.ServiceError as exc:
                return _out(f"Error: {exc}")
        if args.command == "incident":
            return _incident(args)
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
        if args.command == "tyres":
            return _out(_handler("pitwall_tyres")({"limit": args.limit}))
        if args.command == "fuel":
            payload = {"limit": args.limit}
            if args.start_kg is not None:
                payload["start_kg"] = args.start_kg
            if args.effect is not None:
                payload["effect"] = args.effect
            return _out(_handler("pitwall_fuel")(payload))
        if args.command == "overtake":
            payload = {"window": args.window}
            if args.floor is not None:
                payload["floor"] = args.floor
            return _out(_handler("pitwall_overtake")(payload))
        if args.command == "pitplan":
            payload = {"limit": args.limit}
            for key, value in (("driver", args.driver), ("stops", args.stops),
                               ("pit_loss", args.pit_loss)):
                if value is not None:
                    payload[key] = value
            return _out(_handler("pitwall_pitplan")(payload))
        if args.command == "undercut":
            payload = {"window": args.window, "limit": args.limit}
            if args.pit_loss is not None:
                payload["pit_loss"] = args.pit_loss
            return _out(_handler("pitwall_undercut")(payload))
        if args.command == "windows":
            return _out(_handler("pitwall_windows")({"limit": args.limit}))
        if args.command == "outlook":
            payload = {"limit": args.limit}
            if args.pit_loss is not None:
                payload["pit_loss"] = args.pit_loss
            return _out(_handler("pitwall_outlook")(payload))
        if args.command == "radio":
            payload = {"laps": args.laps, "summary": args.summary}
            if args.limit is not None:
                payload["limit"] = args.limit
            if args.driver:
                payload["driver"] = args.driver
            return _out(_handler("pitwall_radio")(payload))
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
