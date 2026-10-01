//
//  LiveLayerTests.swift
//  flyfun-weatherTests
//
//  Live observation layer (#637): the DTO decode contract against
//  `models/live.py`, the snapshot patch gate (`BriefingViewModel.applyLive`),
//  the caching repository's network-first + newest-wins live cache, and the
//  sync path that notices new observations on an unchanged pack.
//
//  Fictional `ZZ` ICAO codes throughout.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Fixtures

private let packTS = "2026-06-24T09:00:00Z"

/// A live-layer payload as the server emits it. `packTimestamp` defaults to
/// the "+00:00" spelling of `packTS` on purpose — the same instant in a
/// different format, which the client must still match.
private func makeLiveLayer(
    packTimestamp: String = "2026-06-24T09:00:00+00:00",
    liveUpdatedAt: String? = "2026-06-24T10:00:00Z",
    withObservations: Bool = true,
    changeKey: String = "metar:ZZAA"
) throws -> LiveLayerResponse {
    let live = liveUpdatedAt.map { "\"\($0)\"" } ?? "null"
    let observations = withObservations ? """
    {
      "corridor_nm": 30.0,
      "fetch_time": "2026-06-24T09:58:00Z",
      "airports_with_metar": 1,
      "airports": [
        {
          "icao": "ZZAA", "has_metar": true, "has_taf": false,
          "metar_flight_category": "IFR", "metar_time": "2026-06-24T09:50:00Z",
          "metar_report_type": "SPECI",
          "metar_previous_flight_category": "VFR",
          "metar_previous_time": "2026-06-24T09:20:00Z",
          "taf_issue_time": "2026-06-24T05:00:00Z"
        }
      ]
    }
    """ : "null"
    let json = """
    {
      "flight_id": "ZZ-flt",
      "pack_timestamp": "\(packTimestamp)",
      "live_updated_at": \(live),
      "route_observations": \(observations),
      "observations_updated_at": "2026-06-24T09:58:00Z",
      "route_sigmets": null,
      "sigmets_updated_at": null,
      "observed_conditions": { "computed_at": "2026-06-24T09:58:00Z", "corridor_nm": 30.0, "summary": "No echoes" },
      "observed_updated_at": "2026-06-24T09:58:00Z",
      "changes": {
        "baseline_at": "2026-06-24T09:00:00Z",
        "computed_at": "2026-06-24T10:00:00Z",
        "changes": [
          {
            "key": "\(changeKey)", "kind": "metar_category", "source": "SPECI",
            "direction": "worse", "tier": "alert", "role": "departure",
            "icao": "ZZAA", "station_id": null, "from_value": "VFR", "to_value": "IFR",
            "observed_at": "2026-06-24T09:50:00Z", "enroute_distance_nm": 0.0,
            "message": "ZZAA SPECI: VFR → IFR", "new_alert": true
          }
        ],
        "worsened_count": 1, "improved_count": 0, "alert_count": 1
      },
      "last_refresh_delta": { "worsened": true, "messages": ["ZZAA SPECI: VFR → IFR"], "computed_at": "2026-06-24T10:00:00Z" }
    }
    """
    return try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(json.utf8))
}

/// A minimal D-0 snapshot with the pack's own (download-time) observations.
private func makeSnapshot(liveUpdatedAt: String? = nil, daysOut: Int = 0) throws -> SnapshotResponse {
    let live = liveUpdatedAt.map { "\"\($0)\"" } ?? "null"
    let json = """
    {
      "route": {
        "name": "ZZAA ZZBB", "waypoints": [],
        "cruise_altitude_ft": 8000, "flight_ceiling_ft": 13000, "flight_duration_hours": 1.5
      },
      "target_date": "2026-06-24",
      "days_out": \(daysOut),
      "route_observations": {
        "fetch_time": "2026-06-24T08:50:00Z",
        "airports": [ { "icao": "ZZAA", "has_metar": true, "metar_flight_category": "VFR" } ]
      },
      "route_sigmets": {
        "fetch_time": "2026-06-24T08:50:00Z",
        "sigmets": [ { "fir_id": "ZZZZ", "hazard": "TURB", "qualifier": "SEV", "raw_text": "ZZZZ SIGMET 3 VALID 240800/241200 ZZZZ-" } ]
      },
      "live_updated_at": \(live)
    }
    """
    return try JSONDecoder.weatherBrief.decode(SnapshotResponse.self, from: Data(json.utf8))
}

