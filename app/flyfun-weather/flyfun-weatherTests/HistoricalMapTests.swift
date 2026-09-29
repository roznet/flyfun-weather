//
//  HistoricalMapTests.swift
//  flyfun-weatherTests
//
//  Unit tests for the iOS historical map (#629): payload decoding (METAR/TAF
//  under `observed`, null consensus for observation-only airports), the
//  per-source projection onto the forecast marker layer, catalog colouring of
//  observed sources, the VM's time grid / source availability / fetch rules,
//  and `/maps.html?tab=historical` universal-link routing.
//

import Foundation
import Testing
@testable import flyfun_weather

/// MainActor as a whole: the app target defaults to MainActor isolation, and
/// the VM tests drive a MainActor view model.
@MainActor
@Suite("HistoricalMap")
struct HistoricalMapTests {

    static let payloadJSON = """
    {
      "at": "2026-09-29T12:00:00+00:00",
      "lead_days": 0,
      "run_cutoff": "2026-09-29T12:00:00+00:00",
      "sources": {
        "metar": { "available": true, "count": 2, "max_age_min": 90 },
        "taf": { "available": false, "count": 0 },
        "gfs": { "available": true, "valid_time": "2026-09-29T12:00:00+00:00",
                 "model_init_time": "2026-09-29T00:00:00+00:00",
                 "fetched_at": "2026-09-29T05:12:33.123456+00:00", "lead_hours": 12, "count": 1 },
        "icon": { "available": false, "reason": "no_run" },
        "ecmwf": { "available": false, "reason": "beyond_horizon" }
      },
      "airports": [
        { "icao": "EGLL", "lat": 51.47, "lon": -0.45, "approach_type": "ILS",
          "models": { "gfs": { "flight_category": "VFR", "ceiling_ft": 4000, "visibility_m": 10000,
                               "convective_risk": "none",
                               "valid_time": "2026-09-29T12:00:00+00:00",
                               "model_init_time": "2026-09-29T00:00:00+00:00",
                               "alt_required": { "faa": false, "easa": false } } },
          "consensus": { "flight_category": "VFR", "agreement": {} },
          "consensus_majority": { "flight_category": "VFR", "agreement": {} },
          "observed": { "metar": { "flight_category": "IFR", "ceiling_ft": 600, "visibility_m": 3000,
                                   "wind_speed_kt": 12, "wind_dir_deg": 240, "wind_gust_kt": null,
                                   "observation_time": "2026-09-29T11:50:00+00:00", "age_min": 10,
                                   "report_type": "SPECI", "raw": "SPECI EGLL 291150Z 24012KT 3000 BR BKN006",
                                   "weather": ["BR"], "dewpoint_c": 9, "qnh": 1012,
                                   "alt_required": { "faa": true, "easa": true } } } },
        { "icao": "LFPG", "lat": 49.0, "lon": 2.55, "approach_type": null,
          "models": {}, "consensus": null, "consensus_majority": null,
          "observed": { "metar": { "flight_category": "VFR", "ceiling_ft": null, "visibility_m": 9999,
                                   "observation_time": "2026-09-29T11:30:00+00:00", "age_min": 30,
                                   "report_type": "METAR", "raw": "LFPG 291130Z CAVOK", "weather": [] } } }
      ]
    }
    """

    static let rangeJSON = """
    {
      "latest": "2026-09-29T12:00:00+00:00",
      "earliest_observation": "2026-09-01T00:00:00+00:00",
      "earliest_model": "2026-09-19T06:00:00+00:00",
      "step_minutes": 30,
      "model_sample_hours": [6, 9, 12, 15, 18],
      "metar_max_age_min": 90,
      "model_max_age_min": 180,
      "models": ["gfs", "icon", "ecmwf"],
      "observed_sources": ["metar", "taf"],
      "leads": [ { "lead_days": 0, "models": ["gfs", "icon", "ecmwf"] },
                 { "lead_days": 6, "models": ["gfs", "ecmwf"] } ]
    }
    """

