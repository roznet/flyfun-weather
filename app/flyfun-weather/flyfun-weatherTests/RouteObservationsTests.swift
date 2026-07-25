//
//  RouteObservationsTests.swift
//  flyfun-weatherTests
//
//  Current Observations section (#492) — the iOS port of the web's
//  METAR/TAF/model comparison table. Covers the decode contract against the
//  Python source of truth (`models/observations.py`), the reporting-airport
//  filter, the comparison join, and the axis model that drives the
//  compact-vs-regular layout split.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Decode contract

@Suite struct RouteObservationsDecodeTests {

    /// A full-shape payload as the server actually emits it (snake_case, the
    /// fields `models/observations.py` declares). Guards against the original
    /// bug: the DTO decoded a handful of fields and silently dropped
    /// `comparisons`, the wind advisories, and the ETA.
    private let json = """
    {
      "corridor_nm": 30.0,
      "fetch_time": "2026-07-25T05:50:00+00:00",
      "airports_found": 4,
      "airports_with_metar": 2,
      "airports_with_taf": 1,
      "worst_metar_category": "IFR",
      "worst_taf_category": "MVFR",
      "has_conflicts": true,
      "phenomena_along_route": ["TSRA", "BR"],
      "airports": [
        {
          "icao": "EGTF", "name": "Fairoaks", "distance_from_route_nm": 1.5,
          "enroute_distance_nm": 12.0, "nearest_waypoint_icao": "EGTF",
          "metar_raw": "EGTF 250550Z 09018G28KT 2500 BR OVC008 17/16 Q1015",
          "metar_time": "2026-07-25T05:50:00+00:00",
          "metar_flight_category": "IFR", "metar_ceiling_ft": 800,
          "metar_visibility_m": 2500, "metar_wind_dir": 90,
          "metar_wind_speed_kt": 18, "metar_wind_gust_kt": 28,
          "metar_weather": ["BR"], "metar_temperature_c": 17,
          "metar_dewpoint_c": 16, "metar_qnh": 1015.0,
          "taf_raw": "EGTF 250500Z 2506/2518 09018G28KT 3000 BR OVC010\\nBECMG 2509/2511 09012KT 9999 SCT025",
          "taf_flight_category_at_eta": "MVFR", "taf_trend_type": "BECMG",
          "taf_wind_dir": 90, "taf_wind_speed_kt": 12, "taf_wind_gust_kt": null,
          "taf_applicable_text": "BECMG 2509/2511 09012KT 9999 SCT025",
          "taf_applicable_lines": [0, 1],
          "metar_wind_advisory": "red", "metar_best_runway_id": "06",
          "metar_crosswind_kt": 17.4, "metar_headwind_kt": 6.1,
          "taf_wind_advisory": "amber", "taf_best_runway_id": "06",
          "taf_crosswind_kt": 11.6, "taf_headwind_kt": 4.1,
          "has_metar": true, "has_taf": true, "eta_hour_offset": 2
        },
        {
          "icao": "EGLL", "name": "Heathrow", "distance_from_route_nm": 8.0,
          "enroute_distance_nm": 20.0, "nearest_waypoint_icao": "EGLL",
          "metar_raw": "EGLL 250550Z 27010KT 9999 FEW030 19/12 Q1016",
          "metar_flight_category": "VFR", "metar_ceiling_ft": 3000,
          "metar_wind_dir": 270, "metar_wind_speed_kt": 10,
          "metar_weather": [], "metar_qnh": 1016.0,
          "metar_wind_advisory": "green", "metar_best_runway_id": "27R",
          "metar_crosswind_kt": 2.0,
          "has_metar": true, "has_taf": false, "eta_hour_offset": 3
        },
        {
          "icao": "EGKB", "name": "Biggin Hill", "distance_from_route_nm": 22.0,
          "nearest_waypoint_icao": "EGLL",
          "metar_weather": [],
          "has_metar": false, "has_taf": false
        }
      ],
      "comparisons": [
        {
          "icao": "EGTF", "obs_category": "IFR", "model_category": "VFR",
          "category_match": "CONFLICTING",
          "ceiling_delta_ft": 3200, "visibility_delta_m": 7499.0,
          "wind_speed_delta_kt": -8.0,
          "model_wind_dir": 100.0, "model_wind_speed_kt": 10.0,
          "model_wind_gust_kt": null,
          "model_wind_advisory": "amber", "model_best_runway_id": "06",
          "model_crosswind_kt": 9.7,
          "wind_advisory_match": "CONFLICTING",
          "detail": "Model shows VFR but METAR reports OVC008 in mist."
        },
        {
          "icao": "EGLL", "obs_category": "VFR", "model_category": "VFR",
          "category_match": "CONFIRMING",
          "model_wind_advisory": "green", "model_crosswind_kt": 3.0,
          "wind_advisory_match": "CONFIRMING",
          "detail": ""
        }
      ]
    }
    """

