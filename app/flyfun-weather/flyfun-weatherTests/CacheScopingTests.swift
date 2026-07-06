//
//  CacheScopingTests.swift
//  flyfun-weatherTests
//
//  Per-user cache scoping. The cache-first cold start (#359) made a pre-existing
//  gap user-visible: the briefing cache and offline PIREP queue lived at a single
//  shared path, so after user A logged out and user B logged in, B's cold-start
//  seed read A's `flights.json` and A's cached briefings sat readable on disk.
//  These tests cover the four moving parts:
//    1. subject extraction from the (unverified) JWT `sub` claim,
//    2. the SHA-256 scope key,
//    3. per-scope on-disk isolation,
//    4. legacy-layout migration + the cross-user inactivity janitor.
//

import Foundation
import Testing
@testable import flyfun_weather

/// JWT-shaped string with the given payload. Signature is never checked (scoping
/// reads the payload unverified — authorization stays server-side).
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

@Suite("unverifiedSubject")
struct UnverifiedSubjectTests {

    @Test func extractsStringSubject() {
        #expect(AppState.unverifiedSubject(of: makeJWT(payload: ["sub": "user-42"])) == "user-42")
    }

    @Test func coercesNumericSubject() {
        // Some issuers encode the user id as an integer.
        #expect(AppState.unverifiedSubject(of: makeJWT(payload: ["sub": 42])) == "42")
    }

    @Test func missingSubjectReturnsNil() {
        #expect(AppState.unverifiedSubject(of: makeJWT(payload: ["scope": "review"])) == nil)
    }

    @Test func emptySubjectReturnsNil() {
        #expect(AppState.unverifiedSubject(of: makeJWT(payload: ["sub": ""])) == nil)
    }

    @Test func nonJWTReturnsNil() {
        #expect(AppState.unverifiedSubject(of: "not-a-jwt") == nil)
    }

    @Test func scopeStillReadsAfterRefactor() {
        // The shared payload decoder must not regress the scope reader.
        #expect(AppState.unverifiedScope(of: makeJWT(payload: ["sub": "u1", "scope": "review"])) == "review")
    }
}

@Suite("cacheScope")
struct CacheScopeTests {

    @Test func deterministicAndHexOnly() {
        let a = AppState.cacheScope(forToken: makeJWT(payload: ["sub": "user-1"]))
        let b = AppState.cacheScope(forToken: makeJWT(payload: ["sub": "user-1"]))
        #expect(a == b)
        #expect(a.count == 16)
        #expect(a.allSatisfy { $0.isHexDigit })
    }

    @Test func distinctSubjectsDistinctScopes() {
        let a = AppState.cacheScope(forToken: makeJWT(payload: ["sub": "user-1"]))
        let b = AppState.cacheScope(forToken: makeJWT(payload: ["sub": "user-2"]))
        #expect(a != b)
    }

    @Test func fallsBackToAnonWithoutSubject() {
        #expect(AppState.cacheScope(forToken: makeJWT(payload: ["scope": "review"])) == "anon")
        #expect(AppState.cacheScope(forToken: "garbage") == "anon")
    }
}

@Suite("BriefingCacheStore scoping")
struct CacheStoreScopingTests {

    @Test func perScopeDirsAreIsolated() async throws {
        let base = makeTempDir(); defer { try? FileManager.default.removeItem(at: base) }
        let storeA = BriefingCacheStore(cacheDir: BriefingCacheStore.scopedCacheDir(base: base, scope: "aaaa"))
        let storeB = BriefingCacheStore(cacheDir: BriefingCacheStore.scopedCacheDir(base: base, scope: "bbbb"))

        try await storeA.writeMetadata(Data("[{\"id\":\"a\"}]".utf8), name: "flights")

        // B must not see A's flight list; A still reads its own.
        #expect(await storeB.readMetadata(name: "flights") == nil)
        #expect(await storeA.readMetadata(name: "flights") != nil)
    }

    @Test func migrateLegacyLayoutRemovesRootArtifactsKeepsUsers() throws {
        let base = makeTempDir(); defer { try? FileManager.default.removeItem(at: base) }
        let fm = FileManager.default
        let root = base.appendingPathComponent("BriefingCache", isDirectory: true)
        let users = root.appendingPathComponent("users", isDirectory: true)
        try fm.createDirectory(at: users, withIntermediateDirectories: true)

        // Pre-scoping artifacts sitting directly under BriefingCache/.
        try Data("[]".utf8).write(to: root.appendingPathComponent("flights.json"))
        try Data("{}".utf8).write(to: root.appendingPathComponent("index.json"))
        let legacyFlightDir = root.appendingPathComponent("flt-1", isDirectory: true)
        try fm.createDirectory(at: legacyFlightDir, withIntermediateDirectories: true)

        // A scoped user's data that must survive.
        let scoped = users.appendingPathComponent("aaaa", isDirectory: true)
        try fm.createDirectory(at: scoped, withIntermediateDirectories: true)
        let survivor = scoped.appendingPathComponent("flights.json")
        try Data("[]".utf8).write(to: survivor)

        BriefingCacheStore.migrateLegacyLayout(base: base)

        #expect(!fm.fileExists(atPath: root.appendingPathComponent("flights.json").path))
        #expect(!fm.fileExists(atPath: root.appendingPathComponent("index.json").path))
        #expect(!fm.fileExists(atPath: legacyFlightDir.path))
        #expect(fm.fileExists(atPath: survivor.path))

        // Idempotent: a second run leaves the users subtree intact.
        BriefingCacheStore.migrateLegacyLayout(base: base)
        #expect(fm.fileExists(atPath: survivor.path))
    }

    @Test func evictInactiveUsersRemovesOnlyStaleOtherUsers() throws {
        let base = makeTempDir(); defer { try? FileManager.default.removeItem(at: base) }
        let fm = FileManager.default

        func makeUserDir(_ scope: String, markerAgeDays: Double?) throws {
            let dir = BriefingCacheStore.scopedCacheDir(base: base, scope: scope)
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
            if let age = markerAgeDays {
                let marker = dir.appendingPathComponent("flights.json")
                try Data("[]".utf8).write(to: marker)
                let date = Date().addingTimeInterval(-age * 86_400)
                try fm.setAttributes([.modificationDate: date], ofItemAtPath: marker.path)
            }
        }

        try makeUserDir("current", markerAgeDays: 100)  // active — kept regardless of age
        try makeUserDir("stale", markerAgeDays: 60)     // other, old marker → evicted
        try makeUserDir("fresh", markerAgeDays: 3)      // other, recent marker → kept
        try makeUserDir("nomarker", markerAgeDays: nil) // no marker → dir mtime (recent) → kept

        BriefingCacheStore.evictInactiveUsers(base: base, keeping: "current", olderThanDays: 30)

        func exists(_ scope: String) -> Bool {
            fm.fileExists(atPath: BriefingCacheStore.scopedCacheDir(base: base, scope: scope).path)
        }
        #expect(exists("current"))
        #expect(!exists("stale"))
        #expect(exists("fresh"))
        #expect(exists("nomarker"))
    }
}
