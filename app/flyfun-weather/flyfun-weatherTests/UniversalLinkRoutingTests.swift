//
//  UniversalLinkRoutingTests.swift
//  flyfun-weatherTests
//
//  Unit tests for AppState.navigationTarget(for:) — the pure parser that turns an
//  inbound Universal Link into a PendingNavigation. It backs the Smart App Banner
//  "Open" deep link and tapped https://weather.flyfun.aero/briefing.html?flight=<id>
//  links. Only the briefing path with a non-empty `flight` param routes; the auth
//  callback and everything else must return nil so onOpenURL falls through to the
//  auth-callback handler.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("navigationTarget")
struct UniversalLinkRoutingTests {

    private func url(_ s: String) -> URL { URL(string: s)! }

    @Test func routesBriefingLinkToFlight() {
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html?flight=egtk_lsgs-2026-02-21"))
        #expect(target == .briefing(flightId: "egtk_lsgs-2026-02-21"))
    }

    @Test func routesSimpleNumericFlightId() {
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html?flight=123"))
        #expect(target == .briefing(flightId: "123"))
    }

    @Test func routesFeedbackEmailLinkWithPackTimestamp() {
        // The feedback/admin notification email appends `&t=<pack ISO timestamp>`
        // (with an unencoded `+00:00` offset) to pin the pack on the web. The app
        // ignores `t` and must still route to the flight.
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html?flight=eglm_sopit_disit_egbo-2026-07-25-781c&t=2026-07-22T16:07:05.119539+00:00"))
        #expect(target == .briefing(flightId: "eglm_sopit_disit_egbo-2026-07-25-781c"))
    }

    @Test func picksFlightParamAmongOthers() {
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html?theme=dark&flight=abc&x=1"))
        #expect(target == .briefing(flightId: "abc"))
    }

    @Test func authCallbackIsNotABriefingLink() {
        // The auth-callback universal link must fall through (nil) so onOpenURL
        // hands it to handleAuthCallback.
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/auth/callback?code=x&state=y")) == nil)
    }

    @Test func missingFlightParamReturnsNil() {
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html")) == nil)
    }

    @Test func emptyFlightParamReturnsNil() {
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/briefing.html?flight=")) == nil)
    }

    @Test func foreignHostReturnsNil() {
        // A look-alike host must never route — only our own domain is trusted.
        #expect(AppState.navigationTarget(
            for: url("https://evil.example.com/briefing.html?flight=123")) == nil)
    }

    @Test func otherPathOnOurHostReturnsNil() {
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/index.html?flight=123")) == nil)
    }

    @Test func customSchemeReturnsNil() {
        // The reviewer-token custom-scheme deep link is handled elsewhere.
        #expect(AppState.navigationTarget(
            for: url("flyfunweather://auth?token=abc")) == nil)
    }

    // MARK: - Share links (#446)

    @Test func routesShareLinkToShareCode() {
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/s/aB3xy7Q9"))
        #expect(target == .share(code: "aB3xy7Q9"))
    }

    @Test func shareLinkWithPackQueryStillRoutes() {
        // The web appends `?pack=` to pin a specific briefing; the code parse
        // ignores the query, and the preview loads the latest pack anyway.
        let target = AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/s/aB3xy7Q9?pack=2026-04-30T08:00:00Z"))
        #expect(target == .share(code: "aB3xy7Q9"))
    }

    @Test func shortShareCodeReturnsNil() {
        // Below the 4-char minimum — reject before presenting an empty preview.
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/s/ab")) == nil)
    }

    @Test func shareCodeWithExtraPathSegmentReturnsNil() {
        // A nested path isn't a valid single-segment code.
        #expect(AppState.navigationTarget(
            for: url("https://weather.flyfun.aero/s/abc/def")) == nil)
    }

    @Test func shareLinkOnForeignHostReturnsNil() {
        #expect(AppState.navigationTarget(
            for: url("https://evil.example.com/s/aB3xy7Q9")) == nil)
    }

    @Test func pendingNavigationRoundTripsShare() {
        // The cold-launch-safe store must round-trip a share target so a link
        // that arrives before AppState/list exists (or across a sign-in) resumes.
        PendingNavigationStore.set(.share(code: "aB3xy7Q9"))
        #expect(PendingNavigationStore.take() == .share(code: "aB3xy7Q9"))
    }

    @Test func shareCodeShapeValidation() {
        #expect(AppState.isValidShareCode("aB3xy7Q9"))
        #expect(AppState.isValidShareCode("abcd"))
        #expect(!AppState.isValidShareCode("abc"))          // too short
        #expect(!AppState.isValidShareCode(String(repeating: "a", count: 17)))  // too long
        #expect(!AppState.isValidShareCode("abc-123"))      // illegal char
        #expect(!AppState.isValidShareCode("abc/def"))      // path separator
    }
}