    static func payload() throws -> HistoricalMapResponse {
        try HistoricalMapResponse.decode(from: Data(payloadJSON.utf8))
    }

    static func range() throws -> HistoricalRangeResponse {
        try JSONDecoder.weatherBrief.decode(HistoricalRangeResponse.self, from: Data(rangeJSON.utf8))
    }

    static func date(_ iso: String) -> Date { HistoricalTime.parse(iso)! }

    // MARK: - Decoding

    @Test func decodesObservedAndNullConsensus() throws {
        let p = try Self.payload()
        #expect(p.airports.count == 2)
        #expect(p.sources["ecmwf"]?.reason == "beyond_horizon")
        #expect(p.sources["gfs"]?.leadHours == 12)

        let egll = try #require(p.airports.first { $0.icao == "EGLL" })
        #expect(egll.metar?.values.flightCategory == "IFR")
        #expect(egll.metar?.reportType == "SPECI")
        #expect(egll.metar?.weather == ["BR"])
        #expect(egll.metar?.values.altRequired?.easa == true)
        #expect(egll.taf == nil)
        #expect(egll.modelRuns["gfs"]?.modelInitTime == "2026-09-29T00:00:00+00:00")

        let lfpg = try #require(p.airports.first { $0.icao == "LFPG" })
        #expect(lfpg.consensus == nil)
        #expect(lfpg.consensusMajority == nil)
        #expect(lfpg.metar?.values.ceilingFt == nil)
    }

    @Test func parsesPythonTimestamps() {
        #expect(HistoricalTime.parse("2026-09-29T12:00:00+00:00") != nil)
        #expect(HistoricalTime.parse("2026-09-29T05:12:33.123456+00:00") != nil)
        #expect(HistoricalTime.timeLabel("2026-09-29T11:50:00+00:00") == "11:50Z")
        #expect(HistoricalTime.runLabel("2026-09-29T00:00:00+00:00") == "29 00Z")
        #expect(HistoricalTime.instant(date: "2026-09-29", time: "14:30") == Self.date("2026-09-29T14:30:00Z"))
    }

    // MARK: - Projection onto the marker layer

    @Test func projectionKeepsOnlyAirportsWithTheSource() throws {
        let p = try Self.payload()
        func shown(_ s: HistoricalSource) -> [String] {
            p.airports.compactMap { $0.forecastAirport(for: s)?.icao }
        }
        #expect(shown(.metar) == ["EGLL", "LFPG"])
        #expect(shown(.taf).isEmpty)
        // An observation-only airport has no consensus: left off, not drawn as VFR.
        #expect(shown(.worst) == ["EGLL"])
        #expect(shown(.gfs) == ["EGLL"])
        #expect(shown(.icon).isEmpty)
    }

    @Test func observedSourceIsPresentedAsTheOnlyModel() throws {
        let p = try Self.payload()
        let egll = try #require(p.airports.first { $0.icao == "EGLL" }?.forecastAirport(for: .metar))
        #expect(Array(egll.models.keys) == ["metar"])
        #expect(egll.cell(for: HistoricalSource.metar.mapMode)?.categoryField("flight_category") == "IFR")
    }

    /// Consensus is a statement about the models: the METAR's alternate flag
    /// must not vote in the worst-of aggregation.
    @Test func consensusAltRequiredIgnoresObservations() throws {
        let p = try Self.payload()
        let egll = try #require(p.airports.first { $0.icao == "EGLL" }?.forecastAirport(for: .worst))
        #expect(egll.models["metar"] == nil)
        let alt = try #require(egll.aggregatedAltRequired(mode: .worst))
        #expect(!alt.faa && !alt.easa)
    }

