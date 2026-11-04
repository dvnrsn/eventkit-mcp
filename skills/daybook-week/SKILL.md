---
name: daybook-week
description: "Plan the user's week on a week page in their markdown planner (weeks/YYYY/YYYY-MM-DD.md, named by the week's Sunday): review last week's page, carry over unfinished items, snapshot the week's calendar and Reminders via bin/cal and call out conflicts, ask for the week's priorities, and commit. Use when asked to \"plan my week\", \"weekly plan\", \"week page\", \"daybook week\", \"Sunday planning\", or \"how did my week go\"."
---

# daybook-week

Planner repo and `bin/cal`: same as the `daybook` skill, with the same confirmation rules for calendar and Reminders changes. If the planner has a `CLAUDE.md`, read it first; its rules win.

Weeks run Sunday to Saturday. The week page is `weeks/<YYYY>/<YYYY-MM-DD>.md`, dated by its Sunday. Planning normally happens on Sunday for the week that starts that day; on a Friday or Saturday, or when the user says "next week", plan the week starting the coming Sunday.

## Plan the week

1. If the week page exists, refresh steps 3–4 on it and skip to step 5. Otherwise copy `templates/week.md` and set the heading to `# Week of <Sun Mon D>` (e.g. `# Week of Sun Oct 18`).
2. **Last week:** find the most recent earlier week page. Under **Last week**, write a few factual lines: how many of its Priorities and Tasks were checked off, and which weren't. Copy every unchecked `- [ ]` item from its **Priorities**, **Tasks**, and **Carried over** into this page's **Carried over**, unchanged. Leave that page alone. With no earlier page, write "First week page." If day pages exist for last week, also note any of their tasks still unchecked on the latest one. If the last few week pages show an item carried over three or more weeks, mention it; describe only, and don't drop items unless the user says to.
3. **Calendar:** run `bin/cal list <SUNDAY> --to <SATURDAY>` and replace the body of **Calendar** with its output. Below it, add a short **Heads up:** list for anything that looks off: overlapping events (unless one is recurring), the same event listed more than once, travel followed by local plans with no return, or a packed day. Only describe; never change events without the usual confirmation.
4. **Reminders:** run `bin/cal reminders --due-by <SATURDAY>`. Under **Reminders**, list the ones due this week, then one line with the count of overdue ones (due before Sunday). Offer to triage the overdue ones; changing a due date or deleting one needs the user's yes, marking one done doesn't.
5. **Priorities:** show the user the calendar, heads up, and carried over items, then ask what three to five things matter most this week. Don't fill **Priorities** yourself; write what the user says as `- [ ]` items.
6. Show the finished page in a short message: priorities, heads up, and carried over.

## During the week

- "Add X to the week": append `- [ ] X` to the week's **Tasks**.
- "Done X": check it off on the week page.
- When opening a day page with the `daybook` skill, mention this week's unchecked **Priorities** in the morning message.
- **Reflection** is the user's own writing, like a day's **Journal**: add only what they dictate, verbatim, and never rewrite it.

## Save

If the planner is a git repo, commit with `<SUNDAY>: week plan` (or `<SUNDAY>: week update` / `<SUNDAY>: week reflection`), and push if it has a remote. If the push fails, say so and leave the commit local.
