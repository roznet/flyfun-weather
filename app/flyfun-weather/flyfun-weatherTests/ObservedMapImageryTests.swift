//
//  ObservedMapImageryTests.swift
//  flyfun-weatherTests
//
//  The route map's observed radar + satellite tile overlays (#654) — the iOS
//  mirror of web/tests/unit/observed-tiles.test.ts: tile URL building, zoom
//  gating, the layer/badge composition (stale frames not drawn, failures named),
//  the radar pick fallback, the corridor box, and the frame-listing model.
//

import CoreLocation
import Foundation
import MapKit
import Testing
import UIKit
@testable import flyfun_weather

@Suite("ObservedMapImagery")
struct ObservedMapImageryTests {

    static let template = "/api/observed/tiles/opera_dbzh/{stamp}/{z}/{x}/{y}.png"
    /// 2026-10-03T14:17Z — twelve minutes after the radar frame below.
    static let now = Date.parseISO8601("2026-10-03T14:17:00Z")!

    static func listing(
        source: String = "opera_dbzh",
        label: String = "Radar reflectivity",
        stamps: [(String, String)] = [("20261003T1405", "2026-10-03T14:05:00+00:00")],
        stale: Bool = false,
        window: Double? = 10,
        attribution: String? = "OPERA / EUMETNET",
        minZoom: Int = 3, maxZoom: Int = 10
    ) -> ObservedFramesResponse {
        ObservedFramesResponse(
            source: source, label: label,
            frames: stamps.map { ObservedImageryFrame(stamp: $0.0, validTime: $0.1, ageMinutes: 12) },
            stale: stale, windowMinutes: window,
            attribution: ObservedImageryAttribution(text: attribution),
            tileUrlTemplate: "/api/observed/tiles/\(source)/{stamp}/{z}/{x}/{y}.png",
            minZoom: minZoom, maxZoom: maxZoom)
    }

    // MARK: Tile URL + zoom gating

    @Test("tile path fills stamp, z, x, y")
    func tilePath() {
        let path = ObservedMapImagery.tilePath(template: Self.template, stamp: "20261003T1405", z: 6, x: 32, y: 21)
        #expect(path == "/api/observed/tiles/opera_dbzh/20261003T1405/6/32/21.png")
    }

    @Test("a stamp can't add path segments")
    func tilePathEncodesStamp() {
        let path = ObservedMapImagery.tilePath(template: Self.template, stamp: "a/b", z: 3, x: 0, y: 0)
        #expect(path == "/api/observed/tiles/opera_dbzh/a%2Fb/3/0/0.png")
    }

    @Test("only zooms the server renders are fetched")
    func zoomGating() {
        #expect(!ObservedMapImagery.isZoomServed(2, minZoom: 3, maxZoom: 10))
        #expect(ObservedMapImagery.isZoomServed(3, minZoom: 3, maxZoom: 10))
        #expect(ObservedMapImagery.isZoomServed(10, minZoom: 3, maxZoom: 10))
        #expect(!ObservedMapImagery.isZoomServed(11, minZoom: 3, maxZoom: 10))
        // Satellite stops at z9.
        #expect(!ObservedMapImagery.isZoomServed(10, minZoom: 3, maxZoom: 9))
    }

    @Test("the overlay takes its zoom range from the listing")
    func overlayZoomRange() {
        let spec = ObservedMapImagery.TileLayerSpec(
            source: "satellite_ir", stamp: "20261003T1400", template: Self.template,
            minZoom: 3, maxZoom: 9, opacity: 0.8, isUnderlay: true, cacheable: true)
        let overlay = ObservedTileOverlay(spec: spec) { _ in Data() }
        #expect(overlay.minimumZ == 3)
        #expect(overlay.maximumZ == 9)
        #expect(!overlay.canReplaceMapContent)
        #expect(abs(overlay.opacity - 0.8) < 0.0001)
    }

    // MARK: Selection

