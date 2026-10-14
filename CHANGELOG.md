# Changelog

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
