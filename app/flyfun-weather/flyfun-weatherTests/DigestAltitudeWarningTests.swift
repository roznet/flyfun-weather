//
//  DigestAltitudeWarningTests.swift
//  flyfun-weatherTests
//
//  The stale-altitude notice on the briefing's digest surfaces. Mirrors the web
//  twin `buildDigestAltitudeWarning` (web/ts/managers/briefing-ui.ts), which has
//  shown this since #259.
//
//  Background: editing a flight's cruise altitude answers
//  `invalidation = "advisories_only"` and only re-runs `advisories/recalculate`.
//  That is deliberate — a full pipeline run per altitude tweak would be wasteful —
//  but it leaves the LLM digest narrating the altitude the pack was built for. A
//  pilot reported exactly that: "I have set the altitude to 2000' but it keeps
//  talking about FL55".
//

import Foundation
import Testing
@testable import flyfun_weather

struct DigestAltitudeWarningTests {

    /// The pack fixture is built at 8,000 ft — the baseline both cases compare to.
    private let snapshot = FixtureBriefingData.snapshot

    @Test func staysSilentWhenTheFlightStillMatchesThePack() {
        #expect(DigestAltitudeWarning.stalePackAltitudeFt(
            snapshot: snapshot,
            flightCruiseAltitudeFt: snapshot.route.cruiseAltitudeFt) == nil)
    }

    @Test func reportsThePackAltitudeWhenTheFlightWasEditedDown() {
        // The reported case: pack built high, flight since re-planned lower.
        #expect(DigestAltitudeWarning.stalePackAltitudeFt(
            snapshot: snapshot, flightCruiseAltitudeFt: 2000) == 8000)
    }

    @Test func reportsThePackAltitudeWhenTheFlightWasEditedUp() {
        #expect(DigestAltitudeWarning.stalePackAltitudeFt(
            snapshot: snapshot, flightCruiseAltitudeFt: 12000) == 8000)
    }

    // Note: the altitude must come from the *snapshot*, never from
    // `AdvisoriesResponse.cruiseAltitudeFt` — `advisories/recalculate` rewrites
    // `route_advisories.json` to the new altitude, so an advisories-based
    // comparison could never differ and the warning would be silently dead.
    // `briefing.json` (this snapshot) is the one artifact that path leaves alone.
    // The helper's signature is what enforces that; there is nothing to assert.

    @Test func formatsAltitudesTheWayTheRestOfTheAppDoes() {
        #expect(DigestAltitudeWarning.flightLevel(5500) == "FL55")
        #expect(DigestAltitudeWarning.flightLevel(2000) == "FL20")
        #expect(DigestAltitudeWarning.flightLevel(8000) == "FL80")
    }
}
