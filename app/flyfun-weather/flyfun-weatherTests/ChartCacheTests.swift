//
//  ChartCacheTests.swift
//  flyfun-weatherTests
//
//  The shared on-disk surface-chart cache (#799): one file per immutable cache
//  key under `charts/`, shared by every pack — so a second leg or a refresh on
//  the same run never re-downloads — plus the mark-and-sweep and size cap that
//  keep it bounded, and the pack-meta `charts` manifest it is keyed from.
//

import Foundation
import Testing
@testable import flyfun_weather

/// One manifest chart. Decoded from JSON like `makePackMeta`, so the wire
/// shape (snake_case) is what's exercised.
private func makeChart(
    id: String = "048",
    cacheKey: String = "dwd/2026-05-08T06Z/048-20260508T044300Z",
    ext: String = "png",
    prefetch: Bool = false
) throws -> PackChart {
    let json = """
    {
      "chart_id": "\(id)", "label": "+48h", "caption": "Valid 2026-05-10 00Z",
      "valid_time": "2026-05-10T00:00:00Z", "init_time": null,
      "url": "/api/flights/f1/packs/ts/dwd-chart/\(id)", "overlay_key": "icon",
      "cache_key": "\(cacheKey)", "file_ext": "\(ext)", "available": true, "prefetch": \(prefetch)
    }
    """
    return try JSONDecoder.weatherBrief.decode(PackChart.self, from: Data(json.utf8))
}

/// A pack meta carrying a one-source charts manifest that lists `cacheKeys`.
private func packMetaJSON(flightId: String, timestamp: String, cacheKeys: [String]) -> Data {
    let charts = cacheKeys.enumerated().map { i, key in
        """
        {"chart_id": "c\(i)", "label": "L", "caption": "", "valid_time": null, "init_time": null,
         "url": "/x", "overlay_key": "icon", "cache_key": "\(key)", "file_ext": "png",
         "available": true, "prefetch": false}
        """
    }.joined(separator: ",")
    let json = """
    {
      "flight_id": "\(flightId)", "fetch_timestamp": "\(timestamp)", "days_out": 1,
      "is_historical": false, "has_gramet": false, "has_skewt": false, "has_digest": false,
      "has_advisories": false, "model_init_times": {}, "grib_init_times": {},
      "models_skipped_region": [],
      "charts": {"in_coverage": true, "sources": [
        {"source": "dwd", "label": "DWD", "attribution": "DWD", "default_chart_id": "c0",
         "overlay_url": "/o", "charts": [\(charts)]}
      ]}
    }
    """
    return Data(json.utf8)
}

@MainActor
@Suite struct ChartCacheTests {

    @Test func manifestDecodesFromPackMeta() throws {
        let data = packMetaJSON(flightId: "f1", timestamp: "t1", cacheKeys: ["dwd/2026-05-08T06Z/ana_w1600-X"])
        let pack = try JSONDecoder.weatherBrief.decode(PackMetaResponse.self, from: data)
        let source = try #require(pack.charts?.sources.first)
        #expect(pack.charts?.inCoverage == true)
        #expect(source.defaultChartId == "c0")
        #expect(source.charts.first?.cachePath == "dwd/2026-05-08T06Z/ana_w1600-X.png")
    }

    @Test func packMetaWithoutChartsStillDecodes() throws {
        #expect(try makePackMeta().charts == nil)
    }

