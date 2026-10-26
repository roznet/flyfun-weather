//
//  CrossSectionFrontsTests.swift
//  flyfun-weatherTests
//
//  The air-mass boundary layer (experimental Hewson fronts, #196): the wire
//  shape of `route_fronts.json`, where the layer sits on the layer bar (web
//  #945 moved it to Stability), and when it greys out. The view-model side
//  (lenses and emulations leave it alone) is in `CrossSectionLensTests`.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite struct CrossSectionFrontsTests {

    private static let json = """
    {
      "schema_version": 1, "route_name": "r", "primary_level_hPa": 850,
      "per_model": {"ecmwf": []},
      "front_chains": {
        "ecmwf": [{
          "kind": "cold", "n_levels": 2, "tilt": "coldward",
          "nodes": [
            {"level_hPa": 925, "distance_km": 120.5, "kind": "cold", "intensity": "sharp",
             "gradient": 3.1, "delta_theta_e": 4.2, "co_location": "convective"},
            {"level_hPa": 850, "distance_km": 131.0, "kind": "cold", "intensity": "classical",
             "gradient": 2.8, "delta_theta_e": 3.9, "co_location": null}
          ]
        }]
      }
    }
    """

    private static func data(chains: [FrontChain]) -> VizRouteData {
        VizRouteData(points: [], cruiseAltitudeFt: 6000, ceilingAltitudeFt: 6000,
                     flightCeilingFt: 11000, totalDistanceNm: 100, waypointMarkers: [],
                     departureTime: "", flightDurationHours: 1, terrainProfile: nil,
                     frontChains: chains)
    }

    @Test func decodesTheServerLinkedChains() throws {
        let manifest = try JSONDecoder.weatherBrief.decode(
            RouteFrontsResponse.self, from: Data(Self.json.utf8))
        let chain = try #require(manifest.frontChains?["ecmwf"]?.first)
        #expect(chain.kind == "cold")
        #expect(chain.nLevels == 2)
        #expect(chain.nodes.map(\.levelHpa) == [925, 850])  // `level_hPa` on the wire
        #expect(chain.nodes[0].distanceKm == 120.5)
        #expect(chain.nodes[0].coLocation == "convective")
        #expect(chain.nodes[1].coLocation == nil)
    }

    @Test func aPackWithoutChainsDecodes() throws {
        let manifest = try JSONDecoder.weatherBrief.decode(
            RouteFrontsResponse.self, from: Data(#"{"per_model": {}}"#.utf8))
        #expect(manifest.frontChains == nil)
    }

    @Test func theBoundarySitsUnderStabilityNotObserved() {
        #expect(LayerFamily.family(for: .fronts) == .stability)
        #expect(LayerFamily.stability.layerIds.contains("fronts-markers"))
        #expect(!LayerFamily.observed.layerIds.contains("fronts-markers"))
    }

    @Test func itIsOffByDefaultAndOutsideTheLensReset() {
        #expect(CrossSectionPresets.bootDefaults["fronts-markers"] == false)
        #expect(!CrossSectionPresets.resetGroups.contains(.fronts))
        for preset in CrossSectionPresets.all {
            #expect(preset.enabledLayers["fronts-markers"] == nil, "\(preset.id) must not touch it")
        }
    }

    @Test func greysOutWithoutChains() throws {
        #expect(NwpFallback.unavailableLayers(in: Self.data(chains: [])).contains("fronts-markers"))
        let manifest = try JSONDecoder.weatherBrief.decode(
            RouteFrontsResponse.self, from: Data(Self.json.utf8))
        let chains = manifest.frontChains?["ecmwf"] ?? []
        #expect(!NwpFallback.unavailableLayers(in: Self.data(chains: chains)).contains("fronts-markers"))
    }
}
