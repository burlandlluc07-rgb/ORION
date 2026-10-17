import time

from orion.background.runners import Completed, RunContext


def test_dummy_end_to_end(make_agent):
    agent = make_agent()
    events = []
    agent.subscribe(lambda e, t: events.append(e))
    agent.start()
    task = agent.submit("hello", kind="dummy", source="cli")
    done = agent.wait(task["id"], 5)
    assert done["status"] == "completed"
    assert "hello" in done["result"]
    assert events[:1] == ["submitted"] and "completed" in events
    assert agent.notifier.sent[-1][0] == "Orion finished a task"


def test_workers_run_in_parallel(make_agent):
    agent = make_agent(tasks={"dummy_seconds": 1.0})
    agent.start()
    t0 = time.monotonic()
    ids = [agent.submit(f"t{i}", kind="dummy")["id"] for i in range(2)]
    for i in ids:
        assert agent.wait(i, 5)["status"] == "completed"
    assert time.monotonic() - t0 < 1.8  # two 1 s tasks overlapped


def test_cancel_running(make_agent):
    agent = make_agent(tasks={"dummy_seconds": 5})
    agent.start()
    task = agent.submit("long", kind="dummy")
    agent.wait(task["id"], 3, until=("running",))
    agent.cancel(task["id"])
    assert agent.wait(task["id"], 3)["status"] == "cancelled"


def test_timeout(make_agent):
    agent = make_agent(tasks={"dummy_seconds": 5})
    agent.start()
    task = agent.submit("slow", kind="dummy", timeout=0.3)
    done = agent.wait(task["id"], 3)
    assert done["status"] == "failed" and "Timed out" in done["error"]


def test_pause_and_resume(make_agent):
    agent = make_agent()
    agent.start()
    agent.pause()
    task = agent.submit("later", kind="dummy")
    time.sleep(0.6)
    assert agent.get(task["id"])["status"] == "queued"
    agent.resume()
    assert agent.wait(task["id"], 3)["status"] == "completed"


def test_approval_round_trip(make_agent):
    agent = make_agent()
    agent.start()
    for approve, word in ((True, "approved"), (False, "denied")):
        task = agent.submit("risky [approval]", kind="dummy")
        agent.wait(task["id"], 3, until=("needs_approval",))
        (pending,) = agent.pending_approvals(task["id"])
        assert pending["tool"] == "dummy_action"
        agent.approve(pending["id"]) if approve else agent.deny(pending["id"])
        done = agent.wait(task["id"], 3)
        assert done["status"] == "completed" and word in done["result"]


def test_unknown_kind_rejected(make_agent):
    agent = make_agent()
    try:
        agent.submit("x", kind="nope")
    except ValueError as exc:
        assert "unknown task kind" in str(exc)
    else:
        raise AssertionError("expected ValueError")


class Resumable:
    """Saves progress, then 'crashes' on the first run; the second run resumes."""
    def __init__(self):
        self.runs = 0

    def run(self, task, ctx: RunContext):
        self.runs += 1
        state = task["state"] or {"step": 0}
        state["step"] += 1
        ctx.save_state(state)
        if self.runs == 1:
            raise SystemExit  # simulates the process dying mid-task
        return Completed(f"finished at step {state['step']}")


def test_restart_resumes_from_saved_state(make_agent, tmp_path):
    db = tmp_path / "shared.db"
    first = make_agent(db=db)
    task = first.store.add("resume me", kind="r")
    claimed = first.store.claim_next()
    runner = Resumable()
    try:
        runner.run(claimed, RunContext(claimed, time.monotonic() + 5, lambda: False,
                                       lambda s: first.store.save_state(task["id"], s), lambda *a: None))
    except SystemExit:
        pass
    assert first.store.get(task["id"])["status"] == "running"
    second = make_agent(runners={"r": runner}, db=db)
    second.start()  # recover() re-queues the running task
    done = second.wait(task["id"], 3)
    assert done["status"] == "completed" and done["result"] == "finished at step 2"