    @Test("None is a choice; a missing pick falls back to reflectivity, then rain rate")
    func resolveSelection() {
        let both = ["opera_dbzh", "opera_rate"]
        #expect(ObservedMapImagery.resolveSelection("", available: both) == "")
        #expect(ObservedMapImagery.resolveSelection("opera_rate", available: both) == "opera_rate")
        #expect(ObservedMapImagery.resolveSelection("opera_rate", available: ["opera_dbzh"]) == "opera_dbzh")
        #expect(ObservedMapImagery.resolveSelection("opera_dbzh", available: ["opera_rate"]) == "opera_rate")
        #expect(ObservedMapImagery.resolveSelection("eumetsat_ctth", available: []) == "")
    }

    @Test("only the radar sources the briefing collected are offered")
    func availableRadar() throws {
        let json = """
        {"radii_nm": [5, 10, 20],
         "rain_rate": {"source": "opera_rate", "quantity": "RATE", "units": "mm/h",
                       "valid_time": "2026-10-03T14:05:00Z", "age_minutes": 12, "stations": []}}
        """
        let observed = try JSONDecoder.weatherBrief.decode(ObservedConditions.self, from: Data(json.utf8))
        #expect(ObservedMapImagery.availableRadar(observed) == ["opera_rate"])
        #expect(ObservedMapImagery.availableRadar(nil).isEmpty)
    }

    // MARK: Composition + badges

    @Test("satellite under radar; radar badge first")
    func composeOrder() {
        let frames = [
            "opera_dbzh": Self.listing(),
            "satellite_ir": Self.listing(
                source: "satellite_ir", label: "Satellite infrared",
                stamps: [("20261003T1300", "2026-10-03T13:00:00+00:00")],
                window: 0, attribution: "EUMETSAT MTG FCI IR 10.5 µm via EUMETView", maxZoom: 9),
        ]
        let result = ObservedMapImagery.compose(
            selection: "opera_dbzh", showSatellite: true, radarOpacity: 0.6,
            frames: frames, failed: [], now: Self.now)
        #expect(result.layers.map(\.source) == ["satellite_ir", "opera_dbzh"])
        #expect(result.layers[0].isUnderlay)
        #expect(result.layers[0].opacity == ObservedMapImagery.satelliteOpacity)
        #expect(result.layers[1].opacity == 0.6)
        #expect(result.layers[1].key == "opera_dbzh|20261003T1405")
        #expect(result.badges.count == 2)
        #expect(result.badges[0] == "Radar reflectivity 14:05Z · 12 min old · 10 min rolling max · OPERA / EUMETNET")
        #expect(result.badges[1] == "Satellite infrared 13:00Z · 77 min old · EUMETSAT MTG FCI IR 10.5 µm via EUMETView")
    }

    @Test("a stale frame is not drawn, and the badge says why")
    func composeStale() {
        let result = ObservedMapImagery.compose(
            selection: "opera_dbzh", showSatellite: false, radarOpacity: 0.75,
            frames: ["opera_dbzh": Self.listing(stale: true)], failed: [], now: Self.now)
        #expect(result.layers.isEmpty)
        #expect(result.badges == ["Radar reflectivity: not shown — newest frame 14:05Z is 12 min old"])
    }

    @Test("an empty stale listing says there is no current frame")
    func staleEmpty() {
        let info = Self.listing(stamps: [], stale: true)
        #expect(ObservedMapImagery.staleBadge(info, now: Self.now) == "Radar reflectivity: no current frame")
    }

    @Test("loading says nothing; a failed listing is named, not silent")
    func composePendingAndFailed() {
        let pending = ObservedMapImagery.compose(
            selection: "opera_rate", showSatellite: true, radarOpacity: 0.75,
            frames: [:], failed: [], now: Self.now)
        #expect(pending.layers.isEmpty)
        #expect(pending.badges.isEmpty)

        let failed = ObservedMapImagery.compose(
            selection: "opera_rate", showSatellite: true, radarOpacity: 0.75,
            frames: [:], failed: ["opera_rate", "satellite_ir"], now: Self.now)
        #expect(failed.badges == ["Radar rain rate: imagery unavailable", "Satellite infrared: imagery unavailable"])
    }