    @Test func catalogColoursObservedSources() throws {
        let cat = try ForecastMapTests.catalog()
        let p = try Self.payload()
        let egll = try #require(p.airports.first { $0.icao == "EGLL" }?.forecastAirport(for: .metar))
        let mode = HistoricalSource.metar.mapMode
        #expect(ForecastMapTests.hex(cat.color(metric: "flight_category", airport: egll, mode: mode)) == "#ef4444")
        // 600 ft: the 500–1000 ft band.
        #expect(ForecastMapTests.hex(cat.color(metric: "ceiling_ft", airport: egll, mode: mode)) == "#ef4444")
        // FAA + EASA both required → red.
        #expect(ForecastMapTests.hex(cat.color(metric: "alternate_needed", airport: egll, mode: mode)) == "#ef4444")
    }

    @Test func cardCellText() throws {
        let p = try Self.payload()
        let egll = try #require(p.airports.first { $0.icao == "EGLL" })
        #expect(HistoricalAirportCard.cellText(egll.metar?.values, metric: "alternate_needed") == "FAA + EASA")
        #expect(HistoricalAirportCard.cellText(egll.metar?.values, metric: "ceiling_ft") == "600")
        #expect(HistoricalAirportCard.cellText(nil, metric: "ceiling_ft") == "—")
    }

    // MARK: - View model

    private static func loadedViewModel(
        deepLink: HistoricalMapDeepLink? = nil
    ) async throws -> (HistoricalMapViewModel, MockBriefingRepository) {
        let repo = MockBriefingRepository()
        let range = try Self.range()
        let payload = try Self.payload()
        repo.historicalRangeHandler = { range }
        repo.historicalMapHandler = { _, _ in payload }
        let vm = HistoricalMapViewModel(repository: repo, deepLink: deepLink,
                                        now: { Self.date("2026-09-29T12:10:00Z") })
        vm.start()
        for _ in 0..<1000 where !vm.didLoadOnce { await Task.yield() }
        return (vm, repo)
    }

    @Test func coldOpenLoadsTheLatestSlot() async throws {
        let (vm, repo) = try await Self.loadedViewModel()
        #expect(vm.didLoadOnce)
        #expect(vm.selectedInstant == Self.date("2026-09-29T12:00:00Z"))
        #expect(repo.historicalMapRequests.count == 1)
        #expect(repo.historicalMapRequests.first?.lead == 0)
        #expect(vm.source == .metar)
        #expect(vm.mapPayload?.airports.count == 2)
        #expect(!vm.canStepForward, "nothing after the latest slot")
        #expect(vm.canStepBack)
    }

    @Test func unavailableSourceExplainsItselfWithoutSwitching() async throws {
        let (vm, _) = try await Self.loadedViewModel()
        vm.selectSource(.taf)
        #expect(vm.source == .metar)
        #expect(vm.notice == "No TAF reports at this time.")

        vm.notice = nil
        vm.selectSource(.ecmwf)
        #expect(vm.source == .metar)
        #expect(vm.notice?.contains("horizon") == true)

        vm.notice = nil
        vm.selectSource(.icon)
        #expect(vm.notice?.contains("No ICON run") == true)
    }

    @Test func sourceSwitchIsARecolourNotAFetch() async throws {
        let (vm, repo) = try await Self.loadedViewModel()
        let revision = vm.payloadRevision
        vm.selectSource(.gfs)
        #expect(vm.source == .gfs)
        #expect(vm.mapPayload?.airports.map(\.icao) == ["EGLL"])
        #expect(vm.payloadRevision != revision, "markers rebuild: the airport set changed")
        vm.metric = "ceiling_ft"
        #expect(repo.historicalMapRequests.count == 1)
        #expect(vm.statusLine?.contains("run 29 00Z") == true)
    }

