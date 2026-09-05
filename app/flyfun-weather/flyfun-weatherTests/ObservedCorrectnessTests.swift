import XCTest
@testable import flyfun_weather

@MainActor
final class ObservedCorrectnessTests: XCTestCase {
    func testAgeAdvancesFromValidTimeNotFrozenServerAge() {
        let time = "2026-08-26T13:00:00Z"
        let now = Date.parseISO8601("2026-08-26T13:05:00Z")!
        XCTAssertTrue(ObservedBadge.ageText(time, 1, "Radar", now: now).contains("5 min old"))
        let later = ObservedBadge.ageText(time, 1, "Radar", now: now.addingTimeInterval(3600))
        XCTAssertTrue(later.contains("65 min old"))
        XCTAssertTrue(later.contains("stale"))
        XCTAssertTrue(later.contains("2026-08-26"))
    }

    func testInvalidAndFutureTimesNeverClaimFreshness() {
        let now = Date.parseISO8601("2026-08-26T13:00:00Z")!
        XCTAssertTrue(ObservedBadge.ageText("broken", 0, now: now).contains("age unknown"))
        let future = ObservedBadge.ageText("2026-08-26T13:01:00Z", 0, now: now)
        XCTAssertTrue(future.contains("check clock"))
        XCTAssertFalse(future.contains("just now"))
    }

    func testSurfaceShowsBothSourcesWithTheirOwnWindowAndTime() throws {
        let observed = try XCTUnwrap(ObservedResolver.resolve(ObservedConditionsTests.decoded()))
        let labels = ObservedBadge.surfaceTexts(observed, now: Date.parseISO8601("2026-08-26T13:05:00Z")!)
        XCTAssertEqual(labels.count, 2)
        XCTAssertTrue(labels[0].contains("13:00Z"))
        XCTAssertTrue(labels[0].contains("10 min composite scan window"))
        XCTAssertTrue(labels[1].contains("12:50Z"))
        XCTAssertTrue(labels[1].contains("10 min accumulation"))
        XCTAssertTrue(labels[0].contains("12:50Z–13:00Z"))
        XCTAssertTrue(labels[1].contains("12:40Z–12:50Z"))
    }

    func testPartialCoverageRetainsDetectionsAndUnknownSurroundings() throws {
        let json = ObservedConditionsTests.payloadJSON.replacingOccurrences(
            of: "\"insufficient_coverage\": false", with: "\"insufficient_coverage\": true")
        let payload = try JSONDecoder.weatherBrief.decode(ObservedConditions.self, from: Data(json.utf8))
        let observed = try XCTUnwrap(ObservedResolver.resolve(payload))
        let point = observed.points[0]
        XCTAssertEqual(point.dbz, 42)
        XCTAssertEqual(point.topsHighestFt!, 37093, accuracy: 1)
        XCTAssertFalse(point.topsBins.isEmpty)
        XCTAssertTrue(point.radarNoCoverage)
        XCTAssertTrue(point.topsNoCoverage)
        var vizPoint = ObservedConditionsTests().vizPoint(0)
        vizPoint.observed = point
        let chips = CrossSectionReadoutView.observedChips(vizPoint, sources: observed, scrubAltitudeFt: 6500)
        XCTAssertTrue(chips.contains { $0.contains("42 dBZ") && $0.contains("partial coverage") })
        XCTAssertTrue(chips.contains { $0.contains("ft MSL") })
        XCTAssertFalse(chips.contains { $0.contains("no echo") })
    }

    func testMethodNineDoesNotAssertMultilayerAndIRIsNotVisibleOpacity() throws {
        let observed = try XCTUnwrap(ObservedResolver.resolve(ObservedConditionsTests.decoded()))
        var point = ObservedConditionsTests().vizPoint(0)
        point.observed = observed.points[0]
        let chips = CrossSectionReadoutView.observedChips(point, sources: observed, scrubAltitudeFt: 6500)
        XCTAssertFalse(chips.contains { $0.contains("multi-layer") || $0.contains("opaque") || $0.contains("thin") })
        XCTAssertTrue(chips.contains { $0.contains("IR effective cloudiness") })
        XCTAssertTrue(chips.contains { $0.contains("0.29 (decoded; scale unverified)") })
        XCTAssertTrue(chips.contains { $0.contains("valid retrieval samples") })
        XCTAssertFalse(chips.contains { $0.contains("of sky") })
        XCTAssertTrue(chips.contains { $0.contains("ft MSL") })
        XCTAssertTrue(chips.contains { $0.contains("≈FL380 pressure") })
        XCTAssertEqual(observed.points[0].topsBins[0].label, "6000–7000 ft MSL")
    }

