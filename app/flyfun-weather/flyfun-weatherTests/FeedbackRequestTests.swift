//
//  FeedbackRequestTests.swift
//  flyfun-weatherTests
//
//  #616 — free-text feedback sent from a briefing must carry the link back to
//  the flight + pack (the server stores them and the admin email links to the
//  pack); app-level feedback from the flight list must not invent one.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite struct GeneralFeedbackRequestTests {

    private func body(_ request: GeneralFeedbackRequest) throws -> [String: Any] {
        let data = try JSONEncoder.weatherBrief.encode(request)
        return try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    @Test func briefingFeedbackCarriesFlightAndPack() throws {
        let json = try body(GeneralFeedbackRequest(
            category: .incorrectInterpretation,
            comment: "No TAF at EGSC today",
            contactOk: true,
            flightId: "lfrm_egsc-2026-09-13-55cd",
            packTimestamp: "2026-09-13T09:01:43.045060+00:00"
        ))

        #expect(json["flight_id"] as? String == "lfrm_egsc-2026-09-13-55cd")
        #expect(json["pack_timestamp"] as? String == "2026-09-13T09:01:43.045060+00:00")
        #expect(json["category"] as? String == "incorrect_interpretation")
        #expect(json["target"] as? String == "general")
        #expect(json["contact_ok"] as? Bool == true)
        #expect(json["comment"] as? String == "No TAF at EGSC today")
    }

    @Test func appLevelFeedbackSendsNoLink() throws {
        let json = try body(GeneralFeedbackRequest(
            category: .other, comment: "Hello", contactOk: false
        ))

        // Empty strings are what the server reads as "no briefing" (stored NULL),
        // matching the web help page.
        #expect(json["flight_id"] as? String == "")
        #expect(json["pack_timestamp"] as? String == "")
        #expect(json["contact_ok"] as? Bool == false)
    }
}
