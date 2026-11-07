# hermes-pitwall

An AI pit wall for [MultiViewer](https://multiviewer.app/): a Hermes Agent
plugin that reads F1 live timing and answers the three questions a qualifying
broadcast actually runs on.

- **Who is on a flying lap right now?** — the cut-to list, with live sector
  deltas and a projected lap time.
- **Who is setting purple sectors?** — session bests and holders per sector,
  who is purple on the lap in progress, and how much time each driver has left
  on the table.
- **Who is realistically in contention for pole?** — the window, unrealised
  pace, and whether the clock still allows an outlap plus a flyer.

## Why it exists

MultiViewer's live timing state is the full official F1 feed document: **about
75 KB mid-session**, or ~19,000 tokens if you hand it to a language model, and
mostly redundant. That is too much to re-read every ten seconds and too slow to
reason over during a lap.

`pitwall` parses it into a small model and renders tight, aligned text:

| call | what it answers | output |
|---|---|---|
| `pitwall_overview` | session, clock, pole, track, weather, latest flags | ~0.35 KB |
| `pitwall_board` | full order, gaps, run states, sector colours | ~1.2 KB |
| `pitwall_flying` | who is on a flyer, per-sector deltas, projection | ~0.35 KB |
| `pitwall_purple` | session best sectors, live purples, unrealised pace | ~0.25 KB |
| `pitwall_pole` | contention, unrealised pace, clock feasibility | ~0.4 KB |
| `pitwall_driver` | one car in detail | ~0.3 KB |
| `pitwall_raw` | escape hatch: section index, one section, one car | varies |
| `pitwall_show` | drive MultiViewer: onboard, data channel, sync | one line |

A full 22-car classification costs about **1.2 KB instead of 75 KB** — roughly a
60x reduction — while keeping thousandths, which is the unit the sport is
decided in.

## Install

Requires MultiViewer running with a session open (F1 TV Pro for live timing),
and Hermes Agent.

```bash
hermes plugins install <owner>/hermes-pitwall --yes-deps --enable
hermes gateway restart     # or start a new session
```

`--yes-deps` lets Hermes install `mvf1` into its own dependency environment.
Then, in a session:

```
/pitwall            full classification
/pitwall pole       pole watch
/pitwall flying     who is on a flyer
/pitwall LEC        one car
```

For development, symlink the checkout into the plugins directory and restart:

```bash
ln -s "$PWD" ~/.hermes/plugins/pitwall
hermes plugins doctor ~/.hermes/plugins/pitwall --ci
```

## The skill

`skills/qualifying-session/SKILL.md` is the operating manual: the polling loop,
how to read each line of output, the decision rules, the language to use on
air, and what to do when the feed is missing. Load it with
`skill_view("hermes-pitwall:qualifying-session")`.

## How the judgement works

Everything is derived from the feed; nothing is invented.

**Run state.** Sectors in the feed always describe the lap being run, so a car
with S1 filled is in sector 2 of a live lap. A lap is `PUSH` when it is within
+0.45 s of the driver's own best sectors after S1 (+0.90 s after S2) **and**
within +3.5 s / +6.0 s of the session best — the second test rejects
installation laps that look fast next to a slow personal reference. Past
+1.6 s / +2.4 s of his own pace it is `COOL`. MultiViewer's own `Outlap`,
`InPit`, `Stopped` and `Retired` flags always win over inference. Thresholds are
constants in `pitwall/model.py`.

**Purple.** The standing session best per sector comes from `TimingStats`
(best ever this session). A live `OverallFastest` flag is only reported as
purple if the time actually beats that best, so the tool never claims a purple
the viewer cannot see on screen.

**Projection.** Completed sectors plus that driver's own best sectors for the
rest of the lap — the standard broadcast "on for a 1:09.8", never presented as
a completed lap.

**Pole.** Contenders are inside a window (default 0.25 s). Unrealised pace is
`best lap − sum of the driver's own best sectors`, which finds the driver with
the pace and no clean lap. Clock feasibility is `2.05 × lap time` for outlap
plus flyer: under that, anyone in the pits is out of time, whatever their pace.

**Cheap polling.** Fetches are cached for one second so a burst of calls costs
one request, and `pitwall_board(only_changes=true)` diffs against the previous
snapshot and returns only new best laps, position changes and state changes —
often a single line.

## Layout

```
plugin.yaml          manifest: tools, python deps
__init__.py          Hermes entry (register)
pitwall/
  timefmt.py         M:SS.mmm <-> float seconds, gaps, clock
  model.py           feed -> Session/Driver, run-state classification
  render.py          compact text renderings
  feed.py            mvf1 client, 1 s cache, previous-snapshot memory
  tools.py           schemas + handlers
  plugin.py          register(): tools, /pitwall, skills
  fixtures.py        fixture generator
skills/qualifying-session/SKILL.md
tests/               63 tests
```

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest
```

`tests/fixtures/fp3_interlagos_real.json` is a real live-timing capture from
MultiViewer 2.15.2 (Interlagos FP3), used as the schema reference. The
qualifying fixtures are generated from it by `pitwall/fixtures.py`: the document
shape is real, the timing values are authored. That is deliberate — the wire
format is what breaks, and qualifying-only fields (`QualifyingPart`,
`KnockedOut`, `Cutoff`) cannot be produced on demand from an offline replay.
Regenerate with `python -m pitwall.fixtures`.

## Known constraints

- **`mvf1>=2.1,<3`.** Installed by `hermes plugins install --yes-deps` into the
  dependency environment Hermes selects, not into `hermes-agent/venv`. mvf1 2.1
  needs `requests>=2.34.2`, which the PM-built environment carries; an older
  Hermes that pins `requests==2.33.0` would need the 2.0 line instead — the
  player sync action falls back to `player.sync()` when
  `player_sync_to_live_timing()` is absent.
- Qualifying has been exercised against schema-accurate synthetic fixtures, not
  yet against a live Q1/Q2/Q3 session. Practice and session-start behaviour is
  verified against the real capture.
- MultiViewer's GraphQL API cannot select a session; the plugin reads whatever
  session the app currently has open.

## License

MIT.
