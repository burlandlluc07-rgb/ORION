# Changelog

## 3.0.3
The notch, offline speech, an approvals queue and background WhatsApp, on top of 3.0.2. Versions 3.0.0 to 3.0.2 were built but never published, so everything
in them is in this release too and listed below. If you're on 2.7.x or older, all of it is new.

### New
- **ORION hides in a notch until you need it.** A small black pill at the top centre of the screen. Hover it to see
  "Ask ORION · Alt Space", click it or press Alt+Space to ask something. While ORION listens, thinks or runs a task
  it springs open into a one-line status, and it opens by itself when an approval is waiting, without taking focus
  from what you're typing. It stays out of the way in full-screen games, films and presentations. It replaces the
  separate Quick Bar window; turn it off in Settings → Shortcuts → Notch (after a restart) to get the Quick Bar back.
- **Speech understood on your PC, no quota needed (optional).** Settings → Voice → Offline speech recognition offers a
  one-time 487 MB download (670 MB on disk, about 900 MB of memory while voice is on). Nothing downloads until you
  tick the box and press Download, and the file is checked before it's used. Once installed, what you say, spoken
  yes/no approvals and wake-word near-misses are understood on the PC first, and Gemini is only asked when that
  fails. 25 European languages; Catalan isn't one of them, so Catalan still goes to Gemini. Live mode is unchanged.
- **Approvals wait in a queue until you answer.** Requests no longer lapse after 3 minutes; they wait in order, one
  card at a time with "1 of 3" and the rest listed below. **Later** moves one to the back without answering it.
  Ctrl+Alt+A jumps to the waiting request (it never answers it), and Ctrl+Enter approves only the front one. Settings
  → Safety → "Waiting approvals" can bring back a time limit. Nothing is ever approved by waiting.
- **Approve on the phone asks for your fingerprint** (or face or screen lock), checked on the PC. Deny never asks.
  It needs the secure Tailscale or Cloudflare address; on the plain home-network address the phone approves as
  before. Settings → Phone app shows it per phone, with Reset fingerprint. Typed and spoken yes/no work as before.
- **Personal WhatsApp in the background (optional, unofficial).** Accounts → WhatsApp → "Send and read in the
  background" links ORION as a WhatsApp linked device, so messages go out with no window, mouse or focus. It's off
  until you tick the warning (WhatsApp may restrict accounts that use unofficial links, and messages ORION reads go
  to its AI model) and link it with a QR or pairing code. Every send still asks yes or no with the exact text, and
  warns when you've never messaged that number. It keeps a human pace (15 s between messages, 20 a day, the same
  text to at most 3 chats an hour, all adjustable), and shows "typing…" first. ORION can also read recent chats, and
  treats what it reads as information, never as instructions. When the link is off, the old way still works.

### Changed
- **Less clutter.** The side panel starts closed and lost the System and Gemini cards (the per-model meters are now in
  Settings → Brain → "Used today"; ask "why is my PC slow?" for the system check). Tasks and What ORION did are one
  Activity page with two tabs. The version line, the hint lines under the composers and two of the six starter chips
  are gone (shortcuts are in Settings → Shortcuts, skills still work through `/`). Pages and panels move with short,
  smooth animations, and Windows' "Show animations" setting is respected.

### From 3.0.2: fixed
- **Emails have real line breaks.** Some AI models (often the free fallbacks) sent an email's text with "\n" typed
  in it, so it arrived in Outlook as one long line with the backslashes showing. ORION now turns a typed \n, \r\n or
  \t into a real line break or tab in the email, forward notes and subject lines, and leaves Windows paths like
  C:\new\tools alone.

### From 3.0.1: new
- **More free answers a day with Gemma.** When every free Flash model has used up its daily quota, ORION now
  keeps going on Google's free Gemma 4 models with the same key, before it switches to the backup brain. Gemma has
  its own daily quota, separate from Flash. It's less capable than Flash, so it comes last. Voice transcription
  stays on Flash because Gemma can't hear audio. ORION starts from a guess at Gemma's limits and corrects it the
  first time Google reports the real one. The "Spare models" switch in Settings turns this off along with the
  other spare models.

### From 3.0.1: fixed
- **Backup brain on Groq's free plan answers instead of saying it's rate-limited.** Groq's free plan allows 8,000
  tokens a minute, and ORION's full request (instructions plus about 75 tools) cost most of that, so a plain "hi" used
  the whole minute and the next message was refused. On Groq ORION now sends a lean request: only the tools your
  message looks like it needs (plus web search, remember and toolkits), the last three chat turns, and a smaller
  reply budget. A "hi" now costs roughly a quarter of what it did. When the plan is still used up, the message says
  how long to wait instead of "try again in a minute".

