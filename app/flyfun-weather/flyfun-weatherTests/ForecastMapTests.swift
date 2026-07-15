//
//  ForecastMapTests.swift
//  flyfun-weatherTests
//
//  Unit tests for the iOS forecast map (#420): the served map-metrics catalog
//  colour evaluation (the "clients render, server decides" contract — colours
//  must match the web/catalog exactly), consensus-block reading, alt-required
//  aggregation, the per-metric agreement key, /maps.html universal-link routing,
//  and the PendingNavigation round-trip for a shared map link.
//

import Foundation
import Testing
import UIKit
@testable import flyfun_weather

@Suite("ForecastMap")
struct ForecastMapTests {

    // A representative slice of web/ts/data/map-metrics-catalog.json (embedded so
    // the test doesn't depend on bundle packaging). Values match the shipped file.
    static let catalogJSON = """
    {
      "version": 1,
      "scales": {
        "categorical": {
          "flight_category": { "VFR": "#22c55e", "MVFR": "#3b82f6", "IFR": "#ef4444", "LIFR": "#a855f7" },
          "convective_risk": { "none": "#22c55e", "marginal": "#eab308", "low": "#facc15", "moderate": "#f97316", "high": "#ef4444", "extreme": "#991b1b" },
          "agreement": { "consistent": "#22c55e", "mixed": "#f97316", "divergent": "#ef4444" }
        },
        "bands": {
          "wind_speed_kt": { "kind": "threshold_asc", "stops": [ { "lt": 10, "color": "#22c55e" }, { "lt": 15, "color": "#84cc16" }, { "lt": 20, "color": "#eab308" }, { "lt": 25, "color": "#f97316" }, { "lt": 35, "color": "#ef4444" } ], "default": "#991b1b" },
          "crosswind_kt": { "kind": "threshold_asc", "stops": [ { "lt": 5, "color": "#22c55e" }, { "lt": 10, "color": "#84cc16" }, { "lt": 15, "color": "#eab308" }, { "lt": 20, "color": "#f97316" }, { "lt": 25, "color": "#ef4444" } ], "default": "#991b1b" },
          "ceiling_ft": { "kind": "threshold_asc", "null_color": "#888", "stops": [ { "lt": 500, "color": "#a855f7" }, { "lt": 1000, "color": "#ef4444" }, { "lt": 3000, "color": "#3b82f6" } ], "default": "#22c55e" },
          "visibility_m": { "kind": "threshold_desc", "convert": "m_to_sm", "stops": [ { "gte": 5, "color": "#22c55e" }, { "gte": 3, "color": "#3b82f6" }, { "gte": 1, "color": "#ef4444" } ], "default": "#a855f7" },
          "cloud_cover_pct": { "kind": "gray_ramp", "base": 220, "span": 160, "blue_boost": 10 }
        }
      },
      "metrics": {
        "flight_category": { "label": "Category", "color": { "kind": "categorical", "scale": "flight_category", "fallback": "#888" }, "legend": { "title": "Flight Category", "items": [ { "color": "#22c55e", "label": "VFR" } ] } },
        "wind_speed_kt": { "label": "Wind", "color": { "kind": "band", "scale": "wind_speed_kt", "field": "wind_speed_kt", "fallback": 0 }, "legend": { "title": "Wind Speed (kt)", "items": [] } },
        "crosswind_kt": { "label": "Xwind", "color": { "kind": "band", "scale": "crosswind_kt", "field": "crosswind_kt", "fallback": null }, "legend": { "title": "Xwind", "items": [] } },
        "ceiling_ft": { "label": "Ceiling", "color": { "kind": "band", "scale": "ceiling_ft", "field": "ceiling_ft", "fallback": null }, "legend": { "title": "Ceiling", "items": [] } },
        "visibility_m": { "label": "Visibility", "color": { "kind": "band", "scale": "visibility_m", "field": "visibility_m", "fallback": 99999 }, "legend": { "title": "Visibility", "items": [] } },
        "cloud_cover_pct": { "label": "Cloud cover", "color": { "kind": "band", "scale": "cloud_cover_pct", "field": "cloud_cover_pct", "fallback": 0 }, "legend": { "title": "Cloud Cover", "items": [] } },
        "alternate_needed": { "label": "Alternate required?", "color": { "kind": "alternate_needed", "fallback": "#888" }, "legend": { "title": "Alt", "items": [] } }
      }
    }
    """