    private func decoded() throws -> RouteObservations {
        try JSONDecoder.weatherBrief.decode(RouteObservations.self, from: Data(json.utf8))
    }

    @Test func decodesSummaryFields() throws {
        let obs = try decoded()
        #expect(obs.corridorNm == 30.0)
        #expect(obs.airportsFound == 4)
        #expect(obs.airportsWithMetar == 2)
        #expect(obs.airportsWithTaf == 1)
        #expect(obs.worstMetarCategory == "IFR")
        #expect(obs.worstTafCategory == "MVFR")
        #expect(obs.hasConflicts == true)
        #expect(obs.phenomenaAlongRoute == ["TSRA", "BR"])
        #expect(obs.airports?.count == 3)
        #expect(obs.comparisons?.count == 2)
    }

    /// The wind-advisory block and ETA are what make the Wind axis renderable —
    /// they were entirely absent from the pre-#492 DTO.
    @Test func decodesWindAdvisoryAndEta() throws {
        let obs = try decoded()
        let egtf = try #require(obs.airports?.first { $0.icao == "EGTF" })
        #expect(egtf.metarWindAdvisory == "red")
        #expect(egtf.metarBestRunwayId == "06")
        #expect(egtf.metarCrosswindKt == 17.4)
        #expect(egtf.metarHeadwindKt == 6.1)
        #expect(egtf.tafWindAdvisory == "amber")
        #expect(egtf.tafCrosswindKt == 11.6)
        #expect(egtf.etaHourOffset == 2)
        #expect(egtf.metarWindDir == 90)
        #expect(egtf.metarWindGustKt == 28)
        #expect(egtf.metarQnh == 1015.0)
        #expect(egtf.metarVisibilityM == 2500)
    }

    /// `taf_applicable_lines` drives the highlight in the detail sheet.
    @Test func decodesTafApplicableLines() throws {
        let obs = try decoded()
        let egtf = try #require(obs.airports?.first { $0.icao == "EGTF" })
        #expect(egtf.tafApplicableLines == [0, 1])
        #expect(egtf.tafTrendType == "BECMG")
        // The raw TAF is multi-line, which is what makes the highlight visible.
        #expect(egtf.tafRaw?.components(separatedBy: .newlines).count == 2)
    }

    @Test func decodesComparison() throws {
        let obs = try decoded()
        let comp = try #require(obs.comparisonsByIcao["EGTF"])
        #expect(comp.categoryMatch == "CONFLICTING")
        #expect(comp.modelCategory == "VFR")
        #expect(comp.obsCategory == "IFR")
        #expect(comp.ceilingDeltaFt == 3200)
        #expect(comp.modelWindAdvisory == "amber")
        #expect(comp.modelCrosswindKt == 9.7)
        #expect(comp.windAdvisoryMatch == "CONFLICTING")
        #expect(comp.detail?.isEmpty == false)
    }

    /// Missing optionals must decode to nil rather than throwing — a small GA
    /// field with no report at all still arrives in the airports array.
    @Test func airportWithNoReportDecodes() throws {
        let obs = try decoded()
        let egkb = try #require(obs.airports?.first { $0.icao == "EGKB" })
        #expect(egkb.hasMetar == false)
        #expect(egkb.metarRaw == nil)
        #expect(egkb.metarFlightCategory == nil)
        #expect(egkb.etaHourOffset == nil)
        #expect(egkb.metarWindAdvisory == nil)
    }

