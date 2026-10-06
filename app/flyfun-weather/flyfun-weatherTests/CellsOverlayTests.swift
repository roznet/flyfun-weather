//
//  CellsOverlayTests.swift
//  flyfun-weatherTests
//
//  The experimental radar-cell overlay on iOS (#661) — the mirror of
//  web/tests/unit/cells-overlay-core.test.ts: stamp pairing and staleness,
//  the badge, motion/trend words, arrows only for measured motion on cores,
//  display decoding through the shared snake_case decoder, the route list and
//  the shared model's caching. Plus the briefing tab bar shapes.
//

import CoreLocation
import Foundation
import Testing
@testable import flyfun_weather

@Suite("CellsOverlay")
struct CellsOverlayTests {

    /// 14:17Z — twelve minutes after the 14:05Z frame.
    static let now = Date.parseISO8601("2026-10-03T14:17:00Z")!

    static func frame(_ stamp: String, _ valid: String) -> CellFrame {
        CellFrame(stamp: stamp, validTime: valid, receivedAt: nil, ageMinutes: nil, key: nil, revision: nil)
    }

    static func listing(enabled: Bool = true, frames: [CellFrame]? = nil) -> CellFramesResponse {
        let frames = frames ?? [
            frame("20261003T1405", "2026-10-03T14:05:00+00:00"),
            frame("20261003T1400", "2026-10-03T14:00:00+00:00"),
            frame("20261003T1350", "2026-10-03T13:50:00+00:00"),
        ]
        return CellFramesResponse(
            enabled: enabled, frames: frames, newest: frames.first, stale: false,
            staleAfterMinutes: 25, unavailableSince: nil,
            urlTemplate: "/api/observed/cells/{stamp}.json")
    }

    /// A display file as the server sends it (snake_case), fictional cells.
    static let displayJSON = """
    {
      "schema": "observed-cells-display/1",
      "policy_version": "cells-v1+abc123",
      "code_revision": null,
      "valid_time": "2026-10-03T14:05:00+00:00",
      "window_minutes": 10,
      "times": {"radar": "2026-10-03T14:05:00+00:00", "rate": null, "lightning": null, "cloud_top": null},
      "unavailable": [{"what": "lightning", "reason": "late"}],
      "rain_min_area_km2": 2000.0,
      "arrow_minutes": 30,
      "outlines": {"core35": [[[44.0, 5.0], [44.1, 5.1], [44.0, 5.2]]], "rain20": []},
      "cells": [
        {"id": "c1", "tier": "core35", "lat": 44.05, "lon": 5.1, "area_km2": 120.5,
         "peak_dbz": 44.5, "rate_peak_mm_h": 18.2, "flashes": 3, "top_fl": 310,
         "truncated": false, "age_min": 25, "event": "continue",
         "trend": {"state": "developing", "window_min": 30, "d_peak_db": 4, "area_ratio": 1.6, "d_flashes": 2},
         "motion": {"status": "available", "reason": null, "speed_kt": 18.4, "toward_deg": 47},
         "arrow": [44.2, 5.3]},
        {"id": "c2", "tier": "core41", "lat": 44.3, "lon": 5.4, "area_km2": 40,
         "peak_dbz": 52, "rate_peak_mm_h": null, "flashes": null, "top_fl": null,
         "truncated": true, "age_min": 5, "event": "new",
         "trend": {"state": "new", "window_min": null, "d_peak_db": null, "area_ratio": null, "d_flashes": null},
         "motion": {"status": "withheld", "reason": "split", "speed_kt": null, "toward_deg": null},
         "arrow": [44.4, 5.5]},
        {"id": "r1", "tier": "rain20", "lat": 45.0, "lon": 6.0, "area_km2": 5000,
         "peak_dbz": 30, "rate_peak_mm_h": 2, "flashes": 0, "top_fl": null,
         "truncated": false, "age_min": 60, "event": "continue",
         "trend": {"state": "steady", "window_min": 30, "d_peak_db": 0, "area_ratio": 1.0, "d_flashes": 0},
         "motion": {"status": "available", "reason": null, "speed_kt": 10, "toward_deg": 90},
         "arrow": [45.0, 6.2]}
      ]
    }
    """

