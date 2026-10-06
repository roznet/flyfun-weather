import Foundation
import MapKit

// =============================================================================
// SYNC — mirrors src/weatherbrief/models/live.py (#688 storms, #690 glance /
// ribbon / focus). Built on the server once per live tick
// (`tasks/live_glance.py`) and served on `GET /flights/{id}/live`, so iOS, web
// and the agent `live` block show the same text. Clients render; they don't
// derive. Decoded with `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`).
//
// Every field is optional and enums stay raw strings: a field or value the
// server adds later renders generically instead of failing the whole decode.
// =============================================================================

/// Where the map opens when an item is tapped (#690 tap-to-map contract).
nonisolated struct LiveFocus: Codable, Sendable, Equatable, Hashable {
    /// storm / sigmet / station / segment.
    let kind: String?
    /// storm: lineage id; sigmet: "sigmet:LECM|6"; station: ICAO; segment: "seg:<n>".
    let id: String?
    /// [min_lon, min_lat, max_lon, max_lat].
    let bbox: [Double]?
    /// Any of route, radar, cells, lightning, sigmets, metar.
    let layers: [String]?
    /// The frame to show; nil = now.
    let time: String?

    var wantsCells: Bool { (layers ?? []).contains("cells") }
    var wantsRadar: Bool { (layers ?? []).contains("radar") }

    /// The map region framing `bbox`, with a little margin. nil without a
    /// usable box.
    var region: MKCoordinateRegion? {
        guard let b = bbox, b.count == 4, b[2] >= b[0], b[3] >= b[1] else { return nil }
        let center = CLLocationCoordinate2D(latitude: (b[1] + b[3]) / 2, longitude: (b[0] + b[2]) / 2)
        let span = MKCoordinateSpan(latitudeDelta: max((b[3] - b[1]) * 1.2, 0.1),
                                    longitudeDelta: max((b[2] - b[0]) * 1.2, 0.1))
        return MKCoordinateRegion(center: center, span: span)
    }
}

// MARK: - Glance (the nutshell)

/// The Observed tab's top block: one "as of", one line comparing with the
/// briefing, one line per phase. Mirrors `models/live.py::LiveGlance`.
nonisolated struct LiveGlance: Codable, Sendable {
    let asOf: String?
    /// "Observed 14:29Z · as briefed, departure improving".
    let headline: String?
    /// as_briefed / worse / better / mixed / unavailable.
    let comparison: String?
    let lines: [LiveGlanceLine]?

    var items: [LiveGlanceLine] { lines ?? [] }
}

/// One nutshell line. The text is the server's, shown as is: observations
/// only, "unavailable" never "clear", counts are storms.
nonisolated struct LiveGlanceLine: Codable, Sendable, Identifiable {
    /// departure / enroute / arrival.
    let phase: String?
    let icao: String?
    let text: String?
    /// An alert-tier change belongs to this phase (styling only).
    let alert: Bool?
    /// Behind the flight at plan (dim it).
    let passed: Bool?
    /// Sources this line could not read: metar / taf / storms / lightning / sigmets.
    let unavailable: [String]?
    let sources: [String]?
    let focus: LiveFocus?

    var id: String { phase ?? (text ?? "") }

    var phaseLabel: String {
        switch phase {
        case "departure": String(localized: "Departure")
        case "enroute": String(localized: "En route")
        case "arrival": String(localized: "Arrival")
        default: phase?.capitalized ?? ""
        }
    }
}

// MARK: - Storms (#688)

/// The radar storms near the route at the newest cell frame. Mirrors
/// `models/live.py::LiveStorms`; `status` is never silently "nothing".
nonisolated struct LiveStorms: Codable, Sendable {
    /// available / stale / disabled / unavailable.
    let status: String?
    let frameTime: String?
    let unavailableSince: String?
    let lightningPending: Bool?
    let corridorNm: Double?
    let routeNm: Double?
    let storms: [LiveStorm]?

    var isAvailable: Bool { status == "available" }
    var items: [LiveStorm] { storms ?? [] }
}

