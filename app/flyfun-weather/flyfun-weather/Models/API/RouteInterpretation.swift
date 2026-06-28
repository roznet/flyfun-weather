import Foundation

/// A resolved waypoint with coordinates and timezone (server `WaypointInfo`).
struct RouteWaypointInfo: Codable, Hashable, Sendable, Identifiable {
    let icao: String
    let name: String
    let lat: Double
    let lon: Double
    let timezone: String?

    var id: String { icao }
}

/// Result of `POST /api/flights/interpret-route` — what the server understood
/// from a free-typed route, what it skipped, and what it dropped as off-route.
struct InterpretRouteResponse: Codable, Sendable {
    let originalTokens: [String]
    let interpreted: [String]
    /// Tokens the server couldn't place at all (typos / unsupported formats).
    let skipped: [String]
    /// Tokens recognised but rejected as too far off the direct leg.
    let offRoute: [String]
    let waypoints: [RouteWaypointInfo]

    /// True when everything the user typed was understood — nothing skipped or
    /// dropped — so the save flow can accept silently (mirrors the web).
    var isClean: Bool { skipped.isEmpty && offRoute.isEmpty }
}

/// Request body for `interpret-route`.
struct InterpretRouteRequest: Encodable {
    let rawRoute: String
}

/// Result of `POST /api/flights/route-distance`.
struct RouteDistanceResponse: Codable, Sendable {
    let totalDistanceNm: Double
    let waypoints: [RouteWaypointInfo]
}

/// Request body for `route-distance`.
struct RouteDistanceRequest: Encodable {
    let waypoints: [String]
}
