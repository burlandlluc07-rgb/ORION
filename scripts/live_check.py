"""End-to-end check against the real Gemini API (uses ~5 free-tier requests).

    GEMINI_API_KEY=... python scripts/live_check.py

Verifies: the key, both routed models, that Google accepts every tool schema,
a real tool-calling round trip, vision, and (optionally) a Live session handshake.
Exits non-zero on failure so CI can use it.

With ORION_BACKUP_API_KEY set (and optionally ORION_BACKUP_PROVIDER, default "groq",
and ORION_BACKUP_MODEL), it also checks the backup brain directly, and the checks
above fall back to it when Gemini's free quota is used up, as they would in the app.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ORION_HOME", tempfile.mkdtemp(prefix="orion-live-"))

from orion.config import Config  # noqa: E402
from orion.engine import Engine  # noqa: E402

results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
    if not ok and os.environ.get("GITHUB_ACTIONS"):  # an annotation shows the failure on the check run itself
        print(f"::error title=live check: {name}::{detail.replace(chr(10), ' ')}", flush=True)


def skip(name: str, detail: str) -> None:
    print(f"SKIP  {name}: {detail}", flush=True)


async def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set — skipping live checks.")
        return 0
    e = Engine(Config())
    e.bus.bind(asyncio.get_running_loop())
    e.load_tools()
    e.skills.load()
    e.config.settings.approval_mode = "trust"  # no UI here; only safe tools are exercised anyway
    from orion.tools.apps import INDEX

    INDEX.ready.set()

    if os.environ.get("ORION_BACKUP_API_KEY"):
        from orion.brain.backup import BackupError

        e.config.settings.backup_provider = os.environ.get("ORION_BACKUP_PROVIDER") or "groq"
        e.config.settings.backup_model = os.environ.get("ORION_BACKUP_MODEL") or ""
        t0 = time.perf_counter()
        try:
            if not e.brain.backup.ready():  # e.g. OpenRouter: pick a free model the way Settings does
                from orion.brain.backup import BackupBrain, pick_default_model

                base, _, key = e.brain.backup.endpoint()
                e.config.settings.backup_model = pick_default_model(
                    e.config.settings.backup_provider, await BackupBrain.list_models(base, key))
            if not e.brain.backup.ready():
                raise BackupError("no model found: set ORION_BACKUP_MODEL for this provider")
            txt = await e.brain.backup.generate_text("Reply with exactly the word: ready")
            report("backup brain reply", "ready" in txt.lower(),
                   f"{e.brain.backup.endpoint()[1]}: {txt.strip()[:40]!r} in {time.perf_counter() - t0:.2f}s")
        except Exception as exc:
            report("backup brain reply", False, repr(exc)[:300])

    try:
        info = await e.brain.discover()
        report("key + model list", True, f"{len(info['models'])} models; live model: {info['live_model']}")
    except Exception as exc:
        report("key + model list", False, repr(exc))
        return 1

    for tier in ("fast", "smart"):
        t0 = time.perf_counter()
        try:
            txt = await e.brain.generate_text("Reply with exactly the word: ready", tier=tier)
            report(f"{tier} model reply", "ready" in txt.lower(), f"{txt.strip()[:40]!r} in {time.perf_counter() - t0:.2f}s")
        except Exception as exc:
            report(f"{tier} model reply", False, repr(exc)[:300])

    # Full agent turn with every tool declared: proves Google accepts all schemas and tool calling works.
    t0 = time.perf_counter()
    res = await e.agent.handle("Use your tools: what is the current date and time on this computer? Answer in one line.",
                               mode="smart")
    steps = [s for t in e.store.list_tasks() for s in t["steps"]]
    used = [s["tool"] for s in steps]
    report("agent tool calling (all schemas)", "get_time_and_date" in used and not res["reply"].startswith("⚠"),
           f"tools={used} reply={res['reply'][:80]!r} in {time.perf_counter() - t0:.2f}s"
           + (" (backup brain)" if e.brain.on_backup else ""))

    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (320, 120), "white")
        ImageDraw.Draw(img).text((20, 45), "ORION 42", fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        ans = await e.brain.vision("What number is written in the image? Reply with just the number.", buf.getvalue(), "image/png")
        report("vision", "42" in ans, ans.strip()[:40] + (" (backup brain)" if e.brain.on_backup else ""))
    except Exception as exc:
        if "backup brain didn't answer" in str(exc) and "quota" in str(exc).lower():
            skip("vision", "Gemini is out of quota and the backup model can't read images")
        else:
            report("vision", False, repr(exc)[:300])

    if e.brain.live_model and os.environ.get("ORION_LIVE_CHECK", "1") == "1":
        try:
            from orion.voice.live import LiveSession

            ls = LiveSession(e, None)  # type: ignore[arg-type]
            async with e.brain.client.aio.live.connect(model=e.brain.live_model, config=ls._config()):
                pass
            report("live voice session", True, f"connected to {e.brain.live_model} with {len(e.registry.all())} tools")
        except Exception as exc:
            report("live voice session", False, repr(exc)[:300])

    e.store.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} live checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