    static func catalog() throws -> ForecastMapCatalog {
        try JSONDecoder().decode(ForecastMapCatalog.self, from: Data(catalogJSON.utf8))
    }

    /// UIColor → "#rrggbb" for exact comparison against the catalog hexes.
    static func hex(_ c: UIColor) -> String {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        c.getRed(&r, green: &g, blue: &b, alpha: &a)
        return String(format: "#%02x%02x%02x",
                      Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded()))
    }

    // MARK: - Band colour evaluation

    @Test func windSpeedThresholdBands() throws {
        let cat = try Self.catalog()
        let band = cat.scales.bands["wind_speed_kt"]!
        #expect(Self.hex(cat.bandColor(band: band, raw: 5, fallbackNumber: 0)) == "#22c55e")   // <10
        #expect(Self.hex(cat.bandColor(band: band, raw: 12, fallbackNumber: 0)) == "#84cc16")  // <15
        #expect(Self.hex(cat.bandColor(band: band, raw: 24, fallbackNumber: 0)) == "#f97316")  // <25
        #expect(Self.hex(cat.bandColor(band: band, raw: 40, fallbackNumber: 0)) == "#991b1b")  // default
    }

    @Test func ceilingNullUsesNullColor() throws {
        let cat = try Self.catalog()
        let band = cat.scales.bands["ceiling_ft"]!
        // Missing ceiling → the scale's null_color (#888), not the default green.
        #expect(Self.hex(cat.bandColor(band: band, raw: nil, fallbackNumber: nil)) == "#888888")
        #expect(Self.hex(cat.bandColor(band: band, raw: 5000, fallbackNumber: nil)) == "#22c55e")
        #expect(Self.hex(cat.bandColor(band: band, raw: 800, fallbackNumber: nil)) == "#ef4444")
    }

    @Test func visibilityConvertsMetresToStatuteMiles() throws {
        let cat = try Self.catalog()
        let band = cat.scales.bands["visibility_m"]!
        // threshold_desc on SM: 10000 m ≈ 6.2 SM (≥5) → VFR green.
        #expect(Self.hex(cat.bandColor(band: band, raw: 10000, fallbackNumber: 99999)) == "#22c55e")
        // 6000 m ≈ 3.7 SM (≥3) → MVFR blue.
        #expect(Self.hex(cat.bandColor(band: band, raw: 6000, fallbackNumber: 99999)) == "#3b82f6")
        // 800 m ≈ 0.5 SM (<1) → LIFR purple (default).
        #expect(Self.hex(cat.bandColor(band: band, raw: 800, fallbackNumber: 99999)) == "#a855f7")
    }

    @Test func cloudCoverGrayRamp() throws {
        let cat = try Self.catalog()
        let band = cat.scales.bands["cloud_cover_pct"]!
        // g = base - pct/100*span; rgb(g, g, g+blue_boost).
        #expect(Self.hex(cat.bandColor(band: band, raw: 0, fallbackNumber: 0)) == "#dcdce6")    // 220,220,230
        #expect(Self.hex(cat.bandColor(band: band, raw: 100, fallbackNumber: 0)) == "#3c3c46")  // 60,60,70
    }

    @Test func crosswindMissingIsMutedNotBanded() throws {
        let cat = try Self.catalog()
        let band = cat.scales.bands["crosswind_kt"]!
        // fallback is null (not a number) → a missing crosswind is muted, never
        // coloured as calm-green.
        #expect(Self.hex(cat.bandColor(band: band, raw: nil, fallbackNumber: nil)) == "#888888")
    }

    // MARK: - Categorical + alternate

    @Test func categoricalFlightCategory() throws {
        let cat = try Self.catalog()
        let airport = try Self.airport(consensusCategory: "IFR")
        #expect(Self.hex(cat.color(metric: "flight_category", airport: airport, mode: .worst)) == "#ef4444")
    }

    @Test func alternateAggregationWorstVsMajority() throws {
        let cat = try Self.catalog()
        // Three models: two say "neither", one says "FAA+EASA".
        let airport = try Self.airport(alt: [
            AltRequired(faa: false, easa: false),
            AltRequired(faa: false, easa: false),
            AltRequired(faa: true, easa: true),
        ])
        // Worst = any yes → both → red.
        #expect(Self.hex(cat.color(metric: "alternate_needed", airport: airport, mode: .worst)) == "#ef4444")
        // Majority = modal → neither → green.
        #expect(Self.hex(cat.color(metric: "alternate_needed", airport: airport, mode: .majority)) == "#22c55e")
    }

    // MARK: - Agreement key (per-active-metric ring)

    @Test func agreementKeyProxies() {
        #expect(ForecastMapCatalog.agreementKey(forMetric: "crosswind_kt") == "wind_speed_kt")
        #expect(ForecastMapCatalog.agreementKey(forMetric: "headwind_kt") == "wind_speed_kt")
        #expect(ForecastMapCatalog.agreementKey(forMetric: "convective_risk") == "cape_jkg")
        #expect(ForecastMapCatalog.agreementKey(forMetric: "ceiling_ft") == "ceiling_ft")
        #expect(ForecastMapCatalog.agreementKey(forMetric: "alternate_needed") == "flight_category")
    }

    // MARK: - Consensus block selection + decoding

    @Test func consensusModePicksMajorityBlock() throws {
        let airport = try Self.airportWithBothConsensus(worst: "IFR", majority: "MVFR")
        #expect(airport.consensus(mode: .worst).flightCategory == "IFR")
        #expect(airport.consensus(mode: .majority).flightCategory == "MVFR")
    }

    @Test func agreementKeyIsPreservedThroughDecode() throws {
        // The decoder must NOT snake→camel the `agreement` dict keys, or the
        // per-metric ring lookup silently misses. Uses a plain decoder in prod.
        let json = """
        { "forecast_time": "2026-07-15T12:00:00+00:00", "model_init_times": {},
          "airports": [ { "icao": "EGKB", "lat": 51.3, "lon": 0.03, "models": {},
            "consensus": { "flight_category": "MVFR", "agreement": { "wind_speed_kt": "divergent" } },
            "consensus_majority": { "flight_category": "MVFR", "agreement": {} } } ] }
        """
        let resp = try ForecastMapResponse.decode(from: Data(json.utf8))
        let apt = resp.airports[0]
        #expect(apt.consensus.agreement["wind_speed_kt"] == "divergent")
        // crosswind proxies to the wind agreement key.
        #expect(apt.consensus.agreement(forMetric: "crosswind_kt") == "divergent")
    }

    // MARK: - Card cell formatting (gust parity with web)

    @Test func compactCellRendersGusts() throws {
        let json = """
        { "icao": "EGKB", "lat": 51.3, "lon": 0.03,
          "models": { "gfs": { "convective_risk": "none", "flight_category": "VFR",
            "wind_speed_kt": 15, "wind_dir_deg": 270, "wind_gust_kt": 22,
            "crosswind_kt": 12, "gust_crosswind_kt": 18,
            "headwind_kt": 8, "gust_headwind_kt": 14 } },
          "consensus": { "flight_category": "VFR", "agreement": {}, "wind_speed_kt": 15, "wind_dir_deg": 270 },
          "consensus_majority": { "flight_category": "VFR", "agreement": {} } }
        """
        let apt = try JSONDecoder().decode(ForecastAirport.self, from: Data(json.utf8))
        let m = apt.models["gfs"].map { $0 as any ForecastCellData }
        // Web: dir@speedGgust, valueGgust — the gust must not be silently dropped.
        #expect(ForecastAirportCard.compactCell(m, metric: "wind_speed_kt") == "270@15G22")
        #expect(ForecastAirportCard.compactCell(m, metric: "crosswind_kt") == "12G18")
        #expect(ForecastAirportCard.compactCell(m, metric: "headwind_kt") == "8G14")
        // The consensus block carries no gust field → steady value only, no "G".
        #expect(ForecastAirportCard.compactCell(apt.consensus, metric: "wind_speed_kt") == "270@15")
    }

    // MARK: - Model mode token round-trip

    @Test func modelModeTokens() {
        #expect(ForecastModelMode(token: "worst") == .worst)
        #expect(ForecastModelMode(token: "majority") == .majority)
        #expect(ForecastModelMode(token: "gfs") == .model("gfs"))
        #expect(ForecastModelMode.model("ecmwf").token == "ecmwf")
        #expect(ForecastModelMode.worst.isConsensus)
        #expect(!ForecastModelMode.model("gfs").isConsensus)
        #expect(ForecastModelMode.model("gfs").consensusMode == .worst)
    }

    // MARK: - Universal link routing (/maps.html)

    @Test func mapsLinkRoutesWithState() {
        let url = URL(string: "https://weather.flyfun.aero/maps.html?fc.day=3&fc.hour=15&fc.model=majority&fc.metric=crosswind_kt&fc.apt=LFMD")!
        let target = AppState.navigationTarget(for: url)
        #expect(target == .forecastMap(MapDeepLink(day: 3, hour: 15, model: "majority", metric: "crosswind_kt", airport: "LFMD")))
    }

    @Test func bareMapsLinkIsEmptyDeepLink() {
        let target = AppState.navigationTarget(for: URL(string: "https://weather.flyfun.aero/maps.html")!)
        guard case .forecastMap(let dl) = target else { Issue.record("expected forecastMap"); return }
        #expect(dl.isEmpty)
    }

    @Test func briefingLinkStillRoutes() {
        let target = AppState.navigationTarget(for: URL(string: "https://weather.flyfun.aero/briefing.html?flight=abc")!)
        #expect(target == .briefing(flightId: "abc"))
    }

    @Test func pendingNavigationRoundTripsForecastMap() {
        let dl = MapDeepLink(day: 2, hour: 6, model: "gfs", metric: "ceiling_ft", airport: "EGLL")
        PendingNavigationStore.set(.forecastMap(dl))
        #expect(PendingNavigationStore.take() == .forecastMap(dl))
    }

    /// The VM applies a `MapDeepLink` at init — the consumption end of the shared
    /// `fc.*` state (the map's `.id` re-creates the VM for a re-entrant link).
    @MainActor
    @Test func viewModelAppliesDeepLinkAtInit() {
        let vm = ForecastMapViewModel(
            repository: MockBriefingRepository(),
            deepLink: MapDeepLink(day: 3, hour: 15, model: "majority", metric: "crosswind_kt", airport: "LFMD"))
        #expect(vm.selectedDay == 3)
        #expect(vm.selectedHour == 15)
        #expect(vm.mode == .majority)
        #expect(vm.metric == "crosswind_kt")
    }

    // MARK: - Fixtures

    private static func airport(consensusCategory: String) throws -> ForecastAirport {
        let json = """
        { "icao": "EGKB", "lat": 51.3, "lon": 0.03, "models": {},
          "consensus": { "flight_category": "\(consensusCategory)", "agreement": {} },
          "consensus_majority": { "flight_category": "\(consensusCategory)", "agreement": {} } }
        """
        return try JSONDecoder().decode(ForecastAirport.self, from: Data(json.utf8))
    }

    private static func airportWithBothConsensus(worst: String, majority: String) throws -> ForecastAirport {
        let json = """
        { "icao": "EGKB", "lat": 51.3, "lon": 0.03, "models": {},
          "consensus": { "flight_category": "\(worst)", "agreement": {} },
          "consensus_majority": { "flight_category": "\(majority)", "agreement": {} } }
        """
        return try JSONDecoder().decode(ForecastAirport.self, from: Data(json.utf8))
    }

    private static func airport(alt: [AltRequired]) throws -> ForecastAirport {
        let modelKeys = ["gfs", "icon", "ecmwf"]
        let models = zip(modelKeys, alt).map { key, a in
            "\"\(key)\": { \"convective_risk\": \"none\", \"flight_category\": \"VFR\", \"alt_required\": { \"faa\": \(a.faa), \"easa\": \(a.easa) } }"
        }.joined(separator: ", ")
        let json = """
        { "icao": "EGKB", "lat": 51.3, "lon": 0.03, "models": { \(models) },
          "consensus": { "flight_category": "VFR", "agreement": {} },
          "consensus_majority": { "flight_category": "VFR", "agreement": {} } }
        """
        return try JSONDecoder().decode(ForecastAirport.self, from: Data(json.utf8))
    }
}
