//
//  LivePositionTests.swift
//  flyfun-weatherTests
//
//  #761 — the GPS fixes the app sends: the wire keys the server reads
//  (`LivePositionFix` in src/weatherbrief/api/packs.py), the recent-fix
//  buffer, and the CLLocation → fix conversion.
//

import Testing
import Foundation
import CoreLocation
@testable import flyfun_weather

@Suite struct LivePositionTests {

    private let t0 = Date(timeIntervalSince1970: 1_791_000_000)

    private func fix(_ seconds: TimeInterval, lon: Double = 1.0) -> LivePositionFix {
        LivePositionFix(at: t0.addingTimeInterval(seconds), lat: 50.0, lon: lon, altFt: 4500,
                        gsKt: 120, trackDeg: 90, hAccuracyM: 8, alongNm: 12.5, crossTrackNm: 0.3)
    }

    @Test func requestUsesTheServersKeys() throws {
        let data = try JSONEncoder.weatherBrief.encode(LivePositionRequest(fixes: [fix(0)], landed: true))
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["landed"] as? Bool == true)
        let first = try #require((json["fixes"] as? [[String: Any]])?.first)
        #expect(Set(first.keys) == ["at", "lat", "lon", "alt_ft", "gs_kt", "track_deg",
                                    "h_accuracy_m", "along_nm", "cross_track_nm"])
        #expect(first["h_accuracy_m"] as? Double == 8)
    }

    @Test func invalidValuesAreOmitted() throws {
        let bare = LivePositionFix(at: t0, lat: 50, lon: 1, altFt: nil, gsKt: nil, trackDeg: nil,
                                   hAccuracyM: 30, alongNm: nil, crossTrackNm: nil)
        let data = try JSONEncoder.weatherBrief.encode(bare)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(Set(json.keys) == ["at", "lat", "lon", "h_accuracy_m"])
    }

    @Test func resultDecodes() throws {
        let json = #"{"flight_id": "f", "accepted": 2, "ignored": [], "landed_recorded": false, "progress": null}"#
        let result = try JSONDecoder.weatherBrief.decode(LivePositionResult.self, from: Data(json.utf8))
        #expect(result.accepted == 2 && result.landedRecorded == false)
    }

    @Test func bufferKeepsTheNewestPlusTwoSpacedFixes() {
        var buffer: [LivePositionFix] = []
        // A fix every 5 s, as the tracking service projects them.
        for s in stride(from: 0.0, through: 130, by: 5) {
            buffer = LivePositionRules.appending(fix(s), to: buffer)
        }
        // The newest, plus the recent ones ~60 s apart.
        #expect(buffer.map { $0.at.timeIntervalSince(t0) } == [60, 120, 130])
        // An older fix arriving late changes nothing; nil is no fix.
        #expect(LivePositionRules.appending(fix(10), to: buffer) == buffer)
        #expect(LivePositionRules.appending(nil, to: buffer) == buffer)
    }

    @Test func onlyANewerFixIsWorthAPost() {
        let fixes = [fix(0), fix(90)]
        #expect(LivePositionRules.hasNew(fixes, since: nil))
        #expect(LivePositionRules.hasNew(fixes, since: t0))
        #expect(!LivePositionRules.hasNew(fixes, since: t0.addingTimeInterval(90)))
        #expect(!LivePositionRules.hasNew([], since: nil))
    }

    @Test @MainActor func locationToFix() throws {
        let loc = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 50.1, longitude: 1.2), altitude: 1000,
            horizontalAccuracy: 12, verticalAccuracy: 5, course: 92, speed: 61.73, timestamp: t0
        )
        let f = try #require(FlightTrackingService.fix(from: loc, alongNm: 30, crossTrackNm: 1))
        #expect(f.at == t0 && f.hAccuracyM == 12 && f.trackDeg == 92 && f.alongNm == 30)
        #expect(abs((f.gsKt ?? 0) - 120) < 0.1)
        #expect(abs((f.altFt ?? 0) - 3280.84) < 0.1)
        // Invalid speed / course / altitude go as nil; no horizontal fix, no fix.
        let noSpeed = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 50.1, longitude: 1.2), altitude: 0,
            horizontalAccuracy: 12, verticalAccuracy: -1, course: -1, speed: -1, timestamp: t0
        )
        let g = try #require(FlightTrackingService.fix(from: noSpeed, alongNm: nil, crossTrackNm: nil))
        #expect(g.gsKt == nil && g.trackDeg == nil && g.altFt == nil)
        let invalid = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 0, longitude: 0), altitude: 0,
            horizontalAccuracy: -1, verticalAccuracy: -1, timestamp: t0
        )
        #expect(FlightTrackingService.fix(from: invalid, alongNm: nil, crossTrackNm: nil) == nil)
    }
}
