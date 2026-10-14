# Changelog

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
