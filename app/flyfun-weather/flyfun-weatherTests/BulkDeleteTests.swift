//
//  BulkDeleteTests.swift
//  flyfun-weatherTests
//
//  Multi-select + bulk delete (#553). Three layers, all reachable without a
//  network: the chunking/merge around the server's 200-id cap, the caching
//  repository's eviction rule (only ids the server *confirmed*), and the
//  sheet's selectable-flight gate.
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("BulkDeleteChunking")
struct BulkDeleteChunkingTests {

    private func ids(_ range: Range<Int>) -> [String] { range.map { "f\($0)" } }

    @Test("A request under the cap is sent as one chunk")
    func singleChunk() async throws {
        var sent: [[String]] = []
        let result = try await BulkDeleteResponse.sendChunked(ids: ids(0..<5)) { chunk in
            sent.append(chunk)
            return BulkDeleteResponse(deleted: chunk, notFound: [])
        }
        #expect(sent == [ids(0..<5)])
        #expect(result.deleted == ids(0..<5))
        #expect(result.notFound.isEmpty)
    }

    /// A "Select All" over a long logbook exceeds the server's `max_length=200`,
    /// which would 422 the whole request rather than delete anything.
    @Test("Over the cap, ids are split into 200-id chunks and the results merged")
    func splitsAtServerCap() async throws {
        var sent: [[String]] = []
        let all = ids(0..<450)
        let result = try await BulkDeleteResponse.sendChunked(ids: all) { chunk in
            sent.append(chunk)
            return BulkDeleteResponse(deleted: chunk, notFound: [])
        }
        #expect(sent.map(\.count) == [200, 200, 50])
        #expect(sent.flatMap { $0 } == all)
        // Merged in request order, so the caller sees one flat result.
        #expect(result.deleted == all)
    }

    @Test("An exact multiple of the cap makes no trailing empty request")
    func exactMultiple() async throws {
        var callCount = 0
        _ = try await BulkDeleteResponse.sendChunked(ids: ids(0..<400)) { chunk in
            callCount += 1
            return BulkDeleteResponse(deleted: chunk, notFound: [])
        }
        #expect(callCount == 2)
    }

    @Test("An empty selection never hits the network")
    func emptySendsNothing() async throws {
        var callCount = 0
        let result = try await BulkDeleteResponse.sendChunked(ids: []) { _ in
            callCount += 1
            return .empty
        }
        #expect(callCount == 0)
        #expect(result.deleted.isEmpty)
    }

    /// `not_found` is the server's owner-scoping channel (someone else's flight,
    /// or already deleted). It must survive the merge — a silently swallowed
    /// partial failure on a destructive action is the worst outcome.
    @Test("notFound entries propagate across chunks")
    func notFoundPropagates() async throws {
        let all = ids(0..<250)
        let result = try await BulkDeleteResponse.sendChunked(ids: all) { chunk in
            // First id of every chunk isn't the caller's.
            BulkDeleteResponse(deleted: Array(chunk.dropFirst()), notFound: [chunk[0]])
        }
        #expect(result.notFound == ["f0", "f200"])
        #expect(result.deleted.count == 248)
    }

    /// Chunks aren't one transaction: whatever an earlier chunk confirmed is
    /// already gone server-side. Losing that to the thrown error would strand
    /// those flights in the local list — tappable, and 404 on open.
    @Test("A failure part-way through reports what earlier chunks deleted")
    func partialFailureCarriesConfirmedIds() async throws {
        var callCount = 0
        do {
            _ = try await BulkDeleteResponse.sendChunked(ids: ids(0..<450)) { chunk in
                callCount += 1
                if callCount == 3 { throw MockError.injected("server down") }
                return BulkDeleteResponse(deleted: chunk, notFound: [])
            }
            Issue.record("expected the third chunk to fail")
        } catch let failure as BulkDeletePartialFailure {
            #expect(failure.partial.deleted == ids(0..<400))
            #expect(failure.underlying is MockError)
        }
    }

    /// Nothing confirmed yet → nothing to salvage, so the caller sees the plain
    /// error rather than an empty partial wrapper it would have to unwrap.
    @Test("A failure on the first chunk rethrows the original error")
    func firstChunkFailureRethrows() async throws {
        await #expect(throws: MockError.self) {
            _ = try await BulkDeleteResponse.sendChunked(ids: ids(0..<450)) { _ in
                throw MockError.injected("server down")
            }
        }
    }

    @Test("Decodes the server's snake_case not_found")
    func decodesWireShape() throws {
        let json = """
        {"deleted": ["a", "b"], "not_found": ["c"]}
        """
        let decoded = try JSONDecoder.weatherBrief.decode(BulkDeleteResponse.self, from: Data(json.utf8))
        #expect(decoded.deleted == ["a", "b"])
        #expect(decoded.notFound == ["c"])
    }
}

@Suite("BulkDeleteCacheEviction")
struct BulkDeleteCacheEvictionTests {

    private let all = CachedPackEntry.requiredEndpoints

