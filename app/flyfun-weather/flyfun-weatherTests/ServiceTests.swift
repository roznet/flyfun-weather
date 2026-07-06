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
//  CachingBriefingRepository's online→cache fallback chain is exercised in
//  ViewModelTests' "cache-first cold start" suite: the #359 refactor made the
//  repo wrap `any BriefingRepository`, so `makeForTesting(online:cache:)` faults
//  the online layer with a `MockBriefingRepository`. This file keeps the on-disk
//  cache/eviction coverage that never needs the network.
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

    /// Index entries written before `departureTime` existed must still decode
    /// (the field is optional → nil), so a cache upgrade doesn't wipe the index.
    @Test func decodesLegacyEntryWithoutDepartureTime() throws {
        let legacy = Data("""
        {"flightId":"f1","timestamp":"t1","flightTitle":"T","assessment":"green",
         "downloadedAt":0,"endpoints":["advisories"],"totalBytes":10}
        """.utf8)
        let decoded = try JSONDecoder().decode(CachedPackEntry.self, from: legacy)
        #expect(decoded.departureTime == nil)
        #expect(decoded.flightId == "f1")
    }

    @Test func departureTimeRoundTrips() throws {
        let e = CachedPackEntry(
            flightId: "f1", timestamp: "t1", flightTitle: "T", assessment: nil,
            downloadedAt: Date(timeIntervalSince1970: 0), endpoints: [], totalBytes: 0,
            departureTime: "2026-06-24T12:00:00Z"
        )
        let back = try JSONDecoder().decode(CachedPackEntry.self, from: JSONEncoder().encode(e))
        #expect(back.departureTime == "2026-06-24T12:00:00Z")
    }
}

// MARK: - AutoDownloadMode (pure connectivity gate)

@Suite struct AutoDownloadModeTests {

    @Test func offNeverDownloads() {
        #expect(AutoDownloadMode.off.allows(isOnWiFi: true, isConnected: true) == false)
    }

    @Test func wifiOnlyRequiresWiFi() {
        #expect(AutoDownloadMode.wifiOnly.allows(isOnWiFi: true, isConnected: true))
        // Connected but on cellular (not Wi-Fi) → blocked.
        #expect(AutoDownloadMode.wifiOnly.allows(isOnWiFi: false, isConnected: true) == false)
        #expect(AutoDownloadMode.wifiOnly.allows(isOnWiFi: false, isConnected: false) == false)
    }

    @Test func wifiAndCellularNeedsOnlyConnectivity() {
        #expect(AutoDownloadMode.wifiAndCellular.allows(isOnWiFi: false, isConnected: true))
        #expect(AutoDownloadMode.wifiAndCellular.allows(isOnWiFi: true, isConnected: true))
        // Fully offline → still nothing to download.
        #expect(AutoDownloadMode.wifiAndCellular.allows(isOnWiFi: false, isConnected: false) == false)
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

    /// `hasCachedPacks` tracks per-flight index membership (with the trailing-slash
    /// guard so `f1` doesn't match `f10`), and `removeFlightDirectory` clears the
    /// flight-level sidecar metadata that pack eviction would otherwise orphan.
    @Test func flightDirectoryCleanupRemovesOrphanedSidecars() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)

        // A flight with one downloaded pack plus its offline-recovery sidecar.
        await store.registerDownload(flightId: "f1", timestamp: "t1", flightTitle: "T",
                                     assessment: nil, endpoints: all, totalBytes: 1)
        try await store.writeFlightMetadata(Data("{}".utf8), flightId: "f1", name: "flight")
        // A second flight whose id has "f1" as a prefix — must not be confused.
        await store.registerDownload(flightId: "f10", timestamp: "t2", flightTitle: "T",
                                     assessment: nil, endpoints: all, totalBytes: 1)

        #expect(await store.hasCachedPacks(flightId: "f1"))
        #expect(await store.hasCachedPacks(flightId: "f10"))
        #expect(await store.hasCachedPacks(flightId: "f2") == false)

