//
//  ReviewTokenScopeTests.swift
//  flyfun-weatherTests
//
//  Unit tests for AppState.unverifiedScope(of:) — the routing gate that decides
//  whether an inbound bare-token deep link is the App Store reviewer link
//  (scope:"review"). Security-critical: it's the only path that accepts a
//  bare token in onOpenURL (H8 carve-out). The claim is read UNVERIFIED (no
//  signature check), so this only routes — authorization stays server-side.
//

import Foundation
import Testing
@testable import flyfun_weather

/// Build a JWT-shaped string with the given payload. The signature segment is
/// irrelevant here — `unverifiedScope` never checks it (that's the point).
private func makeJWT(payload: [String: Any]) -> String {
    func b64url(_ d: Data) -> String {
        d.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    let header = b64url(Data(#"{"alg":"HS256","typ":"JWT"}"#.utf8))
    let body = b64url(try! JSONSerialization.data(withJSONObject: payload))
    return "\(header).\(body).signature-not-checked"
}

@Suite("unverifiedScope")
struct ReviewTokenScopeTests {

    @Test func extractsReviewScope() {
        let token = makeJWT(payload: ["sub": "u1", "scope": "review"])
        #expect(AppState.unverifiedScope(of: token) == "review")
    }

    @Test func missingScopeReturnsNil() {
        let token = makeJWT(payload: ["sub": "u1", "email": "a@b.com"])
        #expect(AppState.unverifiedScope(of: token) == nil)
    }

    @Test func nonReviewScopeReturnsItsValue() {
        // A different scope must not read as "review" (the caller gates on ==).
        let token = makeJWT(payload: ["sub": "u1", "scope": "mcp"])
        #expect(AppState.unverifiedScope(of: token) == "mcp")
    }

    @Test func nonJWTShapedStringReturnsNil() {
        #expect(AppState.unverifiedScope(of: "not-a-jwt") == nil)
        #expect(AppState.unverifiedScope(of: "only.two") == nil)
    }

    @Test func nonJSONPayloadReturnsNil() {
        let token = "header.bm90LWpzb24.sig"  // "not-json" base64url, not JSON
        #expect(AppState.unverifiedScope(of: token) == nil)
    }

    @Test func handlesBase64PayloadNeedingPadding() {
        // A payload whose base64url length isn't a multiple of 4 exercises the
        // padding loop. {"scope":"review"} encodes to a length needing padding.
        let token = makeJWT(payload: ["scope": "review"])
        #expect(AppState.unverifiedScope(of: token) == "review")
    }

    @Test func scopeWrongTypeReturnsNil() {
        // A numeric `scope` is not a String → nil (never mistaken for "review").
        let token = makeJWT(payload: ["sub": "u1", "scope": 42])
        #expect(AppState.unverifiedScope(of: token) == nil)
    }
}
