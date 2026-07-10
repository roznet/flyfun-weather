import Foundation

/// Body for `POST /api/devices` — registers/upserts this device's APNs token.
/// Encoded with `JSONEncoder.weatherBrief` (`.convertToSnakeCase`), so
/// `environment` → `environment` and the token maps verbatim.
struct DeviceRegistrationRequest: Encodable, Sendable {
    let token: String
    let environment: String   // "sandbox" | "production"
}

/// Body for `PUT /api/user/preferences` when toggling push from the app.
/// Only the changed key is sent; the server merges it into `app_prefs_json`.
struct NotifyPushUpdateRequest: Encodable, Sendable {
    let notifyPush: Bool   // -> "notify_push"
}

/// Body for `PUT /api/user/preferences` when toggling the email channel.
struct NotifyEmailUpdateRequest: Encodable, Sendable {
    let notifyEmail: Bool   // -> "notify_email"
}

/// Body for `PUT /api/user/preferences` when changing the "Briefing updates"
/// 3-stop. Sends both stored fields the picker folds into (scope + change-only).
struct BriefingUpdatesUpdateRequest: Encodable, Sendable {
    let notifyScope: String       // -> "notify_scope" ("all" | "off")
    let notifyChangeOnly: Bool    // -> "notify_change_only"
}

/// Body for dismissing the one-time channel-invariant decay notice.
struct NotifyDecayDismissRequest: Encodable, Sendable {
    let notifyDecayNotice: Bool   // -> "notify_decay_notice" (always false)
}

/// Response of `GET /api/flights/badge` and `POST /api/flights/{id}/seen`:
/// the server-derived count of flights with an unseen briefing update.
struct BadgeStatus: Decodable, Sendable {
    let count: Int
}
