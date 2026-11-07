import json

import pytest

from pitwall import feed, tools


def handler(name: str):
    return next(h for n, _schema, h in tools.schemas() if n == name)


def test_manifest_declares_every_registered_tool():
    from pathlib import Path

    import yaml

    manifest = yaml.safe_load((Path(__file__).resolve().parent.parent / "plugin.yaml").read_text())
    declared = set(manifest["provides_tools"])
    registered = {name for name, _s, _h in tools.schemas()}
    assert declared == registered


def test_overview_handler(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_overview")({"messages": 5})
    assert "Qualifying Q3" in text
    assert "GREEN LIGHT" in text


def test_board_handler_and_gap_filter(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_board")({"gap_tol": 0.25})
    assert "LEC" in text
    assert "PER" not in text


def test_board_changes_after_a_new_lap(monkeypatch, q3_state):
    feed.reset()
    live = {"state": q3_state}
    monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: live["state"])
    board = handler("pitwall_board")
    assert "first read" in board({"only_changes": True})

    improved = json.loads(json.dumps(q3_state))
    improved["TimingData"]["Lines"]["16"]["BestLapTime"]["Value"] = "1:09.990"
    improved["TimingData"]["Lines"]["16"]["MVStatus"]["InPit"] = True
    live["state"] = improved
    text = board({"only_changes": True})
    assert "LEC best 1:09.990" in text
    assert "PUSH -> PIT" in text


def test_board_changes_reports_nothing_when_idle(offline, q3_state):
    offline(q3_state)
    board = handler("pitwall_board")
    board({"only_changes": True})
    assert "no changes" in board({"only_changes": True})


def test_driver_lookup_by_number_and_code(offline, q3_state):
    offline(q3_state)
    by_num = handler("pitwall_driver")({"driver": "16"})
    by_code = handler("pitwall_driver")({"driver": "lec"})
    assert "Charles LECLERC" in by_num
    assert by_num.splitlines()[0] == by_code.splitlines()[0]


def test_driver_lookup_failure_lists_the_grid(offline, q3_state):
    offline(q3_state)
    text = handler("pitwall_driver")({"driver": "XYZ"})
    assert text.startswith("Error: no driver matching")
    assert "VER" in text


def test_raw_index_then_section(offline, q3_state):
    offline(q3_state)
    raw = handler("pitwall_raw")
    index = raw({})
    assert "TimingData" in index and "bytes" in index

    body = raw({"sections": ["RaceControlMessages"], "max_chars": 400})
    assert "GREEN LIGHT" in body

    missing = raw({"sections": ["Nope"]})
    assert "absent: Nope" in missing


def test_raw_one_driver(offline, q3_state):
    offline(q3_state)
    body = handler("pitwall_raw")({"driver": "16"})
    payload = json.loads(body)
    assert payload["DriverList"]["16"]["Tla"] == "LEC"
    assert "Sectors" in payload["TimingData"]["16"]


def test_raw_truncates_to_budget(offline, q3_state):
    offline(q3_state)
    body = handler("pitwall_raw")({"sections": ["TimingData"], "max_chars": 400})
    assert body.endswith("chars]") or "truncated at 400" in body


def test_friendly_error_when_multiviewer_is_down(offline, monkeypatch):
    def boom(*, fresh=False):
        raise feed.PitwallError("MultiViewer is not reachable")

    feed.reset()
    monkeypatch.setattr(feed, "raw_state", boom)
    text = handler("pitwall_board")({})
    assert text == "Error: MultiViewer is not reachable"


def test_show_without_players(offline, q3_state, monkeypatch):
    offline(q3_state)

    class FakeRemote:
        players = []

    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    text = handler("pitwall_show")({"action": "onboard", "driver": "LEC"})
    assert "no players open" in text


def test_show_switches_to_driver_onboard(offline, q3_state, monkeypatch):
    offline(q3_state)
    calls = []

    class FakePlayer:
        id = 7

        def switch_stream(self, title):
            calls.append(title)

    class FakeRemote:
        players = [FakePlayer()]

        def player(self, pid):
            return FakePlayer()

    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    text = handler("pitwall_show")({"driver": "LEC"})
    assert calls == ["LEC"]
    assert "LEC onboard" in text


def test_show_data_channel(offline, q3_state, monkeypatch):
    offline(q3_state)
    calls = []

    class FakePlayer:
        id = 3

        def switch_stream(self, title):
            calls.append(title)

    class FakeRemote:
        players = [FakePlayer()]

    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    handler("pitwall_show")({"action": "data"})
    assert calls == ["DATA"]


def test_show_sync_prefers_the_live_timing_aware_call(offline, q3_state, monkeypatch):
    offline(q3_state)

    class FakeRemote:
        players = []

        def player_sync_to_live_timing(self):
            return {"synced_players": [6, 7]}

    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    text = handler("pitwall_show")({"action": "sync"})
    assert "synced players to live timing" in text


def test_show_sync_falls_back_to_player_sync_on_mvf1_2_0(offline, q3_state, monkeypatch):
    offline(q3_state)
    state = {"synced": False}

    class FakePlayer:
        id = 5

        def sync(self):
            state["synced"] = True

    class FakeRemote:
        players = [FakePlayer()]

    monkeypatch.setattr(feed, "_client", lambda: FakeRemote())
    text = handler("pitwall_show")({"action": "sync"})
    assert state["synced"] is True
    assert "synced 1 player(s)" in text


def test_every_schema_is_well_formed():
    for name, schema, _handler in tools.schemas():
        assert schema["name"] == name
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
