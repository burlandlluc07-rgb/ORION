"""Command line: `python -m orion_bg <command>` (or `orion-bg <command>`)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from . import config
from .client import ClientError, DaemonNotRunning, env_client


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def _print_task(t: dict, full: bool = False) -> None:
    print(f"{t['id']}  {t['status']:<15} {t['kind']:<6} {t['source']:<9} {_when(t['created'])}  {t['prompt'][:70]}")
    if full:
        if t.get("result"):
            print("\nResult:\n" + t["result"])
        if t.get("error"):
            print("\nError:\n" + t["error"])
        for a in t.get("approvals") or []:
            print(f"\napproval {a['id']} [{a['status']}] {a['tool']}: {a['reason']}\n  {json.dumps(a['input'])[:300]}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orion-bg", description="ORION background agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daemon", help="run the daemon in this process (no window)")
    d.add_argument("--console", action="store_true", help="also log to the console")
    d.add_argument("--no-tray", action="store_true")
    sub.add_parser("run", help="run the daemon under a supervisor that restarts it on crash")

    s = sub.add_parser("submit", help="queue a task")
    s.add_argument("prompt")
    s.add_argument("--kind", default="agent", help="agent (Claude) or dummy (no API, for testing)")
    s.add_argument("--timeout", type=float, help="seconds")
    s.add_argument("--wait", action="store_true", help="wait and print the result")

    ls = sub.add_parser("list", help="task history")
    ls.add_argument("--status")
    ls.add_argument("--limit", type=int, default=20)
    sh = sub.add_parser("show", help="one task in full")
    sh.add_argument("id")
    c = sub.add_parser("cancel")
    c.add_argument("id")

    sub.add_parser("approvals", help="actions waiting for your OK")
    a = sub.add_parser("approve")
    a.add_argument("id")
    dn = sub.add_parser("deny")
    dn.add_argument("id")

    sc = sub.add_parser("schedule", help="recurring tasks")
    scs = sc.add_subparsers(dest="action", required=True)
    add = scs.add_parser("add")
    add.add_argument("spec", help="'daily 08:00', 'weekdays 07:30', 'every 30m', 'cron 0 8 * * *', 'at 2026-10-18 09:00'")
    add.add_argument("prompt")
    add.add_argument("--kind", default="agent")
    add.add_argument("--name")
    scs.add_parser("list")
    rm = scs.add_parser("remove")
    rm.add_argument("id")

    sub.add_parser("status")
    sub.add_parser("pause", help="stop starting new tasks")
    sub.add_parser("resume")
    sub.add_parser("stop", help="stop the daemon cleanly")
    sub.add_parser("ui", help="print the task history page URL")
    sub.add_parser("init-config", help="write a config file with the defaults")
    sub.add_parser("install-autostart", help="start with Windows (Task Scheduler)")
    sub.add_parser("uninstall-autostart")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "daemon":
        from .daemon import main as daemon_main

        return daemon_main(console=args.console, tray=False if args.no_tray else None)
    if args.cmd == "run":
        from . import log as logsetup
        from .supervisor import supervise

        logsetup.setup(config.load())
        return supervise()
    if args.cmd == "init-config":
        print(config.write_template())
        return 0
    if args.cmd in ("install-autostart", "uninstall-autostart"):
        from . import autostart

        try:
            print(autostart.install() if args.cmd == "install-autostart" else autostart.uninstall())
            return 0
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    try:
        client = env_client()
        return _client_command(client, args)
    except DaemonNotRunning as exc:
        print(f"error: {exc}. Start it with: python -m orion_bg run", file=sys.stderr)
        return 2
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _client_command(client, args) -> int:
    if args.cmd == "submit":
        task = client.submit(args.prompt, kind=args.kind, source="cli", timeout=args.timeout)
        print(f"queued {task['id']}")
        if args.wait:
            while True:
                task = client.task(task["id"])
                if task["status"] in ("completed", "failed", "cancelled"):
                    break
                if task["status"] == "needs_approval":
                    pending = [a for a in task["approvals"] if a["status"] == "pending"]
                    print("waiting for approval: " + ", ".join(f"{a['id']} ({a['tool']})" for a in pending))
                    return 0
                time.sleep(0.5)
            _print_task(task, full=True)
            return 0 if task["status"] == "completed" else 1
        return 0
    if args.cmd == "list":
        for t in client.tasks(status=args.status, limit=args.limit):
            _print_task(t)
        return 0
    if args.cmd == "show":
        _print_task(client.task(args.id), full=True)
        return 0
    if args.cmd == "cancel":
        print(client.cancel(args.id)["status"])
        return 0
    if args.cmd == "approvals":
        items = client.approvals()
        if not items:
            print("nothing is waiting")
        for a in items:
            print(f"{a['id']}  task {a['task_id']}  {a['tool']}: {a['reason']}\n    {json.dumps(a['input'])[:300]}")
        return 0
    if args.cmd in ("approve", "deny"):
        out = client.approve(args.id) if args.cmd == "approve" else client.deny(args.id)
        print(f"{args.cmd}d" + (" (task queued again)" if out.get("requeued") else ""))
        return 0
    if args.cmd == "schedule":
        if args.action == "add":
            s = client.add_schedule(args.prompt, args.spec, kind=args.kind, name=args.name)
            print(f"{s['id']}  next run {s['next_run'] or 'when the scheduler starts'}  {s['trigger']}")
        elif args.action == "list":
            for s in client.schedules():
                print(f"{s['id']}  {s['trigger']:<40} next {s['next_run']}  {s['prompt'][:50]}")
        else:
            client.remove_schedule(args.id)
            print("removed")
        return 0
    if args.cmd in ("status", "pause", "resume"):
        out = getattr(client, args.cmd)()
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "stop":
        client.shutdown()
        print("stopping")
        return 0
    if args.cmd == "ui":
        print(client.ui_url())
        return 0
    raise SystemExit(f"unknown command {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())


