//
//  RouteRibbonInspectorRulesTests.swift
//  flyfun-weatherTests
//
//  The ribbon inspector card's pure rules (#747).
//
//  SYNC — the content and `bandAt` cases mirror
//  web/tests/unit/ribbon-tooltip.test.ts with the same inputs and the same
//  expected strings, so the iOS card and the web tooltip say the same thing.
//  The hit test, chips and live-refresh pruning are iOS only.
//

import Testing
import Foundation
@testable import flyfun_weather

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try JSONDecoder.weatherBrief.decode(type, from: Data(json.utf8))
}

private let width: CGFloat = 520
private typealias I = RouteRibbonInspectorRules

/// The web test's `band()` helper.
private func band(
    id: String = "b1", tier: String = "rain", side: String = "left",
    profile: String = "[[15, -20, -5], [20, -18, -6], [25, -15, -5]]",
    extra: String = ""
) throws -> RibbonWeather {
    try decode(RibbonWeather.self, """
    {"id": "\(id)", "tier": "\(tier)", "from_nm": 10, "to_nm": 30, "side": "\(side)",
     "near_nm": 5, "far_nm": 20, "peak_dbz": 30, "profile": \(profile)\(extra)}
    """)
}

/// The web test's `ribbon()` helper; `body` adds lanes.
private func ribbon(_ body: String = "") throws -> LiveRibbon {
    try decode(LiveRibbon.self, """
    {"route_nm": 100, "segment_nm": 25, "radar_radius_nm": 10,
     "weather_status": "available", "weather_corridor_nm": 30, "weather_bin_nm": 5\(body)}
    """)
}

private func station(_ json: String) throws -> RibbonStation { try decode(RibbonStation.self, json) }

private func airport(_ json: String) throws -> AirportObservation { try decode(AirportObservation.self, json) }

private let noLeak = ["Optional(", "NaN"]

private func leaks(_ c: RibbonCardContent) -> Bool {
    noLeak.contains { c.text.contains($0) }
}

// MARK: - Content (ribbon-tooltip.test.ts)

@MainActor @Suite struct RibbonInspectorContentTests {

