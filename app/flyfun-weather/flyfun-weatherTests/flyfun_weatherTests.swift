//
//  flyfun_weatherTests.swift
//  flyfun-weatherTests
//
//  Created by Brice Rosenzweig on 01/03/2026.
//

import Testing
import Foundation
@testable import flyfun_weather

struct flyfun_weatherTests {

    @Test func example() async throws {
        // Write your test here and use APIs like `#expect(...)` to check expected conditions.
    }

}

// MARK: - Phase 0 (#286): Data plumbing decode tests
//
// Verify the extended fields the server already sends now decode into the iOS
// models instead of being dropped. Fixtures use the same snake_case JSON the API
// emits and decode through the production `JSONDecoder.weatherBrief`.

struct Phase0DataPlumbingTests {

    /// Extended `SoundingProfileLevel` fields (RH, θe, lapse, Ri, ω, CC, CLW, ICE,
    /// icing indices) decode from snake_case JSON.
    @Test func soundingProfileLevelDecodesExtendedFields() throws {
        let json = """
        {
          "point_index": 3,
          "lat": 43.5,
          "lon": 6.95,
          "distance_from_origin_nm": 42.0,
          "waypoint_icao": "LFMD",
          "model": "gfs",
          "time": "2026-06-24T12:00:00Z",
          "cruise_altitude_ft": 8000,
          "levels": [
            {
              "pressure_hpa": 850,
              "altitude_ft": 4781.0,
              "temperature_c": 12.5,
              "dewpoint_c": 9.0,
              "wind_speed_kt": 18.0,
              "wind_direction_deg": 270.0,
              "relative_humidity_pct": 79.0,
              "dewpoint_depression_c": 3.5,
              "wet_bulb_c": 10.6,
              "theta_e_k": 318.4,
              "lapse_rate_c_per_km": 6.2,
              "icing_index": 22.0,
              "icing_index_nwp": 18.0,
              "sfip_100": 30.0,
              "cloud_liquid_water_g_m3": 0.12,
              "ice_mixing_ratio_g_kg": 0.04,
              "cloud_area_fraction_pct": 65.0,
              "richardson_number": 1.8,
              "omega_pa_s": -0.35,
              "w_fpm": 120.0
            }
          ]
        }
        """
        let data = Data(json.utf8)
        let response = try JSONDecoder.weatherBrief.decode(SoundingProfileResponse.self, from: data)

        #expect(response.levels.count == 1)
        let level = try #require(response.levels.first)

        // Basic fields still decode.
        #expect(level.pressureHpa == 850)
        #expect(level.temperatureC == 12.5)
        #expect(level.dewpointC == 9.0)
        #expect(level.windDirectionDeg == 270.0)

        // Extended fields — previously dropped, now decoded.
        #expect(level.relativeHumidityPct == 79.0)
        #expect(level.dewpointDepressionC == 3.5)
        #expect(level.wetBulbC == 10.6)
        #expect(level.thetaEK == 318.4)
        #expect(level.lapseRateCPerKm == 6.2)
        #expect(level.icingIndex == 22.0)
        #expect(level.icingIndexNwp == 18.0)
        #expect(level.sfip100 == 30.0)
        #expect(level.cloudLiquidWaterGM3 == 0.12)
        #expect(level.iceMixingRatioGKg == 0.04)
        #expect(level.cloudAreaFractionPct == 65.0)
        #expect(level.richardsonNumber == 1.8)
        #expect(level.omegaPaS == -0.35)
        #expect(level.wFpm == 120.0)
    }

    /// Extended fields are optional: a minimal (basic-only) level still decodes,
    /// with the extended fields nil. Guards against regressing offline/cached packs
    /// produced before the server emitted the extended fields.
    @Test func soundingProfileLevelExtendedFieldsAreOptional() throws {
        let json = """
        {
          "point_index": 0,
          "lat": 43.0,
          "lon": 5.0,
          "distance_from_origin_nm": 0.0,
          "waypoint_icao": "LFML",
          "model": "ecmwf",
          "time": "2026-06-24T12:00:00Z",
          "cruise_altitude_ft": 8000,
          "levels": [
            {
              "pressure_hpa": 1000,
              "altitude_ft": 364.0,
              "temperature_c": 20.0,
              "dewpoint_c": 14.0,
              "wind_speed_kt": 10.0,
              "wind_direction_deg": 200.0
            }
          ]
        }
        """
        let data = Data(json.utf8)
        let response = try JSONDecoder.weatherBrief.decode(SoundingProfileResponse.self, from: data)
        let level = try #require(response.levels.first)

        #expect(level.temperatureC == 20.0)
        #expect(level.relativeHumidityPct == nil)
        #expect(level.thetaEK == nil)
        #expect(level.richardsonNumber == nil)
        #expect(level.icingIndexNwp == nil)
    }

    /// `cross_check` decodes into `ModelAdvisoryResult`, and `parameters_used`
    /// reaches the model (surfaced for the advisory-detail UI).
    @Test func modelAdvisoryResultDecodesCrossCheckAndParameters() throws {
        let json = """
        {
          "advisories": [
            {
              "advisory_id": "convective",
              "aggregate_status": "red",
              "aggregate_detail": "RED on GFS/ICON",
              "parameters_used": {
                "cape_red_jkg": 1000.0,
                "affected_pct_red": 50.0
              },
              "per_model": [
                {
                  "model": "gfs",
                  "status": "red",
                  "detail": "CAPE 1200 J/kg near LFMD",
                  "affected_points": 4,
                  "total_points": 12,
                  "affected_pct": 33.0,
                  "affected_nm": 30.0,
                  "total_nm": 90.0,
                  "cross_check": "High CAPE (1200 J/kg); NWP scheme quiet (0% cover) — expected pattern."
                },
                {
                  "model": "ecmwf",
                  "status": "amber",
                  "detail": "Marginal",
                  "affected_points": 1,
                  "total_points": 12,
                  "affected_pct": 8.0,
                  "affected_nm": 7.0,
                  "total_nm": 90.0
                }
              ]
            }
          ],
          "catalog": [],
          "route_name": "LFML LFMD",
          "cruise_altitude_ft": 8000,
          "flight_ceiling_ft": 13000,
          "total_distance_nm": 90.0,
          "models": ["gfs", "ecmwf"],
          "aggregation": "worst"
        }
        """
        let data = Data(json.utf8)
        let response = try JSONDecoder.weatherBrief.decode(AdvisoriesResponse.self, from: data)

        let advisory = try #require(response.advisories.first)
        #expect(advisory.advisoryId == "convective")
        // parameters_used surfaced.
        #expect(advisory.parametersUsed["cape_red_jkg"] == 1000.0)
        #expect(advisory.parametersUsed["affected_pct_red"] == 50.0)

        let gfs = try #require(advisory.perModel.first { $0.model == "gfs" })
        #expect(gfs.crossCheck?.contains("High CAPE") == true)

        // cross_check is optional — models without it decode with nil.
        let ecmwf = try #require(advisory.perModel.first { $0.model == "ecmwf" })
        #expect(ecmwf.crossCheck == nil)
    }
}
