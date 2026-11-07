import pytest

from pitwall import timefmt


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1:14.933", 74.933),
        ("1:09.876", 69.876),
        ("0:59.999", 59.999),
        ("19.638", 19.638),
        ("+1.234", 1.234),
        ("1:14", 74.0),
    ],
)
def test_parse_time(raw, expected):
    assert timefmt.parse_time(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "-", "abc", "-1"])
def test_parse_time_rejects_junk(raw):
    assert timefmt.parse_time(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("00:59:59", 3599), ("00:03:20", 200), ("01:00:00", 3600), ("0:30", 30)],
)
def test_parse_clock(raw, expected):
    assert timefmt.parse_clock(raw) == pytest.approx(expected)


def test_lap_always_carries_minutes():
    assert timefmt.fmt_lap(74.933) == "1:14.933"
    assert timefmt.fmt_lap(59.999) == "0:59.999"
    assert timefmt.fmt_lap(None) == "-"


def test_gap_keeps_three_decimals():
    assert timefmt.fmt_gap(0.1) == "+0.100"
    assert timefmt.fmt_gap(-0.021) == "-0.021"
    assert timefmt.fmt_gap(0) == "+0.000"
    assert timefmt.fmt_gap(None) == "-"


def test_clock_and_age():
    assert timefmt.fmt_clock(252) == "4:12"
    assert timefmt.fmt_clock(None) == "-"
    assert timefmt.fmt_age(0.4) == "live"
    assert timefmt.fmt_age(12.2) == "unchanged 12s"
    assert timefmt.fmt_age(300) == "STALLED 5m"
