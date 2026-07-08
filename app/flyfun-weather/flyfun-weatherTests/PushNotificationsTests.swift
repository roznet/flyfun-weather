//
//  PushNotificationsTests.swift
//  flyfun-weatherTests
//
//  Unit tests for PushSupport — the pure APNs helpers (token hex encoding and
//  tap-payload → PendingNavigation parsing) that back the app delegate. Kept
//  device-free so the deep-link routing and token format can't silently regress.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("PushSupport")
struct PushSupportTests {

    @Test func hexEncodeLowercasePadded() {
        // 0x00 and 0x0f must render as "00" and "0f" (zero-padded, lowercase).
        let data = Data([0x00, 0x0f, 0xab, 0xff])
        #expect(PushSupport.hexEncode(data) == "000fabff")
    }

    @Test func hexEncodeEmpty() {
        #expect(PushSupport.hexEncode(Data()) == "")
    }

    @Test func pendingNavigationFromBriefingPayload() {
        let userInfo: [AnyHashable: Any] = [
            "flight_id": "egtf-lfat-2026-07-10-abc",
            "timestamp": "2026-07-08T10:00:00Z",
            "aps": ["alert": ["title": "EGTF → LFAT"]],
        ]
        #expect(
            PushSupport.pendingNavigation(from: userInfo)
                == .briefing(flightId: "egtf-lfat-2026-07-10-abc")
        )
    }

    @Test func pendingNavigationNilForSilentBadgePush() {
        // A silent badge-sync push carries no flight_id → no navigation.
        let userInfo: [AnyHashable: Any] = ["aps": ["content-available": 1, "badge": 2]]
        #expect(PushSupport.pendingNavigation(from: userInfo) == nil)
    }

    @Test func pendingNavigationNilForEmptyFlightId() {
        #expect(PushSupport.pendingNavigation(from: ["flight_id": ""]) == nil)
    }

    @Test func pendingNavigationNilForWrongType() {
        #expect(PushSupport.pendingNavigation(from: ["flight_id": 42]) == nil)
    }

    @Test func environmentIsSandboxUnderDebug() {
        // Tests build in DEBUG, so the reported environment must be sandbox
        // (a debug build's token is APNs-sandbox).
        #expect(PushSupport.environment == "sandbox")
    }
}