        // Evict f1's only pack → no index entry references f1 any more.
        await store.deletePack(flightId: "f1", timestamp: "t1")
        #expect(await store.hasCachedPacks(flightId: "f1") == false)
        #expect(await store.hasCachedPacks(flightId: "f10"))  // prefix flight untouched

        // The sidecar survives pack deletion (deletePack only removes the pack
        // subdir) — removeFlightDirectory is what clears the orphan.
        #expect(await store.readFlightMetadata(flightId: "f1", name: "flight") != nil)
        await store.removeFlightDirectory(flightId: "f1")
        #expect(await store.readFlightMetadata(flightId: "f1", name: "flight") == nil)
    }

    /// `flightDirectoryIDs` enumerates only the top-level flight subdirectories,
    /// skipping the root-level metadata files (index.json / flights.json) that
    /// sit beside them — this is the seam the viewed-only eviction sweep walks.
    @Test func flightDirectoryIDsListsOnlyFlightSubdirs() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)

        // Root-level metadata file (flights.json) — not a flight directory.
        try await store.writeMetadata(Data("[]".utf8), name: "flights")
        // A downloaded pack: creates f1/<timestamp>/… plus the root index.json.
        await store.registerDownload(flightId: "f1", timestamp: "2026-06-24T09:00:00Z",
                                     flightTitle: "T", assessment: nil, endpoints: all, totalBytes: 1)
        try await store.writeData(Data("{}".utf8), flightId: "f1",
                                  timestamp: "2026-06-24T09:00:00Z", endpoint: "advisories")
        // A viewed-only flight: sidecar directory, no index entry.
        try await store.writeFlightMetadata(Data("{}".utf8), flightId: "f2", name: "flight")

        let ids = Set(await store.flightDirectoryIDs())
        #expect(ids == ["f1", "f2"])            // both flight dirs are listed…
        #expect(!ids.contains("flights"))       // …but the root-level files are not
        #expect(!ids.contains("index"))
        #expect(!ids.contains("flights.json"))
    }
}

// MARK: - Cache eviction staleness (pure cutoff + legacy fallback)

@Suite struct PackStalenessTests {

    private let cutoff = Date(timeIntervalSince1970: 1_000_000)          // reference "now - N days"
    private func iso(_ t: TimeInterval) -> String {
        ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: t))
    }

    @Test func departureBeforeCutoffIsStale() {
        let stale = CachingBriefingRepository.isPackStale(
            entryDepartureTime: iso(500_000), fallbackDeparture: nil, cutoff: cutoff)
        #expect(stale == true)
    }

    @Test func departureAfterCutoffSurvives() {
        let stale = CachingBriefingRepository.isPackStale(
            entryDepartureTime: iso(2_000_000), fallbackDeparture: nil, cutoff: cutoff)
        #expect(stale == false)
    }

    /// Legacy entries lacking `departureTime` fall back to the cached flight record.
    @Test func legacyEntryUsesFallbackDeparture() {
        let stale = CachingBriefingRepository.isPackStale(
            entryDepartureTime: nil,
            fallbackDeparture: Date(timeIntervalSince1970: 500_000),
            cutoff: cutoff)
        #expect(stale == true)

        let fresh = CachingBriefingRepository.isPackStale(
            entryDepartureTime: nil,
            fallbackDeparture: Date(timeIntervalSince1970: 2_000_000),
            cutoff: cutoff)
        #expect(fresh == false)
    }

    /// No departure anywhere → nil (caller keeps the pack; never delete blind).
    @Test func unknownDepartureIsKept() {
        #expect(CachingBriefingRepository.isPackStale(
            entryDepartureTime: nil, fallbackDeparture: nil, cutoff: cutoff) == nil)
        // An unparseable timestamp with no fallback is likewise indeterminate.
        #expect(CachingBriefingRepository.isPackStale(
            entryDepartureTime: "not-a-date", fallbackDeparture: nil, cutoff: cutoff) == nil)
    }

    /// A present-but-unparseable `departureTime` falls through to the fallback.
    @Test func unparseableDepartureFallsBackToFlightRecord() {
        let stale = CachingBriefingRepository.isPackStale(
            entryDepartureTime: "garbage",
            fallbackDeparture: Date(timeIntervalSince1970: 500_000),
            cutoff: cutoff)
        #expect(stale == true)
    }
}

