"""The scheduler adapters: rendered text, and the fact that dry-run is dry.

Nothing here touches systemctl, launchctl or schtasks. The renderers are pure,
so the shapes can be asserted on any OS -- which is the point: CI on Linux can
check the launchd plist and the Task Scheduler XML without a Mac or a PC.
"""

import os
import plistlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from pitwall import cli, lock, service

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def fake_paths(monkeypatch, tmp_path):
    """Keep every unit inside tmp_path, whatever the platform would use."""
    def install(kind: str):
        roles = {"systemd": ("service", "timer"), "launchd": ("plist",),
                 "taskscheduler": ("task",)}[kind]
        return {role: tmp_path / f"{role}.txt" for role in roles}
    monkeypatch.setattr(service, "unit_paths", install)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return install


@pytest.fixture
def no_scheduler(monkeypatch):
    """Make any real scheduler call fail the test loudly."""
    calls: list[list[str]] = []

    def fake_run(command, *, allow_failure=False):
        calls.append(command)
        if not allow_failure:
            raise AssertionError(f"a test tried to run {command}")
        return 1, ""

    monkeypatch.setattr(service, "_run", fake_run)
    return calls


# --- platform mapping ------------------------------------------------------


def test_platform_kind_maps_each_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert service.platform_kind() == "systemd"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert service.platform_kind() == "launchd"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "name", "nt")
    assert service.platform_kind() == "taskscheduler"


# --- systemd ---------------------------------------------------------------


def test_systemd_units_carry_the_running_interpreter(fake_paths):
    files = service.render("systemd", interval=20, notify=False)
    assert "ExecStart=" in files["service"]
    assert sys.executable in files["service"]
    assert "watch --once --react" in files["service"]
    assert "OnUnitActiveSec=20s" in files["timer"]
    assert "pitwall-service.log" in files["service"]


def test_systemd_notify_is_opt_in(fake_paths):
    assert "--notify" not in service.render("systemd", notify=False)["service"]
    assert "--notify" in service.render("systemd", notify=True)["service"]


# --- launchd ---------------------------------------------------------------


def test_launchd_plist_is_a_kept_alive_loop(fake_paths):
    payload = plistlib.loads(service.render("launchd", interval=25)["plist"].encode("utf-8"))
    assert payload["Label"] == service.LAUNCHD_LABEL
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    argv = payload["ProgramArguments"]
    assert "--loop" in argv and "--interval" in argv
    assert argv[argv.index("--interval") + 1] == "25"
    assert argv[1:3] == ["-m", "pitwall.cli"]


# --- Task Scheduler --------------------------------------------------------


def test_task_xml_is_a_logon_loop(fake_paths):
    text = service.render("taskscheduler", interval=20)["task"]
    root = ET.fromstring(text)
    settings = root.find("t:Settings", NS)
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"

    action = root.find("t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text
    arguments = action.find("t:Arguments", NS).text
    assert "--loop" in arguments and "--interval 20" in arguments
    assert root.find("t:Triggers/t:LogonTrigger", NS) is not None


def test_task_xml_escapes_the_python_path(fake_paths, monkeypatch):
    monkeypatch.setattr(service, "_windows_python", lambda: r"C:\Program Files\Py\pythonw.exe")
    text = service.render("taskscheduler")["task"]
    root = ET.fromstring(text)
    assert root.find("t:Actions/t:Exec/t:Command", NS).text == r"C:\Program Files\Py\pythonw.exe"


# --- verbs -----------------------------------------------------------------


def test_dry_run_writes_nothing_and_runs_nothing(fake_paths, no_scheduler, capsys):
    text = service.install(kind="systemd", dry_run=True)
    assert "ExecStart=" in text and "systemctl --user daemon-reload" in text
    assert not any(path.exists() for path in fake_paths("systemd").values())


def test_install_writes_the_unit_then_registers_it(fake_paths, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(command, *, allow_failure=False):
        calls.append(command)
        return 0, ""

    monkeypatch.setattr(service, "_run", fake_run)
    text = service.install(kind="systemd")
    paths = fake_paths("systemd")
    assert paths["service"].exists() and paths["timer"].exists()
    assert sys.executable in paths["service"].read_text(encoding="utf-8")
    assert calls[0][:3] == ["systemctl", "--user", "daemon-reload"]
    assert "loginctl enable-linger" in text


def test_launchd_install_reloads_the_agent(fake_paths, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_run", lambda command, allow_failure=False: (calls.append(command), (0, ""))[1])
    service.install(kind="launchd")
    assert ["launchctl", "bootout", f"gui/{service._uid()}", str(fake_paths("launchd")["plist"])] == calls[0]
    assert calls[1][:2] == ["launchctl", "bootstrap"]
    assert calls[2][:3] == ["launchctl", "kickstart", "-k"]


def test_status_says_missing_when_there_is_no_unit(fake_paths, no_scheduler):
    text = service.status(kind="systemd")
    assert "missing" in text and "not active" in text


# --- the loop and its lock -------------------------------------------------


def test_the_lock_keeps_a_second_watcher_out(tmp_path):
    first = lock.SingleInstance(tmp_path / "watch.lock")
    assert first.acquire()
    second = lock.SingleInstance(tmp_path / "watch.lock")
    assert second.acquire() is False
    first.release()
    assert lock.SingleInstance(tmp_path / "watch.lock").acquire() is True


def test_watch_loop_refuses_a_second_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    held = lock.SingleInstance(lock.path())
    assert held.acquire()
    try:
        args = SimpleNamespace(react=False, log=None, notify=False, json=False,
                               min_level="MED", interval=5)
        assert cli._watch_loop(args, tmp_path / "events.log") == 1
    finally:
        held.release()


def test_a_timer_tick_is_skipped_while_a_loop_holds_the_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    held = lock.SingleInstance(lock.path())
    assert held.acquire()
    try:
        args = SimpleNamespace(recent=0, log=None, react=False, notify=False, json=False,
                               min_level="MED", interval=5, loop=False)
        assert cli._watch(args) == 0        # a skipped tick is not a failure
    finally:
        held.release()