    /// The acceptance check: the same cache key on a second pack (another leg,
    /// or a refresh on the same run) is served from disk, not re-downloaded.
    @Test func secondRequestForSameKeyDoesNotRedownload() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let online = MockBriefingRepository()
        let bytes = Data("chart-bytes".utf8)
        online.chartImageHandler = { _ in bytes }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: store)

        let chart = try makeChart()
        #expect(try await repo.chartImage(chart) == bytes)
        #expect(try await repo.chartImage(chart) == bytes)
        #expect(online.chartImageCallCount == 1)
        #expect(await store.hasChart(cachePath: chart.cachePath))

        // A new version of the chart (new key) is fetched.
        let replaced = try makeChart(cacheKey: "dwd/2026-05-08T06Z/048-20260509T044300Z")
        _ = try await repo.chartImage(replaced)
        #expect(online.chartImageCallCount == 2)
    }

    @Test func failedFetchIsNotCached() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let online = MockBriefingRepository()
        online.chartImageHandler = { _ in throw APIError.serverError(410, "Chart not available") }
        let repo = CachingBriefingRepository.makeForTesting(online: online, cache: store)

        let chart = try makeChart()
        await #expect(throws: APIError.self) { try await repo.chartImage(chart) }
        #expect(await store.hasChart(cachePath: chart.cachePath) == false)
    }

    @Test func cachePathsThatEscapeTheCacheAreRefused() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        for bad in ["../escape.png", "dwd//x.png", "/abs.png", "dwd/../../x.png"] {
            await #expect(throws: (any Error).self) { try await store.writeChart(Data([1]), cachePath: bad) }
            #expect(await store.hasChart(cachePath: bad) == false)
        }
    }

    /// Prune keeps charts some pack on disk still refers to (an indexed pack's
    /// own manifest, or a flight-level sidecar) and sweeps the rest.
    @Test func sweepRemovesUnreferencedCharts() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let repo = CachingBriefingRepository.makeForCacheTesting(cache: store)

        let kept = "dwd/2026-05-08T06Z/048-A"
        let viewed = "dwd/2026-05-08T06Z/060-A"
        let orphan = "dwd/2026-05-06T06Z/048-A"
        for key in [kept, viewed, orphan] {
            try await store.writeChart(Data(key.utf8), cachePath: "\(key).png")
        }
        // A downloaded pack references `kept` through its per-pack manifest…
        let soon = ISO8601DateFormatter().string(from: Date().addingTimeInterval(86_400))
        await store.registerDownload(flightId: "f1", timestamp: "t1", flightTitle: "T", assessment: nil,
                                     endpoints: CachedPackEntry.requiredEndpoints, totalBytes: 1,
                                     departureTime: soon)
        try await store.writeData(packMetaJSON(flightId: "f1", timestamp: "t1", cacheKeys: [kept]),
                                  flightId: "f1", timestamp: "t1",
                                  endpoint: CachingBriefingRepository.packMetaEndpoint)
        // …and a viewed-only flight references `viewed` through latest-pack.json.
        try await store.writeFlightMetadata(packMetaJSON(flightId: "f2", timestamp: "t2", cacheKeys: [viewed]),
                                            flightId: "f2", name: "latest-pack")
        await repo.cacheFlightData(makeFlight(id: "f2", departureTime: soon))

        await repo.pruneStalePacks(olderThanDays: 7)

        #expect(await store.hasChart(cachePath: "\(kept).png"))
        #expect(await store.hasChart(cachePath: "\(viewed).png"))
        #expect(await store.hasChart(cachePath: "\(orphan).png") == false)
        // The emptied run-cycle directory goes too, and is never mistaken for a flight.
        #expect(!FileManager.default.fileExists(atPath: dir.appendingPathComponent("charts/dwd/2026-05-06T06Z").path))
        #expect(await store.flightDirectoryIDs().contains(BriefingCacheStore.chartsDirName) == false)
    }

    @Test func sizeCapEvictsLeastRecentlyRead() async throws {
        let dir = makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let store = BriefingCacheStore(cacheDir: dir)
        let chunk = Data(repeating: 7, count: 1_000)
        for name in ["a", "b", "c"] {
            try await store.writeChart(chunk, cachePath: "dwd/rc/\(name).png")
        }
        // Age a and b, then read a: b is now the least recently used.
        let old = Date().addingTimeInterval(-3_600)
        for name in ["a", "b"] {
            try FileManager.default.setAttributes(
                [.modificationDate: old],
                ofItemAtPath: dir.appendingPathComponent("charts/dwd/rc/\(name).png").path
            )
        }
        _ = await store.readChart(cachePath: "dwd/rc/a.png")

        let removed = await store.enforceChartSizeCap(maxBytes: 2_000)

        #expect(removed == 1)
        #expect(await store.hasChart(cachePath: "dwd/rc/b.png") == false)
        #expect(await store.hasChart(cachePath: "dwd/rc/a.png"))
        #expect(await store.hasChart(cachePath: "dwd/rc/c.png"))
        #expect(await store.chartCacheSize() == 2_000)
    }

    @Test func unavailableTextNamesTheValidTime() {
        #expect(SurfaceChartsView.unavailableText(validTime: "2026-05-10T12:00:00Z")
                == "Chart no longer available (valid 2026-05-10 12Z).")
        #expect(SurfaceChartsView.unavailableText(validTime: nil) == "Chart no longer available.")
    }
}