    @Test("None + satellite off draws nothing")
    func composeNothing() {
        let result = ObservedMapImagery.compose(
            selection: "", showSatellite: false, radarOpacity: 0.75,
            frames: ["opera_dbzh": Self.listing()], failed: [], now: Self.now)
        #expect(result.layers.isEmpty)
        #expect(result.badges.isEmpty)
    }

    @Test("a young satellite cycle is not pinned in the tile cache; radar is")
    func cacheability() {
        let young = Self.listing(source: "satellite_ir", label: "Satellite infrared",
                                 stamps: [("20261003T1400", "2026-10-03T14:00:00+00:00")])
        let old = Self.listing(source: "satellite_ir", label: "Satellite infrared",
                               stamps: [("20261003T1340", "2026-10-03T13:40:00+00:00")])
        func layer(_ sat: ObservedFramesResponse) -> ObservedMapImagery.TileLayerSpec? {
            ObservedMapImagery.compose(selection: "", showSatellite: true, radarOpacity: 0.75,
                                       frames: ["satellite_ir": sat], failed: [], now: Self.now).layers.first
        }
        #expect(layer(young)?.cacheable == false)
        #expect(layer(old)?.cacheable == true)
        let radar = ObservedMapImagery.compose(selection: "opera_dbzh", showSatellite: false, radarOpacity: 0.75,
                                               frames: ["opera_dbzh": Self.listing()], failed: [], now: Self.now)
        #expect(radar.layers.first?.cacheable == true)
    }

