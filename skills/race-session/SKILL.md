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
resolves the content id. In a race, `pitwall_race`, `pitwall_battle`,
`pitwall_strategy`, `pitwall_tyres`, `pitwall_fuel`, `pitwall_overtake`,
`pitwall_pitplan` and `pitwall_radio` are the tools on offer; the lap-time set
is hidden.

## The loop

```
pitwall_overview    ~90 tokens   clock, laps, track status, latest race control
pitwall_race       ~1.4 KB       order, interval, last lap, tyre, stops, status
pitwall_battle      ~200 tokens  every pair inside a second
pitwall_strategy    ~500 tokens  tyre runs, stops, laps remaining
pitwall_tyres       ~500 tokens  fitted degradation per car, worst first
pitwall_fuel        ~400 tokens  the last lap with the fuel load removed
pitwall_overtake    ~400 tokens  pairs in range, pace delta, projected pass lap
pitwall_pitplan     ~400 tokens  ranked stop plans from here to the flag
pitwall_undercut    ~400 tokens  who can win a place in the pit lane
pitwall_radio       ~300 tokens  team radio as text, cut at the playhead
pitwall_windows     ~500 tokens  laps left on each set against laps to the flag
pitwall_outlook     ~400 tokens  projected finishing order from here
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

## Radio

`pitwall_radio` is MultiViewer's own transcription of the team-radio clips, not
ours — the words arrive already written, a second or two behind the audio, and
the tool never invents a line. It prints the time, the driver, and the quote:

```
RADIO  Race  LAP 12/71  Mexico City GP / Mexico City  last 3 of 376  cut at 20:12:03
20:11:54  RUS  "I suggest we introduce lift and coast turn 12."
20:11:58  LIN  "Make sure we confirm the lift-off and make sure we keep looking at the tyres."
```

The endpoint returns the whole session at once, so the tool cuts the list at the
playhead: on a replay you see only what the feed has already reached. Read the
`cut at` stamp — if it is minutes behind the wall clock, the feed is behind, not
the radio. Filter with `driver=NOR` when you want one car.

For the conversation rather than the last few calls, ask for the summary:
`pitwall_radio(summary=true, laps=4)`, `/pitwall radio summary`, or
`pitwall-agent radio --summary`. It returns every driver who spoke in the last
four laps, busiest first, with call counts, airtime and their latest lines:

```
RADIO SUMMARY  Race  LAP 8/71  Mexico City GP / Mexico City  laps 5-8  cut at 20:14:49
10 drivers, 112 calls, 421.2s of radio, busiest first
ALB  20 calls  70.3s
  20:14:34  "Oscar, you're strong, turns 1 to 3."
  +17 earlier
```

That is selection and counting, not prose: the plugin has no model, so the
sentences are yours to write from the digest. `laps=` widens the window, and a
driver with nine calls shows three — the "+N earlier" is how you know there is
more without reading it.

When someone asks "what were they saying", summarise: one or two sentences per
driver who spoke, in their own terms, and no pasted quotes unless a single line
is the story. A digest read aloud is not a summary, and five verbatim quotes in
a row is a transcript.

Bracketed fragments like `[D5?]` are the transcriber's uncertainty. Say "he is
asking for lift and coast", never read the brackets out. Most of the feed is
"Radio check" and "copy", and a quote is what the team heard, not a fact.

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

## The models — and what they assume

Four tools go beyond what the feed states outright. Each one is an estimate
built from observed laps, and each one refuses to answer when it lacks the laps
to fit. Never repeat their numbers as facts: say "the model has him losing two
tenths a lap", not "his tyres are losing two tenths".

`pitwall_tyres` fits a least-squares line to that car's own laps on its current
set, with the fuel load already subtracted, and reports seconds lost per lap,
the cost over the next five laps, and the sample count. Under four laps and the
slope is not reported at all; under six it carries a `~` and is provisional —
the early-race fit is track evolution, not tyre life. A slope above about
0.12 s/lap, or a fit that started late in a stint, is the "he is about to fall
off the cliff" call.

`pitwall_fuel` removes the fuel penalty: a car starts a race on about 110 kg and
burns it over the distance, at roughly 0.033 s per kg per lap. That is why a
1:21 in lap 2 and a 1:19 in lap 40 are the same pace — compare the `EMPTY TANK`
column, not `LAST`. Both constants are tool arguments if a circuit needs
different numbers.

`pitwall_overtake` compares recent median pace of adjacent cars and projects:
laps until DRS, and a pass lap. The floor is 0.12 s/lap — below that a car
cannot close and pass, it just sits there, and the tool says `holding station`.
A projected pass lap is arithmetic, not a prediction of traffic, team orders,
or a driver who defends well.

`pitwall_pitplan` enumerates stop plans from the current lap to the flag, prices
each one with the compound curves fitted from this session, adds a pit loss of
21 s per stop (a tool argument) and ranks them. The last line says whether each
compound's wear was **measured** or is the **book** figure — say which, on air.
A fit only counts as measured when it has at least six laps in it and an R² of
0.20 or more; a line through the noise, or a slope pointing the wrong way, is
priced at the book figure and labelled `book`. That matters most in the first
third of a race, when the fuel correction and a track still rubbering in will
happily produce a "wear rate" that explains three percent of the variance.
If the current set cannot reach the flag the tool says a stop is mandatory and
stops comparing against staying out, because that comparison would be fiction.
Otherwise the line that matters is whether staying out is quicker: if it is,
pitting costs the race.

## The pit window and the likely outcome

`pitwall_undercut` prices the fight in the pit lane. It reports how much the car
ahead is losing per lap and how many laps of cover it has — `gap / wear rate` —
so the line is "Pérez has five laps of cover, then the undercut is live", or
"cannot be undercut before the flag" when the gap is too big for the distance
left. A pair with the car ahead *gaining* gets `no undercut`; do not dress that
up as a threat.

`pitwall_windows` is the tyre clock: laps left on each set against laps left in
the race, the last lap to change it, and every car that cannot reach the flag.
Read the bottom of the table — a car running out before the flag has a stop
coming whether the team has called it or not.

`pitwall_outlook` is the likely outcome, not the result. It runs every car to
the flag on its current tyres, prices any mandatory stop, and starts from the
current gaps. Three things it does not know: traffic, safety cars, and what the
other teams are about to do. Say "the model has him ninth", never "he will
finish ninth".

Intervals are the weak point. The feed does not always publish them, so when it
does not they are rebuilt by summing every car's recorded laps over the same lap
window, and the header says `intervals from laps`. A car with a missing lap (a
deleted one) is left out rather than handed a made-up gap. If the tool says
`no intervals ... cannot be priced`, that is the rebuild failing, not the race.

All of these need lap history. The feed carries only the current lap per car, so
`pitwall-agent` records every lap it sees into `~/.hermes/state/pitwall-laps.json`
— the gateway, the CLI and the 20-second watcher all append to it. If a replay
is started mid-race, the models stay quiet until enough laps have been
collected; say "not enough laps yet" rather than guessing.

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
`rewind blocked by MultiViewer; player paused to scrub`, the onboard is up and
frozen at the incident: drag it back in the app yourself, then let green resume
it. The API cannot seek live content even though the UI can.

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