nonisolated struct LiveStormTrackPoint: Codable, Sendable {
    let at: String?
    let offtrackNm: Double?
    let crossNm: Double?
}

/// Closest approach at current motion: a projection, shown only in the storm
/// detail, labelled "Estimate at current motion" (observed-tab §2).
nonisolated struct LiveStormEstimate: Codable, Sendable {
    let cpaNm: Double?
    let cpaTime: String?
    let atEtaOfftrackNm: Double?
    let horizonMin: Double?
}

/// One storm against the route. Mirrors `models/live.py::LiveStorm`.
nonisolated struct LiveStorm: Codable, Sendable, Identifiable {
    let id: String
    let cellIds: [String]?
    let lat: Double?
    let lon: Double?
    let peakDbz: Double?
    /// heavy / very heavy / extreme; nil below heavy.
    let intensity: String?
    let flashes: Int?
    let flashesPending: Bool?
    let topFl: Int?
    let truncated: Bool?
    let trend: String?
    let dPeakDb: Double?
    let areaRatio: Double?
    let dFlashes: Int?
    let motionStatus: String?
    let speedKt: Double?
    let towardDeg: Double?
    let alongNm: Double?
    let offtrackNm: Double?
    /// Signed: + right of track, − left.
    let crossNm: Double?
    let side: String?
    let end: String?
    let endIcao: String?
    let endBearing: String?
    let abeamEta: String?
    let minutesToAbeam: Double?
    let ahead: Bool?
    /// closing / moving_away / parallel / stationary / unknown.
    let relativeMotion: String?
    let closingKt: Double?
    let history: [LiveStormTrackPoint]?
    let backing: [String]?
    let estimate: LiveStormEstimate?
    let focus: LiveFocus?

    var isClosing: Bool { relativeMotion == "closing" }
}

// MARK: - Ribbon

/// The route ribbon: x = distance along the route with ETAs, y = left/right of
/// track. The storm lane is `LiveLayerResponse.storms`. Mirrors
/// `models/live.py::LiveRibbon`.
nonisolated struct LiveRibbon: Codable, Sendable {
    let routeNm: Double?
    let flownNm: Double?
    let departureAt: String?
    let arrivalAt: String?
    let segmentNm: Double?
    let radarRadiusNm: Double?
    let radarTime: String?
    let waypoints: [RibbonWaypoint]?
    let segments: [RibbonSegment]?
    let stations: [RibbonStation]?
    let sigmets: [RibbonSigmet]?
}

nonisolated struct RibbonWaypoint: Codable, Sendable {
    let icao: String?
    let alongNm: Double?
    let eta: String?
}

nonisolated struct RibbonSegment: Codable, Sendable, Identifiable {
    let index: Int
    let fromNm: Double?
    let toNm: Double?
    let etaFrom: String?
    let etaTo: String?
    /// nil with `radarStatus == "measured"` = looked, nothing detected.
    let radarMaxDbz: Double?
    let radarIntensity: String?
    /// measured / no_coverage / no_sample.
    let radarStatus: String?
    let lightning: Bool?
    let sigmetIds: [String]?
    let stormIds: [String]?
    let focus: LiveFocus?

    var id: Int { index }
}

nonisolated struct RibbonStation: Codable, Sendable, Identifiable {
    let icao: String
    let role: String?
    let alongNm: Double?
    let crossNm: Double?
    let eta: String?
    let metarCategory: String?
    let metarTime: String?
    let convective: [String]?
    let tafCategoryAtEta: String?
    let tafTemporaryType: String?
    let tafTemporaryCategory: String?
    let tafWeather: [String]?
    let focus: LiveFocus?

    var id: String { icao }
}

nonisolated struct RibbonSigmet: Codable, Sendable, Identifiable {
    let id: String
    let label: String?
    let hazard: String?
    let qualifier: String?
    let fromNm: Double?
    let toNm: Double?
    let minDistanceNm: Double?
    let validFrom: String?
    let validTo: String?
    let pending: Bool?
    let new: Bool?
    /// toward / away / parallel / stationary / unknown.
    let motion: String?
    let focus: LiveFocus?
}
