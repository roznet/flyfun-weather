import Foundation

/// A navigation target requested from outside the SwiftUI view tree — today only
/// App Intents (Siri / Shortcuts / Spotlight), reusing the same seam `onOpenURL`
/// already relies on. Foregrounding intents set one of these; the UI consumes it
/// on the next `.active` scene phase and routes.
enum PendingNavigation: Equatable, Sendable {
    /// Show the flight list root (no selection).
    case flightList
    /// Open a specific flight's briefing by id.
    case briefing(flightId: String)
    /// Open the forecast map, optionally in a shared `fc.*` state (#420). A map
    /// link shared from desktop opens the phone on the same day/hour/metric/airport.
    case forecastMap(MapDeepLink)
    /// Open a shared flight by its share code (`/s/{code}`) as a preview with a
    /// Subscribe banner (#446). The flight isn't in `/api/flights` until the
    /// viewer subscribes, so the UI resolves the code via the by-share endpoint.
    case share(code: String)

    /// The target flight id, when this navigation names one.
    var flightId: String? {
        if case .briefing(let id) = self { return id }
        return nil
    }
}

/// Forecast-map deep-link state parsed from `/maps.html?fc.*`. All fields are
/// optional — an unqualified `/maps.html` opens the map at its cold-open default.
struct MapDeepLink: Equatable, Sendable {
    var day: Int?
    var hour: Int?
    var model: String?
    var metric: String?
    var airport: String?

    var isEmpty: Bool {
        day == nil && hour == nil && model == nil && metric == nil && airport == nil
    }
}

/// Cold-launch-safe hand-off for a `PendingNavigation`.
///
/// A foregrounding intent may run *before* `AppState` exists (cold launch, the
/// app process is spun up just to serve the intent), so an in-memory property on
/// `AppState` isn't enough on its own. The intent writes the target here; the app
/// reads + clears it on every `.active` transition — which fires on both cold
/// launch and warm foreground — so a single path covers both.
///
/// Backed by `UserDefaults.standard`: App Intents defined in the main app target
/// run **in-process**, so no App Group container is needed to share this. (When
/// Widgets/Live Activities later run in a separate process, this suite is the one
/// place that would move to a shared App Group — see the App-Group note in
/// `designs/ios-app-intents.md`.)
enum PendingNavigationStore {
    private static let key = "pendingNavigation"
    private static var defaults: UserDefaults { .standard }

    /// Record a target for the app to consume on next activation. Overwrites any
    /// earlier unconsumed target — the most recent user request wins.
    static func set(_ nav: PendingNavigation) {
        defaults.set(encode(nav), forKey: key)
    }

    /// Read and clear the pending target, if any.
    static func take() -> PendingNavigation? {
        guard let raw = defaults.string(forKey: key) else { return nil }
        defaults.removeObject(forKey: key)
        return decode(raw)
    }

    // Compact string encoding (no Codable ceremony for a few cases).
    private static func encode(_ nav: PendingNavigation) -> String {
        switch nav {
        case .flightList: "flightList"
        case .briefing(let id): "briefing:\(id)"
        case .forecastMap(let dl): "forecastMap:" + encodeMap(dl)
        case .share(let code): "share:\(code)"
        }
    }

    private static func decode(_ raw: String) -> PendingNavigation? {
        if raw == "flightList" { return .flightList }
        if raw.hasPrefix("briefing:") {
            let id = String(raw.dropFirst("briefing:".count))
            return id.isEmpty ? nil : .briefing(flightId: id)
        }
        if raw.hasPrefix("forecastMap:") {
            return .forecastMap(decodeMap(String(raw.dropFirst("forecastMap:".count))))
        }
        if raw.hasPrefix("share:") {
            let code = String(raw.dropFirst("share:".count))
            return code.isEmpty ? nil : .share(code: code)
        }
        return nil
    }

    private static func encodeMap(_ dl: MapDeepLink) -> String {
        var parts: [String] = []
        if let d = dl.day { parts.append("day=\(d)") }
        if let h = dl.hour { parts.append("hour=\(h)") }
        if let m = dl.model { parts.append("model=\(m)") }
        if let mt = dl.metric { parts.append("metric=\(mt)") }
        if let a = dl.airport { parts.append("apt=\(a)") }
        return parts.joined(separator: "&")
    }

    private static func decodeMap(_ query: String) -> MapDeepLink {
        var dl = MapDeepLink()
        for pair in query.split(separator: "&") {
            let kv = pair.split(separator: "=", maxSplits: 1)
            guard kv.count == 2 else { continue }
            let value = String(kv[1])
            switch String(kv[0]) {
            case "day": dl.day = Int(value)
            case "hour": dl.hour = Int(value)
            case "model": dl.model = value
            case "metric": dl.metric = value
            case "apt": dl.airport = value
            default: break
            }
        }
        return dl
    }
}