    @Test("the badge age is recomputed from the frame's own valid time")
    func badgeAgeFromValidTime() {
        let info = Self.listing(window: 0, attribution: nil)
        let later = Self.now.addingTimeInterval(5 * 60)
        #expect(ObservedMapImagery.frameBadge(info, info.frames[0], now: later)
                == "Radar reflectivity 14:05Z · 17 min old")
    }

    // MARK: Corridor box

    @Test("box pads the route by the corridor width, wider in longitude")
    func corridorBox() throws {
        let points = [CLLocationCoordinate2D(latitude: 48.0, longitude: 2.0),
                      CLLocationCoordinate2D(latitude: 51.0, longitude: -0.5)]
        let box = try #require(ObservedMapImagery.corridorBox(points, radiusNm: 20))
        let padLat = 20 * 1.852 / 111.0
        #expect(abs(box.south - (48.0 - padLat)) < 1e-9)
        #expect(abs(box.north - (51.0 + padLat)) < 1e-9)
        let padLon = padLat / cos(51.0 * .pi / 180)
        #expect(abs(box.west - (-0.5 - padLon)) < 1e-9)
        #expect(abs(box.east - (2.0 + padLon)) < 1e-9)
        #expect(ObservedMapImagery.corridorBox([], radiusNm: 20) == nil)
    }

    @Test("corridor radius follows a sampled pick, else the widest")
    func corridorRadius() throws {
        let observed = try JSONDecoder.weatherBrief.decode(
            ObservedConditions.self, from: Data(#"{"radii_nm": [5, 10, 20]}"#.utf8))
        #expect(ObservedMapImagery.corridorRadius(observed, picked: 10) == 10)
        #expect(ObservedMapImagery.corridorRadius(observed, picked: 15) == 20)
        #expect(ObservedMapImagery.corridorRadius(observed, picked: nil) == 20)
        #expect(ObservedMapImagery.corridorRadius(nil, picked: 10) == nil)
    }

    // MARK: Legend

    @Test("hex colours and legend ends")
    func legend() {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        ObservedMapImagery.color(hex: "#2563eb")?.getRed(&r, green: &g, blue: &b, alpha: &a)
        #expect(abs(r - 0x25 / 255.0) < 0.001 && abs(g - 0x63 / 255.0) < 0.001 && abs(b - 0xEB / 255.0) < 0.001)
        #expect(ObservedMapImagery.color(hex: "red") == nil)
        #expect(ObservedMapImagery.legendValue(5, units: nil) == "5")
        #expect(ObservedMapImagery.legendValue(0.5, units: "mm/h") == "0.5 mm/h")
        #expect(ObservedMapImagery.legendValue(55, units: "dBZ") == "55 dBZ")
    }

    // MARK: Wire format

    @Test("frames listing decodes from the server's snake_case shape")
    func decodeFrames() throws {
        let json = """
        {"source": "opera_dbzh", "label": "Radar reflectivity",
         "frames": [{"stamp": "20261003T1405", "valid_time": "2026-10-03T14:05:00+00:00", "age_minutes": 12.0}],
         "stale": false, "window_minutes": 10.0,
         "attribution": {"producer": "EUMETNET", "license": null, "url": null, "text": "OPERA / EUMETNET"},
         "tile_url_template": "/api/observed/tiles/opera_dbzh/{stamp}/{z}/{x}/{y}.png",
         "min_zoom": 3, "max_zoom": 10}
        """
        let info = try JSONDecoder.weatherBrief.decode(ObservedFramesResponse.self, from: Data(json.utf8))
        #expect(info.frames.first?.stamp == "20261003T1405")
        #expect(info.tileUrlTemplate.hasSuffix("{y}.png"))
        #expect(info.minZoom == 3 && info.maxZoom == 10)
        #expect(info.attribution?.text == "OPERA / EUMETNET")

        // Radar with no frame yet: `attribution` is `{}`.
        let empty = """
        {"source": "opera_rate", "label": "Radar rain rate", "frames": [], "stale": true,
         "window_minutes": 0.0, "attribution": {},
         "tile_url_template": "/x/{stamp}/{z}/{x}/{y}.png", "min_zoom": 3, "max_zoom": 10}
        """
        let none = try JSONDecoder.weatherBrief.decode(ObservedFramesResponse.self, from: Data(empty.utf8))
        #expect(none.attribution?.text == nil)
        #expect(ObservedMapImagery.currentFrame(none) == nil)
    }

    @Test("status legends decode")
    func decodeStatus() throws {
        let json = """
        {"sources": [{"source": "opera_dbzh", "label": "Radar reflectivity", "units": "dBZ",
          "interval_minutes": 5.0, "window_minutes": 10.0, "renders_imagery": true, "available": true,
          "legend": [{"value": 5, "color": "#9be1ff"}, {"value": 55, "color": "#ff00ff"}],
          "attribution": {"producer": null, "license": null, "url": null, "text": null}}]}
        """
        let status = try JSONDecoder.weatherBrief.decode(ObservedImageryStatusResponse.self, from: Data(json.utf8))
        #expect(status.sources.first?.legend?.count == 2)
        #expect(status.sources.first?.units == "dBZ")
    }
}

// MARK: - Frame-listing model

@Suite("RouteObservedImageryModel")
struct RouteObservedImageryModelTests {

    @Test("a failed refresh keeps the last listing; with none held the source is failed")
    func keepsLastListing() async {
        let repo = MockBriefingRepository()
        repo.observedFramesHandler = { _ in ObservedMapImageryTests.listing() }
        let model = RouteObservedImageryModel(repository: repo)
        let t0 = Date()

        await model.refresh(["opera_dbzh"], at: t0)
        #expect(model.frames["opera_dbzh"] != nil)
        #expect(model.failed.isEmpty)

        repo.observedFramesHandler = { _ in throw APIError.serverError(503, "busy") }
        await model.refresh(["opera_dbzh", "satellite_ir"], at: t0.addingTimeInterval(120))
        #expect(model.frames["opera_dbzh"]?.frames.first?.stamp == "20261003T1405")
        #expect(!model.failed.contains("opera_dbzh"))
        #expect(model.failed.contains("satellite_ir"))
    }

    @Test("a listing younger than the TTL is not refetched")
    func ttl() async {
        let repo = MockBriefingRepository()
        repo.observedFramesHandler = { _ in ObservedMapImageryTests.listing() }
        let model = RouteObservedImageryModel(repository: repo)
        let t0 = Date()
        await model.refresh(["opera_dbzh"], at: t0)
        await model.refresh(["opera_dbzh"], at: t0.addingTimeInterval(30))
        #expect(repo.observedFramesRequests == ["opera_dbzh"])
        await model.refresh(["opera_dbzh"], at: t0.addingTimeInterval(RouteObservedImageryModel.refreshInterval + 1))
        #expect(repo.observedFramesRequests == ["opera_dbzh", "opera_dbzh"])
    }
}
