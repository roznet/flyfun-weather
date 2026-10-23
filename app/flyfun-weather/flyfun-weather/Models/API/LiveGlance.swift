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
///
/// iOS honours less than the contract carries: `bbox` frames the map and the
/// `cells` / `radar` layers are switched on; `sigmets` / `metar` and `time`
/// are ignored (the map draws no SIGMET polygons and always shows the newest
/// frame). A client that implements more should not take this as the spec.
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
    var wantsLightning: Bool { (layers ?? []).contains("lightning") }

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
    /// The model-written one-glance line (#697). nil until the first
    /// generation lands, after a rejected one and after arrival: show
    /// `headline` in its place then.
    var highlight: LiveHighlight? = nil
    /// #816: the flight strip and "Destination at your ETA". nil from an older
    /// server; `flight` also nil for a viewer the owner hides position from.
    var flight: LiveGlanceFlight? = nil
    var destination: LiveGlanceDestination? = nil
    /// #880: the departure card's block, the destination's twin read at the
    /// ETD plus the fold. nil from an older server.
    var departure: LiveGlanceDeparture? = nil

    var items: [LiveGlanceLine] { lines ?? [] }
    /// Alert-tier lines sit above the highlight and are never folded (#697, #816).
    var alertItems: [LiveGlanceLine] { items.filter { $0.alert == true } }
    var otherItems: [LiveGlanceLine] { items.filter { $0.alert != true } }

    /// The non-alert lines the tab lays out itself: an airport card stands in
    /// for its non-alert terminal line (#816 arrival, #882 departure).
    /// SYNC — `nutshellHtml` in web/ts/visualization/observed/nutshell-view.ts.
    private var restItems: [LiveGlanceLine] {
        otherItems.filter {
            !(destination != nil && $0.phase == "arrival") && !(departure != nil && $0.phase == "departure")
        }
    }
    /// Lines still ahead of the flight: first in the Details fold (#882).
    var aheadItems: [LiveGlanceLine] { restItems.filter { $0.passed != true } }
    /// Lines the flight has passed: in the Details fold, dimmed.
    var passedItems: [LiveGlanceLine] { restItems.filter { $0.passed == true } }
}

/// The flight strip (#816), `models/live.py::LiveGlanceFlight`: where the
/// flight is and its ETA, in the server's words.
nonisolated struct LiveGlanceFlight: Codable, Sendable, Equatable {
    /// planned / ground / enroute / landed.
    let phase: String?
    /// "120 of 280 NM · ETA LPPT 10:32Z (+12 min vs plan) · position 4 min ago".
    let text: String?
    let source: String?
    let eta: String?
    let etaDelayMin: Int?
    let alongNm: Double?
    let routeNm: Double?
    let fixAgeMin: Double?
    let estimated: Bool?
    let departedAt: String?
    let landedAt: String?
    /// The ribbon marker's caption: "You · 4 min ago", "Estimated position",
    /// "Planned position" or "Landed".
    let markerLabel: String?

    var isLanded: Bool { phase == "landed" }
}

/// One storm within 20 NM of the destination (#816).
nonisolated struct LiveDestinationCell: Codable, Sendable, Equatable, Identifiable {
    let id: String
    /// "9 NM NE · 45 dBZ, lightning · closing 8 kt".
    let text: String?
    let focus: LiveFocus?
}

/// The fields both airport cards read (#880). The departure and destination
/// blocks are twins on the server (`LiveGlanceDeparture` extends the
/// destination's fields); the card draws either through this.
nonisolated protocol LiveGlanceTerminal: Sendable {
    var icao: String? { get }
    var name: String? { get }
    var metarText: String? { get }
    var metarCategory: String? { get }
    var metarTime: String? { get }
    var proxyCaption: String? { get }
    var tafText: String? { get }
    var windText: String? { get }
    var cellsText: String? { get }
    var cellsShort: String? { get }
    var cells: [LiveDestinationCell]? { get }
    var lightningText: String? { get }
    var cellsAlert: Bool? { get }
    var lightningAlert: Bool? { get }
    var alert: Bool? { get }
    var focus: LiveFocus? { get }
    var airport: AirportObservation? { get }
}

