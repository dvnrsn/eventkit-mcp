"""EventKit MCP server: exposes bin/cal to MCP clients over stdio, standard library only."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

CAL = Path(__file__).resolve().parent.parent / "bin" / "cal"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_SIGNATURE = "~ Added via eventkit-mcp"
LAST_SIGNATURE = Path.home() / ".cache" / "eventkit-mcp" / "last-signature"

USAGE = """usage: python3 mcp/server.py

An MCP server over stdio (newline-delimited JSON-RPC) that exposes bin/cal as tools.
Register it with an MCP client rather than running it by hand.

Environment:
  EVENTKIT_MCP_SIGNATURE     text appended to the notes of events added through the server;
                             several lines rotate, one per event; an empty string turns
                             signing off
  EVENTKIT_CALENDARS         comma-separated calendars (Name or Account/Name) that the tools
                             may list, add to, edit, or delete from
  EVENTKIT_DEFAULT_CALENDAR  calendar for add_event when none is given
  EVENTKIT_DEFAULT_LIST      reminder list for add_reminder when none is given
"""

INSTRUCTIONS = (
    "Tools for the user's calendar and reminders (macOS Calendar and Reminders via EventKit). "
    "Resolve relative dates yourself and pass YYYY-MM-DD. When a phrase like 'next Thursday' could mean "
    "two dates, ask. Before add_event, edit_event, or delete_event, show the exact event "
    "(title, day, time, calendar, location; before and after for edits) and wait for a yes. "
    "Adding a reminder the user asked for or marking one done needs no extra confirmation; "
    "deleting a reminder or changing its due date does. "
    "To change or delete an event, first call list_events with match and ids=true to get its ID; "
    "if zero or several events match, ask instead of guessing. Pass that event's line from the "
    "listing as `event` so the user can see what is being changed; the server refuses if it doesn't match. "
    "For repeating events, only change one occurrence unless the user explicitly says the whole series; "
    "series=true also needs confirm_series set to the event's exact title. "
    "Reminders are found the same way: list_reminders with match and ids=true, then edit_reminder "
    "or delete_reminder by ID, passing the reminder's line as `reminder`."
)

DATE = {"type": "string", "description": "YYYY-MM-DD"}
TIME = {"type": "string", "description": "7pm, 7:30pm, or 19:30"}
DURATION = {"type": "string", "description": "2h, 90m, or 1h30m"}
ON = {
    "type": "string",
    "description": "With repeat=monthly, repeat on a weekday of the month instead of the date, e.g. 2mon "
    "(second Monday) or last-fri. The date must itself be that day.",
}
PRIORITY = {"type": "string", "enum": ["high", "medium", "low", "none"]}
EVENT_LINE = {
    "type": "string",
    "description": "The event as list_events printed it for that one day, without the leading '- ' or the "
    "[id: ...] part, e.g. '12:00-13:00 Lunch @ Cafe (Personal)'. Must match the event's current state.",
}
REMINDER_LINE = {
    "type": "string",
    "description": "The reminder as list_reminders printed it, without the leading '- ' or the [id: ...] "
    "part, e.g. '[ ] Pay rent (due Wed Sep 30) (Reminders, repeats)'. Must match its current state.",
}
DETAILS = {
    "calendar": {"type": "string", "description": "Calendar name from list_calendars; defaults to the default calendar"},
    "location": {"type": "string", "description": "Empty string clears it"},
    "notes": {"type": "string", "description": "Empty string clears it"},
    "alert": {"type": "string", "description": "Alert before start, e.g. 15m or 1h; replaces existing alerts; 'none' removes them"},
}
EDIT_DETAILS = {**DETAILS, "notes": {"type": "string", "description": "Replaces the existing notes; empty string clears them"}}
SERIES = {
    "type": "boolean",
    "description": "Also apply to all future occurrences. Needs confirm_series; only when the user explicitly asks for the whole series",
}
CONFIRM_SERIES = {
    "type": "string",
    "description": "Required with series=true: the event's exact current title, as a check that the whole series is meant",
}

TOOLS = [
    {
        "name": "list_events",
        "title": "List calendar events",
        "description": "List events for a day or an inclusive date range, optionally filtered by title text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {**DATE, "description": "First day, YYYY-MM-DD; defaults to today"},
                "end": {**DATE, "description": "Last day (inclusive), YYYY-MM-DD"},
                "match": {"type": "string", "description": "Only events whose title contains this text"},
                "ids": {"type": "boolean", "description": "Include event IDs, needed for edit_event and delete_event"},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_calendars",
        "title": "List calendars",
        "description": "List calendars with their account, which are read-only, and which is the default.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_event",
        "title": "Add calendar event",
        "description": "Create an event. Omit time for an all-day event; duration defaults to 1h. "
        "The server may append a signature to the notes so events added this way can be told apart.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date": DATE,
                "time": TIME,
                "duration": DURATION,
                **DETAILS,
                "repeat": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"]},
                "on": ON,
                "until": {**DATE, "description": "Last day of the repeat, YYYY-MM-DD"},
            },
            "required": ["title", "date"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "edit_event",
        "title": "Edit calendar event",
        "description": "Change one event, found by the ID and date from list_events. Only given fields change; "
        "`event` is its current state, so the other fields read as the new values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event": EVENT_LINE,
                "id": {"type": "string"},
                "date": {**DATE, "description": "The day of the occurrence being changed"},
                "title": {"type": "string", "description": "New title"},
                "new_date": DATE,
                "time": TIME,
                "duration": DURATION,
                "all_day": {"type": "boolean"},
                **EDIT_DETAILS,
                "series": SERIES,
                "confirm_series": CONFIRM_SERIES,
            },
            "required": ["event", "id", "date"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "delete_event",
        "title": "Delete calendar event",
        "description": "Delete one event, found by the ID and date from list_events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event": EVENT_LINE,
                "id": {"type": "string"},
                "date": {**DATE, "description": "The day of the occurrence being deleted"},
                "series": SERIES,
                "confirm_series": CONFIRM_SERIES,
            },
            "required": ["event", "id", "date"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "list_reminders",
        "title": "List reminders",
        "description": "List incomplete reminders (optionally only those due by a date), or ones completed in the last 7 days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "list": {"type": "string", "description": "Reminder list name from list_reminder_lists"},
                "due_by": {**DATE, "description": "Only reminders due on or before this day, YYYY-MM-DD"},
                "completed": {"type": "boolean", "description": "Show reminders completed in the last 7 days instead"},
                "match": {"type": "string", "description": "Only reminders whose title contains this text"},
                "ids": {"type": "boolean", "description": "Include reminder IDs, needed for edit_reminder and delete_reminder"},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_reminder_lists",
        "title": "List reminder lists",
        "description": "List Reminders lists with their account and which is the default.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_reminder",
        "title": "Add reminder",
        "description": "Create a reminder. A due time also sets an alert at that time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due_date": DATE,
                "time": {**TIME, "description": "Due time (needs due_date): 7pm, 7:30pm, or 19:30"},
                "list": {"type": "string"},
                "notes": {"type": "string"},
                "priority": PRIORITY,
                "repeat": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "description": "Needs due_date"},
                "on": ON,
                "until": {**DATE, "description": "Last day of the repeat, YYYY-MM-DD"},
            },
            "required": ["title"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "edit_reminder",
        "title": "Edit reminder",
        "description": "Change a reminder by ID from list_reminders: title, due date/time, list, notes, priority, "
        "or done state. `reminder` is its current state, so the other fields read as the new values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reminder": REMINDER_LINE,
                "id": {"type": "string"},
                "title": {"type": "string", "description": "New title"},
                "due_date": DATE,
                "time": TIME,
                "clear_due": {"type": "boolean", "description": "Remove the due date"},
                "completed": {"type": "boolean", "description": "true marks it done, false reopens it"},
                "list": {"type": "string"},
                "notes": {"type": "string", "description": "Replaces the existing notes; empty string clears them"},
                "priority": PRIORITY,
            },
            "required": ["reminder", "id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "delete_reminder",
        "title": "Delete reminder",
        "description": "Delete a reminder by ID from list_reminders.",
        "inputSchema": {
            "type": "object",
            "properties": {"reminder": REMINDER_LINE, "id": {"type": "string"}},
            "required": ["reminder", "id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
]


def is_true(a, key):
    return a.get(key) is True


def reminder_detail_args(a):
    args = []
    for key in ("list", "notes", "priority"):
        if key in a:
            args += [f"--{key}", a[key]]
    return args


def detail_args(a):
    args = []
    for key in DETAILS:
        if key in a:
            args += [f"--{key}", a[key]]
    return args


def series_args(a):
    if not is_true(a, "series"):
        return []
    return ["--series", "--confirm-series", a["confirm_series"]]


def cal_args(name, a):
    """Return bin/cal's argv with positional values after a bare `--`, so a title like `--all-day` stays a title."""
    command, flags, positional = cal_parts(name, a)
    return [command, *flags, "--", *positional]


