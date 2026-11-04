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

USAGE = """usage: python3 mcp/server.py

An MCP server over stdio (newline-delimited JSON-RPC) that exposes bin/cal as tools.
Register it with an MCP client rather than running it by hand.

Environment:
  EVENTKIT_MCP_SIGNATURE     text appended to the notes of events added through the server;
                             set it to an empty string to turn signing off
  EVENTKIT_CALENDARS         comma-separated calendar names that list_events may show
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
    "For repeating events, only change one occurrence unless the user explicitly says the whole series. "
    "Reminders are found the same way: list_reminders with match and ids=true, then edit_reminder "
    "or delete_reminder by ID, passing the reminder's line as `reminder`."
)

DATE = {"type": "string", "description": "YYYY-MM-DD"}
TIME = {"type": "string", "description": "7pm, 7:30pm, or 19:30"}
DURATION = {"type": "string", "description": "2h, 90m, or 1h30m"}
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
    "alert": {"type": "string", "description": "Alert before start, e.g. 15m or 1h; 'none' removes alerts"},
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
                **DETAILS,
                "series": {"type": "boolean", "description": "Also change all future occurrences"},
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
                "series": {"type": "boolean", "description": "Also delete all future occurrences"},
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
                "notes": {"type": "string"},
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


def cal_args(name, a):
    if name == "list_events":
        args = ["list", a.get("start") or date.today().isoformat()]
        if a.get("end"):
            args += ["--to", a["end"]]
        if a.get("match"):
            args += ["--match", a["match"]]
        if a.get("ids"):
            args.append("--ids")
        return args
    if name == "list_calendars":
        return ["calendars"]
    if name == "add_event":
        args = ["add", a["title"], a["date"]]
        if a.get("time"):
            args.append(a["time"])
            if a.get("duration"):
                args.append(a["duration"])
        args += detail_args(a)
        if a.get("repeat"):
            args += ["--repeat", a["repeat"]]
            if a.get("until"):
                args += ["--until", a["until"]]
        return args
    if name == "edit_event":
        args = ["edit", a["id"], a["date"]]
        for key, flag in [("title", "--title"), ("new_date", "--date"), ("time", "--time"), ("duration", "--duration")]:
            if a.get(key):
                args += [flag, a[key]]
        if a.get("all_day"):
            args.append("--all-day")
        args += detail_args(a)
        if a.get("series"):
            args.append("--series")
        return args
    if name == "delete_event":
        args = ["delete", a["id"], a["date"]]
        if a.get("series"):
            args.append("--series")
        return args
    if name == "list_reminders":
        args = ["reminders"]
        if a.get("list"):
            args += ["--list", a["list"]]
        if a.get("due_by"):
            args += ["--due-by", a["due_by"]]
        if a.get("completed"):
            args.append("--completed")
        if a.get("match"):
            args += ["--match", a["match"]]
        if a.get("ids"):
            args.append("--ids")
        return args
    if name == "list_reminder_lists":
        return ["reminder-lists"]
    if name == "add_reminder":
        args = ["remind", a["title"]]
        if a.get("due_date"):
            args.append(a["due_date"])
            if a.get("time"):
                args.append(a["time"])
        args += reminder_detail_args(a)
        if a.get("repeat"):
            args += ["--repeat", a["repeat"]]
            if a.get("until"):
                args += ["--until", a["until"]]
        return args
    if name == "edit_reminder":
        args = ["edit-reminder", a["id"]]
        if a.get("title"):
            args += ["--title", a["title"]]
        if a.get("clear_due"):
            args.append("--no-due")
        else:
            if a.get("due_date"):
                args += ["--due", a["due_date"]]
            if a.get("time"):
                args += ["--time", a["time"]]
        if "completed" in a:
            args.append("--done" if a["completed"] else "--undone")
        return args + reminder_detail_args(a)
    if name == "delete_reminder":
        return ["delete-reminder", a["id"]]
    raise KeyError(name)


def signed(a):
    signature = os.environ.get("EVENTKIT_MCP_SIGNATURE", DEFAULT_SIGNATURE).strip()
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
        for line in text.splitlines():
            if line.endswith(suffix):
                return line.removeprefix("- ").removesuffix(suffix)
    return None


def normalized(line):
    return " ".join(line.removeprefix("- ").split())


def mismatch(name, a):
    """Check that the event or reminder the caller described is the one the ID points at."""
    if name in ("edit_event", "delete_event"):
        kind, claimed = "event", a["event"]
        current = listed_line([["list", a["date"], "--ids"]], a["id"])
    elif name in ("edit_reminder", "delete_reminder"):
        kind, claimed = "reminder", a["reminder"]
        current = listed_line([["reminders", "--ids"], ["reminders", "--completed", "--ids"]], a["id"])
    else:
        return None
    if current is None:
        return f"No {kind} with that ID" + (f" on {a['date']}." if kind == "event" else ".") + " Nothing changed."
    if normalized(current) != normalized(claimed):
        return f"That ID is not the {kind} you described. It is currently:\n{current}\nNothing changed."
    return None


def call_tool(name, arguments):
    a = arguments or {}
    try:
        if name == "add_event":
            a = signed(a)
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


def main():
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print(USAGE, end="")
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if "id" not in message:
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(message)}
        except LookupError as method:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"Unknown method {method}"}}
        except Exception as error:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": str(error)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
