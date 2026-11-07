---
name: race-session
description: Run a race or sprint broadcast on MultiViewer - order, battles, strategy.
version: 0.2.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [f1, motorsport, race, sprint, broadcast, multiviewer, pitwall]
---

# Race session

A grand prix or a sprint. The clock is not the story any more — the running
order, the gaps, and what the tyres are about to do are. A race call has four
layers and they always come in this order:

1. **Flag state** — safety car, yellow, red. Nothing else matters while one is
   live.
2. **Penalties and investigations** — a position is provisional until the
   stewards are done.
3. **Battles and DRS** — where the moves are happening.
4. **Strategy** — who is about to pit, who is committed, who has to defend.

`/pitwall init` confirms MultiViewer is up, identifies the session, and
resolves the content id. In a race, `pitwall_race`, `pitwall_battle` and
`pitwall_strategy` are the tools on offer; the lap-time set is hidden.

## The loop

```
pitwall_overview    ~90 tokens   clock, laps, track status, latest race control
pitwall_race       ~1.4 KB       order, interval, last lap, tyre, stops, status
pitwall_battle      ~200 tokens  every pair inside a second
pitwall_strategy    ~500 tokens  tyre runs, stops, laps remaining
```

Every turn starts with `pitwall_overview`: the track status and race-control
messages decide what you say before any of the timing does. Then pick the two
that answer the question. `pitwall_board(only_changes=true)` still works and is
the cheapest way to see position changes, but `pitwall_race` carries the tyres,
which the board does not.

## Reading the race table

```
Race  LAP 32/57  São Paulo Grand Prix / Interlagos  52:10 LEFT  STARTED
track ALL CLEAR  air 31.3C  track 47.9C  wind 1.7m/s
P  DRV  INT      LAST       TYRE  STOPS  STATUS
1  VER  -        1:12.410   M14   1
2  NOR  +4.212   1:12.388   M14   1
3  LEC  +6.901   1:12.201   H20   1
4  PIA  +0.412   1:12.150   H20   1      *
```

- `INT` — interval **to the car ahead**, always three decimals. The leader is
  `-`. For the gap to the leader use `pitwall_board` (its `GAP` column).
- `LAST` — the last completed lap time.
- `TYRE` — compound letter and **age in laps**: `M14` is a medium on its 14th
  lap. `S7` a soft on its 7th.
- `STOPS` — stops completed, inferred from the number of tyre runs.
- `STATUS` — blank while racing. `PIT`, `OUT`, `STOPPED`, `RETIRED`, `DONE`
  only appear when they mean something.
- `*` — the car behind is inside one second, i.e. DRS range.
- Trailing `R` retired, `S` stopped.

## Battles

`pitwall_battle` lists every pair inside the window, closest first:

```
P3 LEC H20 vs P4 PIA H20  gap +0.412 DRS  PIA faster on the last lap (0.051)
```

That `faster on the last lap` phrase is the whole point: a car 0.4 s behind and
0.05 s a lap quicker is a move waiting to happen; a car 0.4 s behind and 0.3 s a
lap slower is holding station. Use `window=2.0` to see what is forming behind
the immediate fight.

Do not read DRS as a pass. It is a *tool*: say "in DRS range", not "will pass".
A car that has been inside a second for three laps without a move is being held.

## Strategy

```
STRATEGY   Race  LAP 32/57  ...  25 laps remaining
P  DRV  NOW    STOPS  RUNS
3  LEC  H20    1      M11 H20
7  ALO  M14    1      S17 M14  last stop 21.9s
oldest tyres: LEC on 20 laps (hard)
```

- `RUNS` is every stint in order, oldest first, each with its age at the end of
  that run. `M11 H20` means an 11-lap medium then a hard now 20 laps old.
- `oldest tyres` is the car most exposed to a stop. That is who the broadcast
  watches when the pit window opens.
- Laps remaining comes from `LapCount`, so it is exact, not estimated.
- There is no degradation model here. Do not invent a pit lap. Report tyre age,
  stops made, laps left, and the gap to the car behind — the viewer can see the
  rest.

An undercut is only a story when the gap is small enough to be covered by a
stop: roughly 20-23 seconds on most circuits. Say "the undercut is live" only
when the gap is inside about 21 s and the car ahead has not stopped.

## Flags and penalties

Track status is the first line of every output. When it is not green:

- **Yellow** — quote the sector and the message from race control. Who is in
  the sector? Do not talk about battles elsewhere.
- **Safety car / VSC** — the gaps are frozen by the queue, not earned. Say so,
  and switch the story to who pits.
- **Red** — the classification stands as it is; report positions as provisional
  and go to the incident.

**The watchdog already does the first move for you.** On a flag it ranks the
cars that are not running (off track, then `Stopped`, then coordinates that have
not moved), syncs the output to the world feed if it has drifted, full-screens
the best candidate's onboard and seeks back 30 s. Check
`pitwall-agent watch --recent 5` for the line it logged — it names the driver,
the player and whether it had to re-sync. If it says
`live stream: MultiViewer refuses to rewind`, the onboard is up but the stream
cannot be walked back through the API; scrub in the app instead, and say on air
that you are going to the replay.

Penalties arrive as race-control messages ("CAR 63 (RUS) 5 SECOND TIME PENALTY
- TRACK LIMITS"). They are **not** applied to `pitwall_race` until the stewards
execute them. Whenever one is live, the position is provisional — say
"provisional" out loud. `pitwall_overview(messages=8)` gives the detail.

## Players

`pitwall_player_open` creates a window and resolves the session content id from
MultiViewer's meetings API — it never guesses. `pitwall_players` shows what is
open, its box, and whether it is still on this session's content id (a player
left over from qualifying is flagged `<- other session`).

For a race:
- **International** on the main screen; **DATA** on a second.
- Onboards only for the fight, and only one or two. Six onboard windows is not
  a broadcast.
- `pitwall_player_control(action="tile", screen="3840x2160", side="right")` lays
  everything out in a grid. Do this once, not on every call.
- Replay an incident with `action="seek"` and `value=-8`, then **return to
  live** with `action="live"`. Never leave the broadcast on replay — say when
  you go back and confirm it.

Player actions are non-destructive by design: `pause`, `seek`, `mute`,
`fullscreen`, `close`. Nothing terminates a stream.

## Language

- Intervals to three decimals, spoken as tenths: "four tenths behind".
- Name the tyre and its age together: "a 20-lap-old hard", never "old tyres".
- Distinguish **gap** (to the car ahead) from **lead** (to the leader) — they
  are different numbers and the board shows the second one.
- Lead with the flag, then the fight, then the strategy. Never reverse that.

## When the data is missing

- `Error: MultiViewer is not reachable` — the app is closed. Say so once.
- `Error: Live timing is idle` — the session has not started or the replay is
  not running.
- Empty `TYRE` / `STOPS` columns mean `TimingAppData` has not populated yet
  (usually the first lap). Report the order and gaps; leave the strategy alone
  until it arrives.
- `replay archive: Generating` means you are on a replay. Do not say "now".
