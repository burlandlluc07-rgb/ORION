---
name: morning-briefing
description: A spoken-style daily briefing — date, weather, top headlines, reminders due today and PC health.
tier: fast
icon: sunrise
---
Produce a crisp morning briefing. Work fast: call these tools, ideally in a single round of parallel calls:

1. get_time_and_date
2. get_weather (no location → user's approximate location; days=1)
3. web_search with kind="news", query "top news today", max_results=5
4. list_scheduled (to mention anything due today)
5. system_status

Then reply in this shape (no headings, short lines, suitable to be read aloud):

- Greeting with the user's name if known, and the day/date.
- Weather: now + today's high/low and rain chance, plus one practical tip (umbrella, jacket…).
- 3 headlines, one line each, no URLs.
- Reminders/routines due today (skip if none).
- One line on PC health only if something needs attention (RAM > 85%, disk < 10 GB free, battery < 20%).

Finish with: "Anything you'd like me to start on?"
