import threading
import time
import xml.dom.minidom

import pytest

from orion_bg import autostart, paths
from orion_bg.client import Client, ClientError, DaemonNotRunning
from orion_bg.hotkey import parse_combo
from orion_bg.scheduler import Scheduler, parse_trigger
from orion_bg.server import ApiServer
from orion_bg.supervisor import supervise


@pytest.fixture
def api(make_agent, tmp_path):
    agent = make_agent()
    Scheduler(agent, db_path=tmp_path / "sched.db")
    agent.start()
    server = ApiServer(agent, "127.0.0.1", 0, "secret-token")
    server.start()
    yield agent, server, Client(server.url, "secret-token")
    server.stop()


def test_api_round_trip(api):
    agent, server, client = api
    assert client.status()["workers"] == 2
    task = client.submit("via api", kind="dummy", source="ui")
    assert agent.wait(task["id"], 5)["status"] == "completed"
    assert client.task(task["id"])["result"].endswith("via api")
    assert client.tasks(status="completed")[0]["id"] == task["id"]

    risky = client.submit("x [approval]", kind="dummy")
    agent.wait(risky["id"], 5, until=("needs_approval",))
    (a,) = client.approvals()
    assert client.approve(a["id"])["requeued"]["status"] == "queued"
    with pytest.raises(ClientError) as err:
        client.approve(a["id"])
    assert err.value.status == 404

    assert client.pause()["paused"] is True
    assert client.resume()["paused"] is False
    with pytest.raises(ClientError):
        client.submit("   ")


def test_api_needs_token(api):
    _, server, _ = api
    with pytest.raises(ClientError) as err:
        Client(server.url, "wrong").status()
    assert err.value.status == 401


def test_history_page_served(api):
    import urllib.request
    _, server, _ = api
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    html = opener.open(f"{server.url}/?token=secret-token").read().decode()
    assert "Orion background tasks" in html


def test_schedule_api_and_firing(api):
    agent, _, client = api
    s = client.add_schedule("tick", "every 1s", kind="dummy")
    assert s["prompt"] == "tick"
    assert [x["id"] for x in client.schedules()] == [s["id"]]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not agent.list():
        time.sleep(0.1)
    fired = agent.list()
    assert fired and fired[0]["source"] == "schedule" and fired[0]["schedule_id"] == s["id"]
    client.remove_schedule(s["id"])
    assert client.schedules() == []
    with pytest.raises(ClientError):
        client.add_schedule("x", "whenever")


def test_schedules_survive_restart(make_agent, tmp_path):
    db = tmp_path / "persist.db"
    first = make_agent()
    sched = Scheduler(first, db_path=db)
    sched.start()
    added = sched.add("morning brief", "daily 08:00", kind="dummy")
    sched.shutdown()
    second = make_agent()
    again = Scheduler(second, db_path=db)
    again.start()
    try:
        (job,) = again.list()
        assert job["id"] == added["id"] and job["prompt"] == "morning brief"
        assert job["next_run"] and "08:00" in job["next_run"]
    finally:
        again.shutdown()


@pytest.mark.parametrize("spec", ["daily 08:00", "weekdays 7:30", "every 30m", "every 2h", "cron 0 8 * * 1", "at 2030-01-01 09:00"])
def test_parse_trigger(spec):
    assert parse_trigger(spec) is not None


def test_supervisor_restarts_until_clean_exit():
    codes = iter([1, 3, 0])
    spawned, slept = [], []
    rc = supervise(["x"], spawn=lambda c: (spawned.append(c), next(codes))[1], sleep=slept.append)
    assert rc == 0 and len(spawned) == 3 and slept == [2.0, 4.0]


def test_supervisor_gives_up():
    rc = supervise(["x"], spawn=lambda c: 5, sleep=lambda s: None, max_restarts=2)
    assert rc == 5


def test_hotkey_combo():
    flags, vk = parse_combo("ctrl+alt+o")
    assert vk == ord("O") and flags & 0x3 == 0x3
    assert parse_combo("win+shift+F5")[1] == 0x74
    with pytest.raises(ValueError):
        parse_combo("o")


def test_autostart_xml_is_valid():
    doc = xml.dom.minidom.parseString(autostart.task_xml("PC\\me", "C:\\py\\pythonw.exe", "-m orion_bg run", "C:\\x"))
    assert doc.getElementsByTagName("Hidden")[0].firstChild.data == "true"
    assert doc.getElementsByTagName("RestartOnFailure")
    assert doc.getElementsByTagName("LogonTrigger")


def test_daemon_end_to_end(cfg, workdir):
    from orion_bg import daemon
    from orion_bg import config as cfgmod

    cfgpath = paths.config_path()
    cfgpath.write_text(
        f'[safety]\nallowed_paths = ["{workdir.as_posix()}"]\n[tasks]\ndummy_seconds = 0.1\n'
        '[hotkey]\nenabled = false\n[tray]\nenabled = false\n[notify]\ntoast = false\n'
    )
    assert cfgmod.load()["tasks"]["dummy_seconds"] == 0.1
    stop, ready = threading.Event(), threading.Event()
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("rc", daemon.main(stop_event=stop, ready=ready)))
    t.start()
    assert ready.wait(10)
    client = Client.from_runtime()
    task = client.submit("through the daemon", kind="dummy")
    for _ in range(50):
        if client.task(task["id"])["status"] == "completed":
            break
        time.sleep(0.1)
    assert client.task(task["id"])["status"] == "completed"
    client.shutdown()
    t.join(10)
    assert result["rc"] == 0
    assert not paths.runtime_file().exists()
    with pytest.raises(DaemonNotRunning):
        Client.from_runtime()
    assert (paths.log_dir() / "background-agent.log").read_text().count("through the daemon") >= 1
