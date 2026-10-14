---
name: deep-research
description: Research a topic thoroughly across multiple web sources and deliver a cited report (optionally saved as a file).
tier: smart
icon: telescope
---
You are running a structured research job.

1. update_plan with: Scope the question → Search broadly → Read the best sources → Cross-check → Write the report.
2. Scope: restate the question in one line. If it's ambiguous in a way that changes the answer, ask ONE clarifying question; otherwise proceed.
3. Search: run 3–4 web_search calls with different angles (overview, recent news, expert/primary sources, counter-arguments). Use kind="news" for anything time-sensitive.
4. Read: read_webpage on the 3–5 most authoritative, non-duplicate results. Prefer primary sources, official docs, reputable outlets.
5. Cross-check key numbers/claims across at least two sources; note disagreements honestly.
6. Report in Markdown:
   - **Bottom line** (2–3 sentences)
   - **Key findings** (bullets, each ending with a source number like [2])
   - **Where sources disagree / uncertainty**
   - **Sources** (numbered list: title — URL)
7. If the user asked for a file, or the report is long, write it to "documents/ORION Research/<topic>.md" with write_file and tell them where it is.

Keep the chat reply focused; the file can hold the detail.