    /// Three flights, each with a downloaded pack and a metadata sidecar.
    private func seededCache() async throws -> BriefingCacheStore {
        let store = BriefingCacheStore(cacheDir: makeTempDir())
        for id in ["f1", "f2", "f3"] {
            await store.registerDownload(flightId: id, timestamp: "t1", flightTitle: "T",
                                         assessment: nil, endpoints: all, totalBytes: 1)
            try await store.writeFlightMetadata(Data("{}".utf8), flightId: id, name: "flight")
        }
        return store
    }

    @Test("Confirmed deletes evict every pack and the sidecar directory")
    func evictsConfirmedIds() async throws {
        let cache = try await seededCache()
        let online = MockBriefingRepository()
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)

        let result = try await repo.bulkDeleteFlights(ids: ["f1", "f2"])

        #expect(result.deleted == ["f1", "f2"])
        for id in ["f1", "f2"] {
            #expect(await cache.hasCachedPacks(flightId: id) == false)
            #expect(await cache.readFlightMetadata(flightId: id, name: "flight") == nil)
        }
        // The unselected flight is untouched.
        #expect(await cache.hasCachedPacks(flightId: "f3"))
        #expect(await cache.readFlightMetadata(flightId: "f3", name: "flight") != nil)
    }

    /// A `not_found` id still exists server-side (it's someone else's flight, or
    /// the delete didn't take) — throwing away its offline packs would be data
    /// loss for a flight the user can still open.
    @Test("Eviction is restricted to the ids the server confirmed")
    func doesNotEvictNotFoundIds() async throws {
        let cache = try await seededCache()
        let online = MockBriefingRepository()
        online.bulkDeleteHandler = { ids in
            BulkDeleteResponse(deleted: ids.filter { $0 == "f1" },
                               notFound: ids.filter { $0 != "f1" })
        }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)

        let result = try await repo.bulkDeleteFlights(ids: ["f1", "f2"])

        #expect(result.notFound == ["f2"])
        #expect(await cache.hasCachedPacks(flightId: "f1") == false)
        #expect(await cache.hasCachedPacks(flightId: "f2"))
        #expect(await cache.readFlightMetadata(flightId: "f2", name: "flight") != nil)
    }

    @Test("A failed request leaves every local pack in place")
    func failureKeepsCache() async throws {
        let cache = try await seededCache()
        let online = MockBriefingRepository()
        online.bulkDeleteHandler = { _ in throw MockError.injected("server down") }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)

        await #expect(throws: MockError.self) {
            _ = try await repo.bulkDeleteFlights(ids: ["f1", "f2"])
        }

        #expect(await cache.hasCachedPacks(flightId: "f1"))
        #expect(await cache.hasCachedPacks(flightId: "f2"))
    }

    /// A chunked request that failed part-way still deleted flights server-side.
    /// Those packs are unreachable now (no refresh, no re-download), so they must
    /// be evicted even though the call threw — while the flights the failing
    /// chunk never reached keep theirs.
    @Test("A partial failure still evicts the ids that were confirmed")
    func partialFailureEvictsConfirmedIds() async throws {
        let cache = try await seededCache()
        let online = MockBriefingRepository()
        online.bulkDeleteHandler = { _ in
            throw BulkDeletePartialFailure(
                partial: BulkDeleteResponse(deleted: ["f1"], notFound: []),
                underlying: MockError.injected("server down")
            )
        }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)

        await #expect(throws: BulkDeletePartialFailure.self) {
            _ = try await repo.bulkDeleteFlights(ids: ["f1", "f2", "f3"])
        }

        #expect(await cache.hasCachedPacks(flightId: "f1") == false)
        #expect(await cache.readFlightMetadata(flightId: "f1", name: "flight") == nil)
        #expect(await cache.hasCachedPacks(flightId: "f2"))
        #expect(await cache.hasCachedPacks(flightId: "f3"))
    }
}

@Suite("BulkDeleteSelection")
struct BulkDeleteSelectionTests {

    static let now = ISO8601DateFormatter().date(from: "2026-07-15T12:00:00Z")!
    static func iso(_ offsetDays: Double) -> String {
        ISO8601DateFormatter().string(from: now.addingTimeInterval(offsetDays * 86400))
    }

    /// A subscriber's row must not be selectable at all, not merely fail on
    /// delete: the server 404s a subscriber's delete (they'd all land in
    /// `not_found`), and a shared flight is dropped with Unsubscribe instead.
    @Test("Subscribed flights are not selectable")
    func excludesSubscribers() {
        let flights = [
            makeFlight(id: "mine"),
            makeFlight(id: "shared", role: .subscriber),
            makeFlight(id: "legacy", role: nil),
        ]
        #expect(FlightSelectionView.selectable(flights).map(\.id) == ["mine", "legacy"])
    }

    /// "Select all past" ticks exactly the Past section — the same grouping the
    /// main list uses, so the two never disagree about what "past" means.
    @Test("Select-all-past covers the Past group only, minus subscribers")
    func pastIDsMatchTheGrouping() {
        let flights = [
            makeFlight(id: "future", departureTime: Self.iso(3)),
            makeFlight(id: "recent", departureTime: Self.iso(-2)),
            makeFlight(id: "old", departureTime: Self.iso(-30)),
            makeFlight(id: "old-shared", departureTime: Self.iso(-40), role: .subscriber),
        ]
        #expect(FlightSelectionView.pastIDs(flights, now: Self.now) == ["old"])
    }
}