def cal_parts(name, a):
    if name == "list_events":
        flags = []
        if a.get("end"):
            flags += ["--to", a["end"]]
        if a.get("match"):
            flags += ["--match", a["match"]]
        if is_true(a, "ids"):
            flags.append("--ids")
        return "list", flags, [a.get("start") or date.today().isoformat()]
    if name == "list_calendars":
        return "calendars", [], []
    if name == "add_event":
        positional = [a["title"], a["date"]]
        if a.get("time"):
            positional.append(a["time"])
            if a.get("duration"):
                positional.append(a["duration"])
        flags = detail_args(a)
        if a.get("on"):
            flags += ["--on", a["on"]]
        if a.get("repeat"):
            flags += ["--repeat", a["repeat"]]
            if a.get("until"):
                flags += ["--until", a["until"]]
        return "add", flags, positional
    if name == "edit_event":
        flags = []
        for key, flag in [("title", "--title"), ("new_date", "--date"), ("time", "--time"), ("duration", "--duration")]:
            if a.get(key):
                flags += [flag, a[key]]
        if is_true(a, "all_day"):
            flags.append("--all-day")
        flags += detail_args(a) + series_args(a)
        return "edit", flags, [a["id"], a["date"]]
    if name == "delete_event":
        return "delete", series_args(a), [a["id"], a["date"]]
    if name == "list_reminders":
        flags = []
        if a.get("list"):
            flags += ["--list", a["list"]]
        if a.get("due_by"):
            flags += ["--due-by", a["due_by"]]
        if is_true(a, "completed"):
            flags.append("--completed")
        if a.get("match"):
            flags += ["--match", a["match"]]
        if is_true(a, "ids"):
            flags.append("--ids")
        return "reminders", flags, []
    if name == "list_reminder_lists":
        return "reminder-lists", [], []
    if name == "add_reminder":
        positional = [a["title"]]
        if a.get("due_date"):
            positional.append(a["due_date"])
            if a.get("time"):
                positional.append(a["time"])
        flags = reminder_detail_args(a)
        if a.get("on"):
            flags += ["--on", a["on"]]
        if a.get("repeat"):
            flags += ["--repeat", a["repeat"]]
            if a.get("until"):
                flags += ["--until", a["until"]]
        return "remind", flags, positional
    if name == "edit_reminder":
        flags = []
        if a.get("title"):
            flags += ["--title", a["title"]]
        if is_true(a, "clear_due"):
            flags.append("--no-due")
        else:
            if a.get("due_date"):
                flags += ["--due", a["due_date"]]
            if a.get("time"):
                flags += ["--time", a["time"]]
        if a.get("completed") is True:
            flags.append("--done")
        elif a.get("completed") is False:
            flags.append("--undone")
        return "edit-reminder", flags + reminder_detail_args(a), [a["id"]]
    if name == "delete_reminder":
        return "delete-reminder", [], [a["id"]]
    raise KeyError(name)


