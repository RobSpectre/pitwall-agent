## 0.4.0

The watcher is multiplatform.

- **`pitwall-agent service {install,uninstall,status}`** — one command that puts
  the 20-second watcher on the machine's own scheduler: a `systemd --user`
  timer on Linux, a `launchd` agent on macOS, a logon task on Windows.
  `--dry-run --kind launchd|systemd|taskscheduler` prints the unit and runs
  nothing, which is also what CI checks.
- **`watch --loop --interval N`** — the process owns the clock, so macOS and
  Windows get a real 20-second cadence (Task Scheduler cannot go below a
  minute). A single-instance lock means a timer tick that lands during a loop
  is skipped rather than double-logged.
- **Notifications on all three** — `notify-send`, `osascript`, and a PowerShell
  toast; still best-effort and still off unless you ask for it.
- Units run the interpreter that ran `service install`, never `python3` from
  PATH, because under Hermes the right Python is a PM-managed venv.
- 13 new tests across the three renderers, the lock and the dry-run path.

# Changelog

## 0.3.0

Driver radio as text, honest degradation numbers, and a multiplatform manifest.

- **`pitwall_radio`** — MultiViewer's published AI transcription of team radio,
  read from its backend and cut at the playhead so a replay never shows radio
  from laps it has not reached. One car or a set (`driver="NOR,HAM"`), a plain
  list or a per-driver digest over the last N laps. The same path behind
  `pitwall-agent radio [--summary] [--laps N] [--driver X]` and
  `/pitwall radio [summary] [laps] [drivers]`.
- **Race intervals fixed** — the table reads `GapToLeader` /
  `IntervalToPositionAhead`, so intervals, DRS marks, `pitwall_battle` and every
  gap-based model work in a real race. Read through the practice field names
  they were silently empty for all 22 cars.
- **`pitwall_board` in a race** — position order and gap to the leader, instead
  of best-lap order and gap to pole.
- **Degradation honesty** — a fit counts as measured only with ≥ 6 laps, a
  positive slope and R² ≥ 0.20; otherwise the plan is priced on the book figure
  and says `book`. `pitwall_tyres` marks the rejected fits `~`.
- **Multiplatform** — `platforms: [linux, macos, windows]` in the manifest, a
  `screen` setting, and tiling reads `PITWALL_SCREEN` instead of assuming 4K.
- Three-OS CI (`ubuntu`, `macos`, `windows` × Python 3.11, 3.13) and the plan
  for launchd / Task Scheduler support in `docs/multiplatform-watcher.md`.

## 0.2.1

Baseline: practice, qualifying and race tool sets over MultiViewer's live
timing, video-player control, the deterministic watcher with incident framing,
and the `practice-session` / `qualifying-session` / `race-session` skills.
