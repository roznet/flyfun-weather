//
//  ObservedConditionsTests.swift
//  flyfun-weatherTests
//
//  Decode + resolve tests for observed conditions (#574) — the iOS port of the
//  web's `buildObserved`/`mergeObserved`.
//
//  Two classes of failure these exist to catch, both of which are SILENT:
//
//   • A key that `.convertFromSnakeCase` does not map the way we assumed decodes
//     to `nil`, not to an error. `flashes_per_1000km2_per_min` is the one with a
//     digit run in the middle, and the histograms are DICTIONARIES, whose keys
//     that strategy also rewrites.
//   • Folding the three-state absence into two. `maxValue == nil` is true both
//     when the radar looked and saw nothing and when it does not look there at
//     all, and about half the OPERA grid is the second case. A renderer that
//     reads the optional instead of `insufficientCoverage` paints half of Europe
//     as clear sky.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite struct ObservedConditionsTests {

    /// Shaped exactly like a real `briefing.json` slice: snake_case keys, the
    /// pydantic computed fields present on the wire, `Z`-suffixed instants, and
    /// dictionary keys in the three histogram shapes the server actually emits.
    static let payloadJSON = """
    {
      "computed_at": "2026-08-26T13:05:07.597699Z",
      "corridor_nm": 30.0,
      "radii_nm": [5.0, 10.0, 20.0],
      "stations": [
        {"id": "p0", "name": null, "lat": 49.0, "lon": 2.0,
         "enroute_distance_nm": 0.0, "distance_from_route_nm": 0.0},
        {"id": "p1", "name": null, "lat": 49.5, "lon": 1.5,
         "enroute_distance_nm": 40.0, "distance_from_route_nm": 0.0}
      ],
      "reflectivity": {
        "source": "opera_dbzh", "quantity": "DBZH", "units": "dBZ",
        "valid_time": "2026-08-26T13:00:00Z", "age_minutes": 5.1,
        "window_minutes": 9.983333333333333,
        "attribution": {"producer": "EUMETNET", "license": null, "url": null,
                        "text": "OPERA / EUMETNET"},
        "stations": [
          {"station_id": "p0", "annuli": [
            {"radius_nm": 20.0, "total_px": 700, "valid_px": 700, "nodata_px": 0,
             "undetect_px": 600, "detected_px": 100, "max_value": 42.0,
             "mean_value": 20.0, "p90_value": 38.0,
             "coverage_fraction": 1.0, "detected_fraction": 0.142,
             "insufficient_coverage": false}]},
          {"station_id": "p1", "annuli": [
            {"radius_nm": 20.0, "total_px": 700, "valid_px": 70, "nodata_px": 630,
             "undetect_px": 70, "detected_px": 0, "max_value": null,
             "mean_value": null, "p90_value": null,
             "coverage_fraction": 0.1, "detected_fraction": 0.0,
             "insufficient_coverage": true}]}
        ]
      },
      "lightning": {
        "source": "eumetsat_li", "quantity": "flashes", "units": "count",
        "valid_time": "2026-08-26T12:50:08Z", "age_minutes": 15.0,
        "window_minutes": 10.0,
        "attribution": {"producer": "EUMETSAT", "license": null, "url": null,
                        "text": "MTG-LI / EUMETSAT"},
        "stations": [
          {"station_id": "p0", "annuli": [
            {"radius_nm": 20.0, "flash_count": 7, "area_km2": 4310.14,
             "window_minutes": 10.0, "nearest_flash_nm": 3.2,
             "latest_flash_time": "2026-08-26T12:49:00Z",
             "flashes_per_1000km2_per_min": 0.1624}]}
        ]
      },
      "cloud_tops": {
        "source": "eumetsat_ctth", "quantity": "cloud_top_height", "units": "m",
        "valid_time": "2026-08-26T12:52:07Z", "age_minutes": 13.0,
        "window_minutes": 0.0,
        "attribution": {"producer": "EUMETSAT", "license": null, "url": null,
                        "text": "MTG-FCI CTTH / EUMETSAT"},
        "stations": [
          {"station_id": "p0", "annuli": [
            {"radius_nm": 20.0, "total_px": 702, "valid_px": 700, "nodata_px": 2,
             "undetect_px": 348, "detected_px": 352, "max_value": 11306.0,
             "highest_fl": 370.93, "coldest_top_k": 214.93,
             "highest_cloudiness": 0.29, "median_cloudiness": 0.31,
             "highest_aviation_fl": 380.0,
             "fl_bins": {"FL250-400": 300, "FL050-150": 52},
             "fl_fine": {"60": 52, "70": 100, "360": 200},
             "quality_method": {"0": 348, "1": 91, "8": 175, "9": 86},
             "coverage_fraction": 0.997, "detected_fraction": 0.502,
             "insufficient_coverage": false}]},
          {"station_id": "p1", "annuli": [
            {"radius_nm": 20.0, "total_px": 700, "valid_px": 70, "nodata_px": 630,
             "undetect_px": 70, "detected_px": 0, "max_value": null,
             "highest_fl": null, "coldest_top_k": null,
             "fl_bins": {}, "fl_fine": {}, "quality_method": {},
             "coverage_fraction": 0.1, "detected_fraction": 0.0,
             "insufficient_coverage": true}]}
        ]
      },
      "summary": "Radar: peak 42 dBZ.",
      "summary_entries": [
        {"kind": "reflectivity", "text": "Radar: peak 42 dBZ.", "metric_id": "observed_surface"}
      ],
      "summary_lines": ["Radar: peak 42 dBZ."],
      "sources": [
        {"source": "opera_rate", "available": false, "reason": "no current frame",
         "latest_valid_time": null}
      ]
    }
    """

    static func decoded() throws -> ObservedConditions {
        try JSONDecoder.weatherBrief.decode(
            ObservedConditions.self, from: Data(payloadJSON.utf8))
    }

    // MARK: - Decoding

    @Test func decodesEveryField() throws {
        let o = try Self.decoded()
        #expect(o.radiiNm == [5, 10, 20])
        #expect(o.stations?.count == 2)
        #expect(o.reflectivity?.source == "opera_dbzh")
        #expect(o.reflectivity?.attribution?.text == "OPERA / EUMETNET")
        #expect(o.cloudTops?.ageMinutes == 13.0)
        #expect(o.lightning?.windowMinutes == 10.0)
        #expect(o.hasAnyField)
        // A source that is present-and-saw-nothing must stay distinct from one
        // that is absent, one level up from the pixel counts.
        #expect(o.sources?.first(where: { $0.source == "opera_rate" })?.available == false)
        #expect(o.rainRate == nil)
    }

    /// `.convertFromSnakeCase` splits on underscores and applies `.capitalized`
    /// to each later component, so `1000km2` becomes `1000Km2` — `capitalized`
    /// uppercases a word's first LETTER, not its first character. The natural
    /// spelling matches nothing and decodes to nil rather than throwing, so the
    /// lightning rate would vanish from the chart in silence. This test caught
    /// exactly that during the port.
    @Test func decodesFlashRateKey() throws {
        let o = try Self.decoded()
        let annulus = o.lightning?.stations.first?.annuli.first
        #expect(annulus?.flashesPer1000Km2PerMin == 0.1624)
        #expect(annulus?.nearestFlashNm == 3.2)
        // The plain-suffix siblings, to show the K is specific to the digit run.
        #expect(annulus?.areaKm2 == 4310.14)
    }

    /// The same strategy rewrites DICTIONARY keys. None of the three histograms
    /// uses an underscore in its keys, which is the only reason they survive
    /// verbatim — add one server-side and this test is what catches it.
    @Test func histogramKeysSurviveKeyConversion() throws {
        let a = try #require(try Self.decoded().cloudTops?.stations.first?.annuli.first)
        #expect(a.flFine?["60"] == 52)
        #expect(a.flFine?["360"] == 200)
        #expect(a.flBins?["FL250-400"] == 300)
        #expect(a.qualityMethod?["9"] == 86)
        #expect(a.qualityMethod?["0"] == 348)
    }

    // MARK: - Resolving

    @Test func resolvesAtTheWidestRadiusByDefault() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        #expect(resolved.radiusNm == 20)
        #expect(resolved.radiiNm == [5, 10, 20])
        #expect(resolved.points.map(\.distanceNm) == [0, 40])
        #expect(resolved.cloudTops?.label == "Satellite cloud tops")
    }

    /// An unsampled radius must not be honoured: the payload only carries discs
    /// the server measured, and quietly resolving to a different width than the
    /// UI claims would mislabel every value on the chart.
    @Test func ignoresARadiusTheServerDidNotSample() throws {
        let resolved = try #require(
            ObservedResolver.resolve(try Self.decoded(), radiusOverrideNm: 7))
        #expect(resolved.radiusNm == 20)
    }

    /// The load-bearing one. `p1` has 90% of its disc outside radar coverage, so
    /// it must read "we cannot see there", never "no echo" — and `p0` with a real
    /// detection must not be dragged into the no-coverage state.
    @Test func noCoverageIsNotClearSky() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let clearOfDoubt = try #require(resolved.points.first { $0.distanceNm == 0 })
        let blind = try #require(resolved.points.first { $0.distanceNm == 40 })

        #expect(clearOfDoubt.dbz == 42)
        #expect(clearOfDoubt.radarNoCoverage == false)

        // Both have `dbz == nil`-shaped absence in the raw payload; only the flag
        // separates them, which is exactly why renderers must read the flag.
        #expect(blind.dbz == nil)
        #expect(blind.radarNoCoverage)
    }

    @Test func prefersTheFineHistogramOverTheCoarseBands() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let p = try #require(resolved.points.first { $0.distanceNm == 0 })
        // 3 fine bands, not the 5 coarse ones — and in ascending altitude order.
        #expect(p.topsBins.count == 3)
        #expect(p.topsBins.map(\.loFt) == [6_000, 7_000, 36_000])
        #expect(p.topsBins[0].label == "FL060-070")
        #expect(p.topsBins[2].label == "FL360-370")
        #expect(p.topsBins[2].count == 200)
    }

    /// A band's share is of the LOOKED-AT SKY (`valid_px` = 700), not of the
    /// cloudy pixels (`detected_px` = 352).
    ///
    /// A share of the cloud answers "of the cloud that was found, how much
    /// topped out here?" — a number with no cockpit meaning, which inflates as
    /// the sky clears. Of the sky, a band reads directly as coverage.
    @Test func bandShareIsOfTheSkyNotOfTheCloud() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let p = try #require(resolved.points.first { $0.distanceNm == 0 })

        #expect(abs(p.topsBins[2].fraction - 200.0 / 700.0) < 1e-9)

        // The bands sum to the disc's cloud cover (352/700), NOT to 1.0. That
        // identity is the whole point: a sum of 1.0 would mean the percentages
        // had been renormalised back onto the cloud.
        let total = p.topsBins.reduce(0.0) { $0 + $1.fraction }
        #expect(abs(total - 352.0 / 700.0) < 1e-9)
        #expect(total < 0.99)
    }

    /// The drawing floor is 5% OF THE SKY, and strictly greater — the same
    /// denominator the band is drawn as, so the floor means what the legend
    /// means. Two cloudy pixels out of 131 are 1.5% of the sky: a sliver drawn
    /// with the visual weight of a deck you could fly into, and now dropped.
    /// Under the older cloud-share floor the same band was 50% and survived.
    @Test func thinBandsBelowFivePercentOfSkyAreDropped() {
        var point = VizObservedPoint(distanceNm: 0)
        point.topsBins = [
            VizObservedTopBin(label: "FL180-190", loFt: 18_000, hiFt: 19_000,
                              fraction: 2.0 / 131.0, count: 2),
            // Exactly at the floor is out: the rule is "more than".
            VizObservedTopBin(label: "FL200-210", loFt: 20_000, hiFt: 21_000,
                              fraction: 0.05, count: 7),
            VizObservedTopBin(label: "FL220-230", loFt: 22_000, hiFt: 23_000,
                              fraction: 0.31, count: 41),
        ]
        let kept = ObservedTopsLayer.significantBins(point)
        #expect(kept.map(\.label) == ["FL220-230"])
    }

    /// A point whose every band fell under the floor keeps its cap: the highest
    /// top comes from its own field and never passes through the filter, which
    /// is what makes dropping the tail safe.
    @Test func theHighestTopSurvivesAFullyFilteredPoint() {
        var point = VizObservedPoint(distanceNm: 0)
        point.topsHighestFt = 37_000
        point.topsBins = [
            VizObservedTopBin(label: "FL360-370", loFt: 36_000, hiFt: 37_000,
                              fraction: 0.01, count: 3),
        ]
        #expect(ObservedTopsLayer.significantBins(point).isEmpty)
        let observed = VizObserved(
            radiiNm: [20], radiusNm: 20, points: [point],
            reflectivity: nil, rainRate: nil, cloudTops: nil, lightning: nil,
            summaryLines: [])
        #expect(ObservedTopsLayer.drawablePoints(observed).count == 1)
    }

    /// Every theme's ramp shares one set of breakpoints, and they START at the
    /// drawing floor — a stop below it would colour bands that never appear.
    /// Only the luminance direction differs between themes, because the skies do.
    @Test func everyThemeRampStartsAtTheDrawingFloor() {
        let expected = [0.05, 0.07, 0.10, 0.15, 0.22, 0.35, 0.55]
        for id in CrossSectionThemeID.allCases {
            #expect(id.theme.observed.shareStops.map(\.0) == expected, "\(id.rawValue)")
            #expect(id.theme.observed.shareStops[0].0 == ObservedTopsLayer.minBinFraction)
        }
    }

    @Test func convertsTopUnits() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let p = try #require(resolved.points.first { $0.distanceNm == 0 })
        // highest_fl is a flight level; the chart works in feet.
        #expect(abs(p.topsHighestFt! - 37_093) < 1)
        // Kelvin on the wire, °C for anything a pilot reads.
        #expect(abs(p.topsColdestC! - (214.93 - 273.15)) < 1e-6)
        // qm 9 over detected pixels, not over the whole disc.
        #expect(abs(p.topsMultiLayerFraction - 86.0 / 352.0) < 1e-9)
        #expect(p.topsHighestAviationFl == 380)
    }

    // MARK: - Merging onto route points

    @Test func mergesOntoRoutePointsWithinTolerance() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        var points = [vizPoint(0), vizPoint(41), vizPoint(120)]
        ObservedResolver.merge(into: &points, observed: resolved)

        #expect(points[0].observed?.dbz == 42)
        // 1 nm off is a rounding difference between two walks of the same route.
        #expect(points[1].observed?.radarNoCoverage == true)
        // 80 nm off is a genuinely different place — no station, no sample. The
        // alternative (nearest-wins with no ceiling) would report a Paris echo
        // over the Alps.
        #expect(points[2].observed == nil)
    }

    // MARK: - Badge

    /// The one payload-level timestamp is an ASSEMBLY time; only per-source
    /// instants may be rendered as an age.
    @Test func badgeRendersThePerSourceInstant() throws {
        let o = try Self.decoded()
        let tops = try #require(o.cloudTops)
        #expect(ObservedBadge.ageText(tops.validTime, tops.ageMinutes, "Satellite")
                == "Satellite 12:52Z · 13 min old")
        let radar = try #require(o.reflectivity)
        #expect(ObservedBadge.ageText(radar.validTime, radar.ageMinutes, "Radar")
                == "Radar 13:00Z · 5 min old")
        // Sub-minute reads as "just now" rather than "0 min old".
        #expect(ObservedBadge.ageText(radar.validTime, 0.4, "Radar").hasSuffix("just now"))
    }

    @Test func badgeSurvivesAnUnparseableInstant() {
        // "--:--" rather than inventing a time, and never a crash.
        #expect(ObservedBadge.ageText("not-a-date", 3, "Radar") == "Radar --:--Z · 3 min old")
    }

    @Test func flLabelRounds() {
        #expect(ObservedBadge.flLabel(37_093) == "FL371")
    }

    // MARK: - Layer helpers

    @Test func thinBandsAreDroppedButTheHighestTopIsNot() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let p = try #require(resolved.points.first { $0.distanceNm == 0 })
        // As a share of the looked-at sky (700 px): 52/700 = 7.4%, 100/700 =
        // 14.3%, 200/700 = 28.6% — all above the 5% floor, so nothing is
        // dropped here…
        #expect(ObservedTopsLayer.significantBins(p).count == 3)
        // …and the point is drawable on any of the three grounds.
        #expect(ObservedTopsLayer.drawablePoints(resolved).contains { $0.distanceNm == 0 })
        // A no-coverage point is drawable too, and that is the whole point: it
        // draws a hatched "could not see" mark. Skipping it would leave a gap,
        // and a gap reads as "nothing up there" — the one thing the retrieval
        // did not say.
        let blind = try #require(resolved.points.first { $0.distanceNm == 40 })
        #expect(blind.topsNoCoverage)
        #expect(blind.topsHighestFt == nil)
        #expect(blind.topsBins.isEmpty)
        #expect(ObservedTopsLayer.drawablePoints(resolved).contains { $0.distanceNm == 40 })
    }

    /// A gap between runs is a real, measured absence of cloud top — the thing
    /// coarse bins destroyed. FL060-070 and FL070-080 are contiguous; FL360 is
    /// not, and must start its own deck.
    @Test func bandRunsSplitOnRealGaps() throws {
        let resolved = try #require(ObservedResolver.resolve(try Self.decoded()))
        let p = try #require(resolved.points.first { $0.distanceNm == 0 })
        let runs = ObservedTopsLayer.bandRuns(ObservedTopsLayer.significantBins(p))
        #expect(runs.count == 2)
        #expect(runs[0].count == 2)
        #expect(runs[1].count == 1)
        #expect(runs[1][0].loFt == 36_000)
    }

    @Test func flashTicksAreLogarithmic() {
        #expect(ObservedSurfaceLayer.flashTickCount(0) == 0)
        #expect(ObservedSurfaceLayer.flashTickCount(1) == 1)
        #expect(ObservedSurfaceLayer.flashTickCount(2) == 2)
        #expect(ObservedSurfaceLayer.flashTickCount(8) == 4)
        // Capped, so a thousand-flash disc doesn't draw a picket fence.
        #expect(ObservedSurfaceLayer.flashTickCount(5000) == 4)
    }

    // MARK: - Absent payload

    @Test func resolvesToNilWhenThereIsNothingToDraw() {
        #expect(ObservedResolver.resolve(nil) == nil)
        var points = [vizPoint(0)]
        ObservedResolver.merge(into: &points, observed: nil)
        #expect(points[0].observed == nil)
    }

    // MARK: - Helpers

    private func vizPoint(_ distanceNm: Double) -> VizPoint {
        VizPoint(
            distanceNm: distanceNm, lat: 49, lon: 2, time: "2026-08-26T13:00:00Z",
            altitudeLines: AltitudeLines(
                freezingLevelFt: nil, minus10cLevelFt: nil, minus20cLevelFt: nil,
                lclAltitudeFt: nil, lfcAltitudeFt: nil, elAltitudeFt: nil),
            cloudLayers: [], nwpCloudLayers: nil, icingZones: [], icingOgimetNwpZones: [],
            sfipZones: [], catLayers: [], inversions: [],
            convectiveRisk: "none", convectiveBaseFt: nil, convectiveTopFt: nil,
            nwpConvectiveRisk: "none", nwpConvectiveBaseFt: nil, nwpConvectiveTopFt: nil,
            nwpConvectiveCoverPct: nil, nwpConvectiveMethod: nil, hasNwpConvective: false,
            cloudCoverTotalPct: 0, cloudCoverLowPct: 0, cloudCoverMidPct: 0,
            headwindKt: 0, crosswindKt: 0, capeSurfaceJkg: 0,
            worstModelAgreement: "good", nwpCloudDiag: nil,
            temperatureC: nil, precipitationMm: nil)
    }
}
