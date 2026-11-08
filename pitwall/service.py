"""Install the 20-second watcher into whatever scheduler this platform has.

Linux keeps the one-shot-per-tick model it already had: a ``systemd --user``
timer every 20 s. macOS and Windows get a kept-alive process instead, because
neither ``StartInterval`` nor ``schtasks`` is a good place to hold a 20-second
cadence — launchd spaces invocations by wall clock and Task Scheduler cannot go
below a minute, so both platforms run ``watch --loop`` and let it own the clock.

Every unit runs the interpreter that ran ``service install`` and not ``python3``
from PATH: under Hermes the right Python is a PM-managed venv, and PATH is not
it. Re-run the install after a Hermes upgrade moves that venv.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "pitwall-watch"
LAUNCHD_LABEL = "io.pitwall.watch"
TASK_NAME = "pitwall-watch"
DOCS = "https://github.com/RobSpectre/pitwall-agent"
KINDS = ("systemd", "launchd", "taskscheduler")


class ServiceError(RuntimeError):
    """The scheduler could not be written or reached."""


# ---------------------------------------------------------------------------
# where things go
# ---------------------------------------------------------------------------


def platform_kind() -> str:
    """The scheduler this OS actually has."""
    if sys.platform.startswith("linux"):
        return "systemd"
    if sys.platform == "darwin":
        return "launchd"
    if os.name == "nt":
        return "taskscheduler"
    raise ServiceError(
        f"no scheduler support for {sys.platform}; run `pitwall-agent watch --loop` yourself"
    )


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def log_path() -> Path:
    return hermes_home() / "state" / "pitwall-service.log"


def unit_paths(kind: str) -> dict[str, Path]:
    """Where each scheduler reads its definition from."""
    if kind == "systemd":
        base = Path.home() / ".config" / "systemd" / "user"
        return {
            "service": base / f"{LABEL}.service",
            "timer": base / f"{LABEL}.timer",
        }
    if kind == "launchd":
        return {"plist": Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"}
    if kind == "taskscheduler":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return {"task": base / "pitwall-agent" / f"{TASK_NAME}.xml"}
    raise ServiceError(f"unknown scheduler {kind!r}; expected one of {', '.join(KINDS)}")


def _windows_python() -> str:
    """``pythonw.exe`` when it exists, so no console window flashes at logon."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def watch_argv(*, kind: str, interval: int, notify: bool) -> list[str]:
    python = _windows_python() if kind == "taskscheduler" else sys.executable
    argv = [python, "-m", "pitwall.cli", "watch"]
    argv += ["--loop", "--interval", str(interval)] if kind != "systemd" else ["--once"]
    argv.append("--react")
    if notify:
        argv.append("--notify")
    return argv


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_systemd(*, interval: int, notify: bool, log: Path) -> dict[str, str]:
    argv = watch_argv(kind="systemd", interval=interval, notify=notify)
    service = (
        "[Unit]\n"
        "Description=pitwall-agent live-timing watch (deterministic, no model)\n"
        f"Documentation={DOCS}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={' '.join(argv)}\n"
        "TimeoutStartSec=45\n"
        "Nice=5\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=pitwall-agent watch every {interval} seconds\n"
        f"Documentation={DOCS}\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=30s\n"
        f"OnUnitActiveSec={interval}s\n"
        "AccuracySec=1s\n"
        f"Unit={LABEL}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {"service": service, "timer": timer}


def render_launchd(*, interval: int, notify: bool, log: Path) -> dict[str, str]:
    payload = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": watch_argv(kind="launchd", interval=interval, notify=notify),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    return {"plist": plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode("utf-8")}


TASK_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>pitwall-agent watcher: deterministic live-timing events every {interval}s</Description>
    <URI>\\{name}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def render_taskscheduler(*, interval: int, notify: bool, log: Path) -> dict[str, str]:
    argv = watch_argv(kind="taskscheduler", interval=interval, notify=notify)
    text = TASK_TEMPLATE.format(
        interval=interval,
        name=TASK_NAME,
        command=escape(argv[0]),
        arguments=escape(" ".join(argv[1:])),
    )
    return {"task": text}


def render(kind: str, *, interval: int = 20, notify: bool = False, log: Path | None = None) -> dict[str, str]:
    """The files this scheduler needs, keyed by role, as text."""
    log = log or log_path()
    if kind == "systemd":
        return render_systemd(interval=interval, notify=notify, log=log)
    if kind == "launchd":
        return render_launchd(interval=interval, notify=notify, log=log)
    if kind == "taskscheduler":
        return render_taskscheduler(interval=interval, notify=notify, log=log)
    raise ServiceError(f"unknown scheduler {kind!r}; expected one of {', '.join(KINDS)}")


# ---------------------------------------------------------------------------
# scheduler commands
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(getattr(os, "getuid", lambda: 0)())


def install_commands(kind: str, paths: dict[str, Path]) -> list[list[str]]:
    if kind == "systemd":
        return [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", f"{LABEL}.timer"],
        ]
    if kind == "launchd":
        plist = paths["plist"]
        domain = f"gui/{_uid()}"
        return [
            ["launchctl", "bootout", domain, str(plist)],          # idempotent reinstall
            ["launchctl", "bootstrap", domain, str(plist)],
            ["launchctl", "kickstart", "-k", f"{domain}/{LAUNCHD_LABEL}"],
        ]
    if kind == "taskscheduler":
        return [["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", str(paths["task"])]]
    raise ServiceError(f"unknown scheduler {kind!r}")


def uninstall_commands(kind: str, paths: dict[str, Path]) -> list[list[str]]:
    if kind == "systemd":
        return [
            ["systemctl", "--user", "disable", "--now", f"{LABEL}.timer"],
            ["systemctl", "--user", "daemon-reload"],
        ]
    if kind == "launchd":
        return [["launchctl", "bootout", f"gui/{_uid()}/{LAUNCHD_LABEL}"]]
    if kind == "taskscheduler":
        return [["schtasks", "/Delete", "/F", "/TN", TASK_NAME]]
    raise ServiceError(f"unknown scheduler {kind!r}")


def status_command(kind: str, paths: dict[str, Path]) -> list[str]:
    if kind == "systemd":
        return ["systemctl", "--user", "is-active", f"{LABEL}.timer"]
    if kind == "launchd":
        return ["launchctl", "print", f"gui/{_uid()}/{LAUNCHD_LABEL}"]
    if kind == "taskscheduler":
        return ["schtasks", "/Query", "/TN", TASK_NAME]
    raise ServiceError(f"unknown scheduler {kind!r}")


def _run(command: list[str], *, allow_failure: bool = False) -> tuple[int, str]:
    if shutil.which(command[0]) is None:
        if allow_failure:
            return 127, f"{command[0]} not found"
        raise ServiceError(f"{command[0]} is not on PATH, so the scheduler cannot be reached")
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        if allow_failure:
            return 1, str(exc)
        raise ServiceError(f"{' '.join(command)} failed: {exc}") from exc
    output = (done.stdout or done.stderr or "").strip()
    if done.returncode != 0 and not allow_failure:
        raise ServiceError(f"{' '.join(command)} failed ({done.returncode}): {output}")
    return done.returncode, output


# ---------------------------------------------------------------------------
# the three public verbs
# ---------------------------------------------------------------------------


def _write(path: Path, text: str, kind: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Task Scheduler wants its XML in UTF-16; everything else is plain UTF-8.
    path.write_text(text, encoding="utf-16" if kind == "taskscheduler" else "utf-8")


def install(
    *,
    kind: str | None = None,
    interval: int = 20,
    notify: bool = False,
    dry_run: bool = False,
) -> str:
    kind = kind or platform_kind()
    paths = unit_paths(kind)
    files = render(kind, interval=interval, notify=notify)
    commands = install_commands(kind, paths)

    lines: list[str] = []
    if dry_run:
        lines.append(f"would install the {kind} watcher every {interval}s:")
        for role, text in files.items():
            lines.append(f"\n--- {paths[role]} ---\n{text.rstrip()}")
        lines.append("\nwould run:")
        lines.extend(f"  {' '.join(cmd)}" for cmd in commands)
        return "\n".join(lines)

    for role, text in files.items():
        _write(paths[role], text, kind)
    lines.append(f"wrote {', '.join(str(p) for p in paths.values())}")
    for command in commands:
        # bootout before bootstrap fails when nothing is loaded; that is expected.
        code, output = _run(command, allow_failure=command[1:2] == ["bootout"])
        lines.append(f"$ {' '.join(command)} -> {code}" + (f" {output}" if output else ""))
    if kind == "systemd":
        lines.append(
            "note: a user timer stops at logout unless you run "
            "`loginctl enable-linger $USER`."
        )
    return "\n".join(lines)


def uninstall(*, kind: str | None = None, dry_run: bool = False) -> str:
    kind = kind or platform_kind()
    paths = unit_paths(kind)
    commands = uninstall_commands(kind, paths)
    if dry_run:
        return "\n".join(
            [f"would remove {', '.join(str(p) for p in paths.values())}", "would run:"]
            + [f"  {' '.join(cmd)}" for cmd in commands]
        )
    lines: list[str] = []
    for command in commands:
        code, output = _run(command, allow_failure=True)
        lines.append(f"$ {' '.join(command)} -> {code}" + (f" {output}" if output else ""))
    for path in paths.values():
        try:
            path.unlink()
            lines.append(f"removed {path}")
        except FileNotFoundError:
            lines.append(f"no unit at {path}")
    return "\n".join(lines)


def status(*, kind: str | None = None) -> str:
    kind = kind or platform_kind()
    paths = unit_paths(kind)
    installed = [f"{role}: {path} ({'present' if path.exists() else 'missing'})"
                 for role, path in paths.items()]
    code, output = _run(status_command(kind, paths), allow_failure=True)
    state = {
        "systemd": "active" if code == 0 else "not active",
        "launchd": "loaded" if code == 0 else "not loaded",
        "taskscheduler": "registered" if code == 0 else "not registered",
    }[kind]
    lines = [f"{kind}: {state}"] + installed
    if output:
        lines.append(output.splitlines()[0])
    return "\n".join(lines)