// MARK: - Decode contract

@Suite struct LiveLayerDecodeTests {

    @Test func decodesFullLayer() throws {
        let layer = try makeLiveLayer()
        #expect(layer.flightId == "ZZ-flt")
        #expect(layer.packTimestamp == "2026-06-24T09:00:00+00:00")
        #expect(layer.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(layer.hasData)
        #expect(layer.observationsUpdatedAt == "2026-06-24T09:58:00Z")
        #expect(layer.routeSigmets == nil)
        #expect(layer.observedConditions?.summary == "No echoes")

        let apt = try #require(layer.routeObservations?.airports?.first)
        #expect(apt.metarReportType == "SPECI")
        #expect(apt.metarPreviousFlightCategory == "VFR")
        #expect(apt.metarPreviousTime == "2026-06-24T09:20:00Z")
        #expect(apt.tafIssueTime == "2026-06-24T05:00:00Z")

        let changes = try #require(layer.changes)
        #expect(changes.baselineAt == "2026-06-24T09:00:00Z")
        #expect(changes.worsenedCount == 1)
        #expect(changes.alertCount == 1)
        let change = try #require(changes.items.first)
        #expect(change.key == "metar:ZZAA")
        #expect(change.kindValue == .metarCategory)
        #expect(change.directionValue == .worse)
        #expect(change.isAlert)
        #expect(change.sourceLabel == "SPECI")
        #expect(change.displayMessage == "ZZAA SPECI: VFR → IFR")
        #expect(change.newAlert == true)
        #expect(changes.changedAirportIcaos == ["ZZAA"])
        #expect(changes.airportDirection(icao: "ZZAA") == .worse)
        #expect(changes.airportDirection(icao: "ZZBB") == nil)

        #expect(layer.lastRefreshDelta?.worsened == true)
        #expect(layer.lastRefreshDelta?.messages == ["ZZAA SPECI: VFR → IFR"])
    }

    /// Before any live data exists the server returns every block null.
    @Test func decodesNullLayer() throws {
        let json = """
        { "flight_id": "ZZ-flt", "pack_timestamp": "2026-06-24T09:00:00Z", "live_updated_at": null,
          "route_observations": null, "route_sigmets": null, "observed_conditions": null,
          "changes": null, "last_refresh_delta": null }
        """
        let layer = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data(json.utf8))
        #expect(!layer.hasData)
        #expect(layer.routeObservations == nil)
        #expect(layer.changes == nil)
    }

    /// A kind / source / direction this client doesn't know must not fail the
    /// decode — it renders generically.
    @Test func unknownEnumValuesDecodeTolerantly() throws {
        let json = """
        { "key": "ash:ZZZZ", "kind": "volcanic_ash", "source": "VAAC", "direction": "sideways",
          "tier": "highlight", "role": "route", "message": "ZZZZ: VA" }
        """
        let change = try JSONDecoder.weatherBrief.decode(LiveChange.self, from: Data(json.utf8))
        #expect(change.kindValue == nil)
        #expect(change.directionValue == nil)
        #expect(change.sourceLabel == "VAAC")
        #expect(!change.isAlert)
        #expect(!change.isAirportChange)
    }

    @Test func packMetaAndRefreshEventCarryLiveFields() throws {
        let pack = try makePackMeta(fetchTimestamp: packTS, daysOut: 0, liveUpdatedAt: "2026-06-24T10:00:00Z")
        #expect(pack.liveUpdatedAt == "2026-06-24T10:00:00Z")
        let legacy = try makePackMeta()
        #expect(legacy.liveUpdatedAt == nil)

        let event = try JSONDecoder.weatherBrief.decode(RefreshEvent.self, from: Data("""
        { "type": "complete", "live_updated_at": "2026-06-24T10:00:00Z",
          "delta": { "worsened": false, "messages": [] },
          "changes": { "baseline_at": "2026-06-24T09:00:00Z", "computed_at": "2026-06-24T10:00:00Z", "changes": [] } }
        """.utf8))
        #expect(event.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(event.delta?.worsened == false)
        #expect(event.changes?.isEmpty == true)
    }
}