    @Test func stepAndLeadRefetch() async throws {
        let (vm, repo) = try await Self.loadedViewModel()
        vm.step(-1)
        for _ in 0..<1000 where repo.historicalMapRequests.count < 2 { await Task.yield() }
        #expect(vm.selectedInstant == Self.date("2026-09-29T11:30:00Z"))
        #expect(repo.historicalMapRequests.last?.at == Self.date("2026-09-29T11:30:00Z"))

        vm.selectLead(2)
        for _ in 0..<1000 where repo.historicalMapRequests.count < 3 { await Task.yield() }
        #expect(repo.historicalMapRequests.last?.lead == 2)
    }

    @Test func deepLinkIsAppliedAndClamped() async throws {
        let link = HistoricalMapDeepLink(date: "2026-09-20", time: "14:40", lead: 3,
                                         source: "ecmwf", metric: "crosswind_kt", airport: nil)
        let (vm, repo) = try await Self.loadedViewModel(deepLink: link)
        // Snapped down onto the 30-min grid.
        #expect(vm.selectedInstant == Self.date("2026-09-20T14:30:00Z"))
        #expect(vm.lead == 3)
        #expect(vm.source == .ecmwf)
        #expect(vm.metric == "crosswind_kt")
        #expect(repo.historicalMapRequests.first?.lead == 3)
    }

    @Test func futureDeepLinkClampsToLatest() async throws {
        let link = HistoricalMapDeepLink(date: "2026-10-05", time: "09:00")
        let (vm, _) = try await Self.loadedViewModel(deepLink: link)
        #expect(vm.selectedInstant == Self.date("2026-09-29T12:00:00Z"))
    }

    @Test func timeGridAndObservationOnlyDays() async throws {
        let (vm, _) = try await Self.loadedViewModel()
        #expect(vm.timeSlots.count == 48)
        #expect(vm.isSelectable(Self.date("2026-09-29T12:00:00Z")))
        #expect(!vm.isSelectable(Self.date("2026-09-29T12:30:00Z")), "future slot")
        #expect(vm.isObservationOnly(Self.date("2026-09-10T12:00:00Z")))
        #expect(!vm.isObservationOnly(Self.date("2026-09-19T00:00:00Z")))
        #expect(vm.models(forLead: 6) == ["gfs", "ecmwf"])
    }

    @Test func selectDayKeepsTheTimeOfDay() async throws {
        let (vm, repo) = try await Self.loadedViewModel()
        vm.selectDay(Self.date("2026-09-25T00:00:00Z"))
        for _ in 0..<1000 where repo.historicalMapRequests.count < 2 { await Task.yield() }
        #expect(vm.selectedInstant == Self.date("2026-09-25T12:00:00Z"))
    }

    // MARK: - Universal link routing

    @Test func historicalMapsLinkRoutesWithState() {
        let url = URL(string: "https://weather.flyfun.aero/maps.html?tab=historical&hist.date=2026-09-20&hist.time=14:30&hist.lead=2&hist.source=taf&hist.metric=ceiling_ft&hist.apt=EGLL")!
        #expect(AppState.navigationTarget(for: url) == .historicalMap(HistoricalMapDeepLink(
            date: "2026-09-20", time: "14:30", lead: 2, source: "taf", metric: "ceiling_ft", airport: "EGLL")))
    }

    @Test func bareHistoricalTabIsEmptyDeepLink() {
        let url = URL(string: "https://weather.flyfun.aero/maps.html?tab=historical")!
        guard case .historicalMap(let dl) = AppState.navigationTarget(for: url) else {
            Issue.record("expected historicalMap"); return
        }
        #expect(dl.isEmpty)
        #expect(dl.instant == nil, "empty date/time means the latest slot")
    }

    @Test func otherTabsStillOpenTheForecastMap() {
        let url = URL(string: "https://weather.flyfun.aero/maps.html?tab=synoptic&fc.day=1")!
        guard case .forecastMap(let dl) = AppState.navigationTarget(for: url) else {
            Issue.record("expected forecastMap"); return
        }
        #expect(dl.day == 1)
    }
}
