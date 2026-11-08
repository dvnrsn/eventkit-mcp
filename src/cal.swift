import EventKit
import Foundation

let usage = """
usage:
  bin/cal list [DATE] [--to DATE] [--match TEXT] [--ids]
  bin/cal calendars
  bin/cal add "Title" DATE [TIME [DURATION]] [details] [--repeat FREQ [--every N] [--on NTH] [--until DATE]]
  bin/cal edit ID DATE [--title T] [--date DATE] [--time TIME] [--duration D] [--all-day] [details]
                       [--series [--confirm-series TITLE]]
  bin/cal delete ID DATE [--series [--confirm-series TITLE]]

  bin/cal reminders [--list NAME] [--due-by DATE] [--completed] [--match TEXT] [--ids]
  bin/cal reminder-lists
  bin/cal remind "Title" [DATE [TIME]] [--list NAME] [--notes TEXT] [--priority P]
                     [--repeat FREQ [--every N] [--on NTH] [--until DATE]]
  bin/cal edit-reminder ID [--title T] [--due DATE] [--time TIME] [--no-due] [--done] [--undone]
                           [--list NAME] [--notes TEXT] [--priority P]
  bin/cal delete-reminder ID

  DATE      YYYY-MM-DD (list defaults to today)
  TIME      7pm, 7:30pm, or 19:30; omit on add for an all-day event
  DURATION  2h, 90m, or 1h30m; defaults to 1h
  details   --calendar NAME  --location TEXT  --notes TEXT  --alert 15m|none
            on edit, --notes replaces the existing notes and --alert replaces existing alerts
  FREQ      daily, weekly, monthly, or yearly; --every 2 with weekly is every other week
  NTH       with --repeat monthly, repeat on a weekday of the month instead of the date:
            2mon, second-monday, "second monday", last-fri; DATE must be that day
  ID DATE   from `list --ids`; DATE picks the occurrence of a repeating event
  --series  edit or delete this and all future occurrences, not just this one;
            --confirm-series refuses unless TITLE is exactly the event's current title
  --        everything after a bare -- is positional, for titles that start with --
  P         high, medium, low, or none
  reminders lists incomplete reminders; --completed shows ones finished in the last 7 days

Environment:
  EVENTKIT_CALENDARS="A,Acct/B"    only these calendars (by name, or Account/Name) can be
                                   listed, added to, edited, or deleted from
  EVENTKIT_DEFAULT_CALENDAR=NAME   calendar for add when --calendar is not given
  EVENTKIT_DEFAULT_LIST=NAME       reminder list for remind when --list is not given
"""

// bin/cal waits for this file because `open -W` can miss a process that exits quickly.
func finish(_ code: Int32) -> Never {
    fflush(stdout)
    if let path = ProcessInfo.processInfo.environment["EVENTKIT_STATUS_FILE"] {
        try? "\(code)".write(toFile: path, atomically: true, encoding: .utf8)
    }
    exit(code)
}

if let path = ProcessInfo.processInfo.environment["EVENTKIT_PID_FILE"] {
    try? "\(getpid())".write(toFile: path, atomically: true, encoding: .utf8)
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    finish(1)
}

let valueFlags: Set<String> = [
    "--to", "--match", "--title", "--date", "--time", "--duration",
    "--calendar", "--location", "--notes", "--alert", "--repeat", "--every", "--on", "--until",
    "--list", "--due-by", "--due", "--priority", "--confirm-series",
]
let switchFlags: Set<String> = ["--ids", "--all-day", "--series", "--completed", "--no-due", "--done", "--undone"]

var positional: [String] = []
var options: [String: String] = [:]
var switches: Set<String> = []
var raw = Array(CommandLine.arguments.dropFirst())
var onlyPositional = false
while !raw.isEmpty {
    let arg = raw.removeFirst()
    if onlyPositional {
        positional.append(arg)
    } else if arg == "--" {
        onlyPositional = true
    } else if valueFlags.contains(arg) {
        guard !raw.isEmpty else { fail("\(arg) needs a value.\n" + usage) }
        options[arg] = raw.removeFirst()
    } else if switchFlags.contains(arg) {
        switches.insert(arg)
    } else if arg.hasPrefix("--") {
        fail("Unknown option \(arg).\n" + usage)
    } else {
        positional.append(arg)
    }
}
guard let command = positional.first else { fail(usage) }
let args = Array(positional.dropFirst())