// MARK: - Pure helpers

@Suite struct LiveLayerHelperTests {

    @Test func sigmetKeyMatchesServerFormat() throws {
        let snapshot = try makeSnapshot()
        let sigmet = try #require(snapshot.routeSigmets?.matched.first)
        #expect(sigmet.sequenceId == "3")
        #expect(sigmet.liveChangeKey == "sigmet:ZZZZ|3")
        #expect(sigmet.matchesLiveChange(keys: ["sigmet:ZZZZ|3"]))
        #expect(!sigmet.matchesLiveChange(keys: ["sigmet:ZZZZ|4"]))
        #expect(!sigmet.matchesLiveChange(keys: []))
    }

    @Test func newestWinsOrdering() throws {
        let base = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:00:00Z")
        let newer = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:10:00.123456+00:00")
        let older = try makeLiveLayer(liveUpdatedAt: "2026-06-24T09:50:00Z")
        let empty = try makeLiveLayer(liveUpdatedAt: nil)
        let nextPack = try makeLiveLayer(packTimestamp: "2026-06-24T12:00:00Z", liveUpdatedAt: "2026-06-24T09:00:00Z")

        #expect(base.supersedes(nil))
        #expect(newer.supersedes(base))
        #expect(!older.supersedes(base))
        #expect(!empty.supersedes(base))
        #expect(base.supersedes(empty))
        // A newer pack resets the layer regardless of its live time…
        #expect(nextPack.supersedes(base))
        // …and a layer for an older pack never replaces it.
        #expect(!base.supersedes(nextPack))
    }

    @Test func ageLabels() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        #expect(LiveTime.ageLabel(from: now, now: now) == "just now")
        #expect(LiveTime.ageLabel(from: now.addingTimeInterval(-12 * 60), now: now) == "12 min ago")
        #expect(LiveTime.ageLabel(from: now.addingTimeInterval(-120 * 60), now: now) == "2 h ago")
        #expect(LiveTime.ageLabel(from: now.addingTimeInterval(-125 * 60), now: now) == "2 h 5 min ago")
        // Clock skew: a future instant is not a negative age.
        #expect(LiveTime.ageLabel(from: now.addingTimeInterval(300), now: now) == "just now")
    }

    @Test func liveObservationWindow() throws {
        let flight = makeFlight(departureTime: "2026-06-24T12:00:00Z", flightDurationHours: 2.0)
        func at(_ iso: String) -> Date { Date.parseISO8601(iso)! }
        #expect(!flight.isInLiveObservationWindow(now: at("2026-06-24T08:59:00Z")))
        #expect(flight.isInLiveObservationWindow(now: at("2026-06-24T09:00:00Z")))   // dep − 3h
        #expect(flight.isInLiveObservationWindow(now: at("2026-06-24T15:00:00Z")))   // arr + 1h
        #expect(!flight.isInLiveObservationWindow(now: at("2026-06-24T15:01:00Z")))
        #expect(!flight.hasLiveObservationWindowEnded(now: at("2026-06-24T08:00:00Z")))
        #expect(flight.hasLiveObservationWindowEnded(now: at("2026-06-24T15:01:00Z")))
    }
}

