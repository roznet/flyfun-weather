//
//  CurrentConditionsTests.swift
//  flyfun-weatherTests
//
//  The cross-section current-conditions layer (METAR columns + SIGMET zones,
//  #641) — the iOS port of web `buildCurrentConditions` (data-extract.ts) and
//  `layers/current-conditions.ts`. Covers the extraction, the span/band
//  geometry, the live-patch path (a tick replacing the SIGMETs reaches the
//  chart), the rebuild token, and the off-by-default product rule.
//
//  Fictional `ZZ` ICAO codes throughout.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Fixtures

private let packTS = "2026-06-24T09:00:00Z"

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try JSONDecoder.weatherBrief.decode(T.self, from: Data(json.utf8))
}

/// One SIGMET, as the server serializes `SigmetAlongRoute`.
private func sigmetJSON(
    fir: String = "ZZZZ", hazard: String? = "TURB", qualifier: String? = "SEV",
    base: Int? = 10000, top: Int? = 24000,
    from: Double? = 40, to: Double? = 60, raw: String = "ZZZZ SIGMET 3 VALID 240800/241200 ZZZZ-"
) -> String {
    func j(_ v: Any?) -> String {
        switch v {
        case let s as String: return "\"\(s)\""
        case let n as Int: return "\(n)"
        case let d as Double: return "\(d)"
        default: return "null"
        }
    }
    return """
    { "fir_id": "\(fir)", "hazard": \(j(hazard)), "qualifier": \(j(qualifier)),
      "base_ft": \(j(base)), "top_ft": \(j(top)),
      "enroute_distance_from_nm": \(j(from)), "enroute_distance_to_nm": \(j(to)),
      "raw_text": "\(raw)" }
    """
}

private func routeSigmets(_ sigmets: [String], fetchTime: String = "2026-06-24T08:50:00Z") throws -> RouteSigmets {
    try decode(RouteSigmets.self, """
    { "fetch_time": "\(fetchTime)", "sigmets": [\(sigmets.joined(separator: ","))] }
    """)
}

private func routeObservations(_ airports: [String]) throws -> RouteObservations {
    try decode(RouteObservations.self, """
    { "fetch_time": "2026-06-24T08:50:00Z", "airports": [\(airports.joined(separator: ","))] }
    """)
}

/// A minimal D-0 snapshot: one VFR airport on the route and one SIGMET.
private func makeSnapshot() throws -> SnapshotResponse {
    try decode(SnapshotResponse.self, """
    {
      "route": {
        "name": "ZZAA ZZBB", "waypoints": [],
        "cruise_altitude_ft": 8000, "flight_ceiling_ft": 13000, "flight_duration_hours": 1.5
      },
      "target_date": "2026-06-24",
      "days_out": 0,
      "route_observations": {
        "fetch_time": "2026-06-24T08:50:00Z",
        "airports": [ { "icao": "ZZAA", "has_metar": true, "metar_flight_category": "VFR",
                        "enroute_distance_nm": 0.0, "distance_from_route_nm": 0.0 } ]
      },
      "route_sigmets": {
        "fetch_time": "2026-06-24T08:50:00Z",
        "sigmets": [ \(sigmetJSON()) ]
      },
      "live_updated_at": null
    }
    """)
}

/// A live tick whose only block is a new SIGMET set (two zones).
private func makeSigmetOnlyLive() throws -> LiveLayerResponse {
    try decode(LiveLayerResponse.self, """
    {
      "flight_id": "ZZ-flt",
      "pack_timestamp": "\(packTS)",
      "live_updated_at": "2026-06-24T10:00:00Z",
      "route_observations": null,
      "route_sigmets": {
        "fetch_time": "2026-06-24T09:58:00Z",
        "sigmets": [
          \(sigmetJSON()),
          \(sigmetJSON(fir: "ZZYY", hazard: "TS", qualifier: "EMBD", base: nil, top: 40000,
                       from: 70, to: 72, raw: "ZZYY SIGMET 1 VALID 240930/241330 ZZYY-"))
        ]
      },
      "observed_conditions": null,
      "changes": null
    }
    """)
}

