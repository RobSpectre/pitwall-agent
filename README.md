# pitwall-agent

An AI pit wall for [MultiViewer](https://multiviewer.app/): compact F1
live-timing reads and video-player control, sized for an agent that has to make
a call in the ten seconds before a lap ends.

Works three ways from one codebase:

- **Hermes plugin** — tools, `/pitwall` command, session-type skills.
- **CLI** — `pitwall-agent board`, for any agent that can run a command.
- **MCP server** — `pitwall-agent mcp`, for LangChain / Claude / any MCP client.

## What it answers

The session type decides the tool set. `/pitwall init` confirms MultiViewer is
running, identifies the session, resolves the F1 TV content id, and says which
set you have.

**Practice and qualifying** — lap time is the story:

| tool | answer | output |
|---|---|---|
| `pitwall_overview` | session, clock, pole, track, weather, latest flags | ~0.35 KB |
| `pitwall_board` | full order, gaps, run states, sector colours | ~1.2 KB |
| `pitwall_flying` | who is on a flyer, per-sector deltas, projection | ~0.35 KB |
| `pitwall_pace` | best lap vs the lap of the run they are on | ~0.7 KB |
| `pitwall_purple` | session best sectors, live purples, unrealised pace | ~0.25 KB |
| `pitwall_pole` | contention, unrealised pace, clock feasibility | ~0.4 KB |

**Races and sprints** — order, fights and tyres:

| tool | answer | output |
|---|---|---|
| `pitwall_race` | interval, last lap, tyre and age, stops, status | ~1.4 KB |
| `pitwall_battle` | every pair inside a second, with the last-lap trend | ~0.2 KB |
| `pitwall_strategy` | tyre runs, stop count, laps remaining, oldest rubber | ~0.5 KB |
| `pitwall_tyres` | fitted degradation per car on its current set | ~0.5 KB |
| `pitwall_fuel` | last lap corrected to an empty tank, and the gap | ~0.4 KB |
| `pitwall_overtake` | pairs inside a window, pace delta, projected pass lap | ~0.4 KB |
| `pitwall_pitplan` | ranked one- and two-stop plans from here to the flag | ~0.4 KB |
| `pitwall_undercut` | pairs who can win a place in the pit lane, and the laps of cover | ~0.4 KB |
| `pitwall_windows` | laps left on each set, the last lap to change it, who cannot reach the flag | ~0.5 KB |
| `pitwall_outlook` | projected finishing order from here, and the places it moves | ~0.4 KB |
| `pitwall_radio` | driver radio as text, newest last, or a per-driver digest over N laps | ~0.3 KB |

**Any session**:

| tool | answer |
|---|---|
| `pitwall_driver` | one car in detail: sector PBs, live deltas, stints, speeds |
| `pitwall_players` | open windows, boxes, and whether they are on this session |
| `pitwall_player_open` | create a player (driver onboard / DATA / INTERNATIONAL) |
| `pitwall_player_control` | sync, live, pause, mute, seek, close, tile |
| `pitwall_raw` | escape hatch: section index, one section, one car as JSON |

## Why it exists

MultiViewer's live timing state is the full official F1 feed document: **about
75 KB mid-session**, or ~19,000 tokens if you hand it to a language model, and
mostly redundant. That is too much to re-read every ten seconds and too slow to
reason over during a lap.

A full 22-car classification costs about **1.2 KB instead of 75 KB** — roughly a
60x reduction — while keeping thousandths, which is the unit the sport is
decided in.

## Install

Requires MultiViewer running with a session open (F1 TV Pro for live timing).

```bash
hermes plugins install <owner>/pitwall-agent --enable
hermes gateway restart     # a running process never switches dependency environments
```

`mvf1` comes from the plugin's own `python_dependencies`, so Hermes installs it
into the dependency environment it selected. An installed plugin only upgrades
when its declared dependency floor changes — hence the `>=2.2` bump below.

## Platforms

The plugin, the CLI and the MCP server are pure Python 3.11+ and speak only to
MultiViewer's local GraphQL, so they run wherever MultiViewer runs.

| | Linux | macOS | Windows |
|---|---|---|---|
| tools, `/pitwall`, CLI, MCP | yes | yes | yes |
| 20-second watcher | `service install` → systemd | `service install` → launchd | `service install` → Task Scheduler |
| desktop notification on an event | `notify-send` | `osascript` | PowerShell toast |

One command puts the watcher on whichever scheduler the machine has
(`pitwall-agent service install`); the platforms differ in how it is held, not
in what it does.

Then, in a Hermes session:

```
/pitwall init       MultiViewer, session type, content id, which tools apply
/pitwall            classification
/pitwall race       race order with tyres and stops
/pitwall radio      the last team-radio calls, as text
/pitwall radio summary 6 NOR HAM   per-driver digest over the last six laps
/pitwall flying     who is on a flyer
/pitwall players    open windows and the resolved content id
/pitwall open LEC   new onboard, tiled
/pitwall LEC        one car
```

Outside Hermes:

```bash
pip install .                 # or: uv pip install -e ".[dev]"
pitwall-agent init
pitwall-agent race --limit 10
pitwall-agent radio --summary --laps 4 --driver NOR,HAM
pitwall-agent open LEC --side right --screen 3840x2160
pitwall-agent mcp             # stdio MCP server
```

Player tiling lays windows out on the display you tell it about:
`--screen 1728x1117`, or once per machine via `PITWALL_SCREEN`. The fallback is
a 4K desktop, and MultiViewer's GraphQL does not publish a display size to
derive a better one.

Point an MCP client at it the same way the notebook points at `mvf1-cli mcp`:

```json
{"mcpServers": {"pitwall": {"command": "pitwall-agent", "args": ["mcp"]}}}
```

## The two-legged contentId

MultiViewer will not create a player without a numeric F1 TV content id, and it
never guesses one. There are two legs:

1. **Live timing** carries `SessionInfo.Meeting.Key` and `SessionInfo.Key`.
2. **`api.multiviewer.app/api/v1/meetings/<key>`** returns every session of that
   meeting with its `f1tv_content_id`.

The join is on `session_key`, which is exact — Practice 3's `session_key` is
`11407`, and so is live timing's `SessionInfo.Key`. A title match exists only as
a fallback and is reported as `matched on title`, so you always know how
trustworthy the id is. Meetings are cached for 15 minutes.

## How the judgement works

Everything is derived from the feed; nothing is invented.

**Run state.** Sectors in the feed always describe the lap being run, so a car
with S1 filled is in sector 2 of a live lap. A lap is `PUSH` when it is within
+0.45 s of the driver's own best sectors after S1 (+0.90 s after S2) **and**
within +3.5 s / +6.0 s of the session best — the second test rejects
installation laps that look fast next to a slow personal reference. Past
+1.6 s / +2.4 s of his own pace it is `COOL`. MultiViewer's own `Outlap`,
`InPit`, `Stopped` and `Retired` flags always win over inference.

**Purple.** The standing session best per sector comes from `TimingStats`. A
live `OverallFastest` flag is only reported as purple if the time actually
beats that best, so the tool never claims a purple the viewer cannot see.

**Projection.** Completed sectors plus that driver's own best sectors for the
rest of the lap — the standard "on for a 1:09.8", never a completed lap. When
those best sectors came from an outlap the projection is suppressed rather than
printed as nonsense.

**Pole.** Contenders inside a window (default 0.25 s); unrealised pace is
`best lap − sum of the driver's own best sectors`; clock feasibility is
`2.05 × lap time` for outlap plus flyer.

**Race.** Tyre age is `TotalLaps − StartLaps` from `TimingAppData`; stops are
inferred from the number of tyre runs; laps remaining is exact from `LapCount`.
Degradation is a least-squares fit on that car's own fuel-corrected laps on its
current set, and it is only called **measured** with six or more laps, a
positive slope and an R² of at least 0.20 — otherwise the plan is priced on the
book figure and labelled `book`. Pit plans enumerate stop laps against the flag
and rank them on total race time, so a "target lap" is an optimum, not a guess.

**Radio.** MultiViewer publishes an AI transcription of each team-radio clip;
the tool reads it from the same backend the app uses, cuts it at the playhead so
a replay cannot show radio from laps it has not reached, and prints it as text.
No audio is downloaded and no speech-to-text runs here.

**Cheap polling.** Fetches are cached for one second so a burst of calls costs
one request, and `pitwall_board(only_changes=true)` diffs against the previous
snapshot — often a single line.

**Session gating.** A tool whose modes exclude the current session reports
itself unavailable through `check_fn`, decided once per session key. When the
session cannot be identified every tool is offered, with an error rather than
silence.

## Watch mode — no model in the loop

A broadcast needs to know when something *happens*, not to re-read the board
every twenty seconds. `pitwall-agent watch --once` is a deterministic pass:

```bash
pitwall-agent watch --once            # prints only what is new, silence otherwise
pitwall-agent watch --once --notify   # also raises a desktop notification
pitwall-agent watch --recent 20       # the last 20 logged events
```

It stores what it saw in `~/.hermes/state/pitwall-watch.json`, logs events to
`~/.hermes/state/pitwall-events.log`, and diffs against that state. Running it
twice reports nothing the second time. A new session key re-baselines silently
rather than dumping the whole field.

What counts as an event:

| level | events |
|---|---|
| `CRIT` | track status change, session aborted/finished, a driver eliminated, clock marks at 5:00 / 2:00 / 0:30 |
| `HIGH` | deleted lap, penalty or investigation, new pole, purple sector, car into the drop zone |
| `MED` | contender personal best, out of the drop zone |

Noise control is deliberate: personal bests only for the top five or anyone
within half a second of pole, and the drop zone only for the cars around the
cutoff. Everything else stays in the state file.

One command installs it, on whichever scheduler the machine has:

```bash
pitwall-agent service install        # systemd, launchd or Task Scheduler
pitwall-agent service status
pitwall-agent service uninstall
pitwall-agent service install --dry-run --kind launchd   # print the unit, write nothing
```

| platform | what gets installed | how the cadence is held |
|---|---|---|
| Linux | `~/.config/systemd/user/pitwall-watch.{service,timer}` | one pass every 20 s |
| macOS | `~/Library/LaunchAgents/io.pitwall.watch.plist` | a kept-alive `watch --loop --interval 20` |
| Windows | a Task Scheduler task, triggered at logon | a kept-alive `watch --loop --interval 20` |

Linux keeps the one-shot-per-tick model. macOS and Windows run a kept-alive
process instead, because launchd's `StartInterval` is wall-clock and Task
Scheduler cannot go below a minute. Every unit runs the interpreter that ran
`service install` — re-run it after a Hermes upgrade moves that venv.
`watch --loop` holds a single-instance lock, and a timer tick that lands while a
loop is running is skipped rather than double-logged. `hermes`'s own cron ticks
once a minute, which is why the cadence needs its own scheduler.

`deploy/` still holds the original bash launcher and units for anyone who would
rather copy them by hand.

## Incident reaction

On a yellow or red flag the watchdog can act by itself — `watch --once --react`,
which the systemd launcher passes:

1. rank the cars that are not running: `Position` status `OffTrack` first, then
   MultiViewer's decoded `Stopped`, then coordinates unchanged between two
   position frames;
2. skip anything in the pit lane or retired — a car in the garage is not the
   incident;
3. sync the output to the world feed if it has drifted more than a second. A
   full-screen onboard two seconds behind the commentary is worse than no
   onboard — on a live check the driver windows sat 2.2 s behind
   `INTERNATIONAL`;
4. full-screen that driver's onboard, reusing a player that already shows him
   or creating one;
5. seek back 30 s, so the cause is on screen instead of the aftermath;
6. when the track goes green, release full screen and return the player to live.

Sync works on live players (`playerSync` is accepted); only the seek is
refused.

Latency is the timer interval: at most 20 s from the flag appearing in the
feed. Run it by hand with `pitwall-agent incident --dry-run` to see the
ranking without touching a player.

**The rewind only works off the live edge.** MultiViewer answers
`playerSeekTo` with `Cannot seek in live mode` for live content — it refuses
even when the player is paused and reports `live: false`, so the guard is on the
stream, and no mutation in the API clears it. The UI's scrubber works because
the renderer keeps its own timeshift buffer, which is not exposed over GraphQL.
Pause *does* work, so the routine pauses and confirms it took:

    LEC stopped -> player 26 fullscreen (existing player; synced to
    INTERNATIONAL) (live stream: rewind blocked by MultiViewer; player paused
    to scrub)

That leaves the frame stopped at the incident for a manual scrub, and green
resumes and re-syncs the player. On an archive or replay session the seek lands
and the 30 s rewind happens by itself.

## Layout

```
plugin.yaml          manifest: tools, python deps
__init__.py          Hermes entry (register)
pitwall/
  timefmt.py         M:SS.mmm <-> float seconds, gaps, clock
  model.py           feed -> Session/Driver, run-state classification
  meeting.py         meetings API, content id, session-type mode
  players.py         list/create/place/sync players, tiling
  render.py          compact text renderings
  feed.py            mvf1 client, 1 s cache, previous-snapshot memory
  tools.py           schemas + handlers + mode gating
  plugin.py          register(): tools, /pitwall, /pitwall init, skills
  cli.py             pitwall-agent command line
  mcp.py             fastmcp server for other agents
  watch.py           deterministic change detection (no model)
  history.py         rolling per-lap timing store (the feed only has the current lap)
  race.py            degradation, fuel correction, overtake and pit-plan models
  fixtures.py        fixture generator
deploy/              systemd service + 20s timer + launcher
skills/
  practice-session/
  qualifying-session/
  race-session/
tests/               137 tests
```

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest
```

`tests/fixtures/fp3_interlagos_real.json` is a real live-timing capture from
MultiViewer 2.15.2 (Interlagos FP3), used as the schema reference. The
qualifying, race and sprint fixtures are generated from it by
`pitwall/fixtures.py`: the document shape is real, the timing values are
authored. That is deliberate — the wire format is what breaks, and
session-only fields (`QualifyingPart`, `KnockedOut`, `Cutoff`, `LapCount`)
cannot be produced on demand from an offline replay. Regenerate with
`python -m pitwall.fixtures`.

## Known constraints

- **`mvf1>=2.2,<3`.** Declared in `plugin.yaml`, so Hermes installs it into the
  dependency environment it selects, not into `hermes-agent/venv`. 2.2 needs
  `requests>=2.34.2` and `click>=8.5.1`; both are satisfied in the PM-built
  environment. 2.2 also types `seek()` as float (fractional offsets) and gives
  `set_always_on_top()` a window `level`, both of which this plugin uses; on an
  older Hermes that pins `requests==2.33.0`, stay on the 2.0 line — the player
  sync action falls back to `player.sync()` when `player_sync_to_live_timing()`
  is absent.
- Qualifying and race behaviour is exercised against schema-accurate synthetic
  fixtures, not yet against a live session. Practice and session-start
  behaviour is verified against the real capture.
- MultiViewer's GraphQL API cannot select a session; the plugin reads whatever
  session the app currently has open.

## License

MIT.
