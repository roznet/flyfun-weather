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

/// Response of `GET /api/flights/badge` and `POST /api/flights/{id}/seen`:
/// the server-derived count of flights with an unseen briefing update.
struct BadgeStatus: Decodable, Sendable {
    let count: Int
}
