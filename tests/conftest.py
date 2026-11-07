import json
from pathlib import Path

import pytest

from pitwall import feed

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
def offline(monkeypatch):
    """Serve a fixed document through the real cache/parse path."""
    def install(state: dict):
        feed.reset()
        monkeypatch.setattr(feed, "raw_state", lambda *, fresh=False: state)
        return state
    return install
