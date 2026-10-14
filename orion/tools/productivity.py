"""Timers, reminders, routines, notes and long-term memory."""

from __future__ import annotations

import datetime as dt
import time
from typing import Literal

from ..tasks.schedule import ScheduleError, describe, next_run, parse_when
from .registry import ToolContext, ToolError, tool

NOTIFY_PREFIX = "!notify:"


def _engine(ctx: ToolContext):
    if ctx is None or ctx.engine is None:
        raise ToolError("Engine unavailable.")
    return ctx.engine


@tool(category="productivity", summary=lambda a: f"Timer: {a.get('duration')}")
def set_timer(duration: str, label: str = "Timer", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Start a countdown timer, e.g. "5 minutes", "90s", "1h 15m".

    Args:
        duration: How long.
        label: What the timer is for.
    """
    try:
        when = parse_when(duration if duration.strip().lower().startswith("in ") else f"in {duration}")
    except ScheduleError as exc:
        raise ToolError(str(exc)) from exc
    r = _engine(ctx).scheduler.add_once(f"⏱ {label}", NOTIFY_PREFIX + f"{label} — time's up!", when)
    secs = int((when - dt.datetime.now()).total_seconds())
    return f"Timer “{label}” set for {secs // 60} min {secs % 60} s (id {r['id']})."


@tool(category="productivity", summary=lambda a: f"Remind “{a.get('message')}” {a.get('when')}")
def set_reminder(message: str, when: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Remind the user about something at a time: "in 20 minutes", "at 5pm", "tomorrow 9:00", or ISO time.

    Args:
        message: What to remind them about.
        when: When to fire.
    """
    try:
        at = parse_when(when)
    except ScheduleError as exc:
        raise ToolError(str(exc)) from exc
    r = _engine(ctx).scheduler.add_once(f"🔔 {message[:40]}", NOTIFY_PREFIX + message, at)
    return f"Reminder set for {at.strftime('%a %d %b at %H:%M')} (id {r['id']})."


@tool(risk="confirm", category="productivity", summary=lambda a: f"Create routine “{a.get('name')}” ({a.get('schedule')})")
def create_routine(name: str, prompt: str, schedule: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Schedule ORION to run a prompt automatically, e.g. a morning briefing every weekday.

    Args:
        name: Short routine name.
        prompt: The instruction ORION will execute each time (as if the user typed it).
        schedule: "daily 08:00", "weekdays 07:30", "weekly mon 09:00", "every 2h" or "once 2026-01-31T18:00".
    """
    try:
        next_run(schedule)
    except (ScheduleError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    r = _engine(ctx).scheduler.add(name, prompt, schedule)
    return f"Routine “{name}” created — {describe(schedule)}. Next run {time.strftime('%a %H:%M', time.localtime(r['next_run']))}."


@tool(category="productivity")
def list_scheduled(ctx: ToolContext = None) -> list[dict]:  # type: ignore[assignment]
    """List active timers, reminders and routines."""
    out = []
    for r in _engine(ctx).store.list_routines():
        out.append({
            "id": r["id"], "name": r["name"], "schedule": describe(r["schedule"]), "enabled": r["enabled"],
            "next": time.strftime("%a %d %b %H:%M", time.localtime(r["next_run"])) if r["next_run"] else None,
        })
    return out


@tool(category="productivity", summary=lambda a: f"Cancel scheduled item {a.get('id_or_name')}")
def cancel_scheduled(id_or_name: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Cancel a timer, reminder or routine by id or (part of) its name.

    Args:
        id_or_name: The id from list_scheduled, or part of the name.
    """
    eng = _engine(ctx)
    q = id_or_name.lower()
    for r in eng.store.list_routines():
        if r["id"] == id_or_name or q in r["name"].lower():
            eng.scheduler.remove(r["id"])
            return f"Cancelled “{r['name']}”."
    raise ToolError("Nothing scheduled matches that.")


@tool(category="memory", summary=lambda a: f"Remember: {a.get('fact')}")
def remember(fact: str, about_user: bool = False, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Save a durable fact to long-term memory (preferences, names, projects, recurring details).

    Args:
        fact: A self-contained statement, e.g. "User's sister is named Maya".
        about_user: True for core profile facts that should always be known.
    """
    mid = _engine(ctx).store.remember(fact, "profile" if about_user else "fact")
    _engine(ctx).bus.emit("memory.changed")
    return f"Remembered (#{mid})."


@tool(category="memory")
def recall(query: str, ctx: ToolContext = None) -> list[dict]:  # type: ignore[assignment]
    """Search long-term memory.

    Args:
        query: Keywords to look for.
    """
    hits = _engine(ctx).store.recall(query, limit=10)
    return [{"id": h["id"], "fact": h["text"]} for h in hits] or [{"fact": "Nothing relevant remembered."}]


@tool(risk="confirm", category="memory", summary=lambda a: f"Forget memory #{a.get('memory_id')}")
def forget(memory_id: int, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Delete a memory by id (use recall to find ids).

    Args:
        memory_id: The memory's id.
    """
    if not _engine(ctx).store.forget(int(memory_id)):
        raise ToolError("No memory with that id.")
    _engine(ctx).bus.emit("memory.changed")
    return "Forgotten."


@tool(category="productivity", summary=lambda a: "Add a note")
def add_note(text: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Append a quick note to the user's ORION notebook (notes.md in the ORION data folder).

    Args:
        text: The note.
    """
    path = _engine(ctx).config.root / "notes.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- [{dt.datetime.now():%Y-%m-%d %H:%M}] {text.strip()}\n")
    return "Noted."


@tool(category="productivity")
def read_notes(last: int = 20, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Read the most recent notes from the ORION notebook.

    Args:
        last: How many recent notes.
    """
    path = _engine(ctx).config.root / "notes.md"
    if not path.exists():
        return "No notes yet."
    lines = path.read_text("utf-8").splitlines()
    return "\n".join(lines[-max(1, last):])


@tool(category="missions", summary=lambda a: f"Start mission: {str(a.get('goal'))[:60]}")
async def start_mission(goal: str, title: str = "", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Start a long-running background MISSION (research, comparisons, reports, multi-step chores) that ORION works on
    autonomously while the user does other things. Use when a job would take more than a few steps or the user says
    "in the background" / "while I'm away". The user is notified when it finishes.

    Args:
        goal: A complete, self-contained description of what to achieve and what to deliver.
        title: Short title (optional).
    """
    m = _engine(ctx).missions.start(goal, title or None)
    return f"Mission started (id {m['id']}). I'll notify you when it's done — you can follow it under Missions."


@tool(category="missions")
def list_missions(ctx: ToolContext = None) -> list[dict]:  # type: ignore[assignment]
    """List recent background missions and their status."""
    return [{"id": m["id"], "title": m["title"], "status": m["status"], "summary": (m.get("summary") or "")[:200]}
            for m in _engine(ctx).missions.list()[:10]]


@tool(category="missions", summary=lambda a: f"Cancel mission {a.get('id')}")
async def cancel_mission(id: str, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Stop a running or queued mission.

    Args:
        id: Mission id from list_missions.
    """
    if not _engine(ctx).missions.cancel(id):
        raise ToolError("No running mission with that id.")
    return "Mission stopped."


@tool(category="productivity", summary=lambda a: f"To-do: {a.get('action')} {a.get('item', '')}".strip())
def todo(action: Literal["add", "list", "done", "undo", "remove", "clear_done"] = "list", item: str = "",
         list_name: str = "to-do", due: str = "", ctx: ToolContext = None) -> list[dict] | str:  # type: ignore[assignment]
    """The user's to-do and shopping lists (several named lists). Adding with a due time also sets a reminder.

    Args:
        action: add, list, done (tick off), undo (un-tick), remove, or clear_done.
        item: The item text (for add), or its number/text (for done/undo/remove).
        list_name: Which list, e.g. "to-do", "shopping", "work".
        due: Optional due time for add, e.g. "tomorrow 9:00" or "friday 5pm".
    """
    e = _engine(ctx)
    items: list[dict] = e.store.kv_get("todos", [])
    lst = list_name.strip().lower() or "to-do"
    mine = [i for i in items if i["list"] == lst]
    if action == "add":
        if not item.strip():
            raise ToolError("What should I add?")
        entry = {"id": max((i["id"] for i in items), default=0) + 1, "list": lst, "text": item.strip(), "done": False,
                 "due": None, "created": time.time()}
        note = ""
        if due:
            try:
                when = parse_when(due)
            except ScheduleError as exc:
                raise ToolError(str(exc)) from exc
            entry["due"] = when.isoformat(timespec="minutes")
            e.scheduler.add_once(f"☐ {item.strip()[:40]}", NOTIFY_PREFIX + f"To-do due: {item.strip()}", when)
            note = f", due {when:%a %d %b %H:%M} (reminder set)"
        items.append(entry)
        e.store.kv_set("todos", items)
        return f"Added “{entry['text']}” to {lst}{note}. {len(mine) + 1} item(s) on it."
    if action == "list":
        if not list_name.strip() or lst == "all":
            mine = items
        open_items = [i for i in mine if not i["done"]]
        if not mine:
            lists = sorted({i["list"] for i in items})
            return f"The {lst} list is empty." + (f" Lists: {', '.join(lists)}." if lists else "")
        return [{"n": i["id"], "item": i["text"], "done": i["done"], **({"due": i["due"]} if i["due"] else {}),
                 **({"list": i["list"]} if lst == "all" else {})} for i in open_items + [i for i in mine if i["done"]]]
    if action == "clear_done":
        kept = [i for i in items if not (i["list"] == lst and i["done"])]
        e.store.kv_set("todos", kept)
        return f"Cleared {len(items) - len(kept)} finished item(s) from {lst}."
    target = None
    key = item.strip().lstrip("#")
    if key.isdigit():
        target = next((i for i in items if i["id"] == int(key)), None)
    if target is None and key:
        target = next((i for i in mine if i["text"].lower() == key.lower()), None) or \
            next((i for i in mine if key.lower() in i["text"].lower()), None)
    if target is None:
        raise ToolError(f"No item “{item}” on the {lst} list.")
    if action == "remove":
        items.remove(target)
        msg = f"Removed “{target['text']}”."
    else:
        target["done"] = action == "done"
        msg = f"{'Ticked off' if target['done'] else 'Reopened'} “{target['text']}”."
    e.store.kv_set("todos", items)
    left = sum(1 for i in items if i["list"] == target["list"] and not i["done"])
    return f"{msg} {left} left on {target['list']}."


@tool(category="web")
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert money between currencies at today's exchange rate (e.g. 25 USD → EUR).

    Args:
        amount: How much.
        from_currency: ISO code like USD, EUR, GBP, ZAR, JPY.
        to_currency: ISO code to convert to.
    """
    import httpx

    src, dst = from_currency.strip().upper()[:3], to_currency.strip().upper()[:3]
    try:
        r = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": src, "symbols": dst}, timeout=8)
        if r.status_code == 200 and dst in r.json().get("rates", {}):
            j = r.json()
            rate, day, source = j["rates"][dst], j.get("date", ""), "European Central Bank"
        else:
            raise ValueError("unsupported pair")
    except Exception:
        try:
            j = httpx.get(f"https://open.er-api.com/v6/latest/{src}", timeout=8).json()
        except Exception as exc:
            raise ToolError(f"Exchange rates are unavailable right now: {exc}") from exc
        if j.get("result") != "success" or dst not in j.get("rates", {}):
            raise ToolError(f"Unknown currency code: {src if j.get('result') != 'success' else dst}.") from None
        rate, day, source = j["rates"][dst], j.get("time_last_update_utc", "")[:16], "open.er-api.com"
    return f"{amount:,.2f} {src} = {amount * rate:,.2f} {dst} (rate {rate:.4f}, {source}, {day})."
