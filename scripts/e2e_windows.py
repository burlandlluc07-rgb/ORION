"""End-to-end test of the *installed* ORION on a real Windows desktop (GitHub Actions runner).

Launches ORION.exe with its native window, drives it through its local API like a user
would, verifies real effects on the PC (processes, files, schedules) and takes screenshots.
Gemini scenarios run only when GEMINI_API_KEY is set.

Outputs e2e/report.md, e2e/report.json and e2e/*.jpg, and prints the screenshots as
base64 log lines ("E2EIMG|name|chunk") so they can be recovered from the job log.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
from PIL import ImageGrab

EXE = Path(os.environ.get("ORION_EXE", Path(os.environ["LOCALAPPDATA"]) / "Programs" / "ORION" / "ORION.exe"))
HOME = Path(os.environ["ORION_HOME"])
OUT = Path("e2e")
OUT.mkdir(exist_ok=True)
HAS_KEY = bool(os.environ.get("GEMINI_API_KEY"))
DESKTOP = Path.home() / "Desktop"

for _stream in (sys.stdout, sys.stderr):  # the runner console is cp1252; keep emoji/quotes from crashing prints
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

results: list[dict] = []
shots: list[str] = []


def shot(name: str) -> None:
    time.sleep(0.8)
    try:
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        img.thumbnail((1400, 1400))
        img.save(OUT / f"{name}.jpg", "JPEG", quality=62, optimize=True)
        shots.append(name)
    except Exception as exc:
        print(f"screenshot {name} failed: {exc}")


def record(name: str, ok: bool, detail: str, **extra) -> None:
    results.append({"name": name, "ok": ok, "detail": detail, **extra})
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail[:300]}", flush=True)


class Orion:
    def __init__(self) -> None:
        info = json.loads((HOME / "instance.json").read_text())
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{info['port']}", headers={"x-orion-token": info["token"]},
                              timeout=60)
        self.conv: str | None = None
        self.approved: list[str] = []
        self._stop = threading.Event()
        threading.Thread(target=self._auto_approve, daemon=True).start()

    def _auto_approve(self) -> None:
        """Plays the user: approves every risky step (after a screenshot of the first approval card)."""
        first = True
        while not self._stop.is_set():
            try:
                for a in self.c.get("/api/state").json().get("approvals", []):
                    if first:
                        shot("05_approval_card")
                        first = False
                    self.c.post(f"/api/approvals/{a['id']}", json={"decision": "approve"})
                    self.approved.append(a["label"])
            except Exception:
                pass
            time.sleep(0.7)

    def ask(self, text: str, timeout: float = 120, mode: str = "auto") -> dict:
        t0 = time.perf_counter()
        r = self.c.post("/api/chat", json={"text": text, "conv_id": self.conv, "mode": mode}).json()
        self.conv = r["conv_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.6)
            data = self.c.get(f"/api/conversations/{self.conv}").json()
            msgs = data["messages"]
            running = [t for t in data["tasks"] if t["status"] in ("running", "waiting")]
            if msgs and msgs[-1]["role"] == "assistant" and not running:
                last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
                if len(msgs) - 1 > last_user:
                    m = msgs[-1]
                    tools = [s["tool"] + ":" + s["status"] for t in data["tasks"] for s in t["steps"]
                             if t["id"] == m["meta"].get("task_id")]
                    return {"reply": m["content"], "model": m["meta"].get("model"), "tools": tools,
                            "seconds": round(time.perf_counter() - t0, 2)}
        return {"reply": "(timeout)", "model": None, "tools": [], "seconds": timeout}

    def window(self, action: str) -> None:
        try:
            self.c.post(f"/api/window/{action}")
        except Exception:
            pass

    def close(self) -> None:
        self._stop.set()


def procs(name: str) -> int:
    return sum(1 for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == name)


def main() -> int:
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "settings.json").write_text(json.dumps({"user_name": "Lluc", "onboarded": True, "voice": {"mode": "classic"}}))
    proc = subprocess.Popen([str(EXE)], env=os.environ.copy())
    for _ in range(90):
        if (HOME / "instance.json").exists():
            break
        time.sleep(1)
    else:
        record("launch", False, "ORION.exe never became ready")
        shot("00_launch_failed")
        return finish(proc)
    time.sleep(6)  # window + UI load
    o = Orion()
    o.window("maximize")
    st = o.c.get("/api/state").json()
    record("launch installed ORION.exe", True, f"v{st['version']}, voice={st['voice'].get('state')}, key={'yes' if st['has_key'] else 'no'}")
    shot("01_home")

    # ── Instant (local) commands: real effects on the PC ───────────────────
    r = o.ask("open notepad")
    time.sleep(2.5)
    record("fast path: open notepad", procs("notepad.exe") > 0, f"{r['reply']} ({r['model']}, {r['seconds']}s)", **r)
    shot("02_notepad_opened")
    o.window("show")

    r = o.ask("what time is it")
    record("fast path: time", r["model"] == "local" and r["reply"].startswith("It's"), f"{r['reply']} ({r['seconds']}s)", **r)

    r = o.ask("set a timer for 10 minutes")
    timers = o.c.get("/api/routines").json()
    record("fast path: timer persisted", any("Timer" in t["name"] for t in timers), r["reply"], **r)

    r = o.ask("take a screenshot")
    pics = list((Path.home() / "Pictures" / "ORION").glob("*.png"))
    record("fast path: screenshot saved", bool(pics), r["reply"], **r)

    r = o.ask("volume 30")
    record("fast path: volume", "30" in r["reply"] or "Volume" in r["reply"], r["reply"], **r)

    r = o.ask("how's my pc doing")
    record("fast path: system status", "CPU" in r["reply"], r["reply"], **r)
    o.window("show")
    shot("03_chat_fast_commands")

    r = o.ask("close notepad")  # needs approval → auto-approver accepts
    time.sleep(2)
    record("approval flow: close notepad", procs("notepad.exe") == 0 and any("notepad" in a.lower() for a in o.approved),
           f"{r['reply']} · approved: {o.approved[-1:]}", **r)

    # ── System check ───────────────────────────────────────────────────────
    checks = o.c.post("/api/diagnostics", json={"generate": HAS_KEY}).json()
    bad = [c for c in checks if c["ok"] is False]
    record("system check", True, "; ".join(f"{c['name']}={'ok' if c['ok'] else ('n/a' if c['ok'] is None else 'FAIL')}"
                                          for c in checks), checks=checks, failures=len(bad))

    # ── Gemini scenarios ───────────────────────────────────────────────────
    if HAS_KEY:
        o.conv = None
        target = DESKTOP / "orion-test.txt"
        r = o.ask("Create a text file on my desktop called orion-test.txt containing a two-line poem about the stars.", 180)
        ok = target.exists() and len(target.read_text("utf-8").strip().splitlines()) >= 2
        record("gemini: create file via tools", ok, f"{r['reply'][:200]} | tools={r['tools']} | {r['seconds']}s",
               file=target.read_text("utf-8") if target.exists() else None, **r)
        o.window("show")
        shot("06_gemini_file")

        r = o.ask("Read that file back to me and tell me how many words it has.", 120)
        record("gemini: follow-up uses earlier action", "read_file:done" in r["tools"] or "search_files:done" in r["tools"],
               f"{r['reply'][:200]} | tools={r['tools']}", **r)

        r = o.ask("undo that last file change", 60)
        record("undo: last file change", not target.exists() or "nothing" in r["reply"].lower(), r["reply"][:200], **r)

        subprocess.Popen(["notepad.exe"])
        time.sleep(2)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Windows.Forms; Start-Sleep 1; "
                        "[System.Windows.Forms.SendKeys]::SendWait('ORION vision test 7381')"])
        time.sleep(1)
        r = o.ask("Look at my screen: what number is typed in Notepad?", 120)
        record("gemini: screen vision", "7381" in r["reply"], f"{r['reply'][:200]} | tools={r['tools']}", **r)
        subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)

        o.conv = None
        r = o.ask("/morning-briefing", 240)
        record("gemini: morning briefing skill", len(r["reply"]) > 80 and "weather" in " ".join(r["tools"]).lower(),
               f"{r['seconds']}s | tools={r['tools']} | {r['reply'][:300]}", **r)
        o.window("show")
        shot("07_morning_briefing")

        r = o.ask("What's the latest news about NASA? Two sentences.", 120)
        record("gemini: web search answer", "web_search:done" in r["tools"] and len(r["reply"]) > 40,
               f"{r['reply'][:250]} | tools={r['tools']}", **r)
        shot("08_web_answer")

        q = o.c.get("/api/quota").json()["models"]
        record("free-tier usage", True, ", ".join(f"{m['model']}: {m['rpd_used']}/{m['rpd']} today" for m in q))
    else:
        record("gemini scenarios", True, "skipped — add a GEMINI_API_KEY repository secret to run them")

    o.close()
    return finish(proc)


def finish(proc: subprocess.Popen) -> int:
    try:
        proc.terminate()
        proc.wait(10)
    except Exception:
        proc.kill()
    log = HOME / "logs" / "orion.log"
    tail = log.read_text("utf-8", errors="replace").splitlines()[-60:] if log.exists() else []
    passed = sum(r["ok"] for r in results)
    lines = [f"# ORION Windows end-to-end — {passed}/{len(results)} passed", ""]
    lines += [f"- {'✅' if r['ok'] else '❌'} **{r['name']}** — {r['detail']}" for r in results]
    lines += ["", "## Log tail", "```", *tail, "```"]
    (OUT / "report.md").write_text("\n".join(lines), "utf-8")
    (OUT / "report.json").write_text(json.dumps(results, indent=1, default=str), "utf-8")
    print("\n".join(lines))
    print("E2E-SCREENSHOTS-BEGIN")
    for name in shots:
        data = base64.b64encode((OUT / f"{name}.jpg").read_bytes()).decode()
        for i in range(0, len(data), 3500):
            print(f"E2EIMG|{name}|{data[i:i + 3500]}")
    print("E2E-SCREENSHOTS-END")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