let calendar = Calendar.current
let environment = ProcessInfo.processInfo.environment

func fixedFormatter(_ format: String) -> DateFormatter {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.timeZone = TimeZone.current
    formatter.dateFormat = format
    return formatter
}

func parseDay(_ text: String) -> Date {
    let formatter = fixedFormatter("yyyy-MM-dd")
    guard let day = formatter.date(from: text) else { fail("Can't read date \"\(text)\"; use YYYY-MM-DD.") }
    return day
}

func formatDay(_ day: Date) -> String {
    fixedFormatter("yyyy-MM-dd").string(from: day)
}

func parseTime(_ text: String, on day: Date) -> Date {
    for format in ["h:mma", "ha", "H:mm"] {
        if let time = fixedFormatter(format).date(from: text.uppercased()) {
            let parts = calendar.dateComponents([.hour, .minute], from: time)
            return calendar.date(bySettingHour: parts.hour!, minute: parts.minute!, second: 0, of: day)!
        }
    }
    fail("Can't read time \"\(text)\"; use 7pm, 7:30pm, or 19:30.")
}

func parseDuration(_ text: String) -> TimeInterval {
    let pattern = try! NSRegularExpression(pattern: "^(?:(\\d+)h)?(?:(\\d+)m)?$")
    let range = NSRange(text.startIndex..., in: text)
    if let match = pattern.firstMatch(in: text, range: range) {
        func group(_ index: Int) -> Double {
            Range(match.range(at: index), in: text).flatMap { Double(text[$0]) } ?? 0
        }
        let seconds = group(1) * 3600 + group(2) * 60
        if seconds > 0 { return seconds }
    }
    fail("Can't read duration \"\(text)\"; use 2h, 90m, or 1h30m.")
}

func nextDay(_ day: Date) -> Date {
    calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: day))!
}

let store = EKEventStore()

func requireAccess(to entity: EKEntityType) {
    let done = DispatchSemaphore(value: 0)
    var granted = false
    let handler: EKEventStoreRequestAccessCompletionHandler = { ok, _ in
        granted = ok
        done.signal()
    }
    if entity == .event {
        store.requestFullAccessToEvents(completion: handler)
    } else {
        store.requestFullAccessToReminders(completion: handler)
    }
    done.wait()
    guard granted else {
        let area = entity == .event ? "Calendars" : "Reminders"
        fail("\(area) access denied. Allow EventKit Helper in System Settings > Privacy & Security > \(area).")
    }
}

// Titles, locations, and calendar names are printed one item per line, and the MCP server
// compares those lines, so no stored value may break a line.
func oneLine(_ text: String) -> String {
    let scalars = text.unicodeScalars.map { scalar -> Unicode.Scalar in
        CharacterSet.newlines.contains(scalar) || scalar.properties.generalCategory == .control ? " " : scalar
    }
    return String(String.UnicodeScalarView(scalars))
}

func calendarLabel(_ cal: EKCalendar) -> String {
    "\(cal.source.title)/\(cal.title)"
}

func calendarMatches(_ cal: EKCalendar, _ name: String) -> Bool {
    cal.title == name || calendarLabel(cal) == name
}

var cachedAllowedCalendars: [EKCalendar]??

// nil means EVENTKIT_CALENDARS is unset, so every calendar is allowed.
func allowedCalendars() -> [EKCalendar]? {
    if let cached = cachedAllowedCalendars { return cached }
    let entries = (environment["EVENTKIT_CALENDARS"] ?? "")
        .split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }
    var result: [EKCalendar]? = nil
    if !entries.isEmpty {
        let all = store.calendars(for: .event)
        let unmatched = entries.filter { entry in !all.contains { calendarMatches($0, entry) } }
        if !unmatched.isEmpty {
            let available = all.map { "  " + oneLine(calendarLabel($0)) }.sorted().joined(separator: "\n")
            fail("EVENTKIT_CALENDARS names no calendar for: \(unmatched.joined(separator: ", ")). "
                + "Use Name or Account/Name. Available calendars:\n" + available)
        }
        result = all.filter { cal in entries.contains { calendarMatches(cal, $0) } }
    }
    cachedAllowedCalendars = .some(result)
    return result
}

