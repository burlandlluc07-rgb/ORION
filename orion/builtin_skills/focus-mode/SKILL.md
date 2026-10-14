---
name: focus-mode
description: Start a distraction-free focus session — closes distracting apps, sets a Pomodoro timer and quiet volume.
tier: fast
icon: target
---
Start a focus session.

1. Ask nothing if the user gave a duration; default to 25 minutes (Pomodoro) otherwise.
2. close_app for common distractions that are actually running: Discord, Steam, WhatsApp, Telegram, Spotify (only if the user didn't say they want music). Skip any that aren't open — don't report them as errors.
3. set_volume to 20 unless the user wants music.
4. set_timer with label "Focus session".
5. Also set_timer for the break: same duration + 5 minutes, label "Break over — back to it?" only if the user asked for repeating cycles.
6. Reply in one line: "Focus mode on — N minutes. I'll let you know when time's up." Mention what you closed.