    /// The table (and the scroll-spy gate) show only airports that reported —
    /// matching the web's `apt.has_metar || apt.has_taf` filter.
    @Test func reportingAirportsFiltersSilentFields() throws {
        let obs = try decoded()
        #expect(obs.reportingAirports.map(\.icao) == ["EGTF", "EGLL"])
    }

    @Test func comparisonJoinIsKeyedByIcao() throws {
        let obs = try decoded()
        let byIcao = obs.comparisonsByIcao
        #expect(byIcao.count == 2)
        #expect(byIcao["EGLL"]?.categoryMatch == "CONFIRMING")
        // No comparison for the silent field — the row renders an em dash.
        #expect(byIcao["EGKB"] == nil)
    }

    /// A D-1+ pack has no observations block at all; the section must simply
    /// not render rather than decoding into an empty shell.
    @Test func absentBlockDecodesToNilOnSnapshot() throws {
        let snapshotJSON = """
        {
          "route": { "name": "EGTF EGLL", "waypoints": [], "cruise_altitude_ft": 5000,
                     "flight_ceiling_ft": 10000, "flight_duration_hours": 1.0 },
          "target_date": "2026-07-27", "days_out": 2
        }
        """
        let snap = try JSONDecoder.weatherBrief.decode(SnapshotResponse.self, from: Data(snapshotJSON.utf8))
        #expect(snap.routeObservations == nil)
    }

    /// An empty airports array must read as "nothing to show" so the section and
    /// its scroll-spy anchor stay hidden.
    @Test func emptyAirportsYieldsNoReportingAirports() throws {
        let empty = """
        { "corridor_nm": 30.0, "fetch_time": "2026-07-25T05:50:00+00:00", "airports": [] }
        """
        let obs = try JSONDecoder.weatherBrief.decode(RouteObservations.self, from: Data(empty.utf8))
        #expect(obs.reportingAirports.isEmpty)
        #expect(obs.comparisonsByIcao.isEmpty)
    }
}

// MARK: - Layout model

@Suite struct ObservationAxisTests {

    /// Compact width renders one group at a time; regular renders both. The
    /// table's column count follows (3 fixed + 4 per visible group), which is
    /// what the grouped header spans must agree with.
    @Test func axisCasesCoverBothWebGroups() {
        #expect(ObservationAxis.allCases.count == 2)
        #expect(ObservationAxis.allCases.map(\.label) == ["Condition", "Wind"])
    }

    @Test func axisIsStableForPickerSelection() {
        // The Picker tags on identity, so these must round-trip.
        #expect(ObservationAxis.condition.id == "condition")
        #expect(ObservationAxis.wind.id == "wind")
        #expect(ObservationAxis(rawValue: "wind") == .wind)
    }
}

// MARK: - Zulu fetch-time label

@Suite struct ObservationFetchTimeTests {

    /// The server sends `datetime.isoformat()` with microseconds; the plain
    /// ISO8601 parser rejects those, so the shared fractional-then-plain
    /// fallback is required.
    @Test func parsesFractionalSecondsTimestamp() {
        #expect(RouteObservationsView.zuluTime("2026-07-25T05:50:00.123456+00:00") == "0550Z")
    }

    @Test func parsesPlainTimestamp() {
        #expect(RouteObservationsView.zuluTime("2026-07-25T05:50:00Z") == "0550Z")
    }

    /// Always rendered in UTC regardless of the device locale — an offset
    /// timestamp must convert, not display its local wall clock.
    @Test func rendersInUtcNotLocalTime() {
        #expect(RouteObservationsView.zuluTime("2026-07-25T07:50:00+02:00") == "0550Z")
    }

    @Test func garbageTimestampYieldsNil() {
        #expect(RouteObservationsView.zuluTime("not a date") == nil)
    }
}