    static func display() throws -> CellDisplay {
        try JSONDecoder.weatherBrief.decode(CellDisplay.self, from: Data(displayJSON.utf8))
    }

    // MARK: Pairing + staleness

    @Test("pairs with the drawn radar frame's stamp")
    func sameStamp() {
        #expect(CellsOverlay.match(Self.listing(), radarStamp: "20261003T1400", now: Self.now)
                == .ok(Self.frame("20261003T1400", "2026-10-03T14:00:00+00:00")))
    }

    @Test("else the newest at or before the radar frame")
    func atOrBefore() {
        #expect(CellsOverlay.match(Self.listing(), radarStamp: "20261003T1402", now: Self.now)
                == .ok(Self.frame("20261003T1400", "2026-10-03T14:00:00+00:00")))
    }

    @Test("the paired overlay's own age decides, not the radar's")
    func pairedButStale() {
        // 13:50Z is 27 min old at 14:17Z — past the 25 min threshold.
        #expect(CellsOverlay.match(Self.listing(), radarStamp: "20261003T1355", now: Self.now)
                == .unavailable(since: "2026-10-03T13:50:00+00:00"))
    }

    @Test("no radar drawn → the newest")
    func newestWithoutRadar() {
        #expect(CellsOverlay.match(Self.listing(), radarStamp: nil, now: Self.now)
                == .ok(Self.frame("20261003T1405", "2026-10-03T14:05:00+00:00")))
    }

    @Test("an overlay older than the server's threshold is not drawn")
    func staleIsUnavailable() {
        let late = Self.now.addingTimeInterval(20 * 60)  // 32 min after 14:05Z
        #expect(CellsOverlay.match(Self.listing(), radarStamp: nil, now: late)
                == .unavailable(since: "2026-10-03T14:05:00+00:00"))
    }

    @Test("disabled server and missing listing are distinct answers")
    func disabledAndMissing() {
        #expect(CellsOverlay.match(Self.listing(enabled: false), radarStamp: nil, now: Self.now) == .disabled)
        #expect(CellsOverlay.match(nil, radarStamp: nil, now: Self.now) == .unavailable(since: nil))
        #expect(CellsOverlay.match(Self.listing(frames: []), radarStamp: nil, now: Self.now) == .unavailable(since: nil))
    }

    @Test("a radar frame older than every overlay has no pair")
    func radarOlderThanAll() {
        #expect(CellsOverlay.match(Self.listing(), radarStamp: "20261003T1300", now: Self.now)
                == .unavailable(since: "2026-10-03T14:05:00+00:00"))
    }

    // MARK: Badge

    @Test("badge carries the overlay's own time, age, count and what is missing")
    func badgeOk() throws {
        let match = CellsOverlay.Match.ok(Self.frame("20261003T1405", "2026-10-03T14:05:00+00:00"))
        #expect(CellsOverlay.badge(match, display: try Self.display(), now: Self.now)
                == "Cells 14:05Z · 12 min old · 3 cells · lightning unavailable · experimental")
    }

    @Test("chip carries the overlay's own age; unavailable is a warning")
    func chip() {
        let ok = CellsOverlay.chip(.ok(Self.frame("20261003T1405", "2026-10-03T14:05:00+00:00")), now: Self.now)
        #expect(ok.text == "Cells 12 min")
        #expect(!ok.isWarning)
        #expect(CellsOverlay.chip(.disabled, now: Self.now).text == "Cells n/a")
        #expect(CellsOverlay.chip(.unavailable(since: nil), now: Self.now).isWarning)
    }

    @Test("badge wording for disabled / unavailable")
    func badgeUnavailable() {
        #expect(CellsOverlay.badge(.disabled, display: nil, now: Self.now)
                == "Cell analysis: not available on this server")
        #expect(CellsOverlay.badge(.unavailable(since: "2026-10-03T13:40:00+00:00"), display: nil, now: Self.now)
                == "Cell analysis unavailable since 13:40Z")
        #expect(CellsOverlay.badge(.unavailable(since: nil), display: nil, now: Self.now)
                == "Cell analysis unavailable (nothing received yet)")
    }

    // MARK: Words

    @Test("motion in words, never codes")
    func motionWords() {
        func m(_ status: String, _ speed: Double? = nil, _ toward: Double? = nil) -> CellMotion {
            CellMotion(status: status, reason: nil, speedKt: speed, towardDeg: toward)
        }
        #expect(CellsOverlay.motionText(m("available", 18.4, 47)) == "moving NE at 18 kt")
        #expect(CellsOverlay.motionText(m("available", 0.5, 47)) == "nearly stationary")
        #expect(CellsOverlay.motionText(m("withheld")) == "motion not yet measured")
        #expect(CellsOverlay.motionText(m("unsupported")) == "motion withheld: too little of the cell in matched tiles")
        #expect(CellsOverlay.motionText(m("no_pair")) == "no motion yet: no earlier radar frame to compare")
        #expect(CellsOverlay.motionText(nil) == "motion unknown")
    }

    @Test("trend with its numbers")
    func trendWords() throws {
        let cells = try Self.display().cells
        #expect(CellsOverlay.trendText(cells[0].trend) == "developing over 30 min (peak +4 dB, area ×1.6, flashes +2)")
        #expect(CellsOverlay.trendText(cells[1].trend) == "new (less than 15 min of history)")
    }

    @Test("compass wraps and rounds")
    func compass() {
        #expect(CellsOverlay.compass(0) == "N")
        #expect(CellsOverlay.compass(359) == "N")
        #expect(CellsOverlay.compass(-90) == "W")
        #expect(CellsOverlay.compass(nil) == "–")
    }

    // MARK: Decoding + arrows

    @Test("decodes the server's display file")
    func decodes() throws {
        let d = try Self.display()
        #expect(d.cells.count == 3)
        #expect(d.cells[0].ratePeakMmH == 18.2)
        #expect(d.cells[0].areaKm2 == 120.5)
        #expect(d.cells[0].trend?.dPeakDb == 4)
        #expect(d.outlines?["core35"]?.first?.count == 3)
        #expect(d.times?.cloudTop == nil)
    }

    @Test("a malformed cell is dropped, not the whole overlay")
    func lossyCells() throws {
        let json = """
        {"valid_time": "2026-10-03T14:05:00+00:00",
         "cells": [
           {"id": "ok", "tier": "core35", "lat": 44.0, "lon": 5.0},
           {"id": "no-position", "tier": "core41"},
           {"id": "ok2", "tier": "core41", "lat": 44.2, "lon": 5.2}
         ]}
        """
        let d = try JSONDecoder.weatherBrief.decode(CellDisplay.self, from: Data(json.utf8))
        #expect(d.cells.map(\.id) == ["ok", "ok2"])
        #expect(d.schema == nil)
        #expect(d.outlines == nil)
    }

    @Test("arrows only for measured motion on cores")
    func arrows() throws {
        let cells = try Self.display().cells
        #expect(CellsOverlay.arrowEnd(cells[0]) != nil)          // core, available
        #expect(CellsOverlay.arrowEnd(cells[1]) == nil)          // withheld, despite an arrow point
        #expect(CellsOverlay.arrowEnd(cells[2]) == nil)          // rain area
    }

    @Test("route list without a route: cores only, strongest first")
    func listedWithoutRoute() throws {
        #expect(CellsOverlay.routeStorms(try Self.display(), route: []).map(\.id) == ["c2", "c1"])
    }

    /// A fictional storm field around a north–south route along 5.0E,
    /// 44.0N → 45.0N (#689): one storm 9 NM off track (a core41 inside a
    /// core35), a stronger one 36 NM off, and a lone core41 15 NM off.
    static let stormsJSON = """
    {"valid_time": "2026-10-03T14:05:00+00:00",
     "cells": [
       {"id": "near35", "tier": "core35", "lat": 44.20, "lon": 5.209, "area_km2": 300, "peak_dbz": 49},
       {"id": "near41", "tier": "core41", "lat": 44.21, "lon": 5.215, "area_km2": 40, "peak_dbz": 49},
       {"id": "far35", "tier": "core35", "lat": 44.50, "lon": 5.84, "area_km2": 900, "peak_dbz": 75.5},
       {"id": "far41", "tier": "core41", "lat": 44.50, "lon": 5.84, "area_km2": 200, "peak_dbz": 75.5},
       {"id": "lone41", "tier": "core41", "lat": 44.70, "lon": 4.65, "area_km2": 30, "peak_dbz": 45},
       {"id": "rain", "tier": "rain20", "lat": 44.30, "lon": 5.00, "area_km2": 5000, "peak_dbz": 30}
     ]}
    """
    static let route = [CLLocationCoordinate2D(latitude: 44.0, longitude: 5.0),
                        CLLocationCoordinate2D(latitude: 45.0, longitude: 5.0)]

    @Test("storms: a core41 inside a core35 is one storm; only within 20 NM; nearest first")
    func routeStorms() throws {
        let display = try JSONDecoder.weatherBrief.decode(CellDisplay.self, from: Data(Self.stormsJSON.utf8))
        let storms = CellsOverlay.routeStorms(display, route: Self.route)
        #expect(storms.map(\.id) == ["near35", "lone41"])
        #expect(storms.map { Int(($0.offTrackNm ?? -1).rounded()) } == [9, 15])
        let wide = CellsOverlay.routeStorms(display, route: Self.route, withinNm: 50)
        #expect(wide.map(\.id) == ["near35", "lone41", "far35"])
    }

    @Test("off-track distance is to the route line, not its waypoints")
    func offTrack() throws {
        let display = try JSONDecoder.weatherBrief.decode(CellDisplay.self, from: Data(Self.stormsJSON.utf8))
        let near = try #require(display.cells.first { $0.id == "near35" })
        // Abeam the middle of the leg, ~9 NM east; the nearest waypoint is 12 NM+ away.
        #expect(abs((CellsOverlay.offTrackNm(near, route: Self.route) ?? 0) - 9.0) < 0.5)
        #expect(CellsOverlay.offTrackNm(near, route: []) == nil)
    }

    @Test("chip counts storms within a stated distance")
    func stormsChip() {
        #expect(CellsOverlay.stormsChipText(0) == "No storms within 20 NM of route")
        #expect(CellsOverlay.stormsChipText(1) == "1 storm within 20 NM of route")
        #expect(CellsOverlay.stormsChipText(3) == "3 storms within 20 NM of route")
    }

    @Test("whole-number dBZ and singular flash")
    func wording() {
        #expect(CellsOverlay.dbzText(75.5) == "76 dBZ")
        #expect(CellsOverlay.dbzText(nil) == "–")
        #expect(CellsOverlay.flashesText(1) == "1 flash")
        #expect(CellsOverlay.flashesText(3) == "3 flashes")
    }

    @Test("location is named by the nearest route waypoint")
    func location() throws {
        let cell = try Self.display().cells[0]  // 44.05N 5.1E
        let waypoints = [("ZZAA", CLLocationCoordinate2D(latitude: 43.8, longitude: 4.8)),
                         ("ZZBB", CLLocationCoordinate2D(latitude: 46.0, longitude: 7.0))]
        let label = CellsOverlay.locationLabel(cell, waypoints: waypoints.map { (icao: $0.0, coordinate: $0.1) })
        #expect(label == "20 NM NE of ZZAA")
    }

    @Test("display path fills the stamp and the box")
    func displayPath() {
        let box = ObservedMapImagery.LatLonBox(south: 43.123, west: 4.5, north: 45, east: 6.789)
        #expect(CellsOverlay.displayPath(template: "/api/observed/cells/{stamp}.json", stamp: "20261003T1405", box: box)
                == "/api/observed/cells/20261003T1405.json?south=43.12&west=4.50&north=45.00&east=6.79")
        #expect(CellsOverlay.displayPath(template: "/api/observed/cells/{stamp}.json", stamp: "20261003T1405", box: nil)
                == "/api/observed/cells/20261003T1405.json")
    }

    @Test("lightning on its way reads pending, never 'no lightning' (#666)")
    func lightningPending() throws {
        let json = """
        {"valid_time": "2026-10-03T14:05:00+00:00", "pending": ["lightning"], "unavailable": [], "revision": 0,
         "cells": [{"id": "c1", "tier": "core41", "lat": 44.0, "lon": 5.0, "flashes": null,
                    "flashes_pending": true, "rate_peak_mm_h": 12, "rate_as_of": "2026-10-03T13:45:00+00:00"}]}
        """
        let display = try JSONDecoder.weatherBrief.decode(CellDisplay.self, from: Data(json.utf8))
        let match = CellsOverlay.Match.ok(Self.frame("20261003T1405", "2026-10-03T14:05:00+00:00"))
        let badge = CellsOverlay.badge(match, display: display, now: Self.now)
        #expect(badge.contains("lightning pending"))
        #expect(!badge.contains("no lightning"))
        let cell = try #require(display.cells.first)
        let lines = CellsOverlay.detailLines(cell)
        #expect(lines.contains { $0.contains("lightning pending") })
        #expect(lines.contains { $0.contains("12 mm/h (as of 13:45Z)") })
    }

    @Test("an amended frame is fetched by its newest revision (#666)")
    func revisionKey() throws {
        let json = """
        {"enabled": true, "stale": false, "stale_after_minutes": 25, "unavailable_since": null,
         "url_template": "/api/observed/cells/{stamp}.json", "newest": null,
         "frames": [{"stamp": "20261003T1405", "key": "20261003T1405.r1", "revision": 1,
                     "valid_time": "2026-10-03T14:05:00+00:00", "received_at": null, "age_minutes": 12}]}
        """
        let listing = try JSONDecoder.weatherBrief.decode(CellFramesResponse.self, from: Data(json.utf8))
        let frame = try #require(listing.frames.first)
        #expect(frame.revision == 1)
        #expect(CellsOverlay.displayPath(template: listing.urlTemplate, stamp: frame.displayKey, box: nil)
                == "/api/observed/cells/20261003T1405.r1.json")
        // A server from before revisions: the bare stamp.
        #expect(Self.frame("20261003T1405", "2026-10-03T14:05:00+00:00").displayKey == "20261003T1405")
    }

    // MARK: Shared model

    @Test("model: one listing per TTL, display cached by path")
    @MainActor
    func modelCaching() async throws {
        let repo = MockBriefingRepository()
        let listing = Self.listing()
        let display = try Self.display()
        repo.observedCellFramesHandler = { listing }
        repo.observedCellDisplayHandler = { _ in display }
        let model = RouteCellsModel(repository: repo)

        await model.refresh(radarStamp: nil, box: nil, at: Self.now)
        await model.refresh(radarStamp: nil, box: nil, at: Self.now.addingTimeInterval(30))
        #expect(repo.observedCellFramesCallCount == 1)
        #expect(repo.observedCellDisplayRequests == ["/api/observed/cells/20261003T1405.json"])
        #expect(model.display?.cells.count == 3)
        #expect(model.badge?.hasPrefix("Cells 14:05Z") == true)

        // A forced refresh (pull-to-refresh) skips the listing TTL.
        await model.refresh(radarStamp: nil, box: nil, at: Self.now.addingTimeInterval(35), force: true)
        #expect(repo.observedCellFramesCallCount == 2)

        // A different radar frame pairs with a different overlay.
        await model.refresh(radarStamp: "20261003T1400", box: nil, at: Self.now.addingTimeInterval(40))
        #expect(repo.observedCellDisplayRequests.last == "/api/observed/cells/20261003T1400.json")
    }

    @Test("model: a failed display fetch reads unavailable, never a stale picture")
    @MainActor
    func modelFailure() async {
        let repo = MockBriefingRepository()
        let listing = Self.listing()
        repo.observedCellFramesHandler = { listing }
        repo.observedCellDisplayHandler = { _ in throw APIError.notFound }
        let model = RouteCellsModel(repository: repo)
        await model.refresh(radarStamp: nil, box: nil, at: Self.now)
        #expect(model.display == nil)
        #expect(model.match == .unavailable(since: "2026-10-03T14:05:00+00:00"))
        #expect(model.hasLoaded)
    }
}

@Suite("BriefingTab")
struct BriefingTabTests {
    @Test("forecast-only briefing keeps the four tabs")
    func forecastOnly() {
        #expect(BriefingTab.tabs(showsObserved: false) == [.advisory, .discussion, .crossSection, .map])
    }

    @Test("flight day appends Observed at the right; the others keep their places")
    func flightDay() {
        #expect(BriefingTab.tabs(showsObserved: true) == [.advisory, .discussion, .crossSection, .map, .observed])
    }

    @Test("PIREPs are off for everyone")
    func pirepsHidden() {
        #expect(PirepFeature.isEnabled == false)
    }
}
