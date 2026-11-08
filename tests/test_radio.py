"""Radio transcriptions: the cut, the driver join, and the wire format.

Every test here runs against the recorded Mexico payload -- no request leaves
the process. The two things worth protecting are the playhead cut (a replay
must never show radio from later in the race) and the tolerant parse (the
published JSON carries literal newlines).
"""

import json
from pathlib import Path

import pytest

from pitwall import radio, render, tools
from pitwall.model import build
from pitwall.timefmt import parse_utc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def clear_cache():
    radio._CACHE.clear()
    yield
    radio._CACHE.clear()


@pytest.fixture
def entries():
    return json.loads((FIXTURES / "radio_mexico.json").read_text(encoding="utf-8"))


def _state(playhead: str | None = "2026-11-01T20:00:00.000Z", series: list | None = None) -> dict:
    messages = [{"Utc": playhead, "Message": "TRACK CLEAR"}] if playhead else []
    return {
        "SessionInfo": {
            "Key": 11404, "Type": "Race", "Name": "Race",
            "Meeting": {"Key": 1298, "Name": "Mexico City Grand Prix",
                        "Circuit": {"ShortName": "Mexico City"}},
        },
        "LapCount": {"CurrentLap": "7", "TotalLaps": "71"},
        "SessionStatus": {"Status": "Started"},
        "RaceControlMessages": {"Messages": messages},
        "SessionData": {"Series": series or []},
        "TimingData": {"Lines": {
            "23": {"RacingNumber": "23", "Position": "15", "NumberOfLaps": 6},
            "87": {"RacingNumber": "87", "Position": "12", "NumberOfLaps": 6},
            "44": {"RacingNumber": "44", "Position": "3", "NumberOfLaps": 6},
            "1": {"RacingNumber": "1", "Position": "7", "NumberOfLaps": 6},
            "16": {"RacingNumber": "16", "Position": "1", "NumberOfLaps": 6},
            "50": {"RacingNumber": "50", "Position": "17", "NumberOfLaps": 6},
        }},
        "DriverList": {
            "23": {"Tla": "ALB"}, "87": {"Tla": "BEA"}, "44": {"Tla": "HAM"},
            "1": {"Tla": "NOR"}, "16": {"Tla": "LEC"}, "50": {"Tla": "HIR"},
        },
    }


def _session(**kw):
    return build(_state(**kw), mode="race")


def _handler(name: str):
    for registered, _schema, handler in tools.schemas():
        if registered == name:
            return handler
    raise AssertionError(f"{name} is not registered")


# --- model -----------------------------------------------------------------


def test_meeting_key_is_carried_on_the_session():
    assert _session().meeting_key == 1298


def test_playhead_is_session_time_not_delivery_time():
    session = _session()
    session.radio = [{"Utc": "2026-11-01T19:59:00.000Z"}]
    assert radio.playhead(session) == pytest.approx(parse_utc("2026-11-01T20:00:00Z"))


def test_no_playhead_when_the_feed_has_no_stamps():
    assert radio.playhead(_session(playhead=None)) is None


def test_playhead_prefers_car_data_over_a_quiet_race_control():
    state = _state()                               # race control says 20:00
    state["CarData"] = {"Entries": [{"Utc": "2026-11-01T20:10:00.000Z"}]}
    session = build(state, mode="race")
    assert radio.playhead(session) == pytest.approx(parse_utc("2026-11-01T20:10:00Z"))


# --- the cut ---------------------------------------------------------------


