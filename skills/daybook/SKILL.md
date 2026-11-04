---
name: daybook
description: "Open or update the user's day page in their markdown planner (days/YYYY/YYYY-MM-DD.md): create it from the template, fill Schedule from the calendar, add/move/cancel calendar events and manage Reminders via bin/cal, carry over yesterday's unchecked tasks, add tasks or notes the user dictates, and commit. Use when asked to \"open today's daybook\", \"daybook\", \"add to my daybook\", \"journal entry\", \"put X on my calendar\", \"move/cancel X\", \"remind me to X\", \"what's on my calendar\", or \"what reminders do I have\"."
---

# daybook

Planner repo: `~/planner` (edit this line if yours lives elsewhere). It holds `days/`, `weeks/`, `templates/`, and `bin/cal`, a symlink to `bin/cal` in an eventkit-mcp checkout. Run every command below from the planner root. If the planner has a `CLAUDE.md`, read it first; its rules win.

## Open today

1. Today's page is `days/<YYYY>/<YYYY-MM-DD>.md`. If it exists, skip to step 4.
2. Copy `templates/day.md` and set the heading to `# <Weekday Mon D>` (e.g. `# Sun Oct 18`).
3. Find the most recent earlier day page. Copy every unchecked `- [ ]` item from its **Tasks** and **Carried over** into today's **Carried over**, unchanged. Leave the earlier page alone.
4. Run `bin/cal list <YYYY-MM-DD>` and replace the body of **Schedule** with its output (see **Calendar access** if it's denied). Then run `bin/cal reminders --due-by <YYYY-MM-DD>`; if it lists any, add them under Schedule after a `**Reminders due:**` line.
5. Show the user the Schedule and Carried over lists in a short message. If this week's page exists (`weeks/<YYYY>/<Sunday's date>.md`, see the `daybook-week` skill), also list its unchecked **Priorities**.

## During the day

- "Add X": append `- [ ] X` to today's **Tasks**.
- "Done X": check it off wherever it is on today's page.
- Notes the user dictates go under **Notes**. Journal text goes under **Journal** verbatim; never rewrite or summarize what's already there.

## Calendar

`bin/cal` (run with no arguments for usage) does everything: `list [DATE] [--to DATE] [--match TEXT] [--ids]`, `calendars`, `add`, `edit ID DATE`, `delete ID DATE`.

- **Look up:** resolve relative dates against today and use `list`, with `--to` for ranges and `--match` to find an event by title.
- **Add:** build `bin/cal add "<Title>" <YYYY-MM-DD> [<time> [<duration>]]` plus any of `--calendar`, `--location`, `--notes`, `--alert 15m`, `--repeat weekly [--until DATE]`. No time means all-day; no duration means 1h.
- **Move, change, cancel:** first run `list <DATE> --match "<words>" --ids` to get the event's ID. If it matches zero or several events, show what you found and ask; never guess. Then `edit <ID> <DATE>` with `--date`, `--time`, `--duration`, `--title`, `--all-day`, or detail options, or `delete <ID> <DATE>`.
- **Overlaps:** when adding something that overlaps an existing event, point it out before asking for the yes.
- **Repeating events:** change or delete only that occurrence. Add `--series` (this and all future occurrences) only when the user says so explicitly, together with `--confirm-series "<exact title>"` so the helper refuses if the ID points at a different event.
- **Always confirm:** before any `add`, `edit`, or `delete`, show the exact event (title, day, time, calendar, and for edits the before and after) and wait for a yes.
- Afterwards, if that day's page exists, rerun `list` for it and refresh its **Schedule**.

## Reminders

Also `bin/cal`: `reminders [--list NAME] [--due-by DATE] [--completed] [--match TEXT] [--ids]`, `reminder-lists`, `remind "<Title>" [DATE [TIME]] [--list] [--notes] [--priority high|medium|low|none] [--repeat FREQ [--until DATE]]`, `edit-reminder ID [--title] [--due DATE] [--time TIME] [--no-due] [--done] [--undone] [--list] [--notes] [--priority]`, `delete-reminder ID`.

- "Remind me to X [when]" → `remind`. A due time also sets an alert at that time. Adding one the user asked for, or marking one done, needs no extra confirmation.
- Find a reminder by `reminders --match "<words>" --ids` before editing or deleting it; ask if zero or several match.
- Confirm before deleting a reminder or changing its due date.
- **Tasks** stay in the markdown page; Reminders are for things that need a phone alert or that the user adds from their phone.

### Calendar access

`bin/cal` runs through the `EventKit Helper` app, which has its own Calendar and Reminders permissions, so it works from any session. If it prints "Calendars access denied" or "Reminders access denied", ask the user to look for the EventKit Helper prompt or turn it on under System Settings > Privacy & Security, then retry once.

## Save

If the planner is a git repo, commit with `<YYYY-MM-DD>: <morning|update|journal>`, and push if it has a remote. If the push fails, say so and leave the commit local.