/// "Destination at your ETA" (#816), `models/live.py::LiveGlanceDestination`:
/// the arrival card since #882. Every clause is the server's.
nonisolated struct LiveGlanceDestination: Codable, Sendable, Equatable, LiveGlanceTerminal {
    let icao: String?
    let name: String?
    let eta: String?
    let metarText: String?
    let metarCategory: String?
    let metarTime: String?
    let proxyCaption: String?
    let tafText: String?
    let tafCategory: String?
    let windText: String?
    /// green / amber / red: the server's own wind advisory.
    let windAdvisory: String?
    let cellsText: String?
    /// "7 cells ≤20 NM": the card's count line (#882 follow-up). Optional,
    /// so an older server falls back to `cellsText`.
    let cellsShort: String?
    let cells: [LiveDestinationCell]?
    let lightningText: String?
    let alert: Bool?
    let unavailable: [String]?
    let focus: LiveFocus?
    /// #881: an alert-tier change stands behind the card's cells / lightning
    /// row: tone that row amber. nil / false = quiet.
    var cellsAlert: Bool? = nil
    var lightningAlert: Bool? = nil
    /// #880: the field as the card reads it (its own row, or with its proxy
    /// station's METAR/TAF laid in). Never pick `route_observations` rows.
    var airport: AirportObservation? = nil
}

/// The departure card's block (#880), `models/live.py::LiveGlanceDeparture`:
/// the destination's twin read at the ETD, plus the fold.
nonisolated struct LiveGlanceDeparture: Codable, Sendable, Equatable, LiveGlanceTerminal {
    let icao: String?
    let name: String?
    /// nil under position_sharing = none.
    let etd: String?
    let metarText: String?
    let metarCategory: String?
    let metarTime: String?
    let proxyCaption: String?
    let tafText: String?
    let tafCategory: String?
    let windText: String?
    let windAdvisory: String?
    let cellsText: String?
    let cellsShort: String?
    let cells: [LiveDestinationCell]?
    let lightningText: String?
    let alert: Bool?
    let unavailable: [String]?
    let focus: LiveFocus?
    var cellsAlert: Bool? = nil
    var lightningAlert: Bool? = nil
    var airport: AirportObservation? = nil
    /// The departure is behind the flight, by evidence (a take-off entered or
    /// fixes), never the plan alone. The server decides; the card folds.
    var folded: Bool? = nil
    var foldedAt: String? = nil
    /// takeoff / fixes.
    var foldedSource: String? = nil
    /// "LFPN 290/15G25 · RWY 25R · departed ~10:09Z"; nil while unfolded.
    var foldedText: String? = nil

    var isFolded: Bool { folded == true }
}

/// `glance.highlight` as `/live` serves it (the server's regeneration gate is
/// stripped). Mirrors `models/live.py::LiveHighlight`. A rating sends these
/// four fields back verbatim (`HighlightFeedbackRequest.context`), so they
/// stay strings: no re-formatting on the way back.
nonisolated struct LiveHighlight: Codable, Sendable, Equatable, Hashable {
    let text: String
    let model: String?
    /// Keys the rating back into the server's `live_highlights.jsonl`.
    let factsHash: String?
    /// When the line was written: older than the layer when carried forward.
    let generatedAt: String?
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
    /// #767: the terminal reads a nearby station ("using EGLL as proxy ·
    /// 8 NM NE"); shown in gray under the text. Nil otherwise.
    let proxyCaption: String?

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
    /// #790: where it is in the pilot's terms; nil on a pre-#790 layer.
    var place: RoutePlace? = nil

    var isClosing: Bool { relativeMotion == "closing" }
}

// MARK: - Route locator (#790)