func requireAllowed(_ cal: EKCalendar) {
    guard let allowed = allowedCalendars() else { return }
    guard allowed.contains(where: { $0.calendarIdentifier == cal.calendarIdentifier }) else {
        fail("Calendar \"\(oneLine(calendarLabel(cal)))\" is outside EVENTKIT_CALENDARS. Nothing changed.")
    }
}

func writableCalendar(named name: String) -> EKCalendar {
    let matches = store.calendars(for: .event).filter { calendarMatches($0, name) && $0.allowsContentModifications }
    guard let match = matches.first else {
        fail("No writable calendar named \"\(name)\". See bin/cal calendars.")
    }
    guard matches.count == 1 else {
        fail("Several writable calendars are named \"\(name)\"; use Account/Name. See bin/cal calendars.")
    }
    requireAllowed(match)
    return match
}

func occurrence(id: String, on day: Date) -> EKEvent {
    let start = calendar.startOfDay(for: day)
    let predicate = store.predicateForEvents(withStart: start, end: nextDay(start), calendars: nil)
    guard let event = store.events(matching: predicate).first(where: { $0.eventIdentifier == id }) else {
        fail("No event with that ID on \(formatDay(start)). Run bin/cal list DATE --ids to find it.")
    }
    requireAllowed(event.calendar)
    return event
}

func requireSeriesConfirmation(_ event: EKEvent) {
    guard switches.contains("--series"), let confirmed = options["--confirm-series"] else { return }
    let title = oneLine(event.title ?? "(untitled)")
    guard confirmed == title else {
        fail("--confirm-series \"\(confirmed)\" is not this event's title \"\(title)\". Nothing changed.")
    }
}

func targetNote(_ repeats: Bool, _ day: Date) -> String {
    guard repeats else { return "" }
    let label = formatDay(day)
    return switches.contains("--series")
        ? " (the \(label) occurrence and all after it)"
        : " (only the \(label) occurrence)"
}

let timeFormat = DateFormatter()
timeFormat.setLocalizedDateFormatFromTemplate("jmm")
let dayLabel = DateFormatter()
dayLabel.dateFormat = "EEE MMM d"

func describe(_ event: EKEvent, withDay: Bool = false, withID: Bool = false) -> String {
    var line = "- "
    if withDay { line += dayLabel.string(from: event.startDate) + " " }
    let title = oneLine(event.title ?? "(untitled)")
    if event.isAllDay {
        line += "All day: \(title)"
    } else {
        line += "\(timeFormat.string(from: event.startDate))-\(timeFormat.string(from: event.endDate)) \(title)"
    }
    if let location = event.location, !location.isEmpty {
        line += " @ " + oneLine(location.split(whereSeparator: \.isNewline).joined(separator: ", "))
    }
    line += " (\(oneLine(event.calendar.title))"
    if event.hasRecurrenceRules { line += ", repeats" }
    line += ")"
    if withID { line += " [id: \(event.eventIdentifier!)]" }
    return line
}

func applyDetails(to event: EKEvent) {
    if let name = options["--calendar"] { event.calendar = writableCalendar(named: name) }
    if let location = options["--location"] { event.location = location.isEmpty ? nil : location }
    if let notes = options["--notes"] { event.notes = notes.isEmpty ? nil : notes }
    if let alert = options["--alert"] {
        event.alarms = alert == "none" ? nil : [EKAlarm(relativeOffset: -parseDuration(alert))]
    }
}

