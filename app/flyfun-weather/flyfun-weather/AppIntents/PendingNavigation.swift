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

    /// The target flight id, when this navigation names one.
    var flightId: String? {
        if case .briefing(let id) = self { return id }
        return nil
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

    // Compact string encoding (no Codable ceremony for two cases).
    private static func encode(_ nav: PendingNavigation) -> String {
        switch nav {
        case .flightList: "flightList"
        case .briefing(let id): "briefing:\(id)"
        }
    }

    private static func decode(_ raw: String) -> PendingNavigation? {
        if raw == "flightList" { return .flightList }
        if raw.hasPrefix("briefing:") {
            let id = String(raw.dropFirst("briefing:".count))
            return id.isEmpty ? nil : .briefing(flightId: id)
        }
        return nil
    }
}