/// A route position in the pilot's terms (`analysis/route_locator.py`).
/// Server-built so every surface words the same point the same way. `text`
/// names the anchor in full ("10 NM before Middelburg (EHMZ)"), `short` by
/// ICAO ("10 NM before EHMZ"); neither carries the lateral offset, which is
/// `lateral` (", 6 NM left of track", leading comma included; nil under 3 NM).
nonisolated struct RoutePlace: Codable, Sendable, Equatable, Hashable {
    let text: String?
    let short: String?
    let lateral: String?
    /// near / before / after / between / over_sea / mid_crossing /
    /// off_departure / past_destination.
    let relation: String?
    /// The anchor's key: an ICAO, a waypoint, "coast_out@127".
    let anchor: String?
    /// waypoint / station / coast_out / coast_in.
    let anchorKind: String?
    let anchorIcao: String?
    /// The anchor's town, when the server knows one.
    let anchorName: String?
    /// NM from the anchor along the route (signed: + after), or from the
    /// route's end for off_departure / past_destination.
    let distanceNm: Double?
    let alongNm: Double?
    let crossNm: Double?
    let side: String?

    /// Off the route's ends: the place says it all, no "left of track".
    var isOffEnd: Bool { relation == "off_departure" || relation == "past_destination" }
}

/// One rung of the route locator's anchor ladder (#790). Informational.
nonisolated struct RouteAnchor: Codable, Sendable {
    let key: String?
    /// waypoint / station / coast_out / coast_in.
    let kind: String?
    let alongNm: Double?
    let lat: Double?
    let lon: Double?
    let icao: String?
    let town: String?
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
    let segments: [LiveRibbonSegment]?
    let stations: [RibbonStation]?
    let sigmets: [RibbonSigmet]?
    /// Rain/core bands from the cells feed; drawn while `weatherStatus` is
    /// "available", else the client falls back to the radar `segments`.
    let weather: [RibbonWeather]?
    let weatherStatus: String?
    let weatherCorridorNm: Double?
    let weatherBinNm: Double?
    /// #790: the anchors the server words positions against. Informational.
    var anchors: [RouteAnchor]? = nil

    var weatherAvailable: Bool { weatherStatus == "available" && weather != nil }
}

/// One rain area or convective core beside the route. Mirrors
/// `models/live.py::RibbonWeather`.
nonisolated struct RibbonWeather: Codable, Sendable, Identifiable {
    let id: String
    /// rain (≥ 20 dBZ outline) / core (≥ 35 dBZ outline).
    let tier: String?
    let fromNm: Double?
    let toNm: Double?
    /// left / right / both (across the track).
    let side: String?
    let nearNm: Double?
    let farNm: Double?
    /// Per bin: [along_nm, cross_lo_nm, cross_hi_nm], signed − left / + right.
    let profile: [[Double]]?
    let peakDbz: Double?
    let intensity: String?
    let flashes: Int?
    /// Motion relative to the course: 0 along it, +90 toward the right.
    let motionRelDeg: Double?
    let speedKt: Double?
    let stormId: String?

    var isCore: Bool { tier == "core" }
}

nonisolated struct RibbonWaypoint: Codable, Sendable {
    let icao: String?
    let alongNm: Double?
    let eta: String?
}

nonisolated struct LiveRibbonSegment: Codable, Sendable, Identifiable {
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
    /// #767: a departure/destination read through these proxy stations.
    let via: [String]?
    /// #790: the airport's town ("Dortmund").
    var name: String? = nil
    /// #790: where it is against the route's other anchors; nil for the
    /// departure / destination and on a pre-#790 layer.
    var place: RoutePlace? = nil

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
    /// #790: the stretch it covers in pilot words ("from Uden (EHVK) to
    /// Rijen (EHGR)"); nil on a pre-#790 layer.
    var spanText: String? = nil
    let validFrom: String?
    let validTo: String?
    let pending: Bool?
    let new: Bool?
    /// toward / away / parallel / stationary / unknown.
    let motion: String?
    let focus: LiveFocus?
}