// MARK: - Snapshot patch gate

@MainActor
@Suite struct ApplyLiveTests {

    /// Same pack in a different spelling ("+00:00" vs "Z") → applied; the
    /// non-nil blocks replace the pack's, and the changes/time are set.
    @Test func samePackDifferentFormatIsApplied() throws {
        let snapshot = try makeSnapshot()
        let layer = try makeLiveLayer()
        let patched = try #require(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS))
        #expect(patched.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(patched.liveChanges?.items.count == 1)
        #expect(patched.routeObservations?.fetchTime == "2026-06-24T09:58:00Z")
        #expect(patched.routeObservations?.airports?.first?.metarFlightCategory == "IFR")
        #expect(patched.observedConditions?.summary == "No echoes")
        // The layer's SIGMET block is nil → the pack's own stays.
        #expect(patched.routeSigmets?.matched.first?.firId == "ZZZZ")
    }

    @Test func fractionalSecondsStillMatchThePack() throws {
        let snapshot = try makeSnapshot()
        let layer = try makeLiveLayer(packTimestamp: "2026-06-24T09:00:00.000000+00:00")
        #expect(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS) != nil)
    }

    @Test func differentPackIsRejected() throws {
        let snapshot = try makeSnapshot()
        let layer = try makeLiveLayer(packTimestamp: "2026-06-24T12:00:00Z")
        #expect(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS) == nil)
    }

    /// The server already overlaid newer data (or a newer layer was applied):
    /// an older or equal layer must not roll it back.
    @Test func olderOrEqualLiveIsRejected() throws {
        let layer = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:00:00Z")
        let newerSnapshot = try makeSnapshot(liveUpdatedAt: "2026-06-24T10:05:00Z")
        #expect(BriefingViewModel.applyLive(layer, to: newerSnapshot, packTimestamp: packTS) == nil)
        let sameSnapshot = try makeSnapshot(liveUpdatedAt: "2026-06-24T10:00:00+00:00")
        #expect(BriefingViewModel.applyLive(layer, to: sameSnapshot, packTimestamp: packTS) == nil)
    }

    @Test func nullLayerIsNeverApplied() throws {
        let snapshot = try makeSnapshot()
        let layer = try makeLiveLayer(liveUpdatedAt: nil)
        #expect(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS) == nil)
    }

    @Test func nilBlocksKeepSnapshotValues() throws {
        let snapshot = try makeSnapshot()
        let layer = try makeLiveLayer(withObservations: false)
        let patched = try #require(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS))
        #expect(patched.routeObservations?.fetchTime == "2026-06-24T08:50:00Z")
        #expect(patched.routeObservations?.airports?.first?.metarFlightCategory == "VFR")
        #expect(patched.liveUpdatedAt == "2026-06-24T10:00:00Z")
    }

    @Test func observedAsOfIsNewestOfLiveAndFetchTime() throws {
        let snapshot = try makeSnapshot()
        // D-0, no live data yet → the observations' own fetch time.
        #expect(BriefingViewModel.observedAsOfDate(for: snapshot) == Date.parseISO8601("2026-06-24T08:50:00Z"))
        let layer = try makeLiveLayer()
        let patched = try #require(BriefingViewModel.applyLive(layer, to: snapshot, packTimestamp: packTS))
        #expect(BriefingViewModel.observedAsOfDate(for: patched) == Date.parseISO8601("2026-06-24T10:00:00Z"))
        // D-1 without live data → hidden.
        let d1 = try makeSnapshot(daysOut: 1)
        #expect(BriefingViewModel.observedAsOfDate(for: d1) == nil)
    }
}

// MARK: - Caching repository (network-first, newest-wins, survives relaunch)

@MainActor
@Suite struct LiveLayerCacheTests {