let ordinals: [String: Int] = [
    "1": 1, "1st": 1, "first": 1, "2": 2, "2nd": 2, "second": 2, "3": 3, "3rd": 3, "third": 3,
    "4": 4, "4th": 4, "fourth": 4, "5": 5, "5th": 5, "fifth": 5, "-1": -1, "last": -1,
]
let weekdayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
let ordinalNames = [-1: "last", 1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"]

// Returns (weekday 1=Sunday...7=Saturday, week of month 1...5 or -1 for last).
func parseNth(_ text: String) -> (weekday: Int, week: Int) {
    let pattern = try! NSRegularExpression(pattern: "^(-1|[1-5](?:st|nd|rd|th)?|first|second|third|fourth|fifth|last)[\\s-]*([a-z]+)$")
    let lower = text.lowercased().trimmingCharacters(in: .whitespaces)
    if let match = pattern.firstMatch(in: lower, range: NSRange(lower.startIndex..., in: lower)),
       let ordinalRange = Range(match.range(at: 1), in: lower),
       let dayRange = Range(match.range(at: 2), in: lower),
       let week = ordinals[String(lower[ordinalRange])] {
        let day = String(lower[dayRange])
        if day.count >= 3, let index = weekdayNames.firstIndex(where: { $0.lowercased().hasPrefix(day) }) {
            return (index + 1, week)
        }
    }
    fail("Can't read --on \"\(text)\"; use e.g. 2mon, second-monday, or last-fri.")
}

func nthWeekday(of day: Date) -> (weekday: Int, week: Int, isLast: Bool) {
    let weekday = calendar.component(.weekday, from: day)
    let week = (calendar.component(.day, from: day) - 1) / 7 + 1
    let isLast = calendar.component(.month, from: calendar.date(byAdding: .day, value: 7, to: day)!)
        != calendar.component(.month, from: day)
    return (weekday, week, isLast)
}

func recurrenceRule(startingOn start: Date?) -> EKRecurrenceRule? {
    guard let repeatText = options["--repeat"] else {
        if options["--on"] != nil { fail("--on needs --repeat monthly.") }
        return nil
    }
    let frequencies: [String: EKRecurrenceFrequency] = ["daily": .daily, "weekly": .weekly, "monthly": .monthly, "yearly": .yearly]
    guard let frequency = frequencies[repeatText] else { fail("--repeat must be daily, weekly, monthly, or yearly.") }
    let end = options["--until"].map { EKRecurrenceEnd(end: nextDay(parseDay($0))) }
    guard let interval = Int(options["--every"] ?? "1"), interval > 0 else { fail("--every must be a positive number.") }
    guard let onText = options["--on"] else {
        return EKRecurrenceRule(recurrenceWith: frequency, interval: interval, end: end)
    }
    guard frequency == .monthly else { fail("--on only works with --repeat monthly.") }
    let nth = parseNth(onText)
    // EventKit always keeps the start date as the first occurrence, so a start off the pattern
    // would add a stray extra one.
    if let start {
        let actual = nthWeekday(of: start)
        let matches = actual.weekday == nth.weekday && (nth.week == -1 ? actual.isLast : actual.week == nth.week)
        guard matches else {
            fail("\(formatDay(start)) is not the \(ordinalNames[nth.week]!) \(weekdayNames[nth.weekday - 1]) of its month; "
                + "start the repeat on one that is. Nothing changed.")
        }
    }
    let day = EKRecurrenceDayOfWeek(EKWeekday(rawValue: nth.weekday)!, weekNumber: nth.week)
    return EKRecurrenceRule(
        recurrenceWith: .monthly, interval: interval, daysOfTheWeek: [day],
        daysOfTheMonth: nil, monthsOfTheYear: nil, weeksOfTheYear: nil, daysOfTheYear: nil,
        setPositions: nil, end: end)
}

func save(_ event: EKEvent) {
    let span: EKSpan = switches.contains("--series") ? .futureEvents : .thisEvent
    do {
        try store.save(event, span: span)
    } catch {
        fail("Couldn't save the event: \(error.localizedDescription)")
    }
}

func list() {
    guard args.count <= 1 else { fail(usage) }
    let start = calendar.startOfDay(for: args.first.map(parseDay) ?? Date())
    let last = options["--to"].map(parseDay) ?? start
    guard last >= start else { fail("--to is before the start date.") }
    var events = store
        .events(matching: store.predicateForEvents(withStart: start, end: nextDay(last), calendars: allowedCalendars()))
        .sorted { $0.startDate < $1.startDate }
    if let text = options["--match"] {
        events = events.filter { ($0.title ?? "").localizedCaseInsensitiveContains(text) }
    }
    if events.isEmpty { print("Nothing on the calendar.") }
    let multiDay = last > start
    for event in events {
        print(describe(event, withDay: multiDay, withID: switches.contains("--ids")))
    }
}

func listCalendars() {
    let defaultID = store.defaultCalendarForNewEvents?.calendarIdentifier
    for cal in store.calendars(for: .event).sorted(by: { $0.source.title + $0.title < $1.source.title + $1.title }) {
        var line = "- \(oneLine(cal.title)) (\(oneLine(cal.source.title)))"
        if !cal.allowsContentModifications { line += ", read-only" }
        if cal.calendarIdentifier == defaultID { line += ", default" }
        print(line)
    }
}

func add() {
    guard (2...4).contains(args.count) else { fail(usage) }
    let day = parseDay(args[1])
    let event = EKEvent(eventStore: store)
    event.title = args[0]
    if let name = environment["EVENTKIT_DEFAULT_CALENDAR"], !name.isEmpty {
        event.calendar = writableCalendar(named: name)
    } else {
        guard let defaultCalendar = store.defaultCalendarForNewEvents else { fail("No default calendar; pass --calendar.") }
        event.calendar = defaultCalendar
    }
    if args.count >= 3 {
        let start = parseTime(args[2], on: day)
        event.startDate = start
        event.endDate = start.addingTimeInterval(args.count == 4 ? parseDuration(args[3]) : 3600)
    } else {
        event.isAllDay = true
        event.startDate = day
        event.endDate = day
    }
    applyDetails(to: event)
    requireAllowed(event.calendar)
    if let rule = recurrenceRule(startingOn: day) { event.addRecurrenceRule(rule) }
    save(event)
    print("Added " + describe(event, withDay: true, withID: true).dropFirst(2))
}

func edit() {
    guard args.count == 2 else { fail(usage) }
    let day = parseDay(args[1])
    let event = occurrence(id: args[0], on: day)
    requireSeriesConfirmation(event)
    let repeats = event.hasRecurrenceRules
    if let title = options["--title"] { event.title = title }

    let newDay = options["--date"].map(parseDay)
    let targetDay = newDay ?? calendar.startOfDay(for: event.startDate)
    let duration = options["--duration"].map(parseDuration)

    if switches.contains("--all-day") {
        event.isAllDay = true
        event.startDate = targetDay
        event.endDate = targetDay
    } else if let time = options["--time"] {
        let start = parseTime(time, on: targetDay)
        let length = duration ?? (event.isAllDay ? 3600 : event.endDate.timeIntervalSince(event.startDate))
        event.isAllDay = false
        event.startDate = start
        event.endDate = start.addingTimeInterval(length)
    } else if event.isAllDay {
        guard duration == nil else { fail("Give --time with --duration to make an all-day event timed.") }
        if let newDay {
            let shift = calendar.dateComponents([.day], from: calendar.startOfDay(for: event.startDate), to: newDay).day!
            event.startDate = calendar.date(byAdding: .day, value: shift, to: event.startDate)!
            event.endDate = calendar.date(byAdding: .day, value: shift, to: event.endDate)!
        }
    } else if newDay != nil || duration != nil {
        let clock = calendar.dateComponents([.hour, .minute], from: event.startDate)
        let start = calendar.date(bySettingHour: clock.hour!, minute: clock.minute!, second: 0, of: targetDay)!
        let length = duration ?? event.endDate.timeIntervalSince(event.startDate)
        event.startDate = start
        event.endDate = start.addingTimeInterval(length)
    }

    applyDetails(to: event)
    save(event)
    print("Updated " + describe(event, withDay: true, withID: true).dropFirst(2) + targetNote(repeats, day))
}

func delete() {
    guard args.count == 2 else { fail(usage) }
    let day = parseDay(args[1])
    let event = occurrence(id: args[0], on: day)
    requireSeriesConfirmation(event)
    let summary = describe(event, withDay: true).dropFirst(2) + targetNote(event.hasRecurrenceRules, day)
    let span: EKSpan = switches.contains("--series") ? .futureEvents : .thisEvent
    do {
        try store.remove(event, span: span)
    } catch {
        fail("Couldn't delete the event: \(error.localizedDescription)")
    }
    print("Deleted " + summary)
}

// MARK: Reminders

func writableList(named name: String) -> EKCalendar {
    guard let match = store.calendars(for: .reminder).first(where: { $0.title == name && $0.allowsContentModifications }) else {
        fail("No writable reminder list named \"\(name)\". See bin/cal reminder-lists.")
    }
    return match
}

func fetchReminders(_ predicate: NSPredicate) -> [EKReminder] {
    let done = DispatchSemaphore(value: 0)
    var found: [EKReminder] = []
    store.fetchReminders(matching: predicate) { reminders in
        found = reminders ?? []
        done.signal()
    }
    done.wait()
    return found
}

func reminder(id: String) -> EKReminder {
    guard let match = store.calendarItem(withIdentifier: id) as? EKReminder else {
        fail("No reminder with that ID. Run bin/cal reminders --ids to find it.")
    }
    return match
}

func dueDate(of reminder: EKReminder) -> Date? {
    reminder.dueDateComponents.flatMap { calendar.date(from: $0) }
}

func hasDueTime(_ reminder: EKReminder) -> Bool {
    reminder.dueDateComponents?.hour != nil
}

func setDue(_ reminder: EKReminder, day: Date, time: String?) {
    if let time {
        let moment = parseTime(time, on: day)
        reminder.dueDateComponents = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: moment)
        reminder.alarms = [EKAlarm(absoluteDate: moment)]
    } else {
        reminder.dueDateComponents = calendar.dateComponents([.year, .month, .day], from: day)
        reminder.alarms = nil
    }
}

