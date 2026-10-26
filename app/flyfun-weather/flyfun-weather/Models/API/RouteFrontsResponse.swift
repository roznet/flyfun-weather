import Foundation

/// The experimental front-detection artifact (`route_fronts.json`, #196), as
/// far as the cross-section needs it: the vertically-linked front chains the
/// server builds per model. Present only when the "Auto Front Detection"
/// preference was on at briefing time; the endpoint 404s otherwise.
///
/// Only `front_chains` is decoded. The per-level `per_model` crossings are what
/// the web falls back to on packs built before vertical linking, but drawing
/// them means deduping crossings across levels on the client; iOS draws only
/// what the server linked, so a pre-linking pack shows no boundary here.
///
/// SYNC: `web/ts/types/fronts.ts` (`RouteFrontsManifest`, `FrontChain`);
/// `src/weatherbrief/models/fronts.py` defines the shape.
nonisolated struct RouteFrontsResponse: Codable, Sendable, Equatable {
    /// model → linked chains. Empty on pre-linking packs.
    let frontChains: [String: [FrontChain]]?
}

/// One air-mass boundary linked across 925/850/700 hPa — the same front seen
/// at each level, displaced toward the cold air with height.
nonisolated struct FrontChain: Codable, Sendable, Equatable {
    /// Ordered bottom → top (925 → 850 → 700).
    let nodes: [FrontChainNode]
    /// Consensus kind: `cold` / `warm` / `quasi-stationary`. Optional, like the
    /// other descriptive fields, so one missing value never drops the whole
    /// artifact; the renderer has a fallback for each.
    let kind: String?
    /// Depth: 1 = shallow / single level.
    let nLevels: Int?
}

nonisolated struct FrontChainNode: Codable, Sendable, Equatable {
    /// `level_hPa` on the wire; the snake-case decoder lowercases the tail.
    let levelHpa: Int
    let distanceKm: Double
    let kind: String?
    /// `significant` / `classical` / `sharp`.
    let intensity: String?
    /// Weather at the node (`dry` / `partly` / `wet` / `convective`); drives the
    /// convective glyph.
    let coLocation: String?
}