def test_recent_cuts_everything_past_the_playhead(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _session()                      # playhead 20:00
    lines, published, cut = radio.recent(session, limit=20)

    assert cut == pytest.approx(parse_utc("2026-11-01T20:00:00Z"))
    assert published == 5                          # the four 22:20 lines are the future
    assert all(parse_utc(line.at) is None or line.ts <= cut for line in lines)
    assert "Three laps to go" not in " ".join(line.text for line in lines)


def test_recent_returns_the_newest_lines_last(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    lines, _published, _cut = radio.recent(_session(), limit=3)
    assert len(lines) == 3
    assert [line.ts for line in lines] == sorted(line.ts for line in lines)
    assert lines[-1].ts == max(line.ts for line in lines)


def test_driver_filter_accepts_a_code(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    lines, published, _cut = radio.recent(_session(), driver="ALB", limit=10)
    assert published == 4
    assert {line.label for line in lines} == {"ALB"}
    assert all(line.num == "23" for line in lines)


def test_driver_filter_accepts_a_number(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    lines, _published, _cut = radio.recent(_session(), driver="87", limit=10)
    assert [line.text for line in lines] == ["Radio check"]


def test_unknown_driver_is_an_error(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    with pytest.raises(radio.RadioError):
        radio.recent(_session(), driver="ZZZ")


def test_without_a_playhead_nothing_is_cut(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    lines, published, cut = radio.recent(_session(playhead=None), limit=20)
    assert cut is None
    assert published == len(entries)


# --- wire format -----------------------------------------------------------


def test_fetch_tolerates_a_literal_newline(monkeypatch):
    body = (
        '[{"driver_number":"16","transcription":"line one\nline two",'
        '"player_ts":"2026-11-01T20:00:00.000Z","duration_ms":1200}]'
    )
    monkeypatch.setattr(radio, "_get", lambda url, timeout, headers=None: (body, ""))
    rows = radio.fetch(1298, 11404)
    assert rows[0]["transcription"] == "line one\nline two"

    monkeypatch.setattr(radio, "fetch", lambda *a, **k: rows)
    lines, _published, _cut = radio.recent(_session(), limit=5)
    assert lines[0].text == "line one line two"     # whitespace collapsed for display


def test_fetch_reports_a_dead_endpoint(monkeypatch):
    def boom(url, timeout, headers=None):
        raise OSError("connection refused")

    monkeypatch.setattr(radio, "_get", boom)
    with pytest.raises(radio.RadioError):
        radio.fetch(1298, 11404)


def test_empty_payload_is_zero_lines_not_an_error(monkeypatch):
    monkeypatch.setattr(radio, "_get", lambda url, timeout, headers=None: (" [] ", ""))
    assert radio.fetch(1298, 11404) == []


def test_revalidation_reuses_the_cached_list_on_304(monkeypatch):
    import urllib.error

    body = '[{"driver_number":"16","transcription":"Box box","player_ts":"2026-11-01T20:00:00.000Z","duration_ms":900}]'
    monkeypatch.setattr(
        radio, "_get", lambda url, timeout, headers=None: (body, "Sun, 08 Nov 2026 09:56:28 GMT")
    )
    first = radio.fetch(1298, 11404)
    assert first[0]["transcription"] == "Box box"

    # Expire the cache but keep the stamp, so the next call revalidates.
    radio._CACHE[("1298", "11404")] = (0.0, first, "Sun, 08 Nov 2026 09:56:28 GMT")

    def not_modified(url, timeout, headers=None):
        assert headers is not None
        assert headers.get("If-Modified-Since") == "Sun, 08 Nov 2026 09:56:28 GMT"
        from email.message import Message

        raise urllib.error.HTTPError(url, 304, "Not Modified", Message(), None)

    monkeypatch.setattr(radio, "_get", not_modified)
    second = radio.fetch(1298, 11404)
    assert second is first or second == first


# --- render and tool -------------------------------------------------------


def test_render_shows_time_driver_and_the_cut(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _session()
    lines, published, cut = radio.recent(session, limit=2)
    text = render.radio(session, lines, total=published, cut=cut)

    assert "RADIO" in text and "LAP 7/71" in text
    assert "last 2 of 5" in text
    assert "cut at 20:00:00" in text
    assert 'ALB' in text and '"' in text


def test_render_says_when_there_is_nothing_yet():
    session = _session()
    text = render.radio(session, [], total=0, cut=None)
    assert "no radio transcribed" in text


def test_handler_returns_text_not_an_exception(entries, monkeypatch, offline):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    offline(_state())
    assert "RADIO" in _handler("pitwall_radio")({"limit": 2})


def test_handler_reports_a_dead_endpoint(monkeypatch, offline):
    def boom(*_a, **_k):
        raise radio.RadioError("radio transcriptions unavailable (OSError: nope)")

    monkeypatch.setattr(radio, "fetch", boom)
    offline(_state())
    text = _handler("pitwall_radio")({})
    assert text.startswith("Error: ") and "unavailable" in text


def test_radio_is_offered_in_every_mode():
    by_name = {spec.name: spec.modes for spec in tools.specs()}
    assert by_name["pitwall_radio"] is None


# --- the lap window and the summary ----------------------------------------

SERIES = [
    {"Utc": "2026-11-01T19:50:00.000Z", "Lap": 1},
    {"Utc": "2026-11-01T19:51:00.000Z", "Lap": 2},
    {"Utc": "2026-11-01T19:51:50.000Z", "Lap": 3},
    {"Utc": "2026-11-01T19:52:40.000Z", "Lap": 4},
    {"Utc": "2026-11-01T19:53:20.000Z", "Lap": 5},
    {"Utc": "2026-11-01T19:54:00.000Z", "Lap": 6},
    {"Utc": "2026-11-01T19:54:40.000Z", "Lap": 7},
]


def _windowed(**kw):
    return build(_state(series=SERIES, **kw), mode="race")


def test_lap_window_counts_the_lap_being_run():
    start, first, last, _cut = radio.lap_window(_windowed(), laps=4)
    assert (first, last) == (4, 7)                 # lap 7 plus the three before it
    assert start == pytest.approx(parse_utc("2026-11-01T19:52:40Z"))


def test_lap_window_never_reaches_below_lap_one():
    start, first, last, _cut = radio.lap_window(_windowed(), laps=20)
    assert (first, last) == (1, 7)
    assert start == pytest.approx(parse_utc("2026-11-01T19:50:00Z"))


def test_lap_window_without_stamps_does_not_invent_one():
    start, first, last, cut = radio.lap_window(build(_state(), mode="race"), laps=4)
    assert start is None and first is None and last == 7


def test_summary_keeps_only_the_window(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, (start, first, last, cut) = radio.summary(_windowed(), laps=4)
    assert (first, last) == (4, 7)
    assert [group.label for group in groups] == ["ALB"]      # BEA spoke before lap 4
    assert groups[0].calls == 3
    assert cut is not None


def test_summary_widens_with_more_laps(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, _window = radio.summary(_windowed(), laps=6)
    assert [(group.label, group.calls) for group in groups] == [("ALB", 4), ("BEA", 1)]


def test_summary_orders_by_how_much_was_said(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, _window = radio.summary(_windowed(), laps=6)
    assert groups[0].calls >= groups[-1].calls
    assert groups[0].airtime >= groups[-1].airtime


def test_summary_can_follow_one_driver(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, window = radio.summary(_windowed(), driver="BEA", laps=6)
    assert [group.label for group in groups] == ["BEA"]
    assert groups[0].lines[0].text == "Radio check"
    assert "1 driver," in render.radio_summary(_windowed(), groups, window=window)


def test_summary_ignores_the_future(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, _window = radio.summary(_windowed(), laps=20)
    assert all(line.ts <= _window[3] for group in groups for line in group.lines)
    assert "Three laps to go" not in " ".join(
        line.text for group in groups for line in group.lines
    )


def test_summary_render_states_the_window_and_the_counts(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _windowed()
    groups, window = radio.summary(session, laps=6)
    text = render.radio_summary(session, groups, window=window)

    assert "RADIO SUMMARY" in text and "laps 2-7" in text
    assert "2 drivers, 5 calls" in text
    assert "ALB  4 calls" in text and "BEA  1 call" in text
    assert '"Radio check"' in text


def test_summary_render_says_when_nobody_spoke():
    session = _windowed()
    text = render.radio_summary(session, [], window=radio.lap_window(session, 4))
    assert "nobody spoke" in text


def test_summary_render_caps_the_calls_per_driver(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _windowed()
    groups, window = radio.summary(session, laps=6)
    text = render.radio_summary(session, groups, window=window, per_driver=1)
    assert "ALB  4 calls" in text
    assert "+3 earlier" in text


def test_tool_summary_flag_switches_the_view(entries, monkeypatch, offline):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    offline(_state(series=SERIES))
    text = _handler("pitwall_radio")({"summary": True, "laps": 6})
    assert "RADIO SUMMARY" in text


def test_command_routes_radio_and_summary(entries, monkeypatch, offline):
    from pitwall import plugin

    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    offline(_state(series=SERIES))
    assert "RADIO" in plugin._command("radio")
    summary = plugin._command("radio summary 6")
    assert "RADIO SUMMARY" in summary and "laps 2-7" in summary
    assert "laps 4-7" in plugin._command("radio sum")


# --- choosing a set of drivers ---------------------------------------------


def test_resolve_takes_one_code_one_number_or_a_list():
    session = _windowed()
    assert [d.label for d in radio.resolve(session, "ALB")] == ["ALB"]
    assert [d.label for d in radio.resolve(session, "23")] == ["ALB"]
    assert [d.label for d in radio.resolve(session, "ALB, BEA")] == ["ALB", "BEA"]
    assert [d.label for d in radio.resolve(session, ["ALB", "87"])] == ["ALB", "BEA"]
    assert [d.label for d in radio.resolve(session, ["ALB,BEA"])] == ["ALB", "BEA"]
    assert radio.resolve(session, None) == []
    assert radio.resolve(session, []) == []


def test_resolve_dedupes_the_set():
    drivers = radio.resolve(_windowed(), ["ALB", "23", "BEA"])
    assert [d.label for d in drivers] == ["ALB", "BEA"]


def test_resolve_names_what_it_could_not_find():
    with pytest.raises(radio.RadioError) as err:
        radio.resolve(_windowed(), ["ALB", "ZZZ"])
    assert "ZZZ" in str(err.value)


def test_summary_covers_a_set_of_drivers(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    groups, _window = radio.summary(_windowed(), driver=["ALB", "BEA"], laps=6)
    assert [group.label for group in groups] == ["ALB", "BEA"]


def test_summary_says_when_a_requested_driver_said_nothing(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _windowed()
    groups, window = radio.summary(session, driver=["ALB", "HAM"], laps=6)
    text = render.radio_summary(session, groups, window=window, wanted=["ALB", "HAM"])
    assert "for ALB HAM" in text
    assert "no radio from HAM in this window" in text


def test_summary_with_only_silent_drivers_says_so(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    session = _windowed()
    groups, window = radio.summary(session, driver=["HAM", "NOR"], laps=6)
    text = render.radio_summary(session, groups, window=window, wanted=["HAM", "NOR"])
    assert groups == []
    assert "no radio from HAM, NOR" in text


def test_recent_can_list_a_set_of_drivers(entries, monkeypatch):
    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    lines, _published, _cut = radio.recent(_windowed(), driver=["ALB", "BEA"], limit=10)
    assert {line.label for line in lines} == {"ALB", "BEA"}


def test_command_takes_several_drivers(entries, monkeypatch, offline):
    from pitwall import plugin

    monkeypatch.setattr(radio, "fetch", lambda *a, **k: entries)
    offline(_state(series=SERIES))
    text = plugin._command("radio summary 6 ALB BEA")
    assert "for ALB BEA" in text
    assert "ALB" in text and "BEA" in text
