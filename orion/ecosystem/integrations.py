"""Integration readiness: what each planned integration can do today, and what stays blocked.

Checking status never changes anything: it reads ORION's own settings and looks for installed
programs, without signing in, sending or running anything. Each entry says::

    {"id", "name", "state": ready | partial | not_set_up | not_found | blocked,
     "can": [...read-only things that work], "blocked": [...things ORION won't do], "next": "what unblocks it"}

Order follows the plan, safest first: Google (read-only), WhatsApp (Cloud API), AutoCAD (Core Console).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..tools.registry import Tool, schema_for

if TYPE_CHECKING:
    from ..engine import Engine


def autocad_installs() -> list[dict]:
    """AutoCAD installations that ship the Core Console (accoreconsole.exe), newest first."""
    found = []
    for var in ("ProgramFiles", "ProgramW6432"):
        root = os.environ.get(var)
        if not root:
            continue
        for exe in sorted((Path(root) / "Autodesk").glob("AutoCAD*/accoreconsole.exe")):
            m = re.search(r"(\d{4})", exe.parent.name)
            entry = {"product": exe.parent.name, "version": m.group(1) if m else "", "core_console": str(exe)}
            if entry not in found:
                found.append(entry)
    return sorted(found, key=lambda e: e["version"], reverse=True)


def _google(engine: "Engine") -> dict:
    pub = engine.accounts.public()
    email = pub.get("email") or {}
    gmail = "gmail" in str(email.get("imap_host", "")).lower() or str(email.get("address", "")).lower().endswith(
        "@gmail.com")
    cals = [c for c in pub.get("calendars", []) if "google" in c.get("host", "")]
    mcp = [c for c in engine.connectors.list() if "google" in c["name"] or "workspace" in c["name"]]
    can, blocked = [], ["sending email still asks first, every time", "creating or changing calendar events directly"]
    if gmail:
        can.append("read and search Gmail (email_inbox, email_search, email_read) and sort it by importance "
                   "(email_by_importance)")
    if cals:
        can.append(f"read {len(cals)} Google calendar(s) (calendar_upcoming)")
    for c in mcp:
        if c.get("read_only"):
            can.append(f"connector {c['name']}: {len(c.get('tools', []))} read-only tool(s), "
                       f"{len(c.get('blocked', []))} write tool(s) blocked")
        else:
            blocked.append(f"connector {c['name']} is not marked read-only; set \"read_only\": true until it is tested")
    if not mcp:
        blocked.append("Google Workspace MCP (Developer Preview): not added; it needs an OAuth sign-in by you")
    state = "ready" if can and mcp else "partial" if can else "not_set_up"
    return {"id": "google", "name": "Google (read-only)", "state": state, "can": can, "blocked": blocked,
            "read_only_test": "ask ORION for today's events or the latest email; nothing is changed",
            "next": "" if state == "ready" else
            "Add a Google calendar's secret iCal address and/or a Gmail app password in Accounts, or add the "
            "Google Workspace MCP server in Connectors with \"read_only\": true after signing in with OAuth."}


def _whatsapp(engine: "Engine") -> dict:
    wa = engine.whatsapp.public()
    base = {"id": "whatsapp", "name": "WhatsApp (Cloud API)"}
    always = ["sending without your approval: every message shows the recipient and exact text first",
              "reading WhatsApp messages (needs a webhook, not built)",
              "your personal WhatsApp account: messages come from your WhatsApp Business number"]
    contacts = len(wa["contacts"])
    draft = (f"open a typed-in message in your own WhatsApp for you to press Send (whatsapp_draft), to "
             f"{contacts} saved contact(s) or any number")
    linked = wa["gateway"]["state"] == "linked"
    if linked:  # the opt-in background link: personal sends and reading, no window
        always = always[:1]
        draft += (f"; send approved messages from your own number {wa['gateway']['account']} and read recent chats "
                  "in the background (unofficial link you turned on)")
    if not wa["configured"]:
        return {**base, "state": "ready" if linked else "partial", "can": [draft],
                "blocked": always[:1] if linked else
                ["sending in the background: that needs the background link or a WhatsApp Business number"]
                + always[1:2],
                "read_only_test": "whatsapp_read or whatsapp_contacts; nothing is sent" if linked else
                "whatsapp_contacts; nothing is opened or sent",
                "next": "Personal WhatsApp works now as a draft you send. For background sending from a business "
                        "number, add the Phone number ID and access token from Meta in Accounts → WhatsApp."}
    if wa["dry_run"]:
        return {**base, "state": "partial", "blocked": ["really sending: dry run is on"] + always,
                "can": [draft, f"draft and approve messages to {contacts} saved contact(s) or any number, without sending"],
                "read_only_test": "ask ORION to WhatsApp someone: you see the approval card, nothing is sent",
                "next": "Add the Phone number ID and access token from Meta to send for real."}
    can = [draft, f"send approved messages from {wa['verified_name'] or 'your business number'} "
           f"{wa['display_number']} to {contacts} saved contact(s) or any number"]
    if wa["business_account_id"]:
        can.append("list your approved message templates (whatsapp_templates)")
    return {**base, "state": "ready" if wa["has_token"] else "partial", "can": can, "blocked": always,
            "read_only_test": "whatsapp_templates or whatsapp_contacts; nothing is sent",
            "next": "" if wa["has_token"] else "The access token is missing; paste it again in Accounts → WhatsApp."}


def _autocad(engine: "Engine") -> dict:
    installs = autocad_installs()
    if not installs:
        return {"id": "autocad", "name": "AutoCAD (Core Console)", "state": "not_found", "can": [], "installs": [],
                "blocked": ["running scripts in AutoCAD"], "read_only_test": "detection only",
                "next": "No AutoCAD with accoreconsole.exe was found on this PC."}
    return {"id": "autocad", "name": "AutoCAD (Core Console)", "state": "partial", "installs": installs,
            "can": [f"found {installs[0]['product']} with the Core Console (runs without a window)"],
            "blocked": ["running scripts on drawings: no workflow is defined yet",
                        "the ActiveX / visible AutoCAD route"],
            "read_only_test": "detection only: nothing was started",
            "next": "Name the AutoCAD workflows you want (for example batch-plot a folder to PDF). Each becomes a "
                    "Core Console script that works on a copy and is checked before the original is touched."}


def status(engine: "Engine") -> list[dict]:
    out = []
    for check in (_google, _whatsapp, _autocad):
        try:
            out.append(check(engine))
        except Exception as exc:  # one broken check must not hide the others
            out.append({"id": check.__name__.strip("_"), "state": "error", "error": f"{type(exc).__name__}: {exc}"})
    return out


def status_tool(engine: "Engine") -> Tool:
    def integration_status() -> list[dict]:
        """Show which integrations (Google, WhatsApp, AutoCAD) are ready, what they can do read-only, what is blocked
        and what would unblock it. Changes nothing."""
        return status(engine)

    schema, _, desc = schema_for(integration_status)
    return Tool(name="integration_status", description=desc, parameters=schema, fn=integration_status, risk="safe",
                category="ecosystem", source="core")
