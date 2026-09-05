import Foundation
import Testing
@testable import flyfun_weather

private func cacheSnapshot(_ motion: RawObservedMotion, marker: String = "keep") -> Data {
    Data("{\"root_unknown\":\"\(marker)\",\"observed_motion\":\(String(decoding: motion.rawJSON, as: UTF8.self))}".utf8)
}

private func cacheBundle(_ snapshot: Data) -> Data {
    Data("{\"snapshot\":\(String(decoding: snapshot, as: UTF8.self)),\"advisories\":{},\"digest\":{},\"route-analyses\":{},\"elevation\":{}}".utf8)
}

private func realtimeMotionEvent(_ motion: RawObservedMotion) throws -> RefreshEvent {
    let data = Data("""
    {"type":"complete","stage":null,"detail":null,"label":null,"progress":null,"pack":null,"elapsed_seconds":1,"refresh_decision":{"mode":"realtime","reason":null,"eta_useful":null,"pending_models":null},"observations":null,"sigmets":null,"observed":null,"observed_motion":\(String(decoding: motion.rawJSON, as: UTF8.self)),"message":null}
    """.utf8)
    return try RefreshEvent.decodePreservingObservedMotion(from: data, capability: .enabled)
}

private func realtimeObservationsOnlyEvent() throws -> RefreshEvent {
    let data = Data(#"{"type":"complete","refresh_decision":{"mode":"realtime"},"observations":{},"observed_motion":null}"#.utf8)
    return try RefreshEvent.decodePreservingObservedMotion(from: data, capability: .enabled)
}

@Suite("Observed motion cache durability")
struct ObservedMotionCacheTests {
    @Test func newerUnavailableSurvivesOlderFullBundleAndUnknownFieldsRemain() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let first = await store.beginPackWrite(flightId: "f1", timestamp: "p1")
        try await store.writeDownloadedBundle(
            cacheBundle(cacheSnapshot(observedMotionFixture(revision: 9, status: "unavailable", runID: nil, expiresAt: nil), marker: "new")),
            token: first, flightTitle: "Flight", assessment: nil)

        let older = await store.beginPackWrite(flightId: "f1", timestamp: "p1")
        try await store.writeDownloadedBundle(
            cacheBundle(cacheSnapshot(observedMotionFixture(revision: 8), marker: "old")),
            token: older, flightTitle: "Flight", assessment: nil)

        let saved = try #require(await store.readData(flightId: "f1", timestamp: "p1", endpoint: "snapshot"))
        let raw = try #require(RawJSONDocument(saved).member(named: "observed_motion"))
        #expect(RawObservedMotion(rawJSON: raw).revision == 9)
        #expect(String(decoding: saved, as: UTF8.self).contains(#""root_unknown":"old""#))
    }

    @Test func sameRevisionConflictIsReported() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        try await store.writeSnapshotData(cacheSnapshot(observedMotionFixture(revision: 8)),
                                          flightId: "f1", timestamp: "p1", allowCreate: true)
        let conflicting = observedMotionFixture(revision: 8, computedAt: "2026-09-05T10:05:00Z")
        await #expect(throws: ObservedMotionCacheError.self) {
            try await store.writeSnapshotData(cacheSnapshot(conflicting),
                                              flightId: "f1", timestamp: "p1", allowCreate: true)
        }
    }

    @Test func realtimePatchPreservesUnknownRawMotionKey() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        try await store.writeSnapshotData(
            cacheSnapshot(observedMotionFixture(revision: 8, extraRoot: #", "Future_ID_Key":{"MiXeD_Value":7}"#)),
            flightId: "f1", timestamp: "p1", allowCreate: true)
        await store.registerDownload(flightId: "f1", timestamp: "p1", flightTitle: "Flight",
                                     assessment: nil, endpoints: CachedPackEntry.requiredEndpoints, totalBytes: 1)
        let event = try realtimeMotionEvent(
            observedMotionFixture(revision: 9, extraRoot: #", "Future_ID_Key":{"MiXeD_Value":9}"#))
        try await store.patchRealtimeSnapshot(event, flightId: "f1", timestamp: "p1")
        let saved = try #require(await store.readData(flightId: "f1", timestamp: "p1", endpoint: "snapshot"))
        #expect(String(decoding: saved, as: UTF8.self).contains(#""Future_ID_Key":{"MiXeD_Value":9}"#))
    }

    @Test func observationsOnlyPatchKeepsExactOpaqueMotionBytes() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let motion = observedMotionFixture(
            revision: 8, extraRoot: #", "Future_ID_Key" : { "MiXeD_Value" : 7 }"#)
        try await store.writeSnapshotData(
            cacheSnapshot(motion), flightId: "f1", timestamp: "p1", allowCreate: true)
        await store.registerDownload(
            flightId: "f1", timestamp: "p1", flightTitle: "Flight", assessment: nil,
            endpoints: CachedPackEntry.requiredEndpoints, totalBytes: 1)
        try await store.patchRealtimeSnapshot(
            realtimeObservationsOnlyEvent(), flightId: "f1", timestamp: "p1")
        let saved = try #require(
            await store.readData(flightId: "f1", timestamp: "p1", endpoint: "snapshot"))
        #expect(RawJSONDocument(saved).member(named: "observed_motion") == motion.rawJSON)
    }

    @Test func deletedPackRejectsLateBundleAndDoesNotRecreateDirectory() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let token = await store.beginPackWrite(flightId: "f1", timestamp: "2026:09")
        await store.deletePack(flightId: "f1", timestamp: "2026:09")
        await #expect(throws: ObservedMotionCacheError.self) {
            try await store.writeDownloadedBundle(cacheBundle(cacheSnapshot(observedMotionFixture(revision: 8))),
                                                  token: token, flightTitle: "Flight", assessment: nil)
        }
        #expect(await store.readData(flightId: "f1", timestamp: "2026:09", endpoint: "snapshot") == nil)
    }

    @Test func deletedDownloadedPackMakesLateRealtimeSaveFail() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        try await store.writeSnapshotData(cacheSnapshot(observedMotionFixture(revision: 8)),
                                          flightId: "f1", timestamp: "p1", allowCreate: true)
        await store.registerDownload(flightId: "f1", timestamp: "p1", flightTitle: "Flight",
                                     assessment: nil, endpoints: CachedPackEntry.requiredEndpoints, totalBytes: 1)
        await store.deletePack(flightId: "f1", timestamp: "p1")
        let event = try realtimeMotionEvent(observedMotionFixture(revision: 9))
        await #expect(throws: ObservedMotionCacheError.self) {
            try await store.patchRealtimeSnapshot(event, flightId: "f1", timestamp: "p1")
        }
    }

    @Test func diskSaveFailurePropagatesInsteadOfReportingDownloaded() async throws {
        let base = makeTempDir(); defer { try? FileManager.default.removeItem(at: base) }
        let blocked = base.appendingPathComponent("not-a-directory")
        try Data("blocked".utf8).write(to: blocked)
        let store = BriefingCacheStore(cacheDir: blocked)
        let token = await store.beginPackWrite(flightId: "f1", timestamp: "p1")
        var didThrow = false
        do {
            try await store.writeDownloadedBundle(cacheBundle(cacheSnapshot(observedMotionFixture(revision: 8))),
                                                  token: token, flightTitle: "Flight", assessment: nil)
        } catch {
            didThrow = true
        }
        #expect(didThrow)
        #expect(await store.isPackCached(flightId: "f1", timestamp: "p1") == false)
    }
}
