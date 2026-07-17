//
//  FlightSharingModelTests.swift
//  flyfun-weatherTests
//
//  Pure-logic tests for the flight-sharing computed properties on
//  `FlightResponse` (#446): `isShareable` — the client mirror of the server's
//  owner-only, non-private, code-present share-affordance gate — and
//  `hasSubscribed` — the optional-tolerant fold that flips the preview banner
//  button. These are privacy-relevant booleans (a wrong `isShareable` would
//  offer a link that 404s the recipient), so pin every permutation rather than
//  trusting them as display formatting.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite struct FlightShareableTests {
    // Owner, not private, code present → the one shareable combination.
    @Test func ownerNonPrivateWithCodeIsShareable() {
        let f = makeFlight(private: false, role: .owner, shareCode: "abc123")
        #expect(f.isShareable)
    }

    // Subscriber never shares — even a non-private flight with a code is the
    // owner's to share, not theirs.
    @Test func subscriberIsNeverShareable() {
        let f = makeFlight(private: false, role: .subscriber, shareCode: "abc123")
        #expect(!f.isShareable)
    }

    // Private owner flight is not shareable: a recipient of a private link 404s.
    @Test func privateOwnerFlightIsNotShareable() {
        let f = makeFlight(private: true, role: .owner, shareCode: "abc123")
        #expect(!f.isShareable)
    }

    // No minted share code (very old row / nil) → nothing to link to.
    @Test func missingShareCodeIsNotShareable() {
        let nilCode = makeFlight(private: false, role: .owner, shareCode: nil)
        let emptyCode = makeFlight(private: false, role: .owner, shareCode: "")
        #expect(!nilCode.isShareable)
        #expect(!emptyCode.isShareable)
    }

    // Absent role (older server) is treated as owner via `isEditable`, so a
    // non-private flight with a code is still shareable.
    @Test func absentRoleTreatedAsOwner() {
        let f = makeFlight(private: false, role: nil, shareCode: "abc123")
        #expect(f.isShareable)
    }
}

@Suite struct FlightHasSubscribedTests {
    @Test func subscribedTrue() {
        #expect(makeFlight(isSubscribed: true).hasSubscribed)
    }

    @Test func subscribedFalse() {
        #expect(!makeFlight(isSubscribed: false).hasSubscribed)
    }

    // Absent on older servers → treated as not-subscribed.
    @Test func subscribedNilTreatedAsFalse() {
        #expect(!makeFlight(isSubscribed: nil).hasSubscribed)
    }
}
