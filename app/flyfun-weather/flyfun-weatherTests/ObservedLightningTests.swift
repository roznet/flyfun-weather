//
//  ObservedLightningTests.swift
//  flyfun-weatherTests
//
//  Lightning on the route map (#665) — the iOS mirror of the flash cases in
//  web/tests/unit/observed-conditions.test.ts: the age fade, which flashes are
//  drawn and in what order, the bolt glyph, the request box, and the badge
//  (unavailable / no frame / late feed said as such, never "no lightning").
//

import CoreLocation
import Foundation
import MapKit
import Testing
import UIKit
@testable import flyfun_weather

@Suite("ObservedLightning")
struct ObservedLightningTests {

    /// 2026-10-03T14:11Z — six minutes after the newest lightning frame below.
    static let now = Date.parseISO8601("2026-10-03T14:11:00Z")!

    static func response(
        flashes: [ObservedFlash] = [
            ObservedFlash(lat: 49.1, lon: 2.1, time: "2026-10-03T14:09:00+00:00"),  // 2 min
            ObservedFlash(lat: 49.2, lon: 2.2, time: "2026-10-03T13:31:00+00:00"),  // 40 min
            ObservedFlash(lat: 49.3, lon: 2.3, time: "2026-10-03T12:41:00+00:00"),  // 90 min
        ],
        newest: String? = "2026-10-03T14:05:00+00:00",
        attribution: String? = "EUMETSAT"
    ) -> ObservedFlashesResponse {
        ObservedFlashesResponse(
            flashes: flashes, count: flashes.count, newestValidTime: newest,
            windowMinutes: 10, retentionMinutes: 180,
            attribution: ObservedImageryAttribution(text: attribution))
    }

    // MARK: Fade

    @Test("fades by age and drops the tail (web flashOpacity)")
    func fade() {
        #expect(ObservedLightning.opacity(ageMinutes: 0) == 0.9)
        #expect(ObservedLightning.opacity(ageMinutes: 0) > ObservedLightning.opacity(ageMinutes: 30))
        #expect(abs(ObservedLightning.opacity(ageMinutes: 30) - 0.45) < 1e-9)
        #expect(ObservedLightning.opacity(ageMinutes: 60) == 0)
        #expect(ObservedLightning.opacity(ageMinutes: 90) == 0)
    }

    @Test("draws flashes inside the trail, oldest first, newest on top")
    func drawable() {
        var flashes = Self.response().flashes
        flashes.append(ObservedFlash(lat: 49.4, lon: 2.4, time: "not a time"))
        let drawn = ObservedLightning.drawable(flashes, now: Self.now)
        #expect(drawn.map(\.lat) == [49.2, 49.1])
        #expect(abs(drawn[0].opacity - ObservedLightning.opacity(ageMinutes: 40)) < 1e-9)
        #expect(drawn[1].opacity > drawn[0].opacity)
    }

    // MARK: Glyph

    @Test("the bolt is centred on the flash and stays inside its box")
    func glyph() {
        let w = ObservedLightning.glyphWidth, h = ObservedLightning.glyphHeight
        let pts = ObservedLightning.glyphPoints(center: CGPoint(x: 100, y: 50), width: w, height: h)
        #expect(pts.count == ObservedLightning.glyph.count)
        for p in pts {
            #expect(abs(p.x - 100) <= w / 2)
            #expect(abs(p.y - 50) <= h / 2)
        }
        // The tip is the bottom-most vertex, at the bottom of the box.
        #expect(abs((pts.map(\.y).max() ?? 0) - (50 + h / 2)) < 1e-9)
    }

    // MARK: Request

