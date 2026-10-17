# ORION background agent

A standalone background task system for ORION on Windows, built from `Orion_Background_Agent_Prompt.pdf`. It runs tasks while you keep using the PC and **never moves the mouse, sends keystrokes, takes focus or opens a window**. It is not wired into ORION yet; the interface for that is at the end.

## What is in it

| PDF item | Where | Notes |
|---|---|---|
| 1. Headless daemon | `daemon.py`, `supervisor.py`, `autostart.py` | One process, no window. `run` = supervisor that restarts the daemon if it crashes. `install-autostart` adds a hidden Task Scheduler task "at logon" with restart on failure. |
| 2. Task queue | `store.py`, `engine.py` | SQLite (`id, prompt, status, created, result, error` plus state). Worker pool, default 2. Tasks come from the CLI, the history page/UI and the hotkey. |
| 3. Scheduler | `scheduler.py` | APScheduler 3 with a SQLite job store; schedules survive restarts. |
| 4. Agent loop | `agent_loop.py`, `gemini_agent.py`, `tools.py` | Gemini API (google-genai, `gemini-flash-latest`, the same SDK, model and key as ORION) with tools: list/read/write/delete files in allowed folders, shell with no window, web search (Google Search grounding), toast notify. No GUI automation. Claude is still available with `provider = "claude"`. |
| 5. Safety | `safety.py`, `tools.py` | Path allowlist; per-task timeout; overwriting or deleting files and any shell command not on the safe list pause the task as `needs_approval`. |
| 6. Quiet results | `notify.py`, `webui.py` | Windows toast (winotify) on done, failed or needs-approval. History page served by the daemon. |
| 7. Tray icon | `tray.py` | pystray: status, pause/resume, open history, quit. |
| 8. Logging | `log.py` | Rotating log at `%APPDATA%\Orion\logs\background-agent.log`. |
| 9. Config | `config.py` | One file, `%APPDATA%\Orion\background-agent.toml`. The key comes from `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), else from the key ORION already saved in the Windows credential vault. It is never written to the config or logs. |

Task lifecycle: `queued → running → completed | failed | cancelled`, and `running → needs_approval → queued` once every pending action is approved or denied. The agent's whole conversation is saved after every step, so a task waiting for approval (or interrupted by a crash or restart) resumes where it stopped.

Data lives in `%APPDATA%\Orion\background\` (`tasks.db`, `schedules.db`, `daemon.json`). Set `ORION_HOME` to use a different folder.

## Install (Windows, Python 3.11+)

```powershell
cd orion-background-agent
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m orion_bg init-config        # writes %APPDATA%\Orion\background-agent.toml
setx GEMINI_API_KEY "your-gemini-key"                # optional if ORION already has your key saved
```

Edit `allowed_paths` in the config before giving the agent real work. The default is `~/Documents/Orion`, created on first start.

## Test each part, in order

Use a second terminal for the client commands. Every step works without an API key until step 5.

1. **Daemon, queue and one dummy task, end to end**
   ```powershell
   .venv\Scripts\python -m orion_bg daemon --console --no-tray
   # second terminal
   .venv\Scripts\python -m orion_bg submit "hello" --kind dummy --wait
   ```
   Expect `completed` and `Dummy task done: hello`, plus a toast. Your mouse and focus do not move.
2. **UI is only a client.** Run `python -m orion_bg ui`, open the URL, queue a dummy task from the page and close the browser tab. `python -m orion_bg list` still shows it completing.
3. **Approval gate.** `python -m orion_bg submit "test [approval]" --kind dummy --wait` stops at `needs_approval`. Then `python -m orion_bg approvals` and `python -m orion_bg approve <id>` (or Approve on the history page). `show <task id>` reports "approved". Try `deny` too.
4. **Cancel, timeout, pause.** `submit "x" --kind dummy --timeout 1` with `dummy_seconds = 5` in the config fails with "Timed out". `cancel <id>` on a running task ends it as `cancelled`. `pause`, submit, see it stay `queued`, `resume`.
5. **Gemini agent.** With `GEMINI_API_KEY` set (or ORION's saved key): put a text file in the allowed folder, then `submit "Summarise notes.txt and write the summary to summary.md" --wait`. Ask it to overwrite or delete a file, or run `git push`, to see it pause for your OK. A path outside the allowlist is refused without asking.
6. **Scheduler.** `schedule add "every 1m" "ping" --kind dummy`, wait a minute, `list` shows a task from `schedule`. Stop and restart the daemon: `schedule list` still has it. Remove it with `schedule remove <id>`.
7. **Hotkey.** Copy some text, press Ctrl+Alt+O: a toast says it was queued. Change `[hotkey].combo` if another app owns it (the log says so).
8. **Tray.** Run `python -m orion_bg daemon` (no `--no-tray`). The tray icon menu shows the status, Pause, Open task history and Quit. Quit stops the daemon cleanly.
9. **Crash restart.** `python -m orion_bg run` starts the supervisor. Kill the daemon child in Task Manager (the `python ... daemon` process): it comes back within a few seconds and re-queues any task that was running.
10. **Start with Windows.** `python -m orion_bg install-autostart`, sign out and in, then `python -m orion_bg status`. Remove with `uninstall-autostart`.
11. **Logs.** `%APPDATA%\Orion\logs\background-agent.log`, rotated at 2 MB with 5 backups.

Automated tests: `pip install pytest` then `python -m pytest -q`.

## Safety rules

- **Paths:** every file tool resolves the path (including `..` and symlinks) and refuses anything outside `allowed_paths`. Relative paths start in the first allowed folder.
- **Needs approval:** overwriting an existing file, deleting a file, and any shell command that is not on `safe_commands` or that chains, redirects or substitutes commands (`& | ; < > $( \``).
- **Runs without asking:** reading and listing, creating new files, safe-list commands, web search, toasts.
- **Shell:** `CREATE_NO_WINDOW` + `SW_HIDE`, no stdin, working folder = first allowed folder, its own timeout.
- **Timeout:** per task (`[tasks].timeout_seconds`, or `--timeout`). Time spent waiting for approval does not count.
- **Never:** no `pyautogui`, `pynput`, `pywinauto`, `SendInput`, focus or window calls anywhere in the package; `tests/test_background_first.py` fails the build if one appears.
- **API:** listens on 127.0.0.1 only and needs the random token from `daemon.json`.