def next_signature():
    raw = os.environ.get("EVENTKIT_MCP_SIGNATURE", DEFAULT_SIGNATURE)
    choices = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(choices) <= 1:
        return choices[0] if choices else ""
    try:
        last = int(LAST_SIGNATURE.read_text())
    except (OSError, ValueError):
        last = -1
    index = (last + 1) % len(choices)
    try:
        LAST_SIGNATURE.parent.mkdir(parents=True, exist_ok=True)
        LAST_SIGNATURE.write_text(str(index))
    except OSError:
        pass
    return choices[index]


def signed(a):
    signature = next_signature()
    if not signature:
        return a
    notes = a.get("notes", "").strip()
    return {**a, "notes": f"{notes}\n\n{signature}" if notes else signature}


def run_cal(args):
    result = subprocess.run([str(CAL), *args], capture_output=True, text=True, timeout=330)
    return result.returncode, (result.stdout + result.stderr).strip()


def listed_line(listing_args, item_id):
    suffix = f" [id: {item_id}]"
    for args in listing_args:
        code, text = run_cal(args)
        if code != 0:
            raise LookupError(text)
        for line in text.split("\n"):
            if line.endswith(suffix):
                return line.removeprefix("- ").removesuffix(suffix)
    return None


def normalized(line):
    return " ".join(line.removeprefix("- ").split())


