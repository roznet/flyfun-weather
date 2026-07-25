import Foundation

/// Briefing snapshot — route info, waypoint analyses, observations.
/// We decode only the fields needed for display.
struct SnapshotResponse: Codable, Sendable {
    let route: RouteConfig
    let targetDate: String
    let daysOut: Int
    let departureTime: String?
    let analyses: [WaypointAnalysis]?
    let routeObservations: RouteObservations?
    /// Weather-based divert candidates (D-2 inward, `compute_alternates` pref).
    /// Present only on marginal D-0/D-1/D-2 packs; nil otherwise. Mirrors
    /// `models/alternates.py` / `designs/future/alternates.md`.
    let alternates: RouteAlternates?
}

struct RouteConfig: Codable, Sendable {
    let name: String
    let waypoints: [Waypoint]
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let flightDurationHours: Double
}

struct Waypoint: Codable, Sendable, Identifiable {
    let icao: String
    let name: String
    let lat: Double
    let lon: Double

    var id: String { icao }
}

struct WaypointAnalysis: Codable, Sendable {
    let waypoint: Waypoint
    let targetTime: String?
    let windComponents: [String: WindComponent]?
    let sounding: [String: SoundingAnalysisSummary]?
}

struct WindComponent: Codable, Sendable {
    let windSpeedKt: Double
    let windDirectionDeg: Double
    let trackDeg: Double
    let headwindKt: Double
    let crosswindKt: Double
}

/// Minimal sounding fields needed for airport conditions display.
struct SoundingAnalysisSummary: Codable, Sendable {
    let indices: ThermodynamicIndicesSummary?
    let cloudCoverLowPct: Double?
    let cloudCoverMidPct: Double?
    let cloudCoverHighPct: Double?
}

struct ThermodynamicIndicesSummary: Codable, Sendable {
    let freezingLevelFt: Double?
    let capeSurfaceJkg: Double?
    let soundingCeilingFt: Double?
    let nwpCeilingFt: Double?
}

/// D-0 METAR/TAF observations along the route, plus the obs-vs-model
/// reconciliation. Mirrors `models/observations.py::RouteObservations`
/// (source of truth) and the web's `renderRouteObservations`.
///
/// Only populated when `days_out == 0` — European TAFs rarely cover the next
/// day, so the pipeline skips the fetch entirely on D-1+.
///
/// SYNC: `web/ts/managers/briefing-ui.ts` (`renderRouteObservations`) renders
/// the same shape; `src/weatherbrief/models/observations.py` defines it.
struct RouteObservations: Codable, Sendable {
    let corridorNm: Double?
    let fetchTime: String?
    let airportsFound: Int?
    let airportsWithMetar: Int?
    let airportsWithTaf: Int?
    let airports: [AirportObservation]?
    let comparisons: [ObservationComparison]?
    let worstMetarCategory: String?
    let worstTafCategory: String?
    let hasConflicts: Bool?
    let phenomenaAlongRoute: [String]?

    /// Comparisons keyed by ICAO — the table joins each airport row to its
    /// model reconciliation.
    var comparisonsByIcao: [String: ObservationComparison] {
        Dictionary(uniqueKeysWithValues: (comparisons ?? []).map { ($0.icao, $0) })
    }

    /// Airports that actually reported something. The web filters the table the
    /// same way (`apt.has_metar || apt.has_taf`) — a spatial query can return
    /// small GA fields with no data at all.
    ///
    /// Server order is along-route (by enroute distance), which is the useful
    /// reading order; everything downstream preserves it.
    var reportingAirports: [AirportObservation] {
        (airports ?? []).filter { $0.hasMetar == true || $0.hasTaf == true }
    }

    /// The subset a phone-width table shows before "Show all": the `limit`
    /// airports nearest the route, **plus** any airport whose model comparison
    /// is CONFLICTING — the conflict banner must never point at a row the cap
    /// hid. Returned in route order, not distance order, so the table still
    /// reads departure→destination.
    ///
    /// A 30 nm corridor on a long route routinely reports 30+ fields, which is a
    /// reasonable table in a browser window but a very long scroll inside the
    /// iOS Advisory tab.
    func nearestReportingAirports(limit: Int) -> [AirportObservation] {
        let reporting = reportingAirports
        guard limit > 0, reporting.count > limit else { return reporting }

        // Missing distance sorts last rather than winning the "nearest" race.
        let byDistance = reporting.sorted {
            ($0.distanceFromRouteNm ?? .greatestFiniteMagnitude)
                < ($1.distanceFromRouteNm ?? .greatestFiniteMagnitude)
        }
        var keep = Set(byDistance.prefix(limit).map(\.icao))

        let conflicting = comparisonsByIcao
            .filter { $0.value.categoryMatch == "CONFLICTING" || $0.value.windAdvisoryMatch == "CONFLICTING" }
            .keys
        keep.formUnion(conflicting)

        return reporting.filter { keep.contains($0.icao) }
    }
}

struct AirportObservation: Codable, Identifiable, Sendable {
    let icao: String
    let name: String?
    let distanceFromRouteNm: Double?
    let enrouteDistanceNm: Double?
    let nearestWaypointIcao: String?

    // METAR
    let metarRaw: String?
    let metarTime: String?
    let metarFlightCategory: String?
    let metarCeilingFt: Int?
    let metarVisibilityM: Int?
    let metarWindDir: Int?
    let metarWindSpeedKt: Int?
    let metarWindGustKt: Int?
    let metarWeather: [String]?
    let metarTemperatureC: Int?
    let metarDewpointC: Int?
    let metarQnh: Double?

    // TAF
    let tafRaw: String?
    let tafFlightCategoryAtEta: String?
    let tafTrendType: String?
    let tafWindDir: Int?
    let tafWindSpeedKt: Int?
    let tafWindGustKt: Int?
    let tafApplicableText: String?
    /// Line indices of the base forecast + the BECMG/TEMPO groups active at the
    /// ETA — used to highlight the applicable lines in the raw TAF.
    let tafApplicableLines: [Int]?

    // Runway wind advisories — lowercase "green"/"amber"/"red"
    let metarWindAdvisory: String?
    let metarBestRunwayId: String?
    let metarCrosswindKt: Double?
    let metarHeadwindKt: Double?
    let tafWindAdvisory: String?
    let tafBestRunwayId: String?
    let tafCrosswindKt: Double?
    let tafHeadwindKt: Double?

    let hasMetar: Bool?
    let hasTaf: Bool?
    /// Rounded hours after departure that the flight passes this airport.
    let etaHourOffset: Int?

    var id: String { icao }
}

/// One airport's observation-vs-model reconciliation.
/// Mirrors `models/observations.py::ObservationComparison`.
struct ObservationComparison: Codable, Sendable {
    let icao: String
    let obsCategory: String?
    let modelCategory: String?
    /// "CONFIRMING" / "SIGNIFICANT" / "CONFLICTING".
    let categoryMatch: String?
    let ceilingDeltaFt: Int?
    let visibilityDeltaM: Double?
    let windSpeedDeltaKt: Double?
    let modelWindDir: Double?
    let modelWindSpeedKt: Double?
    let modelWindGustKt: Double?
    let modelWindAdvisory: String?
    let modelBestRunwayId: String?
    let modelCrosswindKt: Double?
    let windAdvisoryMatch: String?
    let detail: String?
}
