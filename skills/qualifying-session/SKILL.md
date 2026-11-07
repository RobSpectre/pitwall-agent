---
name: qualifying-session
description: Run a qualifying broadcast on MultiViewer - flyers, purple sectors, pole fight.
version: 0.1.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [f1, motorsport, qualifying, broadcast, multiviewer, pitwall]
---

# Qualifying session

Live timing at a keyboard, with a viewer watching eight feeds at once. The job
is not to summarise the session — it is to answer three questions, in this
order, and answer them faster than the graphics on screen:

1. **Who is on a flying lap right now?** (what to cut to)
2. **Who is setting purple sectors?** (what changed)
3. **Who is realistically in contention for pole?** (what it means)

Everything below assumes the `pitwall_*` tools are available. They read
MultiViewer's live timing state and return compact text — the whole 22-car
field for about 1.2 KB, instead of the 75 KB raw document.

## The 60-second loop

```
pitwall_overview          ~90 tokens   session, clock, pole, track, flags
pitwall_board             ~300 tokens  full order, gaps, run state
pitwall_flying            ~100 tokens  who is on a flyer, with projection
pitwall_pole              ~130 tokens  contention + clock feasibility
```

During a live Q1/Q2/Q3:

- **Start every turn with `pitwall_overview`.** Track status, remaining time and
  the latest race-control message change what you can say. Never answer a
  pole question without knowing the clock.
- **Use `pitwall_board` for anything order-related**, and
  `pitwall_board(only_changes=true)` when you are polling every 10-20 seconds.
  It returns only the drivers whose best lap, position or run state moved —
  often one line, sometimes nothing.
- **`pitwall_flying` is the cut-to list.** If a flyer is inside a tenth of pole
  in projection, say so before the lap is complete.
- **`pitwall_purple` when a sector goes purple**, and to answer "who has the
  pace they haven't used yet".
- **`pitwall_driver(code)` for one car** — sector PBs, live deltas, stints,
  speeds. Use it when the viewer asks about a specific driver.

Do not call all five every turn. Overview, then the one or two that answer the
question. Repeated calls inside the same second are cached, so a burst is cheap
in latency but not in context.

## Reading the board

```
P  DRV  BEST      GAP     LAPS  RUN    S1 S2 S3
3  LEC  1:10.044  +0.168  L17   PUSH   G P -
```

- `BEST` — best lap of the session for that car; `-` until a lap is completed.
- `GAP` — to pole, always three decimals. This is the number that matters.
- `LAPS` — laps completed.
- `RUN` — the live state of the lap being run, see below.
- `S1 S2 S3` — colours of the sectors *on the lap being run*: `P` purple
  (fastest in session), `G` green (personal best), `-` nothing yet.
- Trailing marks: `X` eliminated, `!` in the drop zone, `R` retired,
  `S` stopped. A legend line appears only when a mark is present.

### RUN states

| state | meaning |
|---|---|
| `PUSH` | on a flying lap: within a few hundredths of his own best sectors and within sight of session pace |
| `BUILD` | on a lap but off his own best pace, or the lap is nowhere near session pace |
| `COOL` | deliberately slow (in-lap prep, cool-down) |
| `LAP` | on a lap, nothing to judge yet — or the lap just completed |
| `OUT` | outlap, from MultiViewer's own flag |
| `PIT` | in the pit lane or garage |
| `STOPPED` / `RETIRED` | stopped on track / out |
| `DONE` | took the chequered flag |

`PUSH` means **intent plus execution**, not absolute pace: a slow car can be on
a flyer. If the session is two minutes old the reference is soft and everything
competitive looks like a push — say "early in the session" rather than
"contending for pole".

## Question 1 — who is on a flying lap

`pitwall_flying`:

```
ON A FLYER NOW (2)  3:20 LEFT   (delta vs own best sector)
LEC  P3   lap 18   S1 19.470 (-0.014)  S2 34.373 (-0.090)  S3 --  proj 1:09.858 (-0.018) vs pole
PIA  P4   lap 15   S1 19.475 (-0.021)  S2 --  S3 --  proj 1:09.982 (+0.106) vs pole
```

- `-0.014` is **ahead** of that driver's own best sector in that position. A
  negative delta in S2 is the real signal: the car is improving on its own best
  lap where it matters.
- `proj` is the completed sectors plus that driver's own best sectors for the
  rest of the lap. It is a projection, not a lap time — phrase it as
  "on for a 1:09.8", never "has done a 1:09.8".
- Where `proj` versus pole is **negative**, the car is on for pole. That is the
  line to say out loud.
- `proj -  (no clean reference lap)` means the driver's best sectors came from
  an outlap or a lap with a stop, so no projection is honest. Say he is on a
  flyer without quoting a time.
- An empty list plus `next out: RUS, ALO` means the track is between runs;
  the next flyer is one outlap away (roughly 105 % of a lap time).

Thresholds live in `pitwall/model.py`: a lap stays `PUSH` within +0.45 s of the
driver's own best sectors after S1 and +0.90 s after S2, **and** within +3.5 s /
+6.0 s of the session best. Beyond that, `BUILD`. Past +1.6 s / +2.4 s of his
own pace it is `COOL`.

## Question 2 — purple sectors

`pitwall_purple`:

```
PURPLE  theoretical 1:09.804  3:20 LEFT
S1 19.447 VER  S2 34.373 LEC  S3 15.984 VER
purple on the live lap: LEC S2 34.373 (push)
time left on the table (own best sectors): PIA 0.099; SAI 0.099
```

- The first line is the **session best in each sector** and its holder. That is
  what the purple column on the timing tower means.
- `purple on the live lap` shows sectors set on the lap in progress — a flag
  only survives if the time really beats the standing best, so it is safe to
  call it purple on air.
- **Theoretical best** (first line, `1:09.804`) is the sum of the three session
  best sectors. Nobody has driven it; it is the floor the session is approaching.
- **Time left on the table** is `best lap − sum of that driver's own best
  sectors`. A big number means a driver with pace but no clean lap — the
  classic "he's got the pace, he just hasn't hooked it up" line. Order by that
  number, not by the board.

Purple means fastest **in this session**. Never call a sector purple because a
driver improved; green (`G`) is a personal best.

## Question 3 — pole contention

`pitwall_pole`:

```
POLE WATCH  pole 1:09.876 VER  2:00 LEFT
CONTENDERS (within 0.250 of pole)
1  VER  1:09.876 +0.000  theo 1:09.828 (-0.048 unrealised)
3  LEC  1:10.044 +0.168  theo 1:09.962 (-0.082 unrealised)
on a flyer now: LEC proj 1:09.858 (-0.018); PIA proj 1:09.982 (+0.106)
clock: 120s vs 143s needed for outlap+flyer -- anyone in the pits is out of time
```

Read it in this order:

1. **Who is in the window** (default 0.25 s). Widen with `tol=0.5` early in Q3
   when nobody has shown their hand.
2. **Unrealised pace** — `theo` below the driver's best lap means the lap is
   there. Someone 0.3 s back with 0.4 s unrealised is more dangerous than
   someone 0.15 s back with nothing left.
3. **Who is on a flyer now**, with the projected gap to pole.
4. **The clock.** `2.05 × lap time` is outlap plus flyer. If the remaining time
   is under that, anyone in the pits cannot take pole, whatever their pace. Say
   it plainly — that is the single most useful call in the last two minutes.
   Under `0.75 × lap time`, only a lap already in progress can count.

For the drop zone in Q1/Q2, use the board markers: `!` marks cars at or below
the cutoff (P15 in Q1, P10 in Q2 — `cutoff_pos` in the model), `X` marks cars
already eliminated. Read the bubble, not just the leader: "BEA is 0.043 from
the drop zone with one lap left".

## Directing the broadcast

`pitwall_show` drives MultiViewer itself:

- `pitwall_show(driver="LEC")` — switch the first open player to that onboard.
- `pitwall_show(driver="LEC", player=4)` — a specific player.
- `pitwall_show(action="data")` — put a player on the live timing data channel.
- `pitwall_show(action="sync")` — re-align every open player to live timing.

Cut **to** the car on the flyer only when it is relevant: on for pole, on for
the drop-zone escape, or a personal best in sector 1 of a lap that started
poorly. Do not chase every `PUSH` — there are usually three or four.

Prefer one action per response. A broadcast operator can only look at one thing.

## Judgement, not narration

- Times to three decimals when quoting a lap, one decimal when speaking
  ("three tenths", not "0.312 seconds"). A tenth is the unit of the sport.
- Never invent a gap. If `BEST` is `-`, the driver has no time; say that.
- Distinguish **on for** (projection), **has done** (best lap) and **could do**
  (theoretical). They are the three different numbers on the screen.
- If a driver's best lap was deleted, race control says so; the board does not
  know. `pitwall_overview(messages=8)` or
  `pitwall_raw(sections=["RaceControlMessages"])` for the detail.
- Session part matters: in Q1 one banker lap can be enough; in Q3 every lap is
  a qualifying lap and track position is the story.

## When the data is missing

- `Error: MultiViewer is not reachable ...` — the app is closed or the GraphQL
  port moved. Tell the operator; do not retry in a loop.
- `Error: Live timing is idle ...` — the session has not started or the replay
  is not running.
- `replay archive: Generating` in the overview means MultiViewer is serving a
  replay/archive, not a live session. Fine to work from, but do not tell the
  viewer something is happening "now" when it happened in a replay.
- `TimingStats.Withheld` — the feed is withholding sector data (sometimes true
  at the very start). Pace columns will be empty; lean on positions and the
  clock and say the sector data is not up yet.

## A worked turn

Operator: "who's quick?"

```
pitwall_overview        -> Q3, 3:20 left, all clear, pole 1:09.876 VER
pitwall_flying          -> LEC on for 1:09.858, PIA on for 1:09.982
pitwall_pole            -> 4 within 0.25; LEC 0.168 back with 0.082 unrealised
```

Answer: "Leclerc is on one now — 19.4 and a purple 34.373 in sector two, on for
a 1:09.8, two hundredths under Verstappen's pole. Piastri is a tenth off that on
his own flyer. Verstappen and Norris are both in the garage with 3:20 left, so
they have time for one more run."

Then `pitwall_show(driver="LEC")` if the operator wants the onboard.

## Reference

- Tool schemas: `pitwall/tools.py`. Run-state thresholds and pole clock rule:
  `pitwall/model.py`. Output formats: `pitwall/render.py`.
- The raw document is always available via `pitwall_raw` — an index with no
  arguments, one section or one car otherwise. Use it before concluding that a
  field does not exist.
