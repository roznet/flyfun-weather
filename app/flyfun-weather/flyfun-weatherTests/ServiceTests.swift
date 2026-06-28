//
//  ServiceTests.swift
//  flyfun-weatherTests
//
//  Tier C (#314) — cache/offline service logic.
//
//  CachedPackEntry rules are pure (no I/O). The two on-disk actors are tested
//  against an injected temp directory (the `init(cacheDir:)` / `init(fileURL:)`
//  seams added for testability), so they never touch the shared app container
//  and can't pollute each other.
//
//  Deferred: CachingBriefingRepository's online→cache fallback chain wraps a
//  *concrete* OnlineBriefingRepository(APIClient), so faulting the online layer
//  needs a protocol-injection refactor — tracked as a follow-up, not done here.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - CachedPackEntry (pure "complete pack" rule + cache key)

@Suite struct CachedPackEntryTests {

    private func entry(_ endpoints: Set<String>) -> CachedPackEntry {
        CachedPackEntry(
            flightId: "flt-1", timestamp: "2026-06-24T09:00:00Z",
            flightTitle: "LFMD → LFML", assessment: "green",
            downloadedAt: Date(timeIntervalSince1970: 0),
            endpoints: endpoints, totalBytes: 1024
        )
    }

    @Test func isCompleteRequiresAllFiveEndpoints() {
        #expect(entry(CachedPackEntry.requiredEndpoints).isComplete)
        // Superset (extra endpoint) is still complete.
        #expect(entry(CachedPackEntry.requiredEndpoints.union(["gramet"])).isComplete)
        // Missing any one required endpoint → incomplete.
        for missing in CachedPackEntry.requiredEndpoints {
            #expect(entry(CachedPackEntry.requiredEndpoints.subtracting([missing])).isComplete == false)
        }
        #expect(entry([]).isComplete == false)
    }

    @Test func idIsFlightSlashTimestamp() {
        #expect(entry([]).id == "flt-1/2026-06-24T09:00:00Z")
    }
}

// MARK: - BriefingCacheStore (on-disk index + pack data, temp dir)

@Suite struct BriefingCacheStoreTests {

    private let all = CachedPackEntry.requiredEndpoints

    @Test func registerThenIsPackCachedReflectsCompleteness() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)

        await store.registerDownload(flightId: "f1", timestamp: "t1", flightTitle: "T",
                                     assessment: "green", endpoints: all, totalBytes: 10)
        #expect(await store.isPackCached(flightId: "f1", timestamp: "t1"))

        await store.registerDownload(flightId: "f2", timestamp: "t2", flightTitle: "T",
                                     assessment: nil, endpoints: ["advisories"], totalBytes: 5)
        #expect(await store.isPackCached(flightId: "f2", timestamp: "t2") == false)  // incomplete
    }

    @Test func dataRoundTrips() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let payload = Data("{\"x\":1}".utf8)

        try await store.writeData(payload, flightId: "f1", timestamp: "2026:01", endpoint: "advisories")
        let back = await store.readData(flightId: "f1", timestamp: "2026:01", endpoint: "advisories")
        #expect(back == payload)
        #expect(await store.readData(flightId: "f1", timestamp: "2026:01", endpoint: "digest") == nil)
    }

    @Test func metadataRoundTrips() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let payload = Data("[]".utf8)
        try await store.writeMetadata(payload, name: "flights")
        #expect(await store.readMetadata(name: "flights") == payload)
        #expect(await store.readMetadata(name: "missing") == nil)
    }

    @Test func indexPersistsAcrossStoreInstances() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store1 = BriefingCacheStore(cacheDir: dir)
        await store1.registerDownload(flightId: "f1", timestamp: "t1", flightTitle: "T",
                                      assessment: "amber", endpoints: all, totalBytes: 99)
        // A fresh store reading the same dir must load the persisted index.
        let store2 = BriefingCacheStore(cacheDir: dir)
        #expect(await store2.isPackCached(flightId: "f1", timestamp: "t1"))
        #expect(await store2.totalCacheSize() == 99)
    }

    @Test func deleteAndClearRemoveEntries() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        await store.registerDownload(flightId: "f1", timestamp: "t1", flightTitle: "T",
                                     assessment: nil, endpoints: all, totalBytes: 1)
        await store.registerDownload(flightId: "f2", timestamp: "t2", flightTitle: "T",
                                     assessment: nil, endpoints: all, totalBytes: 1)
        #expect(await store.cachedPacks().count == 2)

        await store.deletePack(flightId: "f1", timestamp: "t1")
        #expect(await store.isPackCached(flightId: "f1", timestamp: "t1") == false)
        #expect(await store.cachedPacks().count == 1)

        await store.clearAll()
        #expect(await store.cachedPacks().isEmpty)
    }
}

// MARK: - PirepOfflineStore (offline queue, temp file)

@Suite struct PirepOfflineStoreTests {

    @Test func enqueueIncrementsPendingCount() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let s = PirepOfflineStore(fileURL: dir.appendingPathComponent("pending.json"))
        #expect(await s.pendingCount == 0)
        await s.enqueue(makePirepRequest(remarks: "a"))
        await s.enqueue(makePirepRequest(remarks: "b"))
        #expect(await s.pendingCount == 2)
    }

    @Test func syncDrainsOnSuccess() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let s = PirepOfflineStore(fileURL: dir.appendingPathComponent("pending.json"))
        await s.enqueue(makePirepRequest())
        await s.enqueue(makePirepRequest())
        let repo = MockBriefingRepository()
        repo.submitPirepsBatchResult = .success([PirepResponse.offline, PirepResponse.offline])

        let synced = await s.sync(using: repo)
        #expect(synced == 2)
        #expect(await s.pendingCount == 0)            // queue cleared
        #expect(repo.submitPirepsBatchCallCount == 1) // single batch call
    }

    @Test func syncKeepsQueueOnFailure() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let s = PirepOfflineStore(fileURL: dir.appendingPathComponent("pending.json"))
        await s.enqueue(makePirepRequest())
        let repo = MockBriefingRepository()
        repo.submitPirepsBatchResult = .failure(MockError.injected("offline"))

        let synced = await s.sync(using: repo)
        #expect(synced == 0)
        #expect(await s.pendingCount == 1)            // unsent, still queued
    }

    @Test func syncEmptyQueueIsNoOp() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let s = PirepOfflineStore(fileURL: dir.appendingPathComponent("pending.json"))
        let repo = MockBriefingRepository()
        let synced = await s.sync(using: repo)
        #expect(synced == 0)
        #expect(repo.submitPirepsBatchCallCount == 0) // never hits the network
    }

    @Test func queuePersistsAcrossStoreInstances() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("pending.json")
        let s1 = PirepOfflineStore(fileURL: file)
        await s1.enqueue(makePirepRequest(remarks: "persisted"))
        // A fresh store on the same file must load the queued PIREP.
        let s2 = PirepOfflineStore(fileURL: file)
        await s2.load()
        #expect(await s2.pendingCount == 1)
    }
}