### From 3.0.0: fixed
- **Backup brain no longer fails on big requests.** On small free plans (like Groq's 8000 tokens a minute) ORION's
  full instructions, tools and chat history could be too big, and the provider refused the request. ORION now reads
  the limit from the error, shrinks the request and tries again: less room for the reply, long tool results cut
  short, shorter tool descriptions, older chat turns dropped (your current message stays whole), a shorter prompt,
  and fewer tools only as a last resort. It remembers the limit so later requests fit first time, waits briefly when
  told to try again in a few seconds, and if something still can't fit you get a plain sentence instead of a raw error.

### From 3.0.0: new since 2.7
- **A new look** closer to Claude's desktop app, still black, white and grey, with an animated Orion logo, an
  activity panel, clearer approval cards and reorganised Settings.
- **Title bar in ORION's colours**, dark grey in dark mode and light grey in light mode. The Windows buttons, Snap
  and resizing work as normal.
- **A phone app made for touch**, with a full-screen Screen tab (pinch to zoom, tap to click).
- **Backup brain** for when Gemini's free quota runs out (OmniRoute, OpenRouter, Groq or any OpenAI-compatible
  service). Setup: docs/backup-brain.md.
- **Auto-correct while typing** on the PC and the phone, with Undo, and it never touches links, paths, code, names or
  commands. Turn it off in Settings → Personal.
- **Answer approvals with yes or no**, typed or spoken, on the PC or the phone, instead of tapping Allow or Deny.
  A yes approves that one step only. Toggle in Settings → Safety & permissions.
- **What ORION did**, a screen listing every action ORION took, how it ran, who approved it and the proof it worked
  or the error. Also in the phone app's Activity tab.
- **More work without taking your screen.** Minimizing, restoring and closing windows, volume and media keys, and
  filling classic text boxes now happen in the background. The real mouse is still the last resort and still asks.

### Already in 2.7.x (for reference)
- Sort email by importance (read-only, learns from your corrections), WhatsApp messages from your own number after
  your yes, and contacts imported from your phone's export or synced over CardDAV.

## 3.0.1
Two fixes for running out of free AI quota, on top of 3.0.0.

### New
- **More free answers a day with Gemma.** When every free Flash model has used up its daily quota, ORION now
  keeps going on Google's free Gemma 4 models with the same key, before it switches to the backup brain. Gemma has
  its own daily quota, separate from Flash. It's less capable than Flash, so it comes last. Voice transcription
  stays on Flash because Gemma can't hear audio. ORION starts from a guess at Gemma's limits and corrects it the
  first time Google reports the real one. The "Spare models" switch in Settings turns this off along with the
  other spare models.

### Fixed
- **Backup brain on Groq's free plan answers instead of saying it's rate-limited.** Groq's free plan allows 8,000
  tokens a minute, and ORION's full request (instructions plus about 75 tools) cost most of that, so a plain "hi" used
  the whole minute and the next message was refused. On Groq ORION now sends a lean request: only the tools your
  message looks like it needs (plus web search, remember and toolkits), the last three chat turns, and a smaller
  reply budget. A "hi" now costs roughly a quarter of what it did. When the plan is still used up, the message says
  how long to wait instead of "try again in a minute".

## 3.0.0
ORION 3.0 rolls up everything since 2.7. Versions 2.8.0 and 2.9.0 were built but never published, so all of their
changes are in this release, plus one fix that came after them. If you're on 2.7.x or older, everything below is new.

### Fixed since 2.9.0
- **Backup brain no longer fails on big requests.** On small free plans (like Groq's 8000 tokens a minute) ORION's
  full instructions, tools and chat history could be too big, and the provider refused the request. ORION now reads
  the limit from the error, shrinks the request and tries again: less room for the reply, long tool results cut
  short, shorter tool descriptions, older chat turns dropped (your current message stays whole), a shorter prompt,
  and fewer tools only as a last resort. It remembers the limit so later requests fit first time, waits briefly when
  told to try again in a few seconds, and if something still can't fit you get a plain sentence instead of a raw error.

### New since 2.7
- **A new look** closer to Claude's desktop app, still black, white and grey, with an animated Orion logo, an
  activity panel, clearer approval cards and reorganised Settings.
- **Title bar in ORION's colours**, dark grey in dark mode and light grey in light mode. The Windows buttons, Snap
  and resizing work as normal.
- **A phone app made for touch**, with a full-screen Screen tab (pinch to zoom, tap to click).
- **Backup brain** for when Gemini's free quota runs out (OmniRoute, OpenRouter, Groq or any OpenAI-compatible
  service). Setup: docs/backup-brain.md.
