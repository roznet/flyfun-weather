import Foundation

// =============================================================================
// SYNC — mirrors src/weatherbrief/models/observed.py (#574 phase 1)
//
// Observed conditions along the route corridor at D-0: OPERA radar reflectivity
// and rain rate, EUMETSAT MTG total lightning and satellite cloud tops, sampled
// in concentric discs around every corridor station. Rides inline on
// `briefing.json` beside `route_observations`, so it arrives with the snapshot
// and inside the offline `/bundle` — no separate endpoint.
//
// Three invariants from the Python module docstring survive the trip, and each
// is load-bearing on this side too:
//
//  1. **Absence is three-state, per source.** `nodataPx` (the sensor does not
//     look here — 49.4% of the OPERA grid) is never folded into `undetectPx`
//     (the sensor looked and saw nothing). Swift `Optional` makes that fold very
//     easy to write by accident: `maxValue == nil` is TRUE in both cases, so
//     renderers must branch on `insufficientCoverage`, never on the optional.
//  2. **No synthetic common timestamp.** Every field carries its own frame's
//     `validTime` / `ageMinutes`. `ObservedConditions.computedAt` is when the
//     payload was ASSEMBLED and must never be rendered as an observation age —
//     `ObservedConditionsTests` asserts no surface does.
//  3. **`qualityMethod` is a retrieval-method histogram, not confidence.**
//     Method 9 is opaque RTM + inversion; 0 can mean bad retrieval or clear.
//     Neither establishes cloud layering. Retained as wire metadata only.
//
// Decoded with the shared `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`).
// That strategy also rewrites DICTIONARY keys — the trap `ForecastMapResponse`
// and `HelpCatalogResponse` both document — so it is worth stating why the three
// histograms here are safe: `flBins` keys are `"FL000-050"` (hyphen, no
// underscore), `flFine` keys are `"60"`, `qualityMethod` keys are `"0"`…`"9"`.
// None contains an underscore, and the strategy leaves such keys untouched. Add
// an underscore-bearing key on the server and this file needs explicit
// `CodingKeys`.
// =============================================================================

/// Legacy histogram resolution in hundreds of geometric feet (not flight levels).
/// Mirrors `CLOUD_TOP_FINE_FL_STEP` in `models/observed.py`.
let observedFineFlStep = 10

/// Coarse flight-level bands, mirroring `CLOUD_TOP_FL_BINS`. Kept only as the
/// fallback for a pack built before `flFine` existed: the coarse buckets are
/// right for prose ("87% of tops above FL250") and actively misleading as
/// geometry — at one measured station the FL050-150 bucket held pixels spanning
/// only FL60–FL92, so 68% of a bar drawn across it was empty air.
let observedCoarseTopBands: [(label: String, loFt: Double, hiFt: Double)] = [
    ("FL000-050", 0, 5_000),
    ("FL050-150", 5_000, 15_000),
    ("FL150-250", 15_000, 25_000),
    ("FL250-400", 25_000, 40_000),
    ("FL400+", 40_000, 60_000),
]

/// Provenance for one observed field, read from the frame itself (the producer
/// varies — one sampled OPERA composite was built by Météo-France rather than
/// centrally by EUMETNET).
struct ObservedAttribution: Codable, Sendable {
    let producer: String?
    let license: String?
    let url: String?
    /// Verbatim attribution line, built server-side so every surface renders the
    /// same string.
    let text: String?
}

/// One station × one radius × one gridded field.
///
/// The pixel counts partition the disc exactly:
///     totalPx == validPx + nodataPx
///     validPx == detectedPx + undetectPx
struct ObservedAnnulus: Codable, Sendable {
    let radiusNm: Double
    let totalPx: Int
    let validPx: Int
    let nodataPx: Int
    let undetectPx: Int
    let detectedPx: Int
    let maxValue: Double?
    let meanValue: Double?
    let p90Value: Double?

    // Server-computed (pydantic `computed_field`), so they arrive on the wire
    // rather than being re-derived here — one definition, no drift.
    let coverageFraction: Double?
    let detectedFraction: Double?
    /// True when the disc must render as "no coverage", never as clear sky.
    let insufficientCoverage: Bool?

    var isInsufficient: Bool { insufficientCoverage ?? false }
}

/// Cloud-top disc: adds the two histograms the tops question needs.
struct ObservedTopsAnnulus: Codable, Sendable {
    let radiusNm: Double
    let totalPx: Int
    let validPx: Int
    let nodataPx: Int
    let undetectPx: Int
    let detectedPx: Int
    let maxValue: Double?
    let coverageFraction: Double?
    let detectedFraction: Double?
    let insufficientCoverage: Bool?

    /// Coarse bands, keyed by label ("FL150-250"). Prose only — see
    /// `observedCoarseTopBands`.
    let flBins: [String: Int]?
    /// Sparse fine histogram keyed in hundreds of geometric feet ("60" ==
    /// 6000–7000 ft MSL, step `observedFineFlStep`). Sparse because empty air is most
    /// of the column, and because here — unlike everywhere else in this payload
    /// — "absent" and "zero" genuinely mean the same thing.
    let flFine: [String: Int]?
    /// Per-pixel height-assignment method, not confidence or cloud layering.
    /// Guide table 10: 0 = bad/clear; 9 = opaque RTM + inversion.
    let qualityMethod: [String: Int]?
    let highestFl: Double?
    /// Coldest top in the disc (K) — the deepest convection, not an average.
    let coldestTopK: Double?
    /// Decoded IR effective cloudiness: cloud amount × emissivity. Historical
    /// granules decode as 0–1 despite percent metadata; scale needs validation.
    /// Not visible opacity or a METAR coverage category.
    let highestCloudiness: Double?
    let medianCloudiness: Double?
    /// Pressure-based FL of the highest top — what an altimeter agrees with,
    /// unlike the geometric `highestFl`. Both travel because they answer
    /// different questions and can differ materially.
    let highestAviationFl: Double?

