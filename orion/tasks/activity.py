"""The "What ORION did" log: one newest-first list of every action ORION took.

Nothing new is stored. Each entry is read from records ORION already keeps:

* every tool step of a task (live ones from the tracker, finished ones from the tasks table), with its
  lifecycle record (route, approval, verification evidence; see orion/tasks/lifecycle.py);
* every background-agent task (orion/background/store.py), with the approvals it asked for.

An entry carries a short summary only. Message text (WhatsApp bodies) is cut out of the summary, results are
shown only when an action failed (an error, not content), and arguments are never included.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

ROUTES = {
    "api_mcp": "API / connector",
    "cli": "Script",
    "headless_browser": "Headless browser",
    "app_automation": "App automation",
    "gui_env": "Separate desktop",
    "foreground": "Your screen",
    "local": "On this PC",
    "background": "Background agent",
}

# Outcome groups the screen filters by.
GROUPS = {
    "ok": {"verified", "done", "unverified"},
    "problem": {"failed", "attention"},
    "declined": {"declined", "stopped"},
    "live": {"running", "waiting"},
}

# Tools whose label quotes the message itself: keep who it went to, drop what it said.
MESSAGE_TOOLS = {"whatsapp_send", "whatsapp_send_personal"}
_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"")
SUMMARY_MAX = 140
ERROR_MAX = 200


def _short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def summary(tool: str, label: str) -> str:
    if tool in MESSAGE_TOOLS:
        label = _QUOTED.sub("(message not shown)", label)
        label = label.replace(" (ORION opens WhatsApp and presses Send)", "")
    return _short(label, SUMMARY_MAX)


def _outcome(step: dict, lc: dict | None) -> str:
    if lc:
        stage = lc.get("stage", "")
        ver = (lc.get("verification") or {}).get("status")
        if stage == "completed":
            return {"passed": "verified", "not_applicable": "done"}.get(ver or "", "unverified")
        if stage == "needs_attention":
            return "attention"
        if stage == "failed":
            return "failed"
        if stage == "cancelled":
            appr = lc.get("approval") or {}
            return "declined" if appr.get("granted") is False else "stopped"
        return "waiting" if stage == "awaiting_permission" else "running"
    return {"done": "done", "failed": "failed", "denied": "declined", "stopped": "stopped", "attention": "attention",
            "waiting": "waiting"}.get(step.get("status", ""), "running")


def _approved_by(lc: dict | None, step: dict) -> str:
    appr = (lc or {}).get("approval")
    if not appr:
        return "You declined" if step.get("status") == "denied" else ""
    by = appr.get("by", "")
    if by == "user":
        return "You approved" if appr.get("granted") else "You declined"
    return {"policy": "Allowed automatically (settings)", "the user's own command": "Your own command",
            "the user's own control": "Your own button"}.get(by, by)


def from_step(task: dict, step: dict) -> dict:
    lc = step.get("lifecycle") or None
    outcome = _outcome(step, lc)
    ver = (lc or {}).get("verification") or {}
    route = ((lc or {}).get("capabilities") or {}).get("route", "")
    evidence = [_short(e, 200) for e in ver.get("evidence", [])][:4]
    error = ""
    if outcome == "attention":
        error = _short(ver.get("reason", ""), ERROR_MAX)
    elif outcome == "failed":
        error = _short(step.get("result") or "", ERROR_MAX)
    elif outcome == "unverified" and ver.get("reason"):
        evidence = evidence or [_short(ver["reason"], 200)]
    return {
        "id": step.get("id"),
        "kind": "step",
        "at": step.get("started") or task.get("created"),
        "ended": step.get("ended"),
        "tool": step.get("tool", ""),
        "summary": summary(step.get("tool", ""), step.get("label", "")),
        "task_id": task.get("id"),
        "task": _short(task.get("title", ""), 90),
        "conv_id": task.get("conv_id"),
        "route": route,
        "route_label": ROUTES.get(route, "Not recorded" if not route else route),
        "desktop": ((lc or {}).get("capabilities") or {}).get("desktop", "none"),
        "risk": step.get("risk", "safe"),
        "outcome": outcome,
        "approved_by": _approved_by(lc, step),
        "evidence": evidence,
        "error": error,
    }


def from_background(task: dict, approvals: list[dict]) -> dict:
    status = task.get("status", "")
    outcome = {"completed": "unverified", "failed": "failed", "cancelled": "stopped",
               "needs_approval": "waiting"}.get(status, "running")
    decided = [a for a in approvals if a.get("status") in ("approved", "denied")]
    if decided:
        approved_by = "; ".join(f"You {'approved' if a['status'] == 'approved' else 'declined'} {a.get('tool', '')}"
                                for a in decided[:3])
    else:
        approved_by = "Waiting for you" if status == "needs_approval" else "Allowed automatically (settings)"
    evidence = []
    if status == "completed" and task.get("result"):
        evidence = [f"agent's own report: {_short(task['result'], 160)}"]
    return {
        "id": f"bg-{task.get('id')}",
        "kind": "background",
        "at": task.get("started") or task.get("created"),
        "ended": task.get("finished"),
        "tool": "background_task",
        "summary": _short(task.get("prompt", ""), SUMMARY_MAX),
        "task_id": task.get("id"),
        "task": "Background task",
        "conv_id": None,
        "route": "background",
        "route_label": ROUTES["background"],
        "desktop": "none",
        "risk": "confirm" if approvals else "safe",
        "outcome": outcome,
        "approved_by": approved_by,
        "evidence": evidence,
        "error": _short(task.get("error") or "", ERROR_MAX) if status == "failed" else "",
    }


def collect(tasks: Iterable[dict], background: Iterable[tuple[dict, list[dict]]] = ()) -> list[dict]:
    """Every entry, newest first. ``tasks`` may repeat an id (live and saved copy): the first one wins."""
    seen: set[str] = set()
    out: list[tuple[float, int, dict]] = []
    for t in tasks:
        if t.get("id") in seen:
            continue
        seen.add(t.get("id"))
        out.extend((e.get("at") or 0, i, e) for i, e in enumerate(from_step(t, s) for s in t.get("steps") or []))
    out.extend((e.get("at") or 0, 0, e) for e in (from_background(t, a) for t, a in background))
    # Steps of one task can share a start time (Windows' clock ticks every ~15 ms): later steps still go first.
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [e for _, _, e in out]


def filter_entries(entries: list[dict], *, outcome: str = "", route: str = "", q: str = "",
                   before: float | None = None, limit: int = 100) -> dict[str, Any]:
    """``outcome`` is a group from GROUPS or a single outcome; ``q`` matches summary, tool and task title."""
    want = GROUPS.get(outcome, {outcome}) if outcome else None
    needle = q.strip().lower()
    rows = [e for e in entries
            if (want is None or e["outcome"] in want)
            and (not route or e["route"] == route)
            and (before is None or (e.get("at") or 0) < before)
            and (not needle or needle in f"{e['summary']} {e['tool']} {e['task']}".lower())]
    page = rows[:limit]
    counts = {g: sum(1 for e in entries if e["outcome"] in outs) for g, outs in GROUPS.items()}
    routes = sorted({e["route"] for e in entries if e["route"]})
    return {"entries": page, "more": len(rows) > len(page), "total": len(entries), "counts": counts,
            "routes": [{"id": r, "label": ROUTES.get(r, r)} for r in routes]}