- **Auto-correct while typing** on the PC and the phone, with Undo, and it never touches links, paths, code, names or
  commands. Turn it off in Settings → Personal.
- **Answer approvals with yes or no**, typed or spoken, on the PC or the phone, instead of tapping Allow or Deny.
  A yes approves that one step only. Toggle in Settings → Safety & permissions.
- **What ORION did**, a screen listing every action ORION took, how it ran, who approved it and the proof it worked
  or the error. Also in the phone app's Activity tab.
- **More work without taking your screen.** Minimizing, restoring and closing windows, volume and media keys, and
  filling classic text boxes now happen in the background. The real mouse is still the last resort and still asks.

### Already in 2.7.x (for reference)
- Sort email by importance (read-only, learns from your corrections), WhatsApp messages from your own number after
  your yes, and contacts imported from your phone's export or synced over CardDAV.

## 2.9.0
Five new things since 2.8.0. 2.8.0 was never published, so everything in it is in this release too (summary at the end).

### New
- **Auto-correct while typing.** When you finish a word, common typos and missing apostrophes are fixed in place
  ("i dont think teh" becomes "I don't think the"). A small pill shows the fix with Undo; Backspace or Ctrl+Z right
  after puts your word back and ORION leaves it alone. Links, paths, code, names and commands are never touched.
  Turn it off in Settings → Personal → "Auto-correct while typing". Works on the PC and in the phone app.
- **Answer approvals with yes or no.** When ORION waits for your OK, type "yes" or "no" in the chat on your PC or
  phone, use phone push-to-talk, or say it during a voice session, instead of tapping Allow or Deny. Only a short,
  plain yes or no counts, a yes approves that one step only, and if it can't tell which approval you mean it asks
  you to tap. Toggle: Settings → Safety & permissions → "Answer approvals with yes or no".
- **What ORION did.** A new screen in the sidebar (and a section in the phone app's Activity tab) lists every action
  newest first: what it did, when, how it ran (API, script, headless browser, app automation, your screen or the
  background agent), who approved it and the proof it worked, or the error. Filter by Worked / Problems / Declined /
  Running, by route, or search. It reads what ORION already records and stays on your PC and paired phones.
- **Title bar in ORION's colours.** The window's title bar is dark grey in dark mode and light grey in light mode
  and follows theme changes. Minimize, maximize, close, dragging, Snap and resizing stay normal Windows ones.
- **More work without taking your screen.** Minimizing, restoring and closing a window no longer brings it to the
  front, volume and media keys no longer press real keys, filling a classic text box no longer moves your focus,
  and ORION can now find minimized windows. Buttons get two more background tries before it falls back to the real
  mouse, which still asks first.

### Also in this release (from 2.8.0)
- **A new look** closer to Claude's desktop app, still black, white and grey, with an animated Orion logo, an
  activity panel, clearer approval cards and reorganised Settings.
- **A phone app made for touch**, with a full-screen Screen tab (pinch to zoom, tap to click).
- **Backup brain** for when Gemini's free quota runs out (OmniRoute, OpenRouter, Groq or any OpenAI-compatible
  service). Setup: docs/backup-brain.md.
- **Sort email by importance**, read-only, and it learns from your corrections.
- **WhatsApp messages from your own number**, shown to you first and sent only after your yes.
- **Your contacts**, imported from your phone's export (.vcf or Google CSV) or kept in sync over CardDAV.

## 2.8.0
A new look for ORION on your PC and your phone. This release also includes everything from 2.7.0, 2.7.1 and 2.7.2,
which were never published, so if you're coming from 2.6.0 all of it is new.

### New look
- **Closer to Claude's desktop app, still black, white and grey.** A quiet sidebar with recent chats grouped by day,
  a centred reading column with your messages as bubbles, and a rounded composer with the Auto / Fast / Deep picker
  inside it. The home screen puts the composer in the middle with one-click starters below.
- **An animated logo.** The Orion constellation draws itself in and its stars twinkle. A pulse runs along it while
  ORION thinks or works, and it breathes with your voice and ORION's while it listens or speaks. Press it to talk.
