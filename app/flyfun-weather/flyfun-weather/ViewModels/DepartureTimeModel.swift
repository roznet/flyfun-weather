import Foundation

/// One selectable timezone for the departure-time picker.
struct DepartureTimeZoneOption: Identifiable, Hashable, Sendable {
    var id: String { identifier }
    let identifier: String   // IANA id, e.g. "Europe/Paris" or "UTC"
    let label: String        // e.g. "Paris (GMT+2)"
}

/// TZ-aware departure-time model (port of the web's `timezone.ts` behaviour).
///
/// The single source of truth is an absolute `instant` (UTC). The form edits a
/// wall-clock (date + hour + minute) *interpreted in the selected timezone*, and
/// the displayed values are always derived from `instant` in that zone — so
/// switching the timezone keeps the same instant and re-displays it (exactly the
/// web's "preserve the instant" behaviour), while editing the wall-clock rebuilds
/// the instant in the selected zone.
///
/// DST is handled natively: `Calendar.date(from:)` and
/// `TimeZone.secondsFromGMT(for:)` both resolve the offset for the actual date,
/// so a summer "Europe/Paris" shows GMT+2 and a winter one GMT+1 with no special
/// casing (the bug flyfun-forms' fixed-offset picker has).
@Observable
@MainActor
final class DepartureTimeModel {
    /// Absolute departure instant (UTC). Everything else is derived from this.
    var instant: Date

    /// IANA timezone the wall-clock is shown/edited in. Defaults to UTC until the
    /// route resolves a departure-airport timezone.
    var timeZoneId: String = "UTC" {
        didSet { /* instant unchanged → displayed wall-clock re-derives */ }
    }

    /// IANA timezones offered in the dropdown, in route order. UTC is always first.
    private var routeTimeZoneIds: [String] = []

    init(instant: Date) {
        self.instant = instant
    }

    var timeZone: TimeZone { TimeZone(identifier: timeZoneId) ?? .gmt }

    private var calendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        return cal
    }

    // MARK: - Displayed wall-clock (in the selected zone)

    var hour: Int { calendar.component(.hour, from: instant) }
    var minute: Int { calendar.component(.minute, from: instant) }

    /// Minute snapped to the 15-minute options the picker offers.
    var minuteOption: Int { Self.nearestMinuteOption(minute) }

    /// A proxy `Date` for a device-timezone `DatePicker` (date component only):
    /// it carries the selected-zone calendar day re-expressed in the device zone,
    /// so the picker shows the right Y/M/D no matter the device's own timezone.
    var dateProxy: Date {
        get {
            let c = calendar.dateComponents([.year, .month, .day], from: instant)
            var deviceComps = DateComponents()
            deviceComps.year = c.year; deviceComps.month = c.month; deviceComps.day = c.day
            deviceComps.hour = 12   // noon avoids DST/midnight edge cases in the device zone
            return Calendar.current.date(from: deviceComps) ?? instant
        }
        set {
            let c = Calendar.current.dateComponents([.year, .month, .day], from: newValue)
            rebuild(year: c.year, month: c.month, day: c.day)
        }
    }

    func setHour(_ newHour: Int) { rebuild(hour: newHour) }
    func setMinute(_ newMinute: Int) { rebuild(minute: newMinute) }

    /// Rebuild `instant` from the current wall-clock with the given overrides,
    /// interpreting the result in the selected timezone (DST-correct).
    private func rebuild(year: Int? = nil, month: Int? = nil, day: Int? = nil,
                         hour: Int? = nil, minute: Int? = nil) {
        var c = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: instant)
        if let year { c.year = year }
        if let month { c.month = month }
        if let day { c.day = day }
        if let hour { c.hour = hour }
        if let minute { c.minute = minute }
        c.second = 0
        if let rebuilt = calendar.date(from: c) { instant = rebuilt }
    }

    // MARK: - Timezone options

    /// Build the dropdown from resolved route waypoint timezones. UTC is always
    /// offered; route zones follow in order, de-duplicated. When the current
    /// selection is still the UTC default, switch to `preferred` (typically the
    /// departure airport) — this only changes the *display* zone, never the
    /// instant.
    func setRouteTimeZones(_ identifiers: [String], preferred: String?) {
        routeTimeZoneIds = identifiers
        let available = Set(["UTC"] + identifiers)
        if !available.contains(timeZoneId) {
            // The route changed and the selected zone is no longer offered —
            // reset so the picker never shows a blank selected row.
            timeZoneId = preferred ?? "UTC"
        } else if timeZoneId == "UTC", let preferred, identifiers.contains(preferred) {
            timeZoneId = preferred
        }
    }

    /// Options shown in the picker, with DST-correct offset labels for the
    /// currently-selected date (recomputed as the date changes).
    var options: [DepartureTimeZoneOption] {
        var seen = Set<String>()
        var result: [DepartureTimeZoneOption] = []
        for id in ["UTC"] + routeTimeZoneIds where !seen.contains(id) {
            seen.insert(id)
            result.append(DepartureTimeZoneOption(identifier: id, label: label(for: id)))
        }
        return result
    }

    private func label(for identifier: String) -> String {
        if identifier == "UTC" { return "UTC" }
        let zone = TimeZone(identifier: identifier) ?? .gmt
        let offsetMin = zone.secondsFromGMT(for: instant) / 60
        let city = identifier.split(separator: "/").last
            .map { $0.replacingOccurrences(of: "_", with: " ") } ?? identifier
        return "\(city) (\(Self.formatOffset(offsetMin)))"
    }

    // MARK: - Pure helpers (unit-tested)

    /// Minute options the picker offers.
    static let minuteOptions = [0, 15, 30, 45]

    static func nearestMinuteOption(_ m: Int) -> Int {
        minuteOptions.min(by: { abs($0 - m) < abs($1 - m) }) ?? 0
    }

    /// Format a signed minute offset as "GMT+2" / "GMT-5:30".
    static func formatOffset(_ offsetMinutes: Int) -> String {
        let sign = offsetMinutes >= 0 ? "+" : "-"
        let abs = Swift.abs(offsetMinutes)
        let h = abs / 60
        let m = abs % 60
        return m != 0 ? String(format: "GMT%@%d:%02d", sign, h, m) : "GMT\(sign)\(h)"
    }
}
