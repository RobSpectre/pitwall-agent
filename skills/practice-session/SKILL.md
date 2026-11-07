---
name: practice-session
description: Run a practice broadcast on MultiViewer - pace, runs, and flyers.
version: 0.2.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [f1, motorsport, practice, fp1, fp2, fp3, broadcast, multiviewer, pitwall]
---

# Practice session

Free practice is not a competition, and calling it like one is the fastest way
to sound wrong. Nobody is "in contention for pole" in FP2. What practice tells
you is:

1. **Who is on a flying lap right now** — the cut-to list, exactly as in
   qualifying.
2. **What the pace actually is** — best lap per car, and the best lap of the
   run they are on.
3. **How the runs are structured** — how many laps on these tyres, and whether
   a car is on a qualifying simulation or a long run.

`/pitwall init` confirms MultiViewer, identifies the session, and resolves the
content id. In practice you get `pitwall_flying` and `pitwall_pace`; the
qualifying-only tools (`pitwall_pole`, `pitwall_purple`) and the race set are
hidden.

## The loop

```
pitwall_overview   ~90 tokens   clock, track status, latest race control
pitwall_board     ~1.2 KB       order, gaps, run state, sector colours
pitwall_pace       ~700 tokens  best lap, current run best, tyre age
pitwall_flying     ~350 tokens  who is on a flyer this second
```

`pitwall_board` is still the classification and still the fastest way to see
the whole field. `pitwall_pace` is the practice-specific read: it pairs each
car's best lap with the best lap of the run it is **currently** on, which is
how you tell a qualifying simulation from a long run.

Between sessions, or when the track is quiet, `pitwall_board(only_changes=true)`
is the cheap poll: it returns only new best laps and position changes.

## Reading the pace table

```
P  DRV  BEST      GAP     RUN BEST   LAPS ON TYRE  THEO
3  LEC  1:10.044  +0.168  1:12.201   H20           1:10.068
```

- `BEST` — best lap of the session for that car.
- `RUN BEST` — the best lap recorded on the current stint, straight from the
  feed's stint data. A run best far off the session best means a long run, not
  a mistake.
- `LAPS ON TYRE` — compound and age of the current run. `H20` is a hard on its
  20th lap: a race simulation, not a qualifying lap.
- `THEO` — that car's own best sectors summed. `BEST − THEO` is the time it has
  left on the table.

A qualifying simulation is short (2-4 laps) and its run best is close to
`BEST`. A long run is 10+ laps and 1-3 s slower. Say which one you are looking
at, and do not compare a long-run time to a qualifying time.

## Flying laps in practice

Installation laps and cool-downs are common, so `pitwall_flying` matters here
as much as in qualifying:

```
ON A FLYER NOW (2)  33:20 LEFT
LEC  P3   lap 18   S1 19.470 (-0.014)  S2 34.373 (-0.090)  S3 --  proj 1:09.858 (-0.018) vs fastest
```

`proj` is completed sectors plus the driver's own best sectors for the rest of
the lap — "on for a 1:09.8", never "has done". `proj -  (no clean reference
lap)` means his best sectors came from an outlap; say he is on a flyer without
quoting a time.

Practice `PUSH` means intent, not consequence. A car in P18 can be on a genuine
flyer. Do not call it a pole lap.

## Track evolution

The single most important thing practice tells you is the direction of the
track. Compare `pitwall_board` at 10:00 and 25:00 into the session: if the whole
field is a second quicker and nobody has changed tyres, that is rubber going
down, and the lap times are not comparable. Say "the track is coming to us"
rather than "everyone has found a second".

`pitwall_overview` gives the track temperature; a big swing explains a lot of
the order changing between runs.

## Players

`pitwall_player_open(driver="LEC")` resolves the content id itself and tiles the
layout. For a practice session:

- **DATA** on one window — the timing channel is the story in practice.
- Two or three onboards at most, on the cars currently on flyers.
- `pitwall_players` tells you if an old window is still on the previous
  session's content (`<- other session`).
- When the session ends, close what you do not need:
  `pitwall_player_control(player=6, action="close")`.

## Language

- Never say "pole" in practice. Say "fastest" or "quickest so far".
- Qualify every comparison with the tyre and run length: "on a 20-lap-old hard"
  is a different claim from "on a new soft".
- A tenth in practice means less than a tenth in qualifying. Say which session
  you are comparing to when you compare at all.
- If the session is less than five minutes old, say the reference is soft
  instead of ranking the field.

## When the data is missing

- `Error: MultiViewer is not reachable` — the app is closed; say so once.
- `Error: Live timing is idle` — the session has not started, or the replay is
  paused.
- `RUN BEST` or `LAPS ON TYRE` empty means no stint data yet (first minutes).
  Fall back to `pitwall_board` and report pace only.
- `replay archive: Generating` — this is a replay; do not say "now".