    func testRefreshPersistsOnlyRealtimeFieldsAcrossRepositoryRecreation() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = BriefingCacheStore(cacheDir: dir)
        let old = Data("""
        {"route":{"name":"test","waypoints":[],"cruise_altitude_ft":6000,"flight_ceiling_ft":18000,"flight_duration_hours":1},"target_date":"2026-08-26","days_out":0,"unknown_future_field":{"keep":true},"route_sigmets":{"fetched_at":"old","sigmets":[]}}
        """.utf8)
        try await cache.writeData(old, flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","refresh_decision":{"mode":"realtime"},"observed":\(ObservedConditionsTests.payloadJSON)}
        """.utf8))
        let repo = CachingBriefingRepository.makeForTesting(online: MockBriefingRepository(), cache: cache)
        try await repo.persistRealtimeRefresh(event, flightId: "f1", timestamp: "pack")
        let reopenedCache = BriefingCacheStore(cacheDir: dir)
        let reopenedRepo = CachingBriefingRepository.makeForTesting(online: MockBriefingRepository(), cache: reopenedCache)
        let snapshot = try await reopenedRepo.snapshot(flightId: "f1", timestamp: "pack")
        XCTAssertEqual(snapshot.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")
        XCTAssertEqual(snapshot.route.name, "test")
        let bytes = await reopenedCache.readData(flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(bytes)) as? [String: Any])
        XCTAssertEqual((object["unknown_future_field"] as? [String: Bool])?["keep"], true)
        XCTAssertEqual((object["route_sigmets"] as? [String: Any])?["fetched_at"] as? String, "old")
        let absent = await reopenedCache.readData(flightId: "other", timestamp: "pack", endpoint: "snapshot")
        XCTAssertNil(absent)
    }

    func testSiriRefreshPersistsBeforeReportingCompleteAndSurfacesSaveFailure() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = BriefingCacheStore(cacheDir: dir)
        let original = try JSONEncoder.weatherBrief.encode(FixtureBriefingData.snapshot)
        try await cache.writeData(original, flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        try await cache.writeData(original, flightId: "f1", timestamp: "history", endpoint: "snapshot")
        let pack = try makePackMeta(flightId: "f1", fetchTimestamp: "pack")
        let packJSON = String(decoding: try JSONEncoder.weatherBrief.encode(pack), as: UTF8.self)
        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","pack":\(packJSON),"refresh_decision":{"mode":"realtime"},
         "observed":\(ObservedConditionsTests.payloadJSON)}
        """.utf8))
        let online = MockBriefingRepository()
        online.refreshEvents = [event]
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)
        let outcome = await RefreshDriver.run(repository: repo, flightId: "f1")
        XCTAssertEqual(outcome, .completed)
        let reopened = CachingBriefingRepository.makeForTesting(
            online: MockBriefingRepository(), cache: BriefingCacheStore(cacheDir: dir))
        let snapshot = try await reopened.snapshot(flightId: "f1", timestamp: "pack")
        XCTAssertEqual(snapshot.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")
        let history = await cache.readData(flightId: "f1", timestamp: "history", endpoint: "snapshot")
        XCTAssertEqual(history, original)

        try await cache.writeData(Data("corrupt".utf8), flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        let failure = await RefreshDriver.run(repository: repo, flightId: "f1")
        guard case .failed = failure else { return XCTFail("Siri falsely reported the offline refresh saved") }
    }

    func testViewModelRefreshSurvivesOfflineReopenAndKeepsForecastFields() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = BriefingCacheStore(cacheDir: dir)
        let flight = makeFlight(id: "f1")
        let pack = try makePackMeta(flightId: "f1", fetchTimestamp: "pack")
        let encoded: [(String, Data)] = [
            ("snapshot", try JSONEncoder.weatherBrief.encode(FixtureBriefingData.snapshot)),
            ("advisories", try JSONEncoder.weatherBrief.encode(FixtureBriefingData.advisories)),
            ("digest", try JSONEncoder.weatherBrief.encode(FixtureBriefingData.digest)),
            ("route-analyses", try JSONEncoder.weatherBrief.encode(FixtureBriefingData.routeAnalyses)),
            ("elevation", try JSONEncoder.weatherBrief.encode(FixtureBriefingData.elevation))
        ]
        for (endpoint, bytes) in encoded {
            try await cache.writeData(bytes, flightId: "f1", timestamp: "pack", endpoint: endpoint)
        }
        await cache.registerDownload(flightId: "f1", timestamp: "pack", flightTitle: "test",
                                     assessment: nil, endpoints: CachedPackEntry.requiredEndpoints,
                                     totalBytes: Int64(encoded.reduce(0) { $0 + $1.1.count }))
        let online = MockBriefingRepository()
        online.latestPackHandler = { pack }
        online.packsResult = .success([pack])
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: cache)
        let vm = BriefingViewModel(flight: flight, repository: repo)
        await vm.loadBriefing()
        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","refresh_decision":{"mode":"realtime"},
         "observations":{"fetch_time":"2026-08-26T13:01:00Z","airports":[]},
         "sigmets":{"fetch_time":"2026-08-26T13:02:00Z","sigmets":[]},
         "observed":\(ObservedConditionsTests.payloadJSON)}
        """.utf8))
        online.refreshEvents = [event]
        await vm.refresh()
        guard case .loaded(let refreshed) = vm.snapshotState else { return XCTFail("snapshot not loaded") }
        XCTAssertEqual(refreshed.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")
        let offline = MockBriefingRepository()
        offline.latestPackHandler = { throw MockError.injected("offline") }
        offline.packsResult = .failure(MockError.injected("offline"))
        let reopenedRepo = CachingBriefingRepository.makeForTesting(
            online: offline, cache: BriefingCacheStore(cacheDir: dir))
        let reopened = BriefingViewModel(flight: flight, repository: reopenedRepo)
        await reopened.loadBriefing()
        guard case .loaded(let snapshot) = reopened.snapshotState else { return XCTFail("offline reload failed") }
        XCTAssertEqual(snapshot.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")
        XCTAssertEqual(snapshot.routeObservations?.fetchTime, "2026-08-26T13:01:00Z")
        XCTAssertEqual(snapshot.routeSigmets?.fetchTime, "2026-08-26T13:02:00Z")
        XCTAssertEqual(snapshot.route.name, FixtureBriefingData.snapshot.route.name)

        // A late event for a different pack must not contaminate this screen.
        let changed = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","refresh_decision":{"mode":"realtime"},"observed":{"computed_at":"wrong pack"}}
        """.utf8))
        await reopened.applyRealtimeRefresh(changed, flightId: "f1", timestamp: "other-pack")
        guard case .loaded(let unchanged) = reopened.snapshotState else { return XCTFail("lost snapshot") }
        XCTAssertEqual(unchanged.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")

        // Failure to update disk must not leave a misleading downloaded/saved
        // state, even though the refreshed values remain available in memory.
        try await cache.writeData(Data("corrupt".utf8), flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        await reopened.applyRealtimeRefresh(event, flightId: "f1", timestamp: "pack")
        guard case .error = reopened.downloadState else { return XCTFail("offline save failure was hidden") }
        guard case .error = reopened.refreshState else { return XCTFail("refresh falsely reported fully saved") }
        guard case .loaded(let retained) = reopened.snapshotState else { return XCTFail("fresh data was discarded") }
        XCTAssertEqual(retained.observedConditions?.reflectivity?.validTime, "2026-08-26T13:00:00Z")
    }

    func testCachePatchSurfacesCorruptSnapshotAndDoesNotReplaceIt() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = BriefingCacheStore(cacheDir: dir)
        let corrupt = Data("not JSON".utf8)
        try await cache.writeData(corrupt, flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","refresh_decision":{"mode":"realtime"},"observed":{}}
        """.utf8))
        let repo = CachingBriefingRepository.makeForTesting(online: MockBriefingRepository(), cache: cache)
        do {
            try await repo.persistRealtimeRefresh(event, flightId: "f1", timestamp: "pack")
            XCTFail("Corrupt cache must not report a successful save")
        } catch { }
        let unchanged = await cache.readData(flightId: "f1", timestamp: "pack", endpoint: "snapshot")
        XCTAssertEqual(unchanged, corrupt)
    }

    func testMissingDownloadedSnapshotIsASaveFailureButUncachedPackIsNotCreated() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = BriefingCacheStore(cacheDir: dir)
        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        {"type":"complete","refresh_decision":{"mode":"realtime"},"observed":{}}
        """.utf8))
        let repo = CachingBriefingRepository.makeForTesting(online: MockBriefingRepository(), cache: cache)
        try await repo.persistRealtimeRefresh(event, flightId: "f1", timestamp: "uncached")
        let absent = await cache.readData(flightId: "f1", timestamp: "uncached", endpoint: "snapshot")
        XCTAssertNil(absent)
        await cache.registerDownload(flightId: "f1", timestamp: "missing", flightTitle: "test",
                                     assessment: nil, endpoints: CachedPackEntry.requiredEndpoints, totalBytes: 0)
        do {
            try await repo.persistRealtimeRefresh(event, flightId: "f1", timestamp: "missing")
            XCTFail("A downloaded pack's missing snapshot cannot be reported saved")
        } catch { }
    }
}
