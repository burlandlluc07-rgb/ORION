"""The "What ORION did" log: one newest-first list of every action ORION took.

Nothing new is stored. Each entry is read from records ORION already keeps:

* every tool step of a task (live ones from the tracker, finished ones from the tasks table), with its
  lifecycle record (route, approval, verification evidence; see orion/tasks/lifecycle.py);
* every background-agent task (orion/background/store.py), with the approvals it asked for.

An entry carries a short summary only. Message text (WhatsApp bodies) is cut out of the summary, results are
shown only when an action failed (an error, not content), and arguments are never included.

``recap`` sums one calendar week of these entries (Monday to Sunday, the PC's local time): counts, who
approved, the routes used, the actions it took most, a few checked results as proof, and every failure.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timedelta
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
                   after: str = "", limit: int = 100) -> dict[str, Any]:
    """``outcome`` is a group from GROUPS or a single outcome; ``q`` matches summary, tool and task title.
    ``after`` is the id of the last entry already shown: the page starts with the one after it (ids, not
    times, so entries that share a start time are never skipped)."""
    want = GROUPS.get(outcome, {outcome}) if outcome else None
    needle = q.strip().lower()
    rows = [e for e in entries
            if (want is None or e["outcome"] in want)
            and (not route or e["route"] == route)
            and (not needle or needle in f"{e['summary']} {e['tool']} {e['task']}".lower())]
    if after:
        idx = next((i for i, e in enumerate(rows) if e["id"] == after), None)
        rows = rows[idx + 1:] if idx is not None else []
    page = rows[:limit]
    counts = {g: sum(1 for e in entries if e["outcome"] in outs) for g, outs in GROUPS.items()}
    routes = sorted({e["route"] for e in entries if e["route"]})
    return {"entries": page, "more": len(rows) > len(page), "total": len(entries), "counts": counts,
            "routes": [{"id": r, "label": ROUTES.get(r, r)} for r in routes]}


# ── Weekly recap ─────────────────────────────────────────────────────────────
RECAP_PROBLEMS = 10
RECAP_PROOF = 3
RECAP_TOP = 6


def week_bounds(now: float, weeks_ago: int = 0) -> tuple[float, float]:
    """Monday 00:00 (local time) of the week ``weeks_ago`` weeks back, and the Monday after it."""
    today = datetime.fromtimestamp(now).date()
    monday = today - timedelta(days=today.weekday(), weeks=weeks_ago)
    return (datetime.combine(monday, time()).timestamp(),
            datetime.combine(monday + timedelta(days=7), time()).timestamp())


def tool_label(tool: str) -> str:
    return "Background task" if tool == "background_task" else (tool.replace("_", " ").strip().capitalize() or "Action")


def _approvals(entries: list[dict]) -> dict[str, int]:
    out = {"you_approved": 0, "you_declined": 0, "automatic": 0, "your_command": 0}
    for e in entries:
        by = e.get("approved_by") or ""
        if "You approved" in by or "You declined" in by:
            out["you_approved"] += by.count("You approved")
            out["you_declined"] += by.count("You declined")
        elif by.startswith("Allowed automatically"):
            out["automatic"] += 1
        elif by.startswith("Your own"):
            out["your_command"] += 1
    return out


def recap(entries: Iterable[dict], now: float, weeks_ago: int = 0) -> dict[str, Any]:
    """What ORION did in one week, from ``collect``'s entries. Read-only: nothing is stored or sent."""
    start, end = week_bounds(now, weeks_ago)
    prev_start, _ = week_bounds(now, weeks_ago + 1)
    entries = list(entries)
    week = sorted((e for e in entries if start <= (e.get("at") or 0) < end), key=lambda e: e["at"], reverse=True)
    outcomes = Counter(e["outcome"] for e in week)
    groups = {g: sum(outcomes[o] for o in outs) for g, outs in GROUPS.items()}

    first = datetime.fromtimestamp(start).date()
    days = []
    for i in range(7):
        d: date = first + timedelta(days=i)
        lo = datetime.combine(d, time()).timestamp()
        hi = datetime.combine(d + timedelta(days=1), time()).timestamp()
        day = [e for e in week if lo <= e["at"] < hi]
        days.append({"date": d.isoformat(), "count": len(day),
                     "problems": sum(1 for e in day if e["outcome"] in GROUPS["problem"]),
                     "future": lo > now})

    tools: Counter = Counter(e["tool"] for e in week)
    failed_by_tool: Counter = Counter(e["tool"] for e in week if e["outcome"] in GROUPS["problem"])
    routes: Counter = Counter(e["route"] for e in week if e["route"])
    return {
        "start": start,
        "end": end,
        "weeks_ago": weeks_ago,
        "total": len(week),
        "previous_total": sum(1 for e in entries if prev_start <= (e.get("at") or 0) < start),
        "has_older": any((e.get("at") or 0) < start for e in entries),
        "tasks": len({e["task_id"] for e in week if e.get("task_id")}),
        "worked": groups["ok"],
        "checked": outcomes["verified"],
        "read_only": outcomes["done"],
        "not_checked": outcomes["unverified"],
        "problems": groups["problem"],
        "declined": groups["declined"],
        "live": groups["live"],
        "desktop": sum(1 for e in week if e.get("desktop", "none") != "none"),
        "approvals": _approvals(week),
        "days": days,
        "routes": [{"id": r, "label": ROUTES.get(r, r), "count": n} for r, n in routes.most_common()],
        "top": [{"tool": t, "label": tool_label(t), "count": n, "problems": failed_by_tool[t]}
                for t, n in tools.most_common(RECAP_TOP)],
        "problem_entries": [e for e in week if e["outcome"] in GROUPS["problem"]][:RECAP_PROBLEMS],
        "proof": [e for e in week if e["outcome"] == "verified" and e["evidence"]][:RECAP_PROOF],
    }
