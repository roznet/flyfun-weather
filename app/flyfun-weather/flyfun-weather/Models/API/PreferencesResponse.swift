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

    var pushEnabled: Bool { notifyPush ?? false }
    var emailEnabled: Bool { notifyEmail ?? true }
    var scope: String { notifyScope ?? "auto" }
    var changeOnly: Bool { notifyChangeOnly ?? true }

    /// Signed-out / no-cache default.
    static let empty = PreferencesResponse(
        pirepCanView: false, pirepCanPublish: false,
        notifyEmail: nil, notifyPush: nil, notifyScope: nil, notifyChangeOnly: nil
    )
}