    @Test("asks for the corridor box and the trail only")
    func path() {
        let box = ObservedMapImagery.LatLonBox(south: 48.5, west: 1.25, north: 50.123456, east: 3)
        #expect(ObservedLightning.flashesPath(box: box)
                == "/api/observed/flashes?south=48.5000&west=1.2500&north=50.1235&east=3.0000&minutes=60")
    }

    // MARK: Badge

    @Test("badge: the frame's own time, its age and the drawn count")
    func badgeLoaded() {
        let state = ObservedLightning.State.loaded(Self.response())
        #expect(ObservedLightning.badge(state, now: Self.now)
                == "Lightning 14:05Z · 6 min old · 2 flashes in the last 60 min · EUMETSAT")
        #expect(ObservedLightning.chip(state, now: Self.now)
                == ObservedMapImagery.SummaryChip(name: "Lightning", status: "6 min", isWarning: false))
    }

    @Test("badge: a single flash is singular")
    func badgeSingular() {
        #expect(ObservedLightning.flashCountText(1) == "1 flash in the last 60 min")
        #expect(ObservedLightning.flashCountText(0) == "0 flashes in the last 60 min")
    }

    @Test("badge: unavailable and no frame are named, never 'no lightning'")
    func badgeUnavailable() {
        #expect(ObservedLightning.badge(.loading, now: Self.now) == nil)
        #expect(ObservedLightning.chip(.loading, now: Self.now) == nil)
        #expect(ObservedLightning.badge(.unavailable, now: Self.now) == "Lightning unavailable")
        #expect(ObservedLightning.chip(.unavailable, now: Self.now)?.isWarning == true)
        let empty = ObservedLightning.State.loaded(Self.response(flashes: [], newest: nil, attribution: nil))
        #expect(ObservedLightning.badge(empty, now: Self.now) == "Lightning: no current frame")
        #expect(ObservedLightning.chip(empty, now: Self.now)
                == ObservedMapImagery.SummaryChip(name: "Lightning", status: "n/a", isWarning: true))
    }

    @Test("badge: a late feed still draws but says newer flashes may be missing")
    func badgeStale() {
        let late = Self.now.addingTimeInterval(30 * 60)  // newest frame 36 min old
        let state = ObservedLightning.State.loaded(Self.response())
        let badge = ObservedLightning.badge(state, now: late) ?? ""
        #expect(badge.hasPrefix("Lightning 14:05Z · 36 min old · feed late, newer flashes may be missing"))
        #expect(ObservedLightning.chip(state, now: late)?.isWarning == true)
    }

    // MARK: Model

    @Test("model: requests the corridor box; a failure reads unavailable and draws nothing")
    @MainActor
    func model() async {
        let repo = MockBriefingRepository()
        let response = Self.response()
        repo.observedFlashesHandler = { _ in response }
        let model = RouteLightningModel(repository: repo)
        let box = ObservedMapImagery.LatLonBox(south: 48, west: 1, north: 50, east: 3)

        await model.refresh(box: box, at: Self.now)
        #expect(repo.observedFlashesRequests
                == ["/api/observed/flashes?south=48.0000&west=1.0000&north=50.0000&east=3.0000&minutes=60"])
        #expect(model.drawable.count == 2)
        #expect(model.badge?.hasPrefix("Lightning 14:05Z") == true)
        let first = model.revision

        repo.observedFlashesHandler = { _ in throw APIError.notFound }
        await model.refresh(box: box, at: Self.now.addingTimeInterval(120))
        #expect(model.state == .unavailable)
        #expect(model.drawable.isEmpty)
        #expect(model.badge == "Lightning unavailable")
        #expect(model.revision > first)
    }

    @Test("decodes the server payload (snake case)")
    func decode() throws {
        let json = """
        {"flashes": [{"lat": 49.1, "lon": 2.1, "time": "2026-10-03T14:09:00+00:00"}],
         "count": 1, "newest_valid_time": "2026-10-03T14:05:00+00:00",
         "window_minutes": 10.0, "retention_minutes": 180.0,
         "attribution": {"text": "EUMETSAT", "producer": "EUMETSAT"}}
        """
        let decoded = try JSONDecoder.weatherBrief.decode(ObservedFlashesResponse.self, from: Data(json.utf8))
        #expect(decoded.flashes.count == 1)
        #expect(decoded.newestValidTime == "2026-10-03T14:05:00+00:00")
        #expect(decoded.attribution?.text == "EUMETSAT")
        let none = """
        {"flashes": [], "count": 0, "newest_valid_time": null, "window_minutes": 10.0,
         "retention_minutes": 180.0, "attribution": {}}
        """
        let empty = try JSONDecoder.weatherBrief.decode(ObservedFlashesResponse.self, from: Data(none.utf8))
        #expect(empty.newestValidTime == nil)
    }
}