    @Test func fetchedLayerSurvivesRelaunchAndServesOffline() async throws {
        let dir = makeTempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let online = MockBriefingRepository()
        let layer = try makeLiveLayer()
        online.liveLayerHandler = { layer }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: BriefingCacheStore(cacheDir: dir))
        let fetched = try await repo.liveLayer(flightId: "ZZ-flt")
        #expect(fetched.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(online.liveLayerCallCount == 1)

        // "Relaunch": a fresh store + repository over the same directory, with
        // the network down.
        let offline = MockBriefingRepository()
        offline.liveLayerHandler = { throw MockError.injected("offline") }
        let relaunched = CachingBriefingRepository.makeForTesting(
            online: offline, cache: BriefingCacheStore(cacheDir: dir))
        let served = try await relaunched.liveLayer(flightId: "ZZ-flt")
        #expect(offline.liveLayerCallCount == 1)   // network was tried first
        #expect(served.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(served.changes?.items.first?.key == "metar:ZZAA")
        // The plain-JSON round trip keeps nested blocks intact.
        #expect(served.routeObservations?.airports?.first?.metarReportType == "SPECI")
        #expect(served.observedConditions?.summary == "No echoes")
        #expect(await relaunched.cachedLiveLayer(flightId: "ZZ-flt") != nil)
    }

    @Test func networkFailureWithoutCacheThrows() async throws {
        let dir = makeTempDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let offline = MockBriefingRepository()
        offline.liveLayerHandler = { throw MockError.injected("offline") }
        let repo = CachingBriefingRepository.makeForTesting(online: offline, cache: BriefingCacheStore(cacheDir: dir))
        await #expect(throws: MockError.self) {
            _ = try await repo.liveLayer(flightId: "ZZ-flt")
        }
    }

    @Test func newestWinsInTheCache() async throws {
        let dir = makeTempDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let repo = CachingBriefingRepository.makeForTesting(
            online: MockBriefingRepository(), cache: BriefingCacheStore(cacheDir: dir))

        let at1000 = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:00:00Z")
        let at0930 = try makeLiveLayer(liveUpdatedAt: "2026-06-24T09:30:00Z")
        let nullLayer = try makeLiveLayer(liveUpdatedAt: nil)
        let at1030 = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:30:00Z")

        #expect(await repo.storeLiveLayer(at1000, flightId: "ZZ-flt") == true)
        // An older layer (slow response, stale realtime event) is dropped…
        #expect(await repo.storeLiveLayer(at0930, flightId: "ZZ-flt") == false)
        // …so is a null layer…
        #expect(await repo.storeLiveLayer(nullLayer, flightId: "ZZ-flt") == false)
        #expect(await repo.cachedLiveLayer(flightId: "ZZ-flt")?.liveUpdatedAt == "2026-06-24T10:00:00Z")
        // …a newer one replaces it.
        #expect(await repo.storeLiveLayer(at1030, flightId: "ZZ-flt") == true)
        #expect(await repo.cachedLiveLayer(flightId: "ZZ-flt")?.liveUpdatedAt == "2026-06-24T10:30:00Z")

        // A network response older than the cache is returned but not persisted.
        let online = MockBriefingRepository()
        let stale = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:20:00Z")
        online.liveLayerHandler = { stale }
        let repo2 = CachingBriefingRepository.makeForTesting(online: online, cache: BriefingCacheStore(cacheDir: dir))
        _ = try await repo2.liveLayer(flightId: "ZZ-flt")
        #expect(await repo2.cachedLiveLayer(flightId: "ZZ-flt")?.liveUpdatedAt == "2026-06-24T10:30:00Z")
    }

    /// The live file sits in the flight directory, so deleting the flight drops it.
    @Test func deleteFlightRemovesTheLiveCache() async throws {
        let dir = makeTempDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let online = MockBriefingRepository()
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: BriefingCacheStore(cacheDir: dir))
        let layer = try makeLiveLayer()
        #expect(await repo.storeLiveLayer(layer, flightId: "ZZ-flt") == true)
        try await repo.deleteFlight(id: "ZZ-flt")
        #expect(await repo.cachedLiveLayer(flightId: "ZZ-flt") == nil)
    }
}