- **Activity panel and approvals.** The side panel is a set of cards you can close in place, and on a small window it
  opens as a drawer over the chat (Esc closes it). Approval cards are clearer and float over any page, so a step
  waiting for you is never hidden.
- **Settings.** A section list follows the scroll, the theme is a Light / Dark / Match Windows switch, and on a small
  window the sections become a row of tabs. Missions use the same composer as chat (Ctrl+Enter starts one).
- Every screen and feature is kept: chat, voice, missions, tasks, routines, Ecosystem (skills, accounts, connectors,
  plugins, tools), memory, Safety & permissions, the phone app pairing and the Quick Bar.

### Phone app
- **Made for touch.** Bigger buttons, a bottom tab bar in thumb reach, room for the notch and home bar, quick-start
  suggestions, a conversations sheet and in-app confirmations instead of browser pop-ups. Approval buttons no
  longer run off the edge of the screen.
- **Full-screen Screen tab.** View your PC full screen with pinch and double-tap zoom and drag to pan, in portrait
  or landscape. In Click mode a tap clicks, holding right-clicks and a double tap double-clicks; the keyboard tray
  stays one tap away. On iPhone, where Safari has no true full screen, the view fills the page instead.

### Also new since 2.6.0
- **Backup brain for when Gemini's free quota runs out.** In Settings → Brain → Backup brain, pick OmniRoute on this
  PC, OpenRouter's free models, Groq or any OpenAI-compatible service, then press Test & save. When every Gemini
  model is out of quota, chat, tools, missions and routines carry on there, and ORION goes back to Gemini as soon as
  it answers again. Voice stays on Gemini. Setup and caveats: docs/backup-brain.md.
- **Sort email by importance.** Ask "sort my unread emails by importance" and ORION ranks them high / medium / low
  with a short reason for each. It only reads: nothing in the mailbox is flagged, moved or marked. Tell it your own
  rules ("emails from my boss are important"), and it learns from "that one wasn't important".
- **WhatsApp messages from your own number.** Ask ORION to WhatsApp someone. It shows the recipient and the exact
  text and asks yes or no (a spoken yes works); after a yes it opens WhatsApp Desktop on that chat and presses Send.
  A WhatsApp Business number through Meta's Cloud API is optional (docs/whatsapp.md).
- **Your contacts.** Import your phone's contacts export (.vcf from Android, iPhone or Google, or a Google Contacts
  CSV) in Accounts → WhatsApp, or keep iCloud or other CardDAV contacts in sync, then just say "WhatsApp Marc".

## Unreleased
- **A better phone app.** The phone app is redesigned for touch: bigger buttons, a bottom tab bar in thumb reach,
  room for the notch and home bar, chat bubbles, quick-start suggestions, a conversation list, and in-app
  confirm sheets instead of browser pop-ups. Approval buttons no longer run off the edge of the screen.
- **Full-screen PC screen on your phone.** In Screen, tap the full-screen button to fill the phone with your PC's
  screen. Pinch or double-tap to zoom and drag to move around. In Click mode, tap to click, hold for a right-click
  and double-tap to double-click. The keyboard tray and live/pause stay one tap away. On Android it uses real full
  screen and turns sideways; on iPhone (which has no full screen for web pages) it fills the page instead, and you
  rotate the phone yourself. The phone app can now be used in landscape.

## 2.7.2
- **Backup brain for when Gemini's free quota runs out.** In Settings → Brain → Backup brain, pick OmniRoute on
  this PC, OpenRouter's free models, Groq or any OpenAI-compatible service, then press Test & save. When every
  Gemini model is out of quota or rate-limited, chat, tools, missions and routines carry on there, and ORION goes
  back to Gemini as soon as it answers again. A toast says when it switches. Voice and audio stay on Gemini. The
  key is kept in Windows Credential Manager. Setup and caveats: docs/backup-brain.md.

## 2.7.1
- **Import your WhatsApp contacts.** In Accounts → WhatsApp, import your phone's contacts export (.vcf from
  Android, iPhone/iCloud or Google, or a Google Contacts CSV) instead of typing numbers. Ask by first name:
  "WhatsApp Marc" finds Marc Puig, and ORION asks which one when two people match.
- **Or keep them in sync.** Connect your iCloud (or other CardDAV) contacts with an app password and ORION reads them
  (read-only, over CardDAV) and refreshes every few hours, so new people work without re-importing.

## 2.7.0
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
