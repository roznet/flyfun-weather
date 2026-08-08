import Foundation

/// Subset of the server's user preferences that the iOS app cares about.
/// Additional server fields are silently ignored by the decoder.
struct PreferencesResponse: Codable, Sendable {
    let pirepCanView: Bool
    let pirepCanPublish: Bool
    // Briefing-refresh notification channels/scope. Optional so an older server
    // that doesn't send them decodes cleanly; accessors below fold in the same
    // defaults the server uses (email on, scope auto, change-only on, push off).
    let notifyEmail: Bool?
    let notifyPush: Bool?
    let notifyScope: String?
    let notifyChangeOnly: Bool?
    // One-time fail-safe notice: email was auto-re-enabled after the last push
    // device was removed while email was off (channel-invariant decay branch).
    let notifyDecayNotice: Bool?
    // Registered APNs device count — push is only actionable with ≥1 device.
    let pushDeviceCount: Int?
    // Ordering of the *upcoming* flights section (#536). Optional for the same
    // reason as the notify fields, and one more: this struct is cached to
    // UserDefaults and decoded at launch, so a non-optional addition would fail
    // that decode and silently reset every cached flag to `.empty`.
    let flightOrder: String?

    var pushEnabled: Bool { notifyPush ?? false }
    var emailEnabled: Bool { notifyEmail ?? true }
    var scope: String { notifyScope ?? "auto" }
    var changeOnly: Bool { notifyChangeOnly ?? true }
    var decayNotice: Bool { notifyDecayNotice ?? false }
    var deviceCount: Int { pushDeviceCount ?? 0 }
    var hasPushDevice: Bool { deviceCount > 0 }
    /// Upcoming-flights ordering, defaulting to today's behaviour on an older
    /// server (or an unknown value written by a future one).
    var flightOrderPreference: FlightOrder { FlightOrder(rawValue: flightOrder ?? "") ?? .furthestFirst }

    /// "Briefing updates" 3-stop — folds scope + change-only into one control
    /// (off / changes / every). Kept identical to the web fold so the two
    /// clients stay consistent.
    var briefingUpdates: BriefingUpdates {
        if scope == "off" { return .off }
        return changeOnly ? .changes : .every
    }

    /// Signed-out / no-cache default.
    static let empty = PreferencesResponse(
        pirepCanView: false, pirepCanPublish: false,
        notifyEmail: nil, notifyPush: nil, notifyScope: nil, notifyChangeOnly: nil,
        notifyDecayNotice: nil, pushDeviceCount: nil, flightOrder: nil
    )
}

/// How the *upcoming* flights section is ordered (#536). Past and Recent are
/// always most-recent-first, under both values. Raw values mirror the server's
/// `flight_order` preference key.
enum FlightOrder: String, CaseIterable, Sendable, Identifiable {
    case furthestFirst = "furthest_first"
    case soonestFirst = "soonest_first"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .furthestFirst: "Furthest first"
        case .soonestFirst: "Soonest first"
        }
    }
}

/// The three "Briefing updates" stops. Off silences default-resolution flights;
/// changes notifies only when the assessment/outlook moves; every notifies on
/// every completion. Maps to the stored `notify_scope` + `notify_change_only`.
enum BriefingUpdates: String, CaseIterable, Sendable, Identifiable {
    case off
    case changes
    case every

    var id: String { rawValue }

    /// The stored fields this stop unfolds to (never writes legacy "auto").
    var scope: String { self == .off ? "off" : "all" }
    var changeOnly: Bool { self == .changes }
}
