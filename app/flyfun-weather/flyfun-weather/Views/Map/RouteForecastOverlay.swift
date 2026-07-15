import Foundation

/// Pure helpers for the briefing route-map airport-forecast overlay (#428, the
/// iOS port of #424/#425). No MapKit / UIKit / side effects, so the day/hour
/// boundary math, hour snapping and deep-link building are unit-testable.
///
/// The forecast horizon and the sample hours each day offers are read from the
/// server's grid (`repository.forecastDays()`), never restated here — the grid is
/// not rectangular (far days carry fewer models and coarser hours) and
/// `designs/forecast-page.md` is explicit that the client must not hardcode it.
/// This mirrors `web/ts/visualization/route-map/forecast-overlay.ts`.
enum RouteForecastOverlay {
    /// The individual forecast models the snapshot backend populates and the full
    /// map's `fc.model` deep-link key accepts (matches web
    /// `FORECAST_INDIVIDUAL_MODELS`). A consensus/other token falls back to the
    /// full map's default (no `fc.model` in the link).
    static let individualModels: Set<String> = ["gfs", "icon", "ecmwf"]

    /// Relative (day, hour) in UTC for a departure ISO string, matching the
    /// forecast endpoint's `now + day` date labelling. `nil` when unparseable.
    /// `now` is injected so the boundary math is deterministic in tests.
    static func relativeDayHour(departureIso: String?, now: Date) -> (day: Int, hour: Int)? {
        guard let departureIso, let dep = parseISO(departureIso) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let depDay = cal.startOfDay(for: dep)
        let nowDay = cal.startOfDay(for: now)
        guard let day = cal.dateComponents([.day], from: nowDay, to: depDay).day else { return nil }
        let hour = cal.component(.hour, from: dep)
        return (day, hour)
    }

    /// Nearest value in `hours` to `h`; ties resolve to the earlier hour. `nil`
    /// for an empty list.
    static func nearestHour(_ hours: [Int], _ h: Int) -> Int? {
        guard !hours.isEmpty else { return nil }
        // `min(by:)` keeps the first element on a tie, and `hours` is sorted
        // ascending, so equidistant hours resolve to the earlier one.
        return hours.min(by: { abs($0 - h) < abs($1 - h) })
    }

    /// Resolve the overlay slot for a flight against the server's advertised grid.
    /// `nil` when the flight is outside the forecast horizon or the target day
    /// carries no data — the overlay is then not offered. Offered hours come from
    /// `days`, so the value always matches what the day actually holds (e.g. D+6's
    /// coarse 6/12/18 rather than the fine near-day set).
    static func resolveSlot(departureIso: String?, days: [ForecastDay], now: Date) -> RouteForecastSlot? {
        guard let rel = relativeDayHour(departureIso: departureIso, now: now) else { return nil }
        guard let entry = days.first(where: { $0.day == rel.day && $0.available && !$0.hours.isEmpty }),
              let hour = nearestHour(entry.hours, rel.hour) else { return nil }
        return RouteForecastSlot(day: rel.day, hour: hour, models: entry.models)
    }

    /// Short UTC label for a forecast valid-time ISO string, e.g. "Wed 12Z".
    /// Empty for an unparseable / missing value.
    static func formatForecastTime(_ iso: String?) -> String {
        guard let iso, let d = parseISO(iso) else { return "" }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        let wd = weekdays[(cal.component(.weekday, from: d) - 1) % 7]
        let hour = cal.component(.hour, from: d)
        return String(format: "%@ %02dZ", wd, hour)
    }

    /// Deep-link into the full forecast map seeded with the same slot/model/metric.
    /// The model is passed through only for a supported individual model; a
    /// consensus/other token is omitted so the full map uses its own default
    /// (matches web `forecastMapUrl`).
    static func deepLink(slot: RouteForecastSlot, model: String, metric: String) -> MapDeepLink {
        MapDeepLink(
            day: slot.day,
            hour: slot.hour,
            model: individualModels.contains(model) ? model : nil,
            metric: metric,
            airport: nil
        )
    }

    /// Parse an ISO8601 instant, tolerating both the `…Z` and fractional-second
    /// forms the API emits.
    private static func parseISO(_ s: String) -> Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: s)
    }
}

/// The resolved overlay slot: the day/hour on the server grid nearest the flight,
/// plus the models that carry airport data for it (used to decide whether the
/// briefing's selected model can be drawn).
struct RouteForecastSlot: Equatable {
    let day: Int
    let hour: Int
    let models: [String]

    /// Identity used to cache the fetched snapshot by slot.
    var key: String { "\(day):\(hour)" }
}
