import Foundation

/// Briefing snapshot — route info, waypoint analyses, observations.
/// We decode only the fields needed for display.
/// The three D-0 fields are `var` because a gated realtime refresh replaces them
/// in place on an otherwise-unchanged pack — see
/// `BriefingViewModel.applyRealtimeRefresh`. Everything else is fixed at pack
/// build and stays `let`.
nonisolated struct SnapshotResponse: Codable, Sendable {
    let route: RouteConfig
    let targetDate: String
    let daysOut: Int
    let departureTime: String?
    let analyses: [WaypointAnalysis]?
    var routeObservations: RouteObservations?
    /// D-0 area hazards (SIGMETs) intersecting the route corridor — the sibling
    /// of `routeObservations` on the same fetch/refresh seam. Nil on D-1+ packs.
    var routeSigmets: RouteSigmets?
    /// Observed radar / lightning / satellite-cloud-top samples along the route
    /// corridor (#574). Present on D-0 packs where the deployment runs the
    /// observed collector (`WB_OBSERVED_ENABLED`); nil otherwise — and nil is not
    /// "clear", it is "we did not look". Recomputed by every gated D-0 refresh,
    /// so the ↻ button moves it.
    var observedConditions: ObservedConditions?
    /// Weather-based divert candidates (D-2 inward, `compute_alternates` pref).
    /// Present only on marginal D-0/D-1/D-2 packs; nil otherwise. Mirrors
    /// `models/alternates.py` / `designs/future/alternates.md`.
    let alternates: RouteAlternates?
    /// "Since this briefing" (#637): what moved vs the pack's own observations.
    /// The server adds it when it overlays the live layer on the latest pack's
    /// snapshot; the client also patches it in from `GET …/live` (see
    /// `BriefingViewModel.applyLive`). nil on packs that don't own a live layer.
    var liveChanges: LiveChanges?
    /// When the overlaid live data was last updated (ISO). nil = the D-0 blocks
    /// are the pack's own; any live layer is newer than that.
    var liveUpdatedAt: String?
}

nonisolated struct RouteConfig: Codable, Sendable {
    let name: String
    let waypoints: [Waypoint]
    let cruiseAltitudeFt: Int
    let flightCeilingFt: Int
    let flightDurationHours: Double
}

nonisolated struct Waypoint: Codable, Sendable, Identifiable {
    let icao: String
    let name: String
    let lat: Double
    let lon: Double

    var id: String { icao }
}

nonisolated struct WaypointAnalysis: Codable, Sendable {
    let waypoint: Waypoint
    let targetTime: String?
    let windComponents: [String: WindComponent]?
    let sounding: [String: SoundingAnalysisSummary]?
}

nonisolated struct WindComponent: Codable, Sendable {
    let windSpeedKt: Double
    let windDirectionDeg: Double
    let trackDeg: Double
    let headwindKt: Double
    let crosswindKt: Double
}

/// Minimal sounding fields needed for airport conditions display.
nonisolated struct SoundingAnalysisSummary: Codable, Sendable {
    let indices: ThermodynamicIndicesSummary?
    let cloudCoverLowPct: Double?
    let cloudCoverMidPct: Double?
    let cloudCoverHighPct: Double?
}

