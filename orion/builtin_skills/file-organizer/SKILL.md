---
name: file-organizer
description: Tidy a messy folder (Downloads by default) into sensible sub-folders, safely, with a preview first.
tier: smart
icon: folder-tree
---
Organise a folder without losing anything.

1. update_plan: Survey → Propose → Approve → Move → Report.
2. Survey: list_folder on the target (default "downloads"). If there are more than ~150 items, work in batches.
3. Propose a structure based on what's actually there, e.g. Documents, Images, Videos, Audio, Archives, Installers, Code, Spreadsheets, "Old (> 6 months)". Show the user a compact table: category → count → examples. Do NOT move anything yet.
4. Ask for a go-ahead in chat ("Shall I go ahead?") unless the user already said to just do it.
5. Move: create_folder for each category, then move_or_copy (operation="move") for each file. Never touch sub-folders that already look organised, and never delete anything — duplicates go into a "Possible duplicates" folder for the user to review.
6. Report: what moved where (counts), anything skipped and why.

Safety: skip files modified in the last 10 minutes (may be downloading), and skip system/hidden files.
