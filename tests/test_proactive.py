from orion.proactive import Proactive, Readings, _in_quiet_hours


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def keys(alerts):
    return [a["key"] for a in alerts]


async def test_battery_warns_once_per_level_and_resets_when_plugged(engine):
    p = Proactive(engine, Clock())
    assert keys(p.evaluate(Readings(battery=50, plugged=False))) == []
    assert keys(p.evaluate(Readings(battery=19, plugged=False))) == ["battery"]
    assert p.evaluate(Readings(battery=18, plugged=False)) == []
    crit = p.evaluate(Readings(battery=9, plugged=False))
    assert crit[0]["critical"] is True
    p.evaluate(Readings(battery=40, plugged=True))
    assert keys(p.evaluate(Readings(battery=19, plugged=False))) == ["battery"]


async def test_cpu_needs_sustained_load_and_has_cooldown(engine):
    clock = Clock()
    p = Proactive(engine, clock)
    hot = Readings(cpu=97, top_cpu="chrome.exe")
    assert p.evaluate(hot) == [] and p.evaluate(hot) == []
    alert = p.evaluate(hot)
    assert keys(alert) == ["cpu"] and "chrome.exe" in alert[0]["message"]
    assert p.evaluate(hot) == []  # cooldown
    clock.t += 1801
    assert keys(p.evaluate(hot)) == ["cpu"]


async def test_download_finished_when_size_is_stable(engine):
    p = Proactive(engine, Clock())
    p.evaluate(Readings(downloads={"old.zip": 10}))  # baseline
    assert p.evaluate(Readings(downloads={"old.zip": 10, "game.zip.crdownload": 50})) == []
    assert p.evaluate(Readings(downloads={"old.zip": 10, "game.zip": 100})) == []  # first sighting
    done = p.evaluate(Readings(downloads={"old.zip": 10, "game.zip": 100}))
    assert keys(done) == ["dl:game.zip"]
    assert p.evaluate(Readings(downloads={"old.zip": 10, "game.zip": 100})) == []


async def test_internet_down_after_a_minute_then_back(engine):
    clock = Clock()
    p = Proactive(engine, clock)
    p.evaluate(Readings(online=True))
    assert p.evaluate(Readings(online=False)) == []
    clock.t += 61
    assert keys(p.evaluate(Readings(online=False))) == ["net_down"]
    assert keys(p.evaluate(Readings(online=True))) == ["net"]


async def test_calendar_event_warned_once(engine):
    p = Proactive(engine, Clock())
    ev = {"title": "Standup", "start": "Wed 14 Oct 09:00", "location": "Zoom", "all_day": False}
    out = p.evaluate(Readings(events=[ev]))
    assert out[0]["message"] == "Standup at 09:00 (Zoom)."
    assert p.evaluate(Readings(events=[ev])) == []


async def test_fire_emits_and_respects_quiet_hours(engine):
    import datetime as dt

    p = Proactive(engine, Clock())
    events = engine.bus.subscribe()
    engine.config.settings.quiet_start, engine.config.settings.quiet_end = "00:00", "23:59"
    p.fire("x", "Title", "Body")
    types_ = [events.get_nowait()["type"] for _ in range(events.qsize())]
    assert "alert" in types_ and "notify" not in types_  # quiet: shown in-app only
    p.fire("y", "Critical", "Body", critical=True)
    types_ = [events.get_nowait()["type"] for _ in range(events.qsize())]
    assert "notify" in types_
    assert _in_quiet_hours(dt.datetime(2026, 1, 1, 23, 30), "23:00", "07:00")
    assert not _in_quiet_hours(dt.datetime(2026, 1, 1, 12, 0), "23:00", "07:00")
