> **Status: implemented in 0.4.0.** `pitwall-agent service install` writes and
> registers the unit for the current platform; `--kind` and `--dry-run` let you
> inspect the other two from any machine. What is still unproven is a real Mac
> and a real Windows box: the renderers are covered by tests that run everywhere,
> but the scheduler calls themselves have only been exercised on Linux.
> The rest of this document is the design, kept because it explains the choices.

# Multiplatform watcher

The tools are portable; the 20-second watcher is not. It is a bash launcher
(`deploy/watch-once.sh`) plus a `systemd --user` timer, which is Linux only.
This is the plan to make the background half work on macOS and Windows without
forking the logic three ways.

## What runs today (Linux)

```
pitwall-watch.timer  (every 20s)
  └─ pitwall-watch.service
       └─ deploy/watch-once.sh
            └─ <venv>/python -m pitwall.cli watch --once --react --notify
```

The launcher resolves Hermes's selected dependency environment out of
`~/.hermes/installs/*/facts.json` and execs a **one-shot** pass. Nothing
survives between ticks except `pitwall-watch.json` and `pitwall-events.log`, so
a missed tick costs nothing.

Three things block the other two platforms: `bash`, `systemd`, and the
interpreter discovery. The first two are scheduler-shaped; the third already has
a better answer in the installed `pitwall-agent` console script, which resolves
the right environment by construction.

## Target design

One Python entry point, three thin scheduler adapters, one cadence.

### 1. A loop mode, so the cadence is not the scheduler's business

Add `pitwall-agent watch --loop --interval 20`. A single process polls at 20 s,
writes the same event log, and holds a single-instance lock:

- lock file `~/.hermes/state/pitwall-watch.lock`, `fcntl.flock` on POSIX and
  `msvcrt.locking` on Windows;
- schedule on `time.monotonic` so a slow pass does not shift the cadence;
- SIGTERM / Ctrl-C exits between passes, after the state file is written;
- a feed error is a skipped pass, not a crash.

That gives every platform the same 20 s behaviour, including Windows, whose
scheduler granularity is one minute.

### 2. `pitwall-agent service {install,uninstall,status}`

The adapter layer. Each writes a unit that runs **the interpreter that ran the
command** (`sys.executable`), not `python3` from PATH — under Hermes the right
environment is a PM-managed venv, and `PATH` is not it.

| Platform | Artifact | Register with |
|---|---|---|
| Linux | `~/.config/systemd/user/pitwall-watch.{service,timer}` | `systemctl --user daemon-reload && enable --now pitwall-watch.timer` |
| macOS | `~/Library/LaunchAgents/io.pitwall.watch.plist` | `launchctl bootstrap gui/$UID <plist>` then `kickstart -k` |
| Windows | `pitwall-watch.xml` for Task Scheduler | `schtasks /Create /F /XML <xml> /TN pitwall-watch` |

Linux keeps the one-shot per tick; macOS and Windows run `watch --loop`, because
`StartInterval` handles 20 s but `schtasks` cannot go below a minute.

launchd, essentially:

```xml
<key>ProgramArguments</key>
<array>
  <string>/path/to/venv/bin/python</string>
  <string>-m</string><string>pitwall.cli</string>
  <string>watch</string><string>--loop</string><string>--react</string>
</array>
<key>StartInterval</key><integer>20</integer>
<key>RunAtLoad</key><true/>
<key>StandardOutPath</key><string>~/Library/Logs/pitwall-watch.log</string>
```

Task Scheduler, essentially: trigger `ONLOGON`, action `python -m pitwall.cli
watch --loop --react`, `RestartOnFailure` every minute, `DisallowStartIfOnBatteries`
false, and `pythonw.exe` where available so no console window appears.

`service install --dry-run` prints the rendered unit and touches nothing; that
is what CI exercises.

### 3. Notifications

`_notify()` currently shells out to `notify-send` and silently no-ops elsewhere.
Keep the no-op as the floor and add: `osascript -e 'display notification …'` on
macOS, and a PowerShell toast (or `msg`) on Windows. `PITWALL_NOTIFY=0` stays the
way to turn it off; the event log remains the source of truth either way.

## Edge cases to design for, not discover

- **Laptop sleep.** Neither launchd's `StartInterval` nor a loop process runs
  while suspended. Missed ticks are harmless: the watcher diffs a state file, so
  the first pass after wake reports what changed while it was asleep.
- **systemd user session.** Timers stop at logout unless
  `loginctl enable-linger $USER`; `service install` should say so rather than
  leave a watcher that dies when the desktop session does.
- **Upgrades move the venv.** Units embed an absolute interpreter path, so
  `service install` must be re-run after a Hermes upgrade — `service status`
  should detect a missing interpreter and say exactly that.
- **Overlap.** The one-shot model has no overlap guard; `--loop` gets one for
  free, and the Linux one-shot keeps the timer but gains the same lock.
- **Paths with spaces.** Windows (`C:\Users\First Last\…`) breaks naive command
  assembly: build `ProgramArguments` as a list, never a joined string.

## Testing

- Render each platform's unit from a fixed fake environment and assert it as
  text (plist through `plistlib`, task XML through `ElementTree`, the systemd
  unit by regex) — that runs on any OS.
- CI runs `service install --dry-run` on `ubuntu`, `macos` and `windows` and
  asserts the output parses.
- One manual smoke test per platform before release: install, force an event by
  hand, confirm it reaches the log and the desktop.

## Work items, in order

1. `watch --loop --interval` with the cross-platform lock (small).
2. `service` command: renderers + install/uninstall/status + `--dry-run` (medium).
3. Notification backends (small).
4. Tests and the CI dry-run matrix (small).
5. README/Platforms table flip from "not yet" to "yes", and keep the bash
   launcher for one release as the fallback (small).

Until then the README says what is true: the tools run everywhere, the watcher is
Linux, and any scheduler you already have can call `pitwall-agent watch --once
--react` on the other two.
