# Changelog

## 2.4.0
- **Toolkits.** Tools are grouped into toolkits (documents, browser, email, Windows, smart home). A toolkit's tools
  are sent to Gemini only when a request needs them, picked up from the wording or loaded mid-task. That keeps
  requests small and tool choice accurate as ORION grows.
- **Documents.** Word and PDF from Markdown (headings, lists, tables, formatting, Unicode); Excel workbooks with
  live formulas, styled headers and bar/line/pie charts; PowerPoint decks (16:9, dark or light, notes, images).
  ORION also edits Word (find/replace, append) and Excel (cells, rows, new sheets), reads .xlsx/.pptx, merges
  PDFs and images, splits/reorders/rotates PDF pages, fills PDF forms, batch-edits images, zips and unzips (safe
  against malicious archives), downloads files and searches inside files. Everything is backed up and undoable.
- **Browser control.** A real Microsoft Edge window driven through Playwright, with a persistent ORION profile.
  It reads pages as numbered elements plus text, then clicks, types, selects, scrolls, goes back, switches tabs
  and looks at the page with vision. It follows pop-ups, refuses password fields, and asks before actions.
- **Email.** Attachments, reply-all with proper threading, forward (keeps attachments), save attachments,
  read/unread/star, archive/trash/spam/move to folder (works with or without IMAP MOVE), folder list, and
  contact lookup by name.
- **Windows.** Wi-Fi, Bluetooth, dark/light mode, wallpaper, power plans, winget install/uninstall/update,
  startup apps, network info, speed test, ping, Windows Update check, drive health, battery health, temp
  cleanup, Recycle Bin, and window snapping and side-by-side. Every change is read back to confirm it, and the
  parsers work with translated Windows output.
- **To-do and shopping lists** with due-time reminders; **currency conversion** (ECB rates, with a fallback).
- Faster replies: the stable part of the prompt comes first, so Gemini can reuse its cache.
- A local-only API runs one tool directly, with approvals still enforced.
- The Windows end-to-end test exercises every new toolkit on a real PC.

## 2.3.0
- **Phone app.** An installable PWA with chat, hold-to-talk voice, a Control tab (media, volume, apps, routines,
  smart home, lock/sleep), a live screen you can tap to click and type into, and an Activity tab. QR pairing
  gives each phone its own revocable key.
- **Push notifications** (Web Push/VAPID) for approvals, with Approve/Deny on the notification, plus mission
  results and alerts.
- **Away from home:** Tailscale (`tailscale serve`) or a free Cloudflare quick tunnel. If the tunnel URL
  changes, ORION re-pairs the phone automatically.
- **Missions:** autonomous background jobs with their own conversation, plan and step budget. They wait out
  per-minute free-tier limits and re-queue themselves instead of failing.
- **Proactive mode:** battery, sustained CPU, RAM, disk, finished downloads, upcoming events and connectivity
  alerts, with quiet hours, spoken on the PC and pushed to the phone.
- **Smart home:** Home Assistant and Philips Hue. Device control and scenes; locks and alarms always ask.
  "Lights off" and "dim X to N%" use the instant fast path.
- **Deep memory:** ORION learns durable facts from finished conversations (deduplicated, with a daily cap).
- Background work (missions, routines, learning) now waits for a Gemini slot instead of giving up.
- **Self-calibrating quota.** Google no longer publishes free-tier limits and has cut them (Flash is about
  20 requests a day). ORION reads the real per-minute and per-day limits from Google's rate-limit replies,
  remembers a model that's out for the day across restarts, and says plainly when quota comes back.
- **Spare models.** ORION finds the other stable Flash and Flash-Lite models your key can use. Each has its
  own free quota, so it keeps going after the main models run out (toggle in Settings → Brain).
- Approvals waiting in other conversations now show in a dock, so none get missed.
- Fixed: the window was cut off on small screens; it now fits and centres.
- Fixed: volume control reported success when the PC had no audio device.
- Vision retries when Gemini is overloaded (503); web search falls back from news to web results.
- Verified on Windows by an end-to-end CI run against the real installer and Gemini API.

## 2.2.0
- Verified on Windows: CI builds the frozen ORION.exe, launches it and smoke-tests the API and UI before
  packaging the installer.
- Fixed: the windowed .exe crashed at start-up (no console means sys.stdout is None and uvicorn logging
  failed).
- Fixed: "look up …" and "search for …" opened a browser tab; they now get a researched answer. Only
  "google …" opens the browser.
- Faster: read-only tool calls in one step run in parallel, and Classic voice speaks sentence by sentence
  while the answer streams.
- Undo: ORION journals file changes and backs up overwritten files; "undo that" reverses the last change.
- Follow-ups understand earlier actions: history carries a compact log of what ORION did.
- Settings → System check diagnoses the key, models, mic, speakers, voice, search and app control.
- Update checker: a daily GitHub Releases check with a download link.
- Falls back to the browser if the WebView2 window can't start.
- scripts/live_check.py runs end-to-end tests against the real Gemini API (CI uses it when a
  GEMINI_API_KEY secret is set).

## 2.1.0
- Spoken approvals: voice-started tasks ask "Shall I go ahead?" out loud and accept yes or no (Live and Classic).
- Phone remote: a restricted mobile UI on your LAN with QR pairing, a rotatable token, quick actions and approvals.
- Accounts: email over IMAP/SMTP (inbox, search, read, send with approval) and calendars via private iCal links
  (recurring events expanded; new events open pre-filled in Google, Outlook or the default calendar app).
- The morning briefing now includes today's calendar and important unread email when those are connected.

## 2.0.0 — complete rewrite

ORION 2.0 replaces the 1.x "LIV Edition" prototype (a PyQt HUD built on a CC BY-NC third-party base) with an
original codebase designed as a real desktop application.

- New engine: async agent loop with plans, live task tracking, approvals and stop-everything.
- Free-tier first: local fast path (no API calls for common commands), Flash-Lite and Flash routing,
  RPM/RPD governor with automatic fallback and cooldowns, compact context with FTS memory recall.
- Voice: Gemini Live native-audio sessions with barge-in, offline "Hey Jarvis" wake word, Classic mode with
  Edge neural TTS.
- Ecosystem: SKILL.md skills (6 built-in plus your own), MCP connectors with a catalog, Python plugins, and
  per-tool enable/disable.
- Routines and reminders that persist across restarts.
- New UI: React desktop app with chat, tasks, routines, ecosystem, memory and settings, plus a Quick Bar
  (Alt+Space), tray icon and global hotkeys. Monochrome, flat design.
- Packaging: PyInstaller build, Inno Setup installer, GitHub Actions CI and release workflow.
- Security: localhost-only API with a per-launch token, origin checks, write sandbox, Recycle Bin deletes and
  verified writes.
