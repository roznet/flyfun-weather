import Foundation

/// One entry from `GET /api/refresh/active` — a flight whose briefing is
/// currently queued or refreshing on the server. Drives the live "Updating…"
/// indicator on the flight-list row, mirroring the web's poll of the same
/// endpoint. Property names rely on the shared decoder's `.convertFromSnakeCase`
/// strategy; extra fields on the server payload are ignored.
struct ActiveRefreshResponse: Codable, Sendable {
    let flightId: String
    /// "queued" | "refreshing".
    let status: String?
    let stage: String?
    let detail: String?
}