## Wiring it into ORION later

Two ways, both without changing this package.

**A. Separate process (what the PDF asks for, recommended).** Autostart runs the daemon; ORION's WebView2 UI and backend talk to it with the client:

```python
from orion_bg.client import Client
bg = Client.from_runtime()                      # port + token from %APPDATA%\Orion\background\daemon.json
task = bg.submit("Tidy my Downloads folder report", source="orion")
bg.task(task["id"]); bg.approvals(); bg.approve(approval_id); bg.cancel(task["id"])
bg.add_schedule("Morning brief", "daily 08:00")
```

The same calls exist as HTTP endpoints (listed at the top of `orion_bg/server.py`), so the React UI can call them directly with the `X-Orion-Token` header.

**B. In-process.** Create one `BackgroundAgent` in `Engine.start()`:

```python
from orion_bg import BackgroundAgent, config
from orion_bg.scheduler import Scheduler
bg = BackgroundAgent(config.load())
Scheduler(bg)                                   # optional
bg.subscribe(lambda event, task: ...)           # push status to ORION's UI
bg.start()
```

Points to decide when integrating (from the Day 1 code map):

- **LLM.** Gemini by default, matching ORION (`google-genai`, `gemini-flash-latest`, same key lookup and credential vault entry). It does not share ORION's free-tier governor yet, so both draw on the same quota. Runners are pluggable: register `runners={"orion": MyRunner()}` to run tasks through ORION's own agent instead, as long as that runner only uses tools that do not take the desktop.
- **Approvals.** ORION has its own `ApprovalBroker` (screen, phone, voice). It can show `pending_approvals()` and call `approve`/`deny`, or `subscribe` to the `needs_approval` event.
- **Autostart.** ORION already autostarts with an HKCU Run key. Pick one: the Task Scheduler task here, or ORION starting the daemon.
- **Data folder.** This uses `%APPDATA%\Orion\background` and `%APPDATA%\Orion\logs`; check that does not clash with ORION's own files.
- **Packaging.** `orion_bg.spec` builds `orion-bg.exe` (windowed) and `orion-bg-cli.exe` (console). Its hidden imports can be merged into `packaging/orion.spec`.

## What was and was not verified

**Windows (GitHub Actions `windows-latest`, branch `ci/background-agent-lab` of the ORION repo, run 39024762510):** 64 unit tests pass (2 skipped: symlinks, Claude SDK not installed), the PyInstaller build works, and all 14 lab checks in `scripts/windows_lab.py` pass. The checks cover:

- The daemon with the tray icon, toasts and the Ctrl+Alt+O hotkey, which queued clipboard text.
- The approval gate, cancel and the timeout.
- Shell commands (`cmd`, PowerShell, Python) running with no window.
- The history page.
- A live Gemini run: it wrote and read a file, an overwrite waited for approval and then applied, and web search answered "Canberra".
- A clean stop.
- The supervisor bringing a killed daemon back, with its schedule kept.
- Task Scheduler autostart: installed, ran a task hidden, then removed.
- The frozen exe.

The mouse position and foreground window were identical before and after every agent step. Two control cases (the lab opening Notepad and moving the cursor itself) prove the probe does detect changes.

On the first run the live Gemini call got HTTP 429 (daily quota on `gemini-flash-latest`). The agent now falls back to `gemini-flash-lite-latest` on a quota error, and the later runs passed that way.

**Linux:** 66 tests pass, and requests through the real `google-genai` and `anthropic` SDKs against a local stub confirmed the request format.

**Not verified:** a run on your own PC, and a logon-triggered start (the lab started the scheduled task by hand).

Known limits: the timeout is checked between steps and bounds shell commands, so one long model API call can overrun it by up to that call's own timeout. If the daemon dies right after a tool ran but before its result was saved, that tool call is checked and handled again on resume: an overwrite asks for approval again, and a delete of a file that is already gone is refused.
