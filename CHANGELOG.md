# Changelog

## Unreleased
- **Sort email by importance.** Ask "sort my unread emails by importance" or "what's important in my inbox" and
  ORION ranks them high / medium / low with a short reason for each: people you've written to, deadlines and
  urgent wording (English, Spanish and Catalan), mail sent straight to you vs. copied, newsletters and automated
  senders, sender-set priority, Gmail's own "important" marker and your stars. It only reads: nothing in the
  mailbox is flagged, labelled, moved or marked. Tell ORION your own rules ("emails from my boss are
  important", "linkedin.com is low priority"); they are kept on this PC in `email_rules.json`. Rules are strong
  defaults rather than absolutes, so a truly urgent email from a "low priority" sender still reaches medium.
- **It adapts to you.** Say "that one wasn't important" or "this should have been higher" about a ranked email and
  ORION nudges that sender, their organisation's domain (never shared providers like gmail.com, nor your own organisation) and the signals
  that fired, a little at a time and within fixed limits. People you email often also rank higher. What it
  learned shows in the reasons, lives on this PC in `email_learning.json`, and can be forgotten ("forget what you
  learned about acme.com").
- **WhatsApp messages.** Ask ORION to WhatsApp someone by saved contact name or number. From your **own
  WhatsApp**: ORION shows the recipient and the exact text and asks yes or no (a spoken yes works for voice
  requests); after a yes it opens WhatsApp Desktop on that chat with the message typed in and presses Send, only
  while WhatsApp is the active window. Or it just types the message in for you to send (`whatsapp_draft`). No
  setup beyond adding contacts in Accounts → WhatsApp. Optionally, a **WhatsApp Business number** sends fully in
  the background through Meta's official Cloud API, also approved per message (see docs/whatsapp.md).

## 2.6.0
- **No more approval prompts by default.** ORION now acts without asking first: typing and clicking, opening or
  changing windows, bringing the browser to the front, deleting files (still to the Recycle Bin), power actions,
  installing software, shell commands, sending email, browser clicks and form submissions, smart-home locks and
  alarms, and every other "confirm" or "danger" action. Settings saved by older
  versions are switched over once on the first start of 2.6. To get the prompts back, open Settings → Safety &
  permissions and set **Approvals** to *Ask before changes* and **Using your desktop** to *Ask before each desktop
  step*; that choice is kept. Work still runs in the background first, every action still gets its lifecycle
  record and result check, and Stop still cancels. **Be aware:** with prompts off, instructions hidden in a web
  page, email or file that ORION reads could make it take these actions without you seeing a prompt.
- Background-agent tasks keep their own OK step for commands that aren't on the safe list.
- **Background agent.** The standalone background agent now runs inside ORION (`orion/background`). Ask ORION to
  "queue a background task", or use `/api/background`. Tasks work on files in the agent's own folders, run
  commands with no window and search the web, and never use the mouse, keyboard, focus or a window. Risky steps
  wait for your OK in ORION's approval prompt (also pushed to paired phones), and both tasks and pending approvals
  survive restarts. Every Gemini request goes through ORION's free-tier governor, so the agent and the assistant
  share one budget. It starts with ORION, so "Start with Windows" covers it; turn it off with the
  `background_agent` setting.

## 2.5.0
- **Instant voice commands.** "Hey ORION, pause the music", "…set the volume to thirty", "…open Spotify",
  "…set a timer for five minutes", "…lock the computer", "stop": about 2,500 everyday phrasings, including your
  installed apps, are recognised offline by Windows together with the wake phrase and run straight away. There's
  no transcription, no Gemini request and no quota. Anything ORION isn't sure about goes to Gemini as before.
- **Smarter listening.** End of speech is detected with WebRTC voice activity detection instead of a volume
  threshold. Short commands finish after about half a second; longer sentences get room to pause for breath.
  What you say right after the wake phrase is no longer lost.
- **Follow-ups without the wake word.** After a reply, ORION keeps listening for a few seconds (Classic mode).
- **Interrupt by talking.** Speak over ORION and it stops to listen (Classic). It tells your voice apart from
  its own echo. The wake word always cuts it off.
- **Knows what "this" is.** "Summarise this", "translate the selected text", "reply to this": ORION reads the
  window you were last using and the text selected in it through Windows UI Automation. No keys are pressed and
  the clipboard is untouched. There's also a new `current_context` tool.
- **Gets your names right.** People and projects from your vault are given to the transcriber as spelling hints.
- **Never goes silent.** If the neural voice can't be reached, replies are spoken with an installed Windows voice.
  When offline, Windows dictation stands in for transcription, so offline commands still work by voice.
- **Feels quicker.** A quick offline transcript appears while Gemini's accurate one is on its way. A soft chime
  plays when ORION starts listening. Spoken "yes/no" approvals are understood offline.
- Settings → Voice: Instant commands, Follow-up listening, Interrupt by talking and Listening chime toggles.

## 2.4.0
- **Choose your own wake word.** Any phrase or name ("Hey ORION", "Hey Friday", "Hey Jarvis"…), several at once,
  in the speech languages Windows supports. It runs on Windows' built-in offline recogniser, so there's no download
  and it works in the installed app (the old "Hey Jarvis" needed a developer-only package). The phrase can be
  followed straight away by a command ("Hey ORION, what's the weather?"). Sentences that merely mention the name
  don't wake it. On Windows speech tests it caught every phrase at 94–99% and ignored the traps. Single words can
  wake by mistake on similar-sounding speech, so Settings suggests two-word phrases. Settings has a sensitivity
  slider and shows what was heard, how sure it was, and whether it woke. The listener restarts itself after
  device changes or sleep.
- **Second brain in Obsidian.** Memory moved from a hidden database into an Obsidian vault of Markdown notes on
  the PC (`Documents\ORION Brain`, or a folder inside your own vault). Permanent notes are `About me`
  (profile + preferences), `People/<name>`, `Projects/<name>`, `Plans & dates` and `Facts`, linked from `Home`.
  Passing details go to `Short-term/This week`. A weekly review (default Sunday 20:00, catches up if the PC was
  off) keeps, carries over or deletes each item and logs every decision. If the review can't run, nothing is
  deleted. The auto-learner chooses permanent or short-term itself. Edits made in Obsidian are re-indexed
  automatically (SQLite FTS stays the fast search index), and existing memories move into the vault on first
  start. Memory page: Permanent / This week tabs, grouped by note, Open in Obsidian (registers the vault), Get
  Obsidian, Review now.
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
- Chat waits up to 30 s for a free-tier per-minute slot instead of failing after 6 s.
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
