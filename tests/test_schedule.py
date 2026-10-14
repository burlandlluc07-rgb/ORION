import datetime as dt

import pytest

from orion.tasks.schedule import ScheduleError, next_run, parse_when

NOW = dt.datetime(2026, 10, 14, 14, 30)  # a Wednesday


def test_daily_and_weekdays():
    assert next_run("daily 08:00", NOW) == dt.datetime(2026, 10, 15, 8, 0)
    assert next_run("daily 16:00", NOW) == dt.datetime(2026, 10, 14, 16, 0)
    fri = dt.datetime(2026, 10, 16, 9, 0)
    assert next_run("weekdays 08:00", fri) == dt.datetime(2026, 10, 19, 8, 0)


def test_weekly_and_every_and_once():
    assert next_run("weekly mon 09:00", NOW) == dt.datetime(2026, 10, 19, 9, 0)
    assert next_run("weekly wed 15:00", NOW) == dt.datetime(2026, 10, 14, 15, 0)
    assert next_run("every 30m", NOW) == NOW + dt.timedelta(minutes=30)
    assert next_run("once 2026-10-14T18:00", NOW) == dt.datetime(2026, 10, 14, 18, 0)
    assert next_run("once 2026-10-14T10:00", NOW) is None


def test_bad_specs():
    for spec in ("every 10s", "weekly funday 9:00", "hourly", "daily 25:00"):
        with pytest.raises((ScheduleError, ValueError)):
            next_run(spec, NOW)


@pytest.mark.parametrize("text,expected", [
    ("in 10 minutes", NOW + dt.timedelta(minutes=10)),
    ("in 1h 30m", NOW + dt.timedelta(hours=1, minutes=30)),
    ("90 seconds", NOW + dt.timedelta(seconds=90)),
    ("at 5pm", dt.datetime(2026, 10, 14, 17, 0)),
    ("at 9am", dt.datetime(2026, 10, 15, 9, 0)),
    ("tomorrow at 7:15", dt.datetime(2026, 10, 15, 7, 15)),
    ("17:45", dt.datetime(2026, 10, 14, 17, 45)),
    ("2026-12-24T18:00", dt.datetime(2026, 12, 24, 18, 0)),
])
def test_parse_when(text, expected):
    assert parse_when(text, NOW) == expected