func applyReminderDetails(to reminder: EKReminder) {
    if let name = options["--list"] { reminder.calendar = writableList(named: name) }
    if let notes = options["--notes"] { reminder.notes = notes.isEmpty ? nil : notes }
    if let priority = options["--priority"] {
        let levels: [String: Int] = ["high": 1, "medium": 5, "low": 9, "none": 0]
        guard let level = levels[priority] else { fail("--priority must be high, medium, low, or none.") }
        reminder.priority = level
    }
}

func describe(_ reminder: EKReminder, withID: Bool = false) -> String {
    var line = reminder.isCompleted ? "- [x] " : "- [ ] "
    line += oneLine(reminder.title ?? "(untitled)")
    if let due = dueDate(of: reminder) {
        line += " (due \(dayLabel.string(from: due))"
        if hasDueTime(reminder) { line += " \(timeFormat.string(from: due))" }
        line += ")"
    }
    switch reminder.priority {
    case 1...4: line += " !high"
    case 5: line += " !medium"
    case 6...9: line += " !low"
    default: break
    }
    line += " (\(oneLine(reminder.calendar.title))"
    if reminder.hasRecurrenceRules { line += ", repeats" }
    line += ")"
    if withID { line += " [id: \(reminder.calendarItemIdentifier)]" }
    return line
}