// MARK: - Cache eviction sweep (viewed-but-never-downloaded flights, temp dir)

/// End-to-end `pruneStalePacks` coverage for the on-disk directory sweep that
/// reclaims flight-level sidecars (`flight.json`, `packs.json`, …) written just
/// by *viewing* a flight — those flights never enter the pack index, so the
/// index-driven pack loop can't reach them. The repository is built with the
/// DEBUG cache-testing factory (its network layer is never touched here).
@Suite struct CacheEvictionSweepTests {

    private let all = CachedPackEntry.requiredEndpoints

    private func iso(daysFromNow days: Double) -> String {
        ISO8601DateFormatter().string(from: Date().addingTimeInterval(days * 86_400))
    }

    /// Viewed-only flight with an old departure → its directory is swept.
    @Test func viewedOnlyFlightWithOldDepartureIsSwept() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let repo = CachingBriefingRepository.makeForCacheTesting(cache: store)

        await repo.cacheFlightData(makeFlight(id: "old", departureTime: iso(daysFromNow: -30)))
        #expect(await store.readFlightMetadata(flightId: "old", name: "flight") != nil)

        await repo.pruneStalePacks(olderThanDays: 7)

        #expect(await store.readFlightMetadata(flightId: "old", name: "flight") == nil)
        #expect(await store.flightDirectoryIDs().contains("old") == false)
    }

    /// Today/future flights are never removed (departure >= cutoff).
    @Test func viewedOnlyFlightWithFutureDepartureIsKept() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let repo = CachingBriefingRepository.makeForCacheTesting(cache: store)

        await repo.cacheFlightData(makeFlight(id: "soon", departureTime: iso(daysFromNow: 3)))

        await repo.pruneStalePacks(olderThanDays: 7)

        #expect(await store.readFlightMetadata(flightId: "soon", name: "flight") != nil)
    }

    /// A flight with a live (recent) downloaded pack keeps its sidecars — the
    /// sweep skips any flight that still has index-tracked packs.
    @Test func flightWithLivePackKeepsSidecars() async {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let repo = CachingBriefingRepository.makeForCacheTesting(cache: store)

        // Recent departure so neither the pack nor the sidecar is stale.
        await store.registerDownload(flightId: "live", timestamp: "t1", flightTitle: "T",
                                     assessment: nil, endpoints: all, totalBytes: 1,
                                     departureTime: iso(daysFromNow: 1))
        await repo.cacheFlightData(makeFlight(id: "live", departureTime: iso(daysFromNow: 1)))

        await repo.pruneStalePacks(olderThanDays: 7)

        #expect(await store.readFlightMetadata(flightId: "live", name: "flight") != nil)
        #expect(await store.hasCachedPacks(flightId: "live"))
    }

    /// Indeterminate departure (a sidecar directory with no readable
    /// `flight.json`) is kept — never delete blind.
    @Test func indeterminateDepartureIsKept() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let repo = CachingBriefingRepository.makeForCacheTesting(cache: store)

        // A packs.json sidecar but no flight.json → no departure to read.
        try await store.writeFlightMetadata(Data("[]".utf8), flightId: "mystery", name: "packs")

        await repo.pruneStalePacks(olderThanDays: 7)

        #expect(await store.readFlightMetadata(flightId: "mystery", name: "packs") != nil)
        #expect(await store.flightDirectoryIDs().contains("mystery"))
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
