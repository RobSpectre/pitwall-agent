import json
import time
from pathlib import Path

import pytest

from pitwall import feed, meeting

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def real_state() -> dict:
    return load("fp3_interlagos_real.json")


@pytest.fixture
def q3_state() -> dict:
    return load("q3_interlagos.json")


@pytest.fixture
def q1_state() -> dict:
    return load("q1_interlagos.json")


@pytest.fixture
def race_state() -> dict:
    return load("race_interlagos.json")


@pytest.fixture
def offline(monkeypatch):
    """Serve a fixed document through the real cache/parse path."""
    def install(state: dict):
        feed.reset()
        monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: state)
        return state
    return install


@pytest.fixture
def fake_meeting(monkeypatch):
    """Serve a meetings-API payload from the cache instead of the network."""
    def install(meeting_key="1299", session_key="11407", content_id=1000010425,
                title="Practice 3", session_type="fp3"):
        entry = meeting.MeetingSession(
            key=str(session_key), title=title, short_title=title, session_type=session_type,
            content_id=content_id, start_at="", live_timing_available=True, archive_status="Complete",
        )
        monkeypatch.setitem(meeting._CACHE, str(meeting_key), (time.time(), [entry]))
    return install