def mismatch(name, a):
    """Check that the event or reminder the caller described is the one the ID points at."""
    if name in ("edit_event", "delete_event"):
        kind, claimed = "event", a["event"]
        current = listed_line([["list", "--ids", "--", a["date"]]], a["id"])
    elif name in ("edit_reminder", "delete_reminder"):
        kind, claimed = "reminder", a["reminder"]
        current = listed_line([["reminders", "--ids"], ["reminders", "--completed", "--ids"]], a["id"])
    else:
        return None
    on_day = f" on {a['date']}" if kind == "event" else ""
    if current is None:
        return f"No {kind} with that ID{on_day}. Nothing changed."
    if normalized(current) != normalized(claimed):
        return f"That ID is not the {kind} you described. Its current line{on_day} is:\n{current}\nNothing changed."
    return None


def series_refusal(name, a):
    if name not in ("edit_event", "delete_event") or not is_true(a, "series"):
        return None
    if a.get("confirm_series"):
        return None
    verb = "change" if name == "edit_event" else "delete"
    return (
        f"series=true would {verb} the {a.get('date')} occurrence and every future occurrence, not just one. "
        "Only do that if the user explicitly asked for the whole series, then call again with confirm_series "
        "set to the event's exact title. Nothing changed."
    )


def call_tool(name, arguments):
    a = arguments or {}
    try:
        if name == "add_event":
            a = signed(a)
        problem = series_refusal(name, a)
        if problem:
            return {"content": [{"type": "text", "text": problem}], "isError": True}
        args = cal_args(name, a)
        problem = mismatch(name, a)
    except KeyError as missing:
        return {"content": [{"type": "text", "text": f"Missing or unknown: {missing}"}], "isError": True}
    except LookupError as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    if problem:
        return {"content": [{"type": "text", "text": problem}], "isError": True}
    code, text = run_cal(args)
    return {"content": [{"type": "text", "text": text or "Done."}], "isError": code != 0}


def handle(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eventkit", "title": "EventKit Calendar and Reminders", "version": "1.0.0"},
            "instructions": INSTRUCTIONS,
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments"))
    if method == "ping":
        return {}
    raise LookupError(method)


def reply(message_id, **body):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, **body}) + "\n")
    sys.stdout.flush()


def main():
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print(USAGE, end="")
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError as error:
            reply(None, error={"code": -32700, "message": f"Parse error: {error}"})
            continue
        if not isinstance(message, dict):
            reply(None, error={"code": -32600, "message": "Invalid request"})
            continue
        if "id" not in message:
            continue
        try:
            reply(message["id"], result=handle(message))
        except LookupError as method:
            reply(message["id"], error={"code": -32601, "message": f"Unknown method {method}"})
        except Exception as error:
            reply(message["id"], error={"code": -32603, "message": str(error)})


if __name__ == "__main__":
    main()
