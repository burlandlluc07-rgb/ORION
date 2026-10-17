import os

import pytest

from orion.background import store as st
from orion.background.safety import ALLOW, APPROVE, DENY, PathNotAllowed, classify_command, resolve_path
from orion.background.store import Store


def test_queue_lifecycle(tmp_path):
    s = Store(tmp_path / "t.db")
    a = s.add("one", kind="dummy")
    b = s.add("two", kind="dummy")
    assert a["status"] == st.QUEUED
    first = s.claim_next()
    assert first["id"] == a["id"] and first["status"] == st.RUNNING
    assert s.claim_next()["id"] == b["id"]
    assert s.claim_next() is None
    s.finish(a["id"], st.COMPLETED, result="ok")
    assert s.get(a["id"])["result"] == "ok"
    assert s.recover() == 1  # b was running
    assert s.get(b["id"])["status"] == st.QUEUED
    assert s.counts()[st.COMPLETED] == 1


def test_persists_across_reopen(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("survive")
    s.save_state(t["id"], {"messages": [1, 2]})
    s.close()
    s2 = Store(tmp_path / "t.db")
    assert s2.get(t["id"])["state"] == {"messages": [1, 2]}


def test_cancel_queued_is_immediate(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("x")
    assert s.request_cancel(t["id"])["status"] == st.CANCELLED


def test_approvals_requeue_only_when_all_decided(tmp_path):
    s = Store(tmp_path / "t.db")
    t = s.add("x")
    s.claim_next()
    a1, a2 = s.park_for_approval(t["id"], {"k": 1}, [
        {"call_id": "c1", "tool": "delete_file", "input": {"path": "a"}, "reason": "r"},
        {"call_id": "c2", "tool": "run_shell", "input": {"command": "x"}, "reason": "r"},
    ])
    assert s.get(t["id"])["status"] == st.NEEDS_APPROVAL
    assert s.decide(a1["id"], True) is None
    task = s.decide(a2["id"], False)
    assert task["status"] == st.QUEUED
    assert task["state"]["decisions"] == {"c1": True, "c2": False}
    with pytest.raises(KeyError):
        s.decide(a1["id"], True)


def test_path_allowlist(tmp_path):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    assert resolve_path("a/b.txt", [root]) == root / "a" / "b.txt"
    assert resolve_path(str(root / "x.txt"), [root]) == root / "x.txt"
    with pytest.raises(PathNotAllowed):
        resolve_path("../escape.txt", [root])
    with pytest.raises(PathNotAllowed):
        resolve_path(str(tmp_path / "other.txt"), [root])
    with pytest.raises(PathNotAllowed):
        resolve_path("x", [])


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin on Windows")
def test_symlink_cannot_escape(tmp_path):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s")
    (root / "link").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(PathNotAllowed):
        resolve_path("link", [root])


@pytest.mark.parametrize("cmd,verdict", [
    ("echo hi", ALLOW),
    ("git status", ALLOW),
    ("GIT   STATUS --short", ALLOW),
    ("git push", APPROVE),
    ("del important.txt", APPROVE),
    ("echo hi & del x", APPROVE),
    ("echo hi > out.txt", APPROVE),
    ("echo $(whoami)", APPROVE),
    ("dirx", APPROVE),
    ("   ", DENY),
])
def test_classify_command(cmd, verdict):
    safe = ["dir", "echo", "git status"]
    assert classify_command(cmd, safe).action == verdict
