//
//  CrossSectionExtractTests.swift
//  flyfun-weatherTests
//
//  Deferred from #314 Tier B (it needed the heavier `RouteAnalysesResponse`
//  fixture, now built as `FixtureBriefingData` for #318): a unit test for the
//  static `CrossSectionViewModel.extractVizData` extractor — the port of the
//  web's `data-extract.ts`. Drives it off the shared briefing fixture, so it
//  also exercises the production decode path (`JSONDecoder.weatherBrief`) end to
//  end. `@testable import` sees the DEBUG-only fixtures because the test target
//  builds against the Debug app.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite @MainActor struct CrossSectionExtractTests {

    /// Every fixture blob decodes through the real decoder. `FixtureBriefingData`
    /// decodes with `try!`, so just touching each property fails loudly here if a
    /// model change breaks the contract — the same guard the UI journeys rely on,
    /// surfaced as a fast unit test instead of only at XCUITest runtime.
    @Test func allBriefingFixturesDecode() {
        #expect(FixtureBriefingData.pack.flightId == "fixture-1")
        #expect(FixtureBriefingData.packs.count == 1)
        #expect(FixtureBriefingData.advisories.advisories.count == 4)
        #expect(FixtureBriefingData.convectiveDetail.advisoryId == "convective")
        #expect(FixtureBriefingData.digest.assessment == "amber")
        #expect(FixtureBriefingData.snapshot.route.waypoints.count == 2)
        #expect(FixtureBriefingData.routeAnalyses.analyses.count == 5)
        #expect(FixtureBriefingData.elevation.points.count == 5)
    }

    /// The convective advisory is the drill-down target: RED overall, with a
    /// per-model split (GFS red + cross-check, ECMWF amber) and the CAPE-vs-cover
    /// reconciliation the detail sheet renders.
    @Test func convectiveAdvisoryIsRedWithPerModelSplit() {
        let convective = FixtureBriefingData.advisories.advisories.first { $0.advisoryId == "convective" }
        let adv = try! #require(convective)
        #expect(adv.aggregateStatus == "red")
        let gfs = adv.perModel.first { $0.model == "gfs" }
        #expect(gfs?.status == "red")
        #expect(gfs?.crossCheck?.isEmpty == false)
        #expect(adv.perModel.first { $0.model == "ecmwf" }?.status == "amber")

        let detail = FixtureBriefingData.convectiveDetail
        #expect(detail.convective?["gfs"]?.thermo?.peak?.capeJkg == 1400.0)
        #expect(detail.convective?["gfs"]?.nwp?.maxCoverPct == 2.0)  // blue-sky RED
    }

    // MARK: - Flight path (#1005)

    /// A manifest with the live fixture's path, changed the way a test needs.
    private func manifest(mode: String?, keepPath: Bool = true) throws -> RouteAnalysesResponse {
        var manifest = FixtureBriefingData.routeAnalysesLive
        manifest.flightPathMode = mode
        if !keepPath { manifest.flightPath = nil }
        return manifest
    }

    @Test func liveFixtureDecodesThePathAndPointFields() throws {
        let manifest = FixtureBriefingData.routeAnalysesLive
        let path = try #require(manifest.flightPath)
        #expect(manifest.flightPathMode == "live")
        #expect(path.cruiseAltitudeFt == 8000)
        #expect(path.cruiseReached)
        #expect(path.tocNm < path.todNm)
        #expect(path.profile.allSatisfy { $0.count == 3 })
        #expect(manifest.analyses.first?.phase == "climb")
        #expect(manifest.analyses.last?.phase == "descent")
        #expect(manifest.analyses.first?.plannedAltitudeFt == 0)
    }

    @Test func packWithoutAPathDecodesAndDrawsFlat() {
        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalyses, model: "gfs",
            elevation: FixtureBriefingData.elevation)
        #expect(FixtureBriefingData.routeAnalyses.flightPath == nil)
        #expect(viz.flightPath == nil)
    }

    @Test func livePathAtThePlannedCruiseIsDrawn() throws {
        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalysesLive, model: "gfs",
            elevation: FixtureBriefingData.elevation)
        let path = try #require(viz.flightPath)
        let profile = try #require(FixtureBriefingData.routeAnalysesLive.flightPath?.profile)
        #expect(path.count == profile.count)
        #expect(path.first?.altitudeFt == profile.first?[1])
        #expect(path.map(\.altitudeFt).max() == 8000)
    }

    @Test func shadowOrUnknownModeDrawsFlat() throws {
        for mode in ["shadow", "off", nil] as [String?] {
            let path = CrossSectionViewModel.drawnFlightPath(
                from: try manifest(mode: mode), effectiveCruiseFt: 8000)
            #expect(path == nil)
        }
    }

    @Test func nullPathDrawsFlat() throws {
        let path = CrossSectionViewModel.drawnFlightPath(
            from: try manifest(mode: "live", keepPath: false), effectiveCruiseFt: 8000)
        #expect(path == nil)
    }

    @Test func anotherAltitudeDrawsFlat() throws {
        let live = try manifest(mode: "live")
        #expect(CrossSectionViewModel.drawnFlightPath(from: live, effectiveCruiseFt: 9000) == nil)
        #expect(CrossSectionViewModel.drawnFlightPath(from: live, effectiveCruiseFt: 8001) == nil)
        #expect(CrossSectionViewModel.drawnFlightPath(from: live, effectiveCruiseFt: 8000.5) != nil)
    }

    @Test func pathLabelSitsAtTopOfClimb() throws {
        let path = try #require(CrossSectionViewModel.drawnFlightPath(
            from: FixtureBriefingData.routeAnalysesLive, effectiveCruiseFt: 8000))
        let toc = try #require(FixtureBriefingData.routeAnalysesLive.flightPath?.tocNm)
        let label = try #require(path.labelPoint(cruiseFt: 8000))
        #expect(abs(label.distanceNm - toc) < 0.01)

        // A short leg that never reaches cruise labels the start of the path.
        let short = [VizFlightPathPoint(distanceNm: 0, altitudeFt: 0),
                     VizFlightPathPoint(distanceNm: 5, altitudeFt: 3000)]
        #expect(short.labelPoint(cruiseFt: 8000)?.distanceNm == 0)
    }

    /// The main extractor: maps the route-analyses manifest into `VizRouteData`
    /// for a model, folding in the elevation profile.
    @Test func extractsVizDataForGFS() {
        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalyses,
            model: "gfs",
            elevation: FixtureBriefingData.elevation
        )

        // One VizPoint per route point; route-level scalars carried through.
        #expect(viz.points.count == 5)
        #expect(viz.totalDistanceNm == 78.0)
        #expect(viz.cruiseAltitudeFt == 8000.0)

        // Waypoint markers only for the points that carry an ICAO (departure +
        // arrival), in route order.
        #expect(viz.waypointMarkers.map(\.icao) == ["LFMD", "LFML"])

        // Terrain profile mirrors the elevation fixture.
        #expect(viz.terrainProfile?.count == 5)

        // Point 0 (LFMD) on GFS is the convective peak: high risk, CAPE 1400,
        // icing zone present, temperature pulled from the divergence block.
        let p0 = viz.points[0]
        #expect(p0.convectiveRisk == "high")
        #expect(p0.capeSurfaceJkg == 1400.0)
        #expect(p0.icingZones.isEmpty == false)
        #expect(p0.temperatureC == 11.0)
        #expect(p0.cloudLayers.isEmpty == false)
    }

    /// The same manifest read for ECMWF resolves the per-model sounding — the
    /// convective peak is only moderate, proving the extractor keys off `model`
    /// rather than collapsing all models together.
    @Test func extractsPerModelSoundingForECMWF() {
        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalyses,
            model: "ecmwf",
            elevation: FixtureBriefingData.elevation
        )
        let p0 = viz.points[0]
        #expect(p0.convectiveRisk == "moderate")
        #expect(p0.capeSurfaceJkg == 600.0)
        #expect(p0.temperatureC == 12.0)
    }

    /// A model with no sounding in the manifest still yields one VizPoint per
    /// route point, with the documented defensive defaults (no convection, zero
    /// CAPE, empty layers) rather than crashing — the "No Data for Model" path.
    @Test func extractsDefensiveDefaultsForUnknownModel() {
        let viz = CrossSectionViewModel.extractVizData(
            from: FixtureBriefingData.routeAnalyses,
            model: "nonexistent",
            elevation: nil
        )
        #expect(viz.points.count == 5)
        #expect(viz.terrainProfile == nil)
        let p0 = viz.points[0]
        #expect(p0.convectiveRisk == "none")
        #expect(p0.capeSurfaceJkg == 0.0)
        #expect(p0.cloudLayers.isEmpty)
        #expect(p0.icingZones.isEmpty)
        #expect(p0.temperatureC == nil)
    }
}
