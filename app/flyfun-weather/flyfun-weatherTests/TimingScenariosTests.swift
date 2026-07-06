//
//  TimingScenariosTests.swift
//  flyfun-weatherTests
//
//  Timing-Flexibility port (#357): the DTO family is decoded straight off the
//  network with hand-written tolerant initialisers, so these cover the decode
//  contract — full-payload field mapping (snake_case ↔ camelCase via the shared
//  `.convertFromSnakeCase` decoder), the "missing optional key → default"
//  resilience, and the enum wire values that must match `models/time_scan.py`.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite struct TimingScenariosDecodeTests {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.weatherBrief.decode(type, from: Data(json.utf8))
    }

    // MARK: Full payload

    @Test func decodesFullTimeOptionsResponse() throws {
        let json = """
        {
          "status": {"status": "done", "flexibility": "same_day", "reason": "", "updated_at": "2026-07-05T10:00:00+00:00"},
          "scan": {
            "flexibility": "same_day",
            "baseline": {"departure_time": "2026-07-05T09:00:00+00:00", "assessment": "AMBER", "assessment_reason": "icing=AMBER"},
            "window": {"start": "2026-07-05T06:00:00+00:00", "end": "2026-07-05T18:00:00+00:00", "daylight_clipped": false, "horizon_clipped": true, "past_clipped": false},
            "candidates": [
              {"departure_time": "2026-07-05T09:00:00+00:00", "departure_shift_hours": 0, "valid_times": ["2026-07-05T09:00:00+00:00"], "assessment": "AMBER", "assessment_reason": "icing=AMBER", "models_used": ["ecmwf", "gfs"], "improves": [], "worsens": [], "margin": 0, "confidence": "confirmed_in_window", "is_baseline": true, "is_alternate": false, "confirmed": null, "confirm_pending": false},
              {"departure_time": "2026-07-05T11:00:00+00:00", "departure_shift_hours": 2, "valid_times": ["2026-07-05T11:00:00+00:00"], "assessment": "GREEN", "assessment_reason": "", "models_used": ["ecmwf"], "improves": ["icing"], "worsens": [], "margin": 1.5, "confidence": "ecmwf_only", "is_baseline": false, "is_alternate": false, "confirmed": null, "confirm_pending": false}
            ],
            "refused_times": [],
            "generated_at": "2026-07-05T10:00:00+00:00"
          }
        }
        """
        let resp = try decode(TimeOptionsResponse.self, json)
        #expect(resp.status?.status == .done)
        #expect(resp.status?.flexibility == .sameDay)

        let scan = try #require(resp.scan)
        #expect(scan.flexibility == .sameDay)
        #expect(scan.baseline.assessment == "AMBER")
        #expect(scan.window?.horizonClipped == true)
        #expect(scan.candidates.count == 2)

        let baseline = scan.candidates[0]
        #expect(baseline.isBaseline)
        #expect(baseline.confidence == .confirmedInWindow)
        #expect(baseline.modelsUsed == ["ecmwf", "gfs"])

        let candidate = scan.candidates[1]
        #expect(candidate.departureShiftHours == 2)
        #expect(candidate.confidence == .ecmwfOnly)
        #expect(candidate.improves == ["icing"])
        #expect(candidate.confirmed == nil)
    }

    // MARK: Tolerant decode — missing optional keys fall back to defaults

    @Test func candidateDecodesWithOnlyRequiredFields() throws {
        // Only `departure_time` + `assessment` are non-optional; everything else
        // must degrade to a default rather than throwing.
        let candidate = try decode(TimeCandidateDTO.self, """
        {"departure_time": "2026-07-05T09:00:00+00:00", "assessment": "GREEN"}
        """)
        #expect(candidate.departureShiftHours == 0)
        #expect(candidate.validTimes.isEmpty)
        #expect(candidate.modelsUsed.isEmpty)
        #expect(candidate.improves.isEmpty)
        #expect(candidate.isBaseline == false)
        #expect(candidate.confirmPending == false)
        #expect(candidate.confidence == .ecmwfOnly)   // default when key absent
        #expect(candidate.id == "2026-07-05T09:00:00+00:00")
    }

    @Test func statusDecodesTolerantlyFromEmptyObject() throws {
        // Regression guard for the resilience fix: a `status` object missing its
        // keys must not fail the decode — it defaults to the terminal `.done` so
        // the poll can't spin forever on schema drift.
        let status = try decode(TimeScanStatusDTO.self, "{}")
        #expect(status.status == .done)
        #expect(status.flexibility == .none)
        #expect(status.reason.isEmpty)
    }

    @Test func scanTolerantOfMissingBaselineAndMalformedCandidate() throws {
        // `baseline` absent + one candidate missing its required `assessment`:
        // the scan must still decode, defaulting baseline to empty and dropping
        // only the malformed candidate (not the whole list).
        let scan = try decode(TimeWindowScanDTO.self, """
        {
          "flexibility": "same_day",
          "window": null,
          "candidates": [
            {"departure_time": "2026-07-05T09:00:00+00:00", "assessment": "GREEN"},
            {"departure_time": "2026-07-05T11:00:00+00:00"}
          ],
          "refused_times": [],
          "generated_at": "2026-07-05T10:00:00+00:00"
        }
        """)
        #expect(scan.baseline.departureTime.isEmpty)   // defaulted to .empty
        #expect(scan.candidates.count == 1)            // malformed row dropped, not all
        #expect(scan.candidates[0].assessment == "GREEN")
    }

    @Test func confirmationDecodesFullAndDegrades() throws {
        let confirmed = try decode(TimeConfirmationDTO.self, """
        {"models_checked": ["ecmwf", "gfs"], "assessment": "GREEN", "better_than_baseline": true,
         "improves": ["icing"], "worsens": [], "confirmed_at": "2026-07-05T12:00:00+00:00"}
        """)
        #expect(confirmed.modelsChecked == ["ecmwf", "gfs"])
        #expect(confirmed.betterThanBaseline)
        #expect(confirmed.perModelReasons.isEmpty)   // absent key → empty map
    }

    // MARK: Wire enums must match the server / web

    @Test func flexibilityModeWireValues() {
        #expect(FlexibilityMode.sameDay.rawValue == "same_day")
        #expect(FlexibilityMode.prevDay.rawValue == "prev_day")
        #expect(FlexibilityMode.nextDay.rawValue == "next_day")
        #expect(FlexibilityMode(rawValue: "alternate") == .alternate)
    }

    @Test func confidenceWireValues() {
        #expect(TimeConfidence.confirmedInWindow.rawValue == "confirmed_in_window")
        #expect(TimeConfidence.ecmwfOnly.rawValue == "ecmwf_only")
    }

    // #358 round-3 fix: `FlightResponse` decodes off the non-tolerant live list
    // path, so an unrecognized `flexibility` value (a mode added server-side
    // before this client knows it) must degrade to `.none` rather than throw and
    // fail the decode of *every* flight in the list.
    private func flightJSON(flexibility: String) -> String {
        """
        {"id":"f1","user_id":"u1","route_name":"LFMD LFML","waypoints":["LFMD","LFML"],
         "departure_time":"2026-07-05T09:00:00+00:00","target_date":"2026-07-05","target_time_utc":900,
         "cruise_altitude_ft":8000,"flight_ceiling_ft":13000,"flight_duration_hours":2.0,
         "private":false,"auto_refresh":false,"created_at":"2026-07-01T00:00:00+00:00",
         "flexibility":"\(flexibility)"}
        """
    }

    @Test func flightResponseDegradesUnknownFlexibilityToNone() throws {
        let flight = try decode(FlightResponse.self, flightJSON(flexibility: "quantum_day"))
        #expect(flight.flexibility == FlexibilityMode.none)
        #expect(flight.effectiveFlexibility == .none)
        // A whole list still decodes even if a row carries the unknown value.
        let list = try decode([FlightResponse].self, "[\(flightJSON(flexibility: "quantum_day"))]")
        #expect(list.count == 1)
    }

    @Test func flightResponseDecodesKnownFlexibility() throws {
        #expect(try decode(FlightResponse.self, flightJSON(flexibility: "same_day")).flexibility == .sameDay)
    }

    // MARK: Usage-summary gate

    @Test func usageSummaryDefaultsToNeverScanned() throws {
        let empty = try decode(UsageSummaryResponse.self, "{}")
        #expect(empty.timeScanUsed == false)
        #expect(empty.timeScanCount == 0)

        let used = try decode(UsageSummaryResponse.self, """
        {"time_scan_used": true, "time_scan_count": 5}
        """)
        #expect(used.timeScanUsed)
        #expect(used.timeScanCount == 5)
    }
}