func saveReminder(_ reminder: EKReminder) {
    do {
        try store.save(reminder, commit: true)
    } catch {
        fail("Couldn't save the reminder: \(error.localizedDescription)")
    }
}

func listReminders() {
    guard args.isEmpty else { fail(usage) }
    let lists = options["--list"].map { name in
        let matches = store.calendars(for: .reminder).filter { $0.title == name }
        if matches.isEmpty { fail("No reminder list named \"\(name)\". See bin/cal reminder-lists.") }
        return matches
    }
    let predicate: NSPredicate
    if switches.contains("--completed") {
        let weekAgo = calendar.date(byAdding: .day, value: -7, to: Date())!
        predicate = store.predicateForCompletedReminders(withCompletionDateStarting: weekAgo, ending: Date(), calendars: lists)
    } else {
        let dueBy = options["--due-by"].map { nextDay(parseDay($0)) }
        predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: dueBy, calendars: lists)
    }
    var reminders = fetchReminders(predicate)
    if let text = options["--match"] {
        reminders = reminders.filter { ($0.title ?? "").localizedCaseInsensitiveContains(text) }
    }
    reminders.sort {
        switch (dueDate(of: $0), dueDate(of: $1)) {
        case let (a?, b?): return a < b
        case (_?, nil): return true
        case (nil, _?): return false
        case (nil, nil): return ($0.title ?? "") < ($1.title ?? "")
        }
    }
    if reminders.isEmpty { print("No reminders.") }
    for item in reminders {
        print(describe(item, withID: switches.contains("--ids")))
    }
}