    @Test func stationWithoutAMatchingAirportShowsTheRibbonFieldsAloneAndNoRawText() throws {
        let st = try station(#"{"icao": "ZZZZ", "role": "route", "metar_category": null, "cross_nm": -4.6, "along_nm": 40}"#)
        let c = I.stationContent(st, airport: nil)
        #expect(c.title == "ZZZZ")
        #expect(c.rows.contains(RibbonCardRow(label: "METAR now", value: "unavailable")))
        #expect(c.rows.contains(RibbonCardRow(label: "Position", value: "5 NM left of course at 40 NM")))
        // Not joined is not "no report": the raw text is unknown, so nothing.
        #expect(!c.text.contains("no report"))
        #expect(!c.text.contains("none issued"))
        #expect(c.raws.isEmpty)
        #expect(!leaks(c))
    }

    @Test func stationWithAnAirportNamesItAndQuotesTheRawReports() throws {
        let st = try station("""
        {"icao": "ZZAA", "role": "destination", "metar_category": "MVFR", "metar_time": "2026-10-02T08:20:00Z",
         "eta": "2026-10-02T09:40:00Z", "taf_category_at_eta": "VFR", "taf_temporary_type": "TEMPO",
         "taf_temporary_category": "IFR", "taf_weather": ["TSRA"], "convective": ["CB"]}
        """)
        let apt = try airport("""
        {"icao": "ZZAA", "name": "Test Field", "metar_raw": "ZZAA 020820Z 18010KT 4000 RA BKN012 15/13 Q1010",
         "metar_report_type": "SPECI", "taf_raw": "TAF ZZAA 020500Z 0206/0306 18010KT 9999 SCT020"}
        """)
        let c = I.stationContent(st, airport: apt)
        #expect(c.subtitle == "Test Field")
        #expect(c.rows.contains(RibbonCardRow(label: "Role", value: "Destination")))
        #expect(c.rows.contains(RibbonCardRow(label: "METAR now", value: "MVFR at 08:20Z")))
        #expect(c.rows.contains(RibbonCardRow(label: "Observed", value: "CB")))
        #expect(c.rows.contains(RibbonCardRow(label: "TAF at 09:40Z", value: "VFR, TEMPO IFR")))
        #expect(c.rows.contains(RibbonCardRow(label: "TAF weather", value: "TSRA")))
        // Departure / destination sit on the line: no position row.
        #expect(!c.rows.contains { $0.label == "Position" })
        #expect(c.raws.map(\.label) == ["SPECI", "TAF"])
        #expect(c.raws.first?.value.hasPrefix("ZZAA 020820Z") == true)
    }

    @Test func stationWithAnAirportThatHasNoTafSaysNoneIssued() throws {
        let st = try station(#"{"icao": "ZZAA", "role": "destination", "metar_category": "VFR"}"#)
        let apt = try airport(#"{"icao": "ZZAA", "name": "Test <Field>", "metar_raw": "ZZAA 091200Z <script>", "taf_raw": null}"#)
        let c = I.stationContent(st, airport: apt)
        // No HTML here: the text is shown as is, not escaped.
        #expect(c.subtitle == "Test <Field>")
        #expect(c.raws == [RibbonCardRow(label: "METAR", value: "ZZAA 091200Z <script>")])
        #expect(c.rows.contains(RibbonCardRow(label: "TAF", value: "none issued")))
    }

    @Test func joinsAirportsByIcao() throws {
        let st = try station(#"{"icao": "ZZBB", "role": "route"}"#)
        let a = try airport(#"{"icao": "ZZAA"}"#), b = try airport(#"{"icao": "ZZBB", "name": "Bravo"}"#)
        #expect(I.airport(for: st, in: [a, b])?.name == "Bravo")
        #expect(I.airport(for: st, in: nil) == nil)
    }

    @Test func aCellSaysStrengthPositionMotionLightningAndTop() throws {
        let storm = try decode(LiveStorm.self, """
        {"id": "c1", "cell_ids": ["c1"], "lat": 0, "lon": 0, "peak_dbz": 47.4, "intensity": "very heavy",
         "along_nm": 60, "offtrack_nm": 8, "cross_nm": -8, "side": "left",
         "relative_motion": "closing", "closing_kt": 12, "flashes": 3, "top_fl": 310, "trend": "growing"}
        """)
        let c = I.stormContent(storm)
        #expect(c.title == "Very heavy cell")
        #expect(c.subtitle == "47 dBZ")
        #expect(c.rows.contains(RibbonCardRow(label: "Position", value: "8 NM left of track at 60 NM")))
        #expect(c.rows.contains(RibbonCardRow(label: "Motion", value: "closing 12 kt")))
        #expect(c.rows.contains(RibbonCardRow(label: "Lightning", value: "3 flashes")))
        #expect(c.rows.contains(RibbonCardRow(label: "Cloud top", value: "FL310")))
        #expect(c.rows.contains(RibbonCardRow(label: "Trend (30 min)", value: "growing")))
        #expect(!leaks(c))
    }

    @Test func aRainBandSaysSpanSideDistanceAndMotion() throws {
        let c = I.bandContent(try band(extra: #", "motion_rel_deg": 80, "speed_kt": 14"#))
        #expect(c.title == "Rain area")
        #expect(c.rows.contains(RibbonCardRow(label: "Along route", value: "10–30 NM")))
        #expect(c.rows.contains(RibbonCardRow(label: "Off track", value: "5–20 NM left of course")))
        #expect(c.rows.contains(RibbonCardRow(label: "Motion", value: "drifting toward the right of course, 14 kt")))
        #expect(!leaks(c))
        let across = I.bandContent(try band(tier: "core", side: "both"))
        #expect(across.title == "Convective core")
        #expect(across.rows.contains(RibbonCardRow(label: "Off track", value: "across the track")))
    }

    @Test func aSigmetSaysWhatAndWhen() throws {
        let s = try decode(RibbonSigmet.self, """
        {"id": "sigmet:ZZZZ|3", "label": "ZZZZ: SIGMET 3 EMBD TS", "hazard": "TS", "qualifier": "EMBD",
         "from_nm": 12.4, "to_nm": 47.6, "valid_from": "2026-10-02T09:00:00Z", "valid_to": "2026-10-02T13:00:00Z",
         "pending": true, "new": true}
        """)
        let c = I.sigmetContent(s)
        #expect(c.title == "ZZZZ EMBD TS")
        #expect(c.subtitle == "new")
        #expect(c.rows.contains(RibbonCardRow(label: "SIGMET", value: "ZZZZ: SIGMET 3 EMBD TS")))
        #expect(c.rows.contains(RibbonCardRow(label: "Along route", value: "12–48 NM")))
        #expect(c.rows.contains(RibbonCardRow(label: "Valid", value: "09:00Z–13:00Z")))
        #expect(c.rows.contains(RibbonCardRow(label: "Status", value: "issued, not yet valid")))
    }

    @Test func aRadarStretchIsItsSegmentLabel() throws {
        let seg = try decode(LiveRibbonSegment.self, #"{"index": 0, "from_nm": 0, "to_nm": 25, "radar_status": "no_coverage"}"#)
        #expect(I.segmentContent(seg).title == "0–25 NM: radar coverage insufficient")
    }

    /// The exported `/live` ticks the iOS UI test and the web tests share:
    /// every station names its airport and quotes its raw METAR / TAF.
    @Test func everyExportedTickStationNamesItsAirportAndQuotesItsRawReports() throws {
        let dir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("flyfun-weatherUITests/LiveScenarios")
        let files = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
        #expect(!files.isEmpty)
        var checked = 0
        for file in files {
            let layer = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(contentsOf: file))
            let airports = layer.routeObservations?.airports
            for st in layer.ribbon?.stations ?? [] {
                let apt = I.airport(for: st, in: airports)
                let c = I.stationContent(st, airport: apt)
                #expect(c.title == st.icao)
                #expect(!leaks(c), "\(file.lastPathComponent) \(st.icao): \(c.text)")
                if let name = apt?.name { #expect(c.subtitle == name) }
                if let raw = apt?.metarRaw, !raw.isEmpty { #expect(c.raws.contains { $0.value == raw }) }
                if let raw = apt?.tafRaw, !raw.isEmpty { #expect(c.raws.contains(RibbonCardRow(label: "TAF", value: raw))) }
                checked += 1
            }
            for s in layer.ribbon?.sigmets ?? [] {
                let c = I.sigmetContent(s)
                #expect(c.text.contains("SIGMET"))
                #expect(!leaks(c))
            }
        }
        #expect(checked > 0)
    }
}

// MARK: - bandAt (ribbon-tooltip.test.ts)

@MainActor @Suite struct RibbonBandAtTests {

    @Test func findsTheBandAtTheCentreOfEveryRectItDraws() throws {
        let r = try ribbon(#", "weather": [{"id": "b1", "tier": "rain", "from_nm": 10, "to_nm": 30, "side": "left", "near_nm": 5, "far_nm": 20, "profile": [[15, -20, -5], [20, -18, -6], [25, -15, -5]]}]"#)
        let rects = I.bandBinRects(r, corridor: 30, width: width)
        #expect(rects.count == 3)
        for (_, rect) in rects {
            #expect(I.bandAt(r, point: CGPoint(x: rect.midX, y: rect.midY), width: width)?.id == "b1")
        }
        // The other side of the line, and past its end: nothing.
        let first = rects[0].rect
        #expect(I.bandAt(r, point: CGPoint(x: first.minX + 1, y: 150), width: width) == nil)
        #expect(I.bandAt(r, point: CGPoint(x: width - 21, y: first.minY + 1), width: width) == nil)
    }

    @Test func aCoreInsideARainAreaWinsBecauseItIsDrawnOnTop() throws {
        let rain = #"{"id": "rain", "tier": "rain", "profile": [[15, -20, -5], [20, -18, -6], [25, -15, -5]]}"#
        let core = #"{"id": "core", "tier": "core", "profile": [[20, -14, -10]]}"#
        let r = try ribbon(#", "weather": [\#(rain), \#(core)]"#)
        let coreRect = try #require(I.bandBinRects(try ribbon(#", "weather": [\#(core)]"#), corridor: 30, width: width).first?.rect)
        #expect(I.bandAt(r, point: CGPoint(x: coreRect.midX, y: coreRect.midY), width: width)?.id == "core")
    }
}

// MARK: - Hit test and selection (iOS only)

@MainActor @Suite struct RibbonHitTestTests {

    private func target(_ key: RibbonMarkKey, _ rect: CGRect, z: Int) -> RibbonHitTarget {
        RibbonHitTarget(key: key, rect: rect, z: z)
    }

    @Test func bareRibbonPicksNothing() {
        let t = [target(.station("ZZAA"), CGRect(x: 100, y: 100, width: 10, height: 10), z: 5)]
        #expect(I.hits(at: CGPoint(x: 300, y: 20), in: t).isEmpty)
    }

    @Test func picksTheNearestMarkWithinTheRadiusFirstAndTheOthersAsChips() {
        let t = [
            target(.station("ZZAA"), CGRect(x: 100, y: 100, width: 10, height: 10), z: 5),
            target(.station("ZZBB"), CGRect(x: 130, y: 100, width: 10, height: 10), z: 5),
            target(.station("ZZCC"), CGRect(x: 300, y: 100, width: 10, height: 10), z: 5),
        ]
        // 8 pt right of ZZAA's edge, 12 pt left of ZZBB's; ZZCC out of reach.
        #expect(I.hits(at: CGPoint(x: 118, y: 105), in: t) == [.station("ZZAA"), .station("ZZBB")])
        // Just past the radius: nothing.
        #expect(I.hits(at: CGPoint(x: 110 + I.hitRadius + 1, y: 80), in: [t[0]]).isEmpty)
    }

    @Test func theMarkUnderTheFingerBeatsANearerEdgeAndTheTopLayerWins() {
        let rain = target(.band("rain"), CGRect(x: 0, y: 0, width: 400, height: 200), z: 1)
        let disc = target(.station("ZZAA"), CGRect(x: 100, y: 100, width: 10, height: 10), z: 5)
        // On the disc, inside the rain area: the disc (painted on top) first.
        #expect(I.hits(at: CGPoint(x: 105, y: 105), in: [rain, disc]) == [.station("ZZAA"), .band("rain")])
        // In the rain, 10 pt off the disc: the rain is under the finger.
        #expect(I.hits(at: CGPoint(x: 120, y: 105), in: [rain, disc]) == [.band("rain"), .station("ZZAA")])
    }

    @Test func aKeyWithSeveralRectsIsListedOnceAtItsBest() {
        let t = [
            target(.band("b1"), CGRect(x: 0, y: 0, width: 10, height: 10), z: 1),
            target(.band("b1"), CGRect(x: 20, y: 0, width: 10, height: 10), z: 1),
        ]
        #expect(I.hits(at: CGPoint(x: 25, y: 5), in: t) == [.band("b1")])
    }

    @Test func aCoreOutliningAKnownCellIsThatCell() throws {
        let r = try ribbon(#", "weather": [{"id": "core1", "tier": "core", "storm_id": "c1", "profile": [[50, 5, 10]]}]"#)
        let storm = try decode(LiveStorm.self, #"{"id": "c1"}"#)
        let targets = I.targets(ribbon: r, storms: [storm], corridorNm: 30, width: width)
        #expect(Set(targets.map(\.key)) == [.storm("c1")])
        let rect = try #require(I.bandBinRects(r, corridor: 30, width: width).first?.rect)
        #expect(I.hits(at: CGPoint(x: rect.midX, y: rect.midY), in: targets).first == .storm("c1"))
        // Without the cell (feed down), the core is a band of its own.
        #expect(Set(I.targets(ribbon: r, storms: [], corridorNm: 30, width: width).map(\.key)) == [.band("core1")])
    }

    @Test func targetsSitWhereTheDrawingPutsTheMarks() throws {
        let r = try ribbon("""
        , "weather": [],
          "stations": [{"icao": "ZZAA", "role": "departure"}, {"icao": "ZZBB", "role": "route", "along_nm": 50, "cross_nm": -3},
                       {"icao": "ZZCC", "role": "destination"}],
          "sigmets": [{"id": "s1", "from_nm": 20, "to_nm": 40}]
        """)
        let t = I.targets(ribbon: r, storms: [], corridorNm: 30, width: width)
        let dep = try #require(t.first { $0.key == .station("ZZAA") })
        #expect(dep.rect.midX == RouteRibbonRules.inset && dep.rect.midY == RouteRibbonRules.trackY)
        let mid = try #require(t.first { $0.key == .station("ZZBB") })
        #expect(mid.rect.midX == RouteRibbonRules.x(50, routeNm: 100, width: width))
        #expect(mid.rect.midY == RouteRibbonRules.leftRowY)
        let sigmet = try #require(t.first { $0.key == .sigmet("s1") })
        #expect(sigmet.rect.midY == RouteRibbonRules.sigmetY)
        #expect(I.hits(at: CGPoint(x: RouteRibbonRules.inset, y: RouteRibbonRules.trackY), in: t).first == .station("ZZAA"))
    }

    @Test func withoutTheCellsFeedTheRadarStretchesAreMarks() throws {
        let r = try decode(LiveRibbon.self, """
        {"route_nm": 100, "weather_status": "unavailable",
         "segments": [{"index": 0, "from_nm": 0, "to_nm": 50, "radar_status": "measured"},
                      {"index": 1, "from_nm": 50, "to_nm": 100, "radar_status": "no_coverage"}]}
        """)
        let t = I.targets(ribbon: r, storms: [], corridorNm: 30, width: width)
        let x = RouteRibbonRules.x(75, routeNm: 100, width: width)
        #expect(I.hits(at: CGPoint(x: x, y: RouteRibbonRules.trackY), in: t).first == .segment(1))
    }

    @Test func aTapOpensSwapsAndClosesTheCard() {
        let a = RibbonMarkKey.station("ZZAA"), b = RibbonMarkKey.storm("c1")
        let opened = I.inspection(after: [a, b], current: nil)
        #expect(opened == RibbonInspection(candidates: [a, b], selected: a))
        // Another mark swaps it in place.
        #expect(I.inspection(after: [b], current: opened)?.selected == b)
        // The same mark again, or bare ribbon, closes it.
        #expect(I.inspection(after: [a, b], current: opened) == nil)
        #expect(I.inspection(after: [], current: opened) == nil)
    }

    @Test func aLiveRefreshKeepsTheSelectionByIdentityAndClosesWhenItsMarkIsGone() {
        let a = RibbonMarkKey.station("ZZAA"), b = RibbonMarkKey.band("b1")
        let open = RibbonInspection(candidates: [a, b], selected: a)
        #expect(open.pruned(available: [a, b]) == open)
        // A chip whose mark is gone drops out; the card stays.
        #expect(open.pruned(available: [a]) == RibbonInspection(candidates: [a], selected: a))
        // The selected mark is gone: the card closes.
        #expect(open.pruned(available: [b]) == nil)
    }

    @Test func availableKeysListEveryDrawnMark() throws {
        let r = try ribbon("""
        , "weather": [{"id": "b1", "tier": "rain", "profile": [[20, -5, -1]]}, {"id": "empty", "tier": "rain", "profile": []}],
          "stations": [{"icao": "ZZAA", "role": "departure"}, {"icao": "ZZXX", "role": "route"}],
          "sigmets": [{"id": "s1", "from_nm": 20, "to_nm": 40}, {"id": "s2"}]
        """)
        // A station without a position, a SIGMET without a span and a band
        // without bins are not drawn, so they cannot be selected.
        #expect(I.availableKeys(ribbon: r, storms: [], corridorNm: 30) == [.band("b1"), .station("ZZAA"), .sigmet("s1")])
    }
}