private let terrain = [TerrainPoint(distanceNm: 0, elevationFt: 0),
                       TerrainPoint(distanceNm: 100, elevationFt: 1000)]

// MARK: - Tests

@Suite @MainActor struct CurrentConditionsTests {

    // (a) SIGMET → x-range (5 nm minimum widening) and altitude band.

    @Test func sigmetMapsToItsEnrouteSpanAndBand() throws {
        let cc = try #require(VizCurrentConditions.build(
            observations: nil, sigmets: try routeSigmets([sigmetJSON()]), terrainProfile: terrain))
        let zone = try #require(cc.sigmets.first)
        #expect(zone.enrouteFromNm == 40)
        #expect(zone.enrouteToNm == 60)
        #expect(zone.baseFt == 10000)
        #expect(zone.topFt == 24000)
        #expect(zone.hazard == "TURB")
        #expect(zone.qualifier == "SEV")
        let span = CurrentConditionsLayer.sigmetSpanNm(zone.enrouteFromNm, zone.enrouteToNm)
        #expect(span.from == 40 && span.to == 60)  // already ≥ 5 nm: unchanged
    }

    @Test func narrowSigmetWidensToFiveNmAroundItsMidpoint() {
        let span = CurrentConditionsLayer.sigmetSpanNm(72, 70)  // reversed + 2 nm wide
        #expect(span.from == 68.5)
        #expect(span.to == 73.5)
        let point = CurrentConditionsLayer.sigmetSpanNm(10, 10)
        #expect(point.from == 7.5 && point.to == 12.5)
    }

    @Test func openBoundsStayNilAndMissingHazardReadsSigmet() throws {
        let cc = try #require(VizCurrentConditions.build(
            observations: nil,
            sigmets: try routeSigmets([sigmetJSON(hazard: nil, qualifier: nil, base: nil, top: nil)]),
            terrainProfile: nil))
        let zone = try #require(cc.sigmets.first)
        #expect(zone.baseFt == nil)  // → the layer spans the full plot height
        #expect(zone.topFt == nil)
        #expect(zone.hazard == "SIGMET")
    }

    @Test func sigmetWithoutAnEnrouteSpanIsSkipped() throws {
        let cc = VizCurrentConditions.build(
            observations: nil,
            sigmets: try routeSigmets([sigmetJSON(from: nil, to: 50), sigmetJSON(from: 10, to: nil)]),
            terrainProfile: terrain)
        #expect(cc == nil)
    }

    @Test func severeStylingCoversSevAndEmbd() {
        #expect(CurrentConditionsLayer.isSevereSigmet("SEV"))
        #expect(CurrentConditionsLayer.isSevereSigmet("embd"))
        #expect(!CurrentConditionsLayer.isSevereSigmet("ISOL"))
        #expect(!CurrentConditionsLayer.isSevereSigmet(nil))
    }

    // (b) METAR columns: along-route distance + terrain base; skips.

    @Test func airportMapsToItsEnrouteDistanceAndTerrainBase() throws {
        let obs = try routeObservations([
            """
            { "icao": "ZZAA", "metar_flight_category": "mvfr", "enroute_distance_nm": 40.0,
              "distance_from_route_nm": 3.5, "metar_ceiling_ft": 2500, "metar_visibility_m": 6000,
              "metar_raw": "ZZAA 240850Z 27010KT 6000 BKN025 12/08 Q1015" }
            """,
            // No category → skipped.
            """
            { "icao": "ZZBB", "enroute_distance_nm": 50.0, "distance_from_route_nm": 1.0 }
            """,
            // No along-route position → skipped.
            """
            { "icao": "ZZCC", "metar_flight_category": "IFR", "distance_from_route_nm": 1.0 }
            """,
        ])
        let cc = try #require(VizCurrentConditions.build(
            observations: obs, sigmets: nil, terrainProfile: terrain))
        #expect(cc.airports.map(\.icao) == ["ZZAA"])
        let col = try #require(cc.airports.first)
        #expect(col.enrouteDistanceNm == 40)
        #expect(col.distanceFromRouteNm == 3.5)
        #expect(col.flightCategory == "MVFR")  // upper-cased
        #expect(col.baseFt == 400)  // terrain interpolated at 40 nm on a 0→1000 ft ramp
        #expect(col.ceilingFt == 2500)
        #expect(col.visibilityM == 6000)
        let span = CurrentConditionsLayer.columnSpanNm(col.enrouteDistanceNm)
        #expect(span.from == 38 && span.to == 42)
    }

    @Test func closestToRouteDrawsLast() throws {
        let obs = try routeObservations([
            #"{ "icao": "ZZNE", "metar_flight_category": "VFR", "enroute_distance_nm": 10.0, "distance_from_route_nm": 1.0 }"#,
            #"{ "icao": "ZZFA", "metar_flight_category": "IFR", "enroute_distance_nm": 11.0, "distance_from_route_nm": 9.0 }"#,
        ])
        let cc = try #require(VizCurrentConditions.build(observations: obs, sigmets: nil, terrainProfile: nil))
        #expect(CurrentConditionsLayer.sortColumnsForDraw(cc.airports).map(\.icao) == ["ZZFA", "ZZNE"])
    }

    // (c) Both empty → nil (and the layer is then unavailable).

    @Test func bothEmptyIsNilAndGreysTheLayerOut() throws {
        #expect(VizCurrentConditions.build(observations: nil, sigmets: nil, terrainProfile: terrain) == nil)
        #expect(VizCurrentConditions.build(
            observations: try routeObservations([]), sigmets: try routeSigmets([]),
            terrainProfile: terrain) == nil)

        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalyses, model: "gfs",
            elevation: FixtureBriefingData.elevation)
        #expect(viz.currentConditions == nil)
        #expect(NwpFallback.unavailableLayers(in: viz).contains("current-conditions"))
    }

    // (d) A live patch replacing the SIGMETs changes the extracted data.

    @Test func liveSigmetPatchReachesTheChartData() throws {
        let snapshot = try makeSnapshot()
        let patched = try #require(
            BriefingViewModel.applyLive(try makeSigmetOnlyLive(), to: snapshot, packTimestamp: packTS))

        func extract(_ s: SnapshotResponse) -> VizRouteData {
            CrossSectionViewModel.extractVizData(
                from: FixtureBriefingData.routeAnalyses, model: "gfs",
                elevation: FixtureBriefingData.elevation,
                routeObservations: s.routeObservations, routeSigmets: s.routeSigmets)
        }
        let before = try #require(extract(snapshot).currentConditions)
        let after = try #require(extract(patched).currentConditions)
        #expect(before.sigmets.count == 1)
        #expect(after.sigmets.count == 2)
        #expect(after.sigmets.last?.hazard == "TS")
        #expect(after.sigmets.last?.baseFt == nil)
        // The tick carried no observations block → the pack's own METAR stays.
        #expect(after.airports.map(\.icao) == ["ZZAA"])
        #expect(before != after)
        #expect(!NwpFallback.unavailableLayers(in: extract(patched)).contains("current-conditions"))
    }

    // (e) The rebuild token moves when only the SIGMETs change.

    @Test func rebuildTokenMovesWhenOnlySigmetsChange() throws {
        let snapshot = try makeSnapshot()
        var sigmetsOnly = snapshot
        sigmetsOnly.routeSigmets = try routeSigmets(
            [sigmetJSON(), sigmetJSON(fir: "ZZYY", raw: "ZZYY SIGMET 1")], fetchTime: "2026-06-24T09:58:00Z")
        #expect(sigmetsOnly.observedConditions?.computedAt == snapshot.observedConditions?.computedAt)
        #expect(sigmetsOnly.crossSectionLiveToken != snapshot.crossSectionLiveToken)

        // Same fetch time, different bulletins — still a rebuild.
        var sameFetch = snapshot
        sameFetch.routeSigmets = try routeSigmets([sigmetJSON(raw: "ZZZZ SIGMET 4")])
        #expect(sameFetch.crossSectionLiveToken != snapshot.crossSectionLiveToken)

        // And through the real patch path.
        let patched = try #require(
            BriefingViewModel.applyLive(try makeSigmetOnlyLive(), to: snapshot, packTimestamp: packTS))
        #expect(patched.crossSectionLiveToken != snapshot.crossSectionLiveToken)

        // Unchanged → stable (no spurious rebuilds).
        #expect(try makeSnapshot().crossSectionLiveToken == snapshot.crossSectionLiveToken)
    }

    // (f) Off by default: shown only when the pilot turns it on.

    @Test func layerIsOffByDefault() {
        #expect(!CrossSectionLayer.defaultEnabled.contains("current-conditions"))
        #expect(CrossSectionPresets.bootDefaults["current-conditions"] == false)
        #expect(CrossSectionLayer.allLayers.contains { $0.id == "current-conditions" })
        // First pill of the Observed family, as on the web.
        #expect(CrossSectionLayer.layerIds(in: .conditions)
                == ["current-conditions", "observed-surface", "observed-tops"])
        // No view model here on purpose: it reads and writes shared
        // UserDefaults, which the serialized lens suite owns.
    }

    // Readout (web tooltip parity).

    @Test func readoutLinesMatchTheWebTooltip() throws {
        let obs = try routeObservations([
            """
            { "icao": "ZZAA", "metar_flight_category": "IFR", "enroute_distance_nm": 50.0,
              "distance_from_route_nm": 2.0, "metar_ceiling_ft": 800, "metar_visibility_m": 3000,
              "metar_raw": "ZZAA 240850Z 3000 BKN008" }
            """,
        ])
        let sig = try routeSigmets([
            sigmetJSON(hazard: "TS", qualifier: "EMBD", base: nil, top: 40000, from: 45, to: 55,
                       raw: "ZZZZ SIGMET 2"),
        ])
        let cc = try #require(VizCurrentConditions.build(observations: obs, sigmets: sig, terrainProfile: nil))

        let inColumn = CurrentConditionsLayer.readoutLines(cc, distanceNm: 51, altitudeFt: 2000)
        #expect(inColumn == [
            "ZZAA IFR · ceil 800 ft · vis 3000 m",
            "ZZAA 240850Z 3000 BKN008",
            "SIGMET EMBD TS SFC–FL400",
            "ZZZZ SIGMET 2",
        ])
        // Above the 5000 ft column, still inside the SIGMET band.
        #expect(CurrentConditionsLayer.readoutLines(cc, distanceNm: 51, altitudeFt: 12000)
                == ["SIGMET EMBD TS SFC–FL400", "ZZZZ SIGMET 2"])
        // Outside both X spans.
        #expect(CurrentConditionsLayer.readoutLines(cc, distanceNm: 80, altitudeFt: 2000).isEmpty)
        #expect(CurrentConditionsLayer.readoutLines(nil, distanceNm: 50, altitudeFt: 2000).isEmpty)
    }

    @Test func accessibilitySummaryCountsZonesAndColumns() throws {
        let cc = try #require(VizCurrentConditions.build(
            observations: try routeObservations([
                #"{ "icao": "ZZAA", "metar_flight_category": "VFR", "enroute_distance_nm": 1.0, "distance_from_route_nm": 0.0 }"#,
            ]),
            sigmets: try routeSigmets([sigmetJSON(), sigmetJSON(fir: "ZZYY")]),
            terrainProfile: nil))
        #expect(CurrentConditionsLayer.accessibilitySummary(cc) == "2 SIGMET zones, 1 METAR column")
    }
}