    var isInsufficient: Bool { insufficientCoverage ?? false }
}

/// Lightning disc. This point payload has no coverage/quality mask; zero is
/// no flashes reported in the window, not guaranteed absence of convection.
struct ObservedFlashAnnulus: Codable, Sendable {
    let radiusNm: Double
    let flashCount: Int
    let areaKm2: Double?
    let windowMinutes: Double?
    let nearestFlashNm: Double?
    let latestFlashTime: String?
    /// Note the capital **K**. `.convertFromSnakeCase` splits
    /// `flashes_per_1000km2_per_min` on underscores and applies `.capitalized` to
    /// each later component — and `"1000km2".capitalized` is `"1000Km2"`, because
    /// `capitalized` uppercases the first *letter* of a word, not its first
    /// character. The obvious spelling (`…Per1000km2PerMin`) matches nothing and
    /// decodes to `nil` rather than throwing, so the lightning rate would vanish
    /// from the chart in silence. Pinned by
    /// `ObservedConditionsTests.decodesFlashRateKey`.
    let flashesPer1000Km2PerMin: Double?
}

/// A corridor station the sampler measured around. Shared across fields so the
/// four sources agree on *where* they sampled even though they disagree on
/// *when*.
struct ObservedStationRef: Codable, Sendable {
    let id: String
    let name: String?
    let lat: Double
    let lon: Double
    let enrouteDistanceNm: Double?
    let distanceFromRouteNm: Double?
}

struct ObservedStationSamples: Codable, Sendable {
    let stationId: String
    let annuli: [ObservedAnnulus]
}

struct ObservedTopsStationSamples: Codable, Sendable {
    let stationId: String
    let annuli: [ObservedTopsAnnulus]
}

struct ObservedFlashStationSamples: Codable, Sendable {
    let stationId: String
    let annuli: [ObservedFlashAnnulus]
}

/// Frame identity for one observed field. There is deliberately no payload-level
/// "observed at" — invariant 2.
protocol ObservedFieldMeta {
    var source: String { get }
    var quantity: String { get }
    var units: String? { get }
    var validTime: String { get }
    var ageMinutes: Double { get }
    /// Width of the product's acquisition window; `0` for an instantaneous
    /// retrieval. DBZH combines radar scans from the preceding 10 minutes.
    var windowMinutes: Double? { get }
    var attribution: ObservedAttribution? { get }
}

struct ObservedField: Codable, Sendable, ObservedFieldMeta {
    let source: String
    let quantity: String
    let units: String?
    let validTime: String
    let ageMinutes: Double
    let windowMinutes: Double?
    let attribution: ObservedAttribution?
    let stations: [ObservedStationSamples]
}

struct ObservedTopsField: Codable, Sendable, ObservedFieldMeta {
    let source: String
    let quantity: String
    let units: String?
    let validTime: String
    let ageMinutes: Double
    let windowMinutes: Double?
    let attribution: ObservedAttribution?
    let stations: [ObservedTopsStationSamples]
}

struct ObservedFlashField: Codable, Sendable, ObservedFieldMeta {
    let source: String
    let quantity: String
    let units: String?
    let validTime: String
    let ageMinutes: Double
    let windowMinutes: Double?
    let attribution: ObservedAttribution?
    let stations: [ObservedFlashStationSamples]
}

/// One clause of the "Observed now" readout, with its provenance. `kind` names
/// the source so a client can pair the clause with that source's own frame age —
/// which must never be blended across sources — rather than parsing the prose.
struct ObservedSummaryEntry: Codable, Sendable, Identifiable {
    /// lightning | reflectivity | rain_rate | cloud_tops | coverage
    let kind: String
    let text: String
    /// Metric-catalog card that explains the clause, for the (i) affordance.
    let metricId: String?

    var id: String { kind + text }
}

/// Why a source is missing, when it is. A source that is absent must say so
/// distinctly from a source that is present and saw nothing — the same
/// three-state discipline as the pixel counts, one level up.
struct ObservedSourceStatus: Codable, Sendable, Identifiable {
    let source: String
    let available: Bool
    let reason: String?
    let latestValidTime: String?

    var id: String { source }
}

/// Observed conditions along the route corridor (D-0).
struct ObservedConditions: Codable, Sendable {
    /// When the payload was ASSEMBLED. Deliberately not an observation time —
    /// never render this as an age (invariant 2).
    let computedAt: String?
    let corridorNm: Double?
    /// Every sampled radius ships together, so changing the corridor re-resolves
    /// the discs from data already in memory with no request.
    let radiiNm: [Double]?
    let stations: [ObservedStationRef]?

    let reflectivity: ObservedField?
    let rainRate: ObservedField?
    let cloudTops: ObservedTopsField?
    let lightning: ObservedFlashField?

    let summary: String?
    let summaryEntries: [ObservedSummaryEntry]?
    let summaryLines: [String]?
    let sources: [ObservedSourceStatus]?

    var hasAnyField: Bool {
        reflectivity != nil || rainRate != nil || cloudTops != nil || lightning != nil
    }
}
