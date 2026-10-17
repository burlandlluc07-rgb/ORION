# Background agent

The background agent is a durable task queue that runs inside ORION (`orion/background`). It does chores
without your desktop: no mouse, keyboard, focus or windows. It started as a standalone daemon and now runs
in-process, started and stopped by the engine.

## What a task can do

| Tool | Runs without asking | Waits for your OK |
|---|---|---|
| `list_dir`, `read_file` | yes | |
| `write_file` | new files | overwriting an existing file |
| `delete_file` | | always |
| `run_shell` (no window, no stdin) | commands on the safe list | anything else, and any chaining or redirection |
| `web_search` (Gemini with Google Search) | yes | |
| `notify` | yes | |

File tools only work inside the allowed folders. A path outside them is refused without asking.

## Approvals

The agent's own database is the record, so a task can wait for an answer for hours and across restarts.
Each pending approval is also shown as an ORION approval prompt (labelled *Background task*), which is
pushed to paired phones. Answering it there decides the approval. If the prompt times out, the approval
stays pending: it can be answered with the API and is asked again the next time ORION starts. Prompts
always appear, whatever the approval mode.

## Gemini and the free tier

Tasks use ORION's Gemini key. Every request goes through ORION's free-tier governor: the smart model
first, then the fast one and any spare models, waiting up to three minutes for a free slot. A 429 cools
the model down for ORION too. When the daily quota is gone, the task fails with the governor's message.

## Start-up

The agent starts with ORION, so ORION's *Start with Windows* setting covers it. The standalone daemon's
tray icon, hotkey, HTTP server, Task Scheduler task and supervisor are not used. Turn the agent off with
the `background_agent` setting.

## Settings file

Optional: `%APPDATA%\ORION\background-agent.toml`.

```toml
[safety]
allowed_paths = ["~/Documents/Orion"]   # the only folders the file tools may touch
# safe_commands = [...]                 # replaces the built-in list of commands that run without asking

[tasks]
timeout_seconds = 900
max_steps = 40

[daemon]
workers = 2
```

Data: `%APPDATA%\ORION\background\tasks.db`. Logs go to ORION's own log.

## API

All endpoints need the usual `X-Orion-Token` header. Paired phones can use them too.

| Method | Path | |
|---|---|---|
| GET | `/api/background` | status, recent tasks, pending approvals |
| POST | `/api/background/tasks` | `{"prompt": "...", "timeout": 600}` queues a task |
| GET | `/api/background/tasks/{id}` | one task with its approvals |
| POST | `/api/background/tasks/{id}/cancel` | cancel a queued, running or waiting task |
| POST | `/api/background/approvals/{id}` | `{"decision": "approve" \| "deny"}` |
| POST | `/api/background/pause`, `/resume` | stop or restart picking up new tasks |

Task changes are published on the event bus as `background.task` events.

## Chat tools

`queue_background_task`, `list_background_tasks` and `cancel_background_task`. ORION uses them when you
ask for the background agent or the task queue; longer jobs that need ORION's other tools are still
missions.
