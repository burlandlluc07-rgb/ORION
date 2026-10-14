---
name: morning-briefing
description: A spoken-style daily briefing — date, weather, today's calendar, unread email, headlines, reminders and PC health.
tier: fast
icon: sunrise
---
Produce a crisp morning briefing. Work fast: call these tools, ideally in a single round of parallel calls:

1. get_time_and_date
2. get_weather (no location → user's approximate location; days=1)
3. web_search with kind="news", query "top news today", max_results=5
4. list_scheduled (to mention anything due today)
5. system_status
6. calendar_upcoming with days=1 — only if that tool exists (calendar connected)
7. email_inbox with unread_only=true, limit=8 — only if that tool exists (email connected)

Then reply in this shape (no headings, short lines, suitable to be read aloud):

- Greeting with the user's name if known, and the day/date.
- Today's schedule: each event as "HH:MM title" (say "a clear calendar" if empty). Flag back-to-back meetings.
- Weather: now + today's high/low and rain chance, plus one practical tip (umbrella, jacket…).
- Email: count of unread, then the 1–3 that look important (from a person, not a newsletter) — sender + gist.
- 3 headlines, one line each, no URLs.
- Reminders/routines due today (skip if none).
- One line on PC health only if something needs attention (RAM > 85%, disk < 10 GB free, battery < 20%).

Finish with: "Anything you'd like me to start on?"