func listReminderLists() {
    let defaultID = store.defaultCalendarForNewReminders()?.calendarIdentifier
    for list in store.calendars(for: .reminder).sorted(by: { $0.source.title + $0.title < $1.source.title + $1.title }) {
        var line = "- \(oneLine(list.title)) (\(oneLine(list.source.title)))"
        if !list.allowsContentModifications { line += ", read-only" }
        if list.calendarIdentifier == defaultID { line += ", default" }
        print(line)
    }
}

func remind() {
    guard (1...3).contains(args.count) else { fail(usage) }
    let item = EKReminder(eventStore: store)
    item.title = args[0]
    if let name = environment["EVENTKIT_DEFAULT_LIST"], !name.isEmpty {
        item.calendar = writableList(named: name)
    } else {
        guard let defaultList = store.defaultCalendarForNewReminders() else { fail("No default reminder list; pass --list.") }
        item.calendar = defaultList
    }
    if args.count >= 2 { setDue(item, day: parseDay(args[1]), time: args.count == 3 ? args[2] : nil) }
    applyReminderDetails(to: item)
    if let rule = recurrenceRule(startingOn: dueDate(of: item)) {
        guard item.dueDateComponents != nil else { fail("A repeating reminder needs a due date.") }
        item.addRecurrenceRule(rule)
    }
    saveReminder(item)
    print("Added " + describe(item, withID: true).dropFirst(2))
}

func editReminder() {
    guard args.count == 1 else { fail(usage) }
    let item = reminder(id: args[0])
    if let title = options["--title"] { item.title = title }
    if switches.contains("--no-due") {
        item.dueDateComponents = nil
        item.alarms = nil
    } else if options["--due"] != nil || options["--time"] != nil {
        guard let day = options["--due"].map(parseDay) ?? dueDate(of: item) else {
            fail("This reminder has no due date; give --due with --time.")
        }
        setDue(item, day: calendar.startOfDay(for: day), time: options["--time"])
    }
    if switches.contains("--done") { item.isCompleted = true }
    if switches.contains("--undone") { item.isCompleted = false }
    applyReminderDetails(to: item)
    saveReminder(item)
    print("Updated " + describe(item, withID: true).dropFirst(2))
}

func deleteReminder() {
    guard args.count == 1 else { fail(usage) }
    let item = reminder(id: args[0])
    let summary = describe(item).dropFirst(2)
    do {
        try store.remove(item, commit: true)
    } catch {
        fail("Couldn't delete the reminder: \(error.localizedDescription)")
    }
    print("Deleted " + summary)
}

let commands: [String: (EKEntityType, () -> Void)] = [
    "list": (.event, list), "calendars": (.event, listCalendars),
    "add": (.event, add), "edit": (.event, edit), "delete": (.event, delete),
    "reminders": (.reminder, listReminders), "reminder-lists": (.reminder, listReminderLists),
    "remind": (.reminder, remind), "edit-reminder": (.reminder, editReminder),
    "delete-reminder": (.reminder, deleteReminder),
]
guard let (entity, run) = commands[command] else { fail(usage) }
requireAccess(to: entity)
run()
finish(0)