nonisolated struct ThermodynamicIndicesSummary: Codable, Sendable {
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
nonisolated struct RouteObservations: Codable, Sendable {
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
    ///
    /// `uniquingKeysWith` rather than `uniqueKeysWithValues`: this is
    /// server-derived JSON, and the trapping initializer would `fatalError` on a
    /// duplicate ICAO — crashing the briefing screen over a malformed payload
    /// instead of degrading. First entry wins, matching `HelpCatalogResponse`,
    /// `SkewTVariableCatalog` and `TimingScenariosView`.
    var comparisonsByIcao: [String: ObservationComparison] {
        Dictionary((comparisons ?? []).map { ($0.icao, $0) }, uniquingKeysWith: { first, _ in first })
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

nonisolated struct AirportObservation: Codable, Identifiable, Sendable {
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
    /// Validity of the TAF and whether it contains the ETA (#610). `false`
    /// keeps the raw text but leaves every at-ETA field empty; nil on packs
    /// built before #610. Read through `tafAtEta`, not directly.
    let tafValidFrom: String?
    let tafValidTo: String?
    let tafValidAtEta: Bool?
    /// Worse of the prevailing and temporary categories at ETA.
    let tafFlightCategoryAtEta: String?
    let tafTrendType: String?
    /// Main body with completed BECMG / started FM groups applied.
    let tafPrevailingCategoryAtEta: String?
    /// Worst TEMPO/PROB group at ETA, set only when worse than prevailing.
    let tafTemporaryCategoryAtEta: String?
    /// "TEMPO", "PROB30 TEMPO"…
    let tafTemporaryType: String?
    /// TS, FG, FZ*, SN, GR… and CB/TCU at ETA.
    let tafSignificantWeather: [String]?
    let tafWindDir: Int?
    let tafWindSpeedKt: Int?
    let tafWindGustKt: Int?
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

    // Live layer (#637) — all optional; absent on older packs.
    /// "METAR" or "SPECI".
    let metarReportType: String?
    /// The category of the report before this one, and its time — lets a
    /// reader see a SPECI's step.
    let metarPreviousFlightCategory: String?
    let metarPreviousTime: String?
    let tafIssueTime: String?

    // Runway + wind widget (#758) — all optional; absent on older packs.
    let metarWindVariableFrom: Int?
    let metarWindVariableTo: Int?
    let tafWindVariableFrom: Int?
    let tafWindVariableTo: Int?
    /// Runways and the METAR / TAF-at-ETA winds on them; nil when the airport
    /// has no runway data or the pack predates #758.
    let runwayWind: RunwayWindPicture?

    var id: String { icao }

    /// The TAF read at this airport's ETA, for the observations table and the
    /// detail sheet (#613).
    ///
    /// SYNC: `web/ts/helpers/taf-at-eta.ts::readTafAtEta`; the server's
    /// English one-liner is `models/observations.py::taf_at_eta_line`.
    var tafAtEta: TafAtEta {
        guard hasTaf == true || !(tafRaw ?? "").isEmpty else { return .noTaf }

        if tafValidAtEta == false {
            return .notValid(window: TafAtEta.validityWindow(from: tafValidFrom, to: tafValidTo))
        }

        guard tafValidAtEta == true else {
            // Packs built before #610 carry only the single-group reading.
            return .reading(TafAtEta.Reading(
                prevailing: tafFlightCategoryAtEta,
                temporary: nil,
                significantWeather: [],
                legacyTrend: tafTrendType
            ))
        }

        let temporary = tafTemporaryCategoryAtEta.map { category in
            TafAtEta.Temporary(category: category, type: tafTemporaryType.flatMap { $0.isEmpty ? nil : $0 } ?? "TEMPO")
        }
        return .reading(TafAtEta.Reading(
            // A reading with no prevailing category (no visibility/ceiling in
            // the base group) still has the combined one to show.
            prevailing: tafPrevailingCategoryAtEta ?? tafFlightCategoryAtEta,
            temporary: temporary,
            significantWeather: tafSignificantWeather ?? [],
            legacyTrend: nil
        ))
    }
}

/// An airport's TAF read at its ETA. See `AirportObservation.tafAtEta`.
nonisolated enum TafAtEta: Equatable, Sendable {
    /// No TAF at all for this airport.
    case noTaf
    /// A TAF exists but its validity does not contain the ETA, e.g. a field
    /// that issues TAFs only in opening hours. `window` is "11/15Z-11/17Z".
    case notValid(window: String?)
    case reading(Reading)

    struct Temporary: Equatable, Sendable {
        let category: String
        /// "TEMPO", "PROB30 TEMPO"…
        let type: String

        /// Compact label for a table cell: "PROB30 TEMPO" → "PROB30".
        var shortLabel: String {
            guard type.hasPrefix("PROB") else { return type }
            return String(type.prefix { !$0.isWhitespace })
        }
    }

    struct Reading: Equatable, Sendable {
        /// Prevailing category; on pre-#610 packs the combined one.
        let prevailing: String?
        /// The worst TEMPO/PROB group, only when worse than prevailing.
        let temporary: Temporary?
        let significantWeather: [String]
        /// Pre-#610 packs: the trend label that set the combined category.
        let legacyTrend: String?
    }

    /// "11/15Z-11/17Z" (day/hour UTC), the server's `taf_at_eta_line` format.
    static func validityWindow(from: String?, to: String?) -> String? {
        guard let a = from.flatMap(dayHourZ), let b = to.flatMap(dayHourZ) else { return nil }
        return "\(a)-\(b)"
    }

    private static func dayHourZ(_ iso: String) -> String? {
        guard let date = Date.parseISO8601(iso) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.day, .hour], from: date)
        guard let d = c.day, let h = c.hour else { return nil }
        return String(format: "%02d/%02dZ", d, h)
    }
}

/// One airport's observation-vs-model reconciliation.
/// Mirrors `models/observations.py::ObservationComparison`.
nonisolated struct ObservationComparison: Codable, Sendable {
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

/// D-0 **area hazards** along the route (#493) — the sibling of
/// `RouteObservations` on the same fetch/refresh seam, but polygon-keyed rather
/// than airport-keyed and with no model comparison (SIGMETs are presented, not
/// reconciled).
///
/// Mirrors `models/observations.py::RouteSigmets` (source of truth) and the
/// web's `renderRouteSigmets`.
///
/// SYNC: `web/ts/managers/briefing-ui.ts` (`renderRouteSigmets`);
/// `src/weatherbrief/models/observations.py` defines the shape.
nonisolated struct RouteSigmets: Codable, Sendable {
    let corridorNm: Double?
    let fetchTime: String?
    /// The band the fetch filtered on: surface to cruise + 5,000 ft.
    let altitudeLowFt: Int?
    let altitudeHighFt: Int?
    let timeWindowFrom: String?
    let timeWindowTo: String?
    /// Every FIR the route crosses — context for "no SIGMETs" as much as for hits.
    let routeFirs: [String]?
    let sigmets: [SigmetAlongRoute]?

    // Server-computed fields (pydantic `computed_field`). Optional, and each has
    // a local fallback below: a pack written before they existed — or any future
    // producer that omits them — must still render rather than showing "0
    // SIGMETs" above a populated table.
    let count: Int?
    let hazards: [String]?
    let hasSevere: Bool?

    var matched: [SigmetAlongRoute] { sigmets ?? [] }

    var sigmetCount: Int { count ?? matched.count }

    /// Sorted union of hazard types, matching the server's `hazards`.
    var hazardTypes: [String] {
        if let hazards, !hazards.isEmpty { return hazards }
        return Array(Set(matched.compactMap(\.hazard))).sorted()
    }

    /// Whether any matched SIGMET is qualified SEV — the summary-bar signal, the
    /// counterpart of `worst_metar_category` on the observations block.
    var severe: Bool { hasSevere ?? matched.contains(where: \.isSevere) }
}

/// One SIGMET intersecting the route corridor.
/// Mirrors `models/observations.py::SigmetAlongRoute`.
///
/// `coords` (the polygon outline, `(lon, lat)` vertices), the enroute span and
/// the vertical band are deliberately retained by the server so a later map /
/// cross-section overlay can be drawn without re-fetching — the table itself
/// doesn't use the polygon.
nonisolated struct SigmetAlongRoute: Codable, Identifiable, Sendable {
    let firId: String
    let firName: String?
    /// TURB / ICE / TS / MTW / VA …
    let hazard: String?
    /// SEV / EMBD / FRQ / ISOL …
    let qualifier: String?
    let baseFt: Int?
    let topFt: Int?
    let validFrom: String?
    let validTo: String?
    /// Movement direction, e.g. "NE". Absent when the area is stationary.
    let direction: String?
    let speedKt: Int?
    let rawText: String?
    let matchedFirs: [String]?
    let minDistanceNm: Double?
    let enrouteDistanceFromNm: Double?
    let enrouteDistanceToNm: Double?
    /// Polygon outline as `[lon, lat]` pairs — decoded from Python tuples, kept
    /// for the future map/cross-section overlay.
    let coords: [[Double]]?

    /// Nothing in the payload is a stable id (a FIR can hold several SIGMETs at
    /// once), so identity is the raw bulletin plus the FIR — the same pair the
    /// server's delta uses to tell a re-issue from a new hazard.
    var id: String { "\(firId)|\(rawText ?? "")" }

    /// "SEV TURB" — the row's leading label, matching the web's header cell.
    /// Falls back to a bare "SIGMET" when the source gave neither field.
    var headline: String {
        let parts = [qualifier, hazard].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? "SIGMET" : parts.joined(separator: " ")
    }

    var isSevere: Bool { qualifier?.uppercased() == "SEV" }

    /// "SFC–FL380". Mirrors the web's `sigmetBand` exactly, including the "?" for
    /// an unknown bound — an omitted base is genuinely unknown, not surface, and
    /// rendering it as SFC would overstate what the bulletin said.
    var levelBand: String {
        if baseFt == nil && topFt == nil { return "—" }
        return "\(Self.level(baseFt))–\(Self.level(topFt))"
    }

    static func level(_ ft: Int?) -> String {
        guard let ft else { return "?" }
        if ft <= 0 { return "SFC" }
        // Floor to match the Python/web helper so the same SIGMET reads as the
        // same flight level on every surface.
        return String(format: "FL%03d", ft / 100)
    }

    /// Where the route meets the area: the along-track span if the corridor is
    /// crossed, otherwise how far off-track the nearest edge sits.
    var enrouteLabel: String {
        if let from = enrouteDistanceFromNm, let to = enrouteDistanceToNm {
            return "\(Int(from.rounded()))–\(Int(to.rounded()))nm"
        }
        if let min = minDistanceNm { return "\(Int(min.rounded()))nm off" }
        return "—"
    }

    /// "NE 30kt", or nil when the bulletin reports no movement (stationary).
    var movementLabel: String? {
        guard let direction, !direction.isEmpty, let speedKt else { return nil }
        return "\(direction) \(speedKt)kt"
    }

    /// "170450Z → 170600Z" in aviation DDHHMM form. Nil when neither bound
    /// parses, so the detail sheet can drop the row instead of showing "? → ?".
    var validityLabel: String? {
        let from = validFrom.flatMap(Self.dayZulu)
        let to = validTo.flatMap(Self.dayZulu)
        if from == nil && to == nil { return nil }
        return "\(from ?? "?") → \(to ?? "?")"
    }

    /// "07:00Z" while the SIGMET is issued but not yet valid at `now` (#683):
    /// the server looks ahead for these, and a row must not read as active.
    /// Nil once valid, or without a parseable start. Mirrors the web's
    /// `sigmetPendingFrom`.
    func pendingFrom(now: Date = Date()) -> String? {
        guard let validFrom, let start = Date.parseISO8601(validFrom), start > now else { return nil }
        return LiveTime.zulu(start)
    }

    /// "170450Z" (DDHHMMZ) from an ISO timestamp, always in UTC.
    static func dayZulu(_ iso: String) -> String? {
        guard let date = Date.parseISO8601(iso) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.day, .hour, .minute], from: date)
        guard let d = c.day, let h = c.hour, let m = c.minute else { return nil }
        return String(format: "%02d%02d%02dZ", d, h, m)
    }
}

nonisolated extension SnapshotResponse {
    /// Change token for the cross-section rebuild: moves whenever a live update
    /// (`BriefingViewModel.applyLive`, or a gated D-0 refresh) replaces ANY of
    /// the blocks the chart draws from — the observed payload, the METARs or the
    /// SIGMETs. Keying on `observedConditions.computedAt` alone missed a tick
    /// that only moved the SIGMETs, leaving the current-conditions layer stale.
    ///
    /// SIGMET identities are folded in as well as the fetch time, so a
    /// replacement that keeps the fetch time but changes the bulletins still
    /// rebuilds. Only ever compared, never displayed or parsed.
    var crossSectionLiveToken: String {
        [
            liveUpdatedAt ?? "-",
            observedConditions?.computedAt ?? "-",
            routeObservations?.fetchTime ?? "-",
            routeSigmets?.fetchTime ?? "-",
            (routeSigmets?.matched ?? []).map(\.id).joined(separator: ","),
        ].joined(separator: "|")
    }
}