// MARK: - View model sync

@MainActor
@Suite struct LiveLayerViewModelTests {

    /// A realtime refresh keeps the pack timestamp; only `live_updated_at`
    /// moves. The sync must notice that and fetch the live layer — and must not
    /// fetch it when nothing moved.
    @Test func syncFetchesLiveWhenOnlyLiveUpdatedAtMoves() async throws {
        let mock = MockBriefingRepository()
        let snapshot = try makeSnapshot()
        mock.snapshotHandler = { snapshot }

        let p0 = try makePackMeta(flightId: "ZZ-flt", fetchTimestamp: packTS, daysOut: 0)
        let first = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:00:00Z")
        mock.latestPackHandler = { p0 }
        mock.liveLayerHandler = { first }

        let vm = BriefingViewModel(flight: makeFlight(id: "ZZ-flt"), repository: mock)
        await vm.loadBriefing()
        // The initial load fetched and applied the layer.
        #expect(mock.liveLayerCallCount == 1)
        #expect(vm.liveLayer?.liveUpdatedAt == "2026-06-24T10:00:00Z")
        #expect(vm.liveChanges?.items.count == 1)

        // Same pack, same live time → no live fetch.
        let p1 = try makePackMeta(flightId: "ZZ-flt", fetchTimestamp: packTS, daysOut: 0,
                                  liveUpdatedAt: "2026-06-24T10:00:00+00:00")
        mock.latestPackHandler = { p1 }
        await vm.syncLatestPack()
        #expect(mock.liveLayerCallCount == 1)

        // Same pack, newer live time → the live layer is fetched and applied.
        let p2 = try makePackMeta(flightId: "ZZ-flt", fetchTimestamp: packTS, daysOut: 0,
                                  liveUpdatedAt: "2026-06-24T10:10:00Z")
        let second = try makeLiveLayer(liveUpdatedAt: "2026-06-24T10:10:00Z", changeKey: "metar:ZZBB")
        mock.latestPackHandler = { p2 }
        mock.liveLayerHandler = { second }
        await vm.syncLatestPack()
        #expect(mock.liveLayerCallCount == 2)
        #expect(vm.pack?.fetchTimestamp == packTS)
        #expect(vm.liveLayer?.liveUpdatedAt == "2026-06-24T10:10:00Z")
        #expect(vm.liveChanges?.items.first?.key == "metar:ZZBB")
        if case .loaded(let s) = vm.snapshotState {
            #expect(s.liveUpdatedAt == "2026-06-24T10:10:00Z")
        } else {
            Issue.record("snapshot should be loaded")
        }

        // Pull-to-refresh fetches the live layer even when nothing moved.
        await vm.pullToRefresh()
        #expect(mock.liveLayerCallCount == 3)
    }

    /// A live layer for another pack (a full refresh happened elsewhere and
    /// the sync hasn't adopted it yet) must not patch the pack on screen.
    @Test func layerForAnotherPackIsIgnored() async throws {
        let mock = MockBriefingRepository()
        let snapshot = try makeSnapshot()
        mock.snapshotHandler = { snapshot }
        let p0 = try makePackMeta(flightId: "ZZ-flt", fetchTimestamp: packTS, daysOut: 0)
        let foreign = try makeLiveLayer(packTimestamp: "2026-06-24T12:00:00Z")
        mock.latestPackHandler = { p0 }
        mock.liveLayerHandler = { foreign }

        let vm = BriefingViewModel(flight: makeFlight(id: "ZZ-flt"), repository: mock)
        await vm.loadBriefing()
        #expect(mock.liveLayerCallCount == 1)
        #expect(vm.liveLayer == nil)
        #expect(vm.liveChanges == nil)
    }
}
