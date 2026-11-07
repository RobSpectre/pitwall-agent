import pytest

from pitwall import feed, render
from pitwall.model import build


class FakeLive:
    """Stands in for mvf1's client: a document that can be swapped under it."""

    def __init__(self, state):
        self.state = state

    @property
    def live_timing_state(self):
        return {"data": {"f1LiveTimingState": self.state}}


@pytest.fixture
def live(monkeypatch):
    def install(state):
        feed.reset()
        client = FakeLive(state)
        monkeypatch.setattr(feed, "_client", lambda: client)
        return client
    return install


def test_data_age_tracks_the_document_not_the_fetch(live, q3_state, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(feed.time, "time", lambda: clock["t"])
    live(q3_state)

    feed.raw_state(fresh=True)
    assert feed.data_age() == pytest.approx(0.0)

    # Six seconds later the document has not changed: the data is six seconds
    # old even though we just fetched it.
    clock["t"] += 6.0
    feed.raw_state(fresh=True)
    assert feed.data_age() == pytest.approx(6.0)


def test_a_changed_document_resets_the_age(live, q3_state, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(feed.time, "time", lambda: clock["t"])
    client = live(q3_state)

    feed.raw_state(fresh=True)
    clock["t"] += 30.0
    feed.raw_state(fresh=True)
    assert feed.data_age() == pytest.approx(30.0)

    changed = dict(q3_state)
    changed["Heartbeat"] = {"Utc": "2026-11-07T18:30:00.000Z"}
    client.state = changed
    feed.raw_state(fresh=True)
    assert feed.data_age() == pytest.approx(0.0)


def test_render_says_stalled_instead_of_live(live, q3_state):
    live(q3_state)
    stale = build(q3_state, data_age=140.0)
    assert "[STALLED 2m]" in render.overview(stale)
    fresh = build(q3_state, data_age=0.2)
    assert "[live]" in render.overview(fresh)


def test_snapshot_carries_the_data_age(live, q3_state, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(feed.time, "time", lambda: clock["t"])
    live(q3_state)
    feed.raw_state(fresh=True)
    clock["t"] += 12.0
    session, _ = feed.snapshot(fresh=True)
    assert session.data_age == pytest.approx(12.0)
