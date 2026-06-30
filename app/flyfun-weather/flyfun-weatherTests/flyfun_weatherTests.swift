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

        // aggregate_mitigations / per-model mitigations are optional — absent on
        // this pack, so they decode to nil (backward-compat with old packs, #330).
        #expect(advisory.aggregateMitigations == nil)
        #expect(gfs.mitigations == nil)
    }

    /// `aggregate_mitigations` (route level) and per-model `mitigations` decode
    /// into the advisory models with snake_case keys mapped via the decoder's
    /// `.convertFromSnakeCase` strategy (#330). Mitigations are advice only and
    /// never change the grade.
    @Test func routeAdvisoryResultDecodesMitigations() throws {
        let json = """
        {
          "advisories": [
            {
              "advisory_id": "vfr_feasibility",
              "aggregate_status": "red",
              "aggregate_detail": "VFR not feasible",
              "parameters_used": {},
              "per_model": [
                {
                  "model": "gfs",
                  "status": "red",
                  "detail": "Departure deck OVC below cruise",
                  "affected_points": 3,
                  "total_points": 12,
                  "affected_pct": 25.0,
                  "affected_nm": 22.0,
                  "total_nm": 90.0,
                  "mitigations": [
                    {
                      "kind": "route_position",
                      "addresses": "climb_deck",
                      "detail": "Climb to cruise after ~40 nm from departure to clear the deck.",
                      "mitigated_status": "amber",
                      "distance_nm": 40.0,
                      "reference": "departure"
                    }
                  ]
                }
              ],
              "aggregate_mitigations": [
                {
                  "kind": "altitude",
                  "addresses": "cruise_imc",
                  "detail": "Fly 6,000 ft to stay below the cloud base.",
                  "mitigated_status": "green",
                  "altitude_ft": 6000
                }
              ]
            }
          ],
          "catalog": [],
          "route_name": "LFML LFMD",
          "cruise_altitude_ft": 8000,
          "flight_ceiling_ft": 13000,
          "total_distance_nm": 90.0,
          "models": ["gfs"],
          "aggregation": "worst"
        }
        """
        let data = Data(json.utf8)
        let response = try JSONDecoder.weatherBrief.decode(AdvisoriesResponse.self, from: data)

        let advisory = try #require(response.advisories.first)
        // Mitigations never change the grade — the advisory stays RED.
        #expect(advisory.aggregateStatus == "red")

        let agg = try #require(advisory.aggregateMitigations)
        #expect(agg.count == 1)
        #expect(agg[0].kind == "altitude")
        #expect(agg[0].addresses == "cruise_imc")
        #expect(agg[0].mitigatedStatus == "green")
        #expect(agg[0].altitudeFt == 6000)
        #expect(agg[0].distanceNm == nil)

        let gfs = try #require(advisory.perModel.first { $0.model == "gfs" })
        let perModel = try #require(gfs.mitigations)
        #expect(perModel.count == 1)
        #expect(perModel[0].kind == "route_position")
        #expect(perModel[0].distanceNm == 40.0)
        #expect(perModel[0].reference == "departure")
    }
}

// MARK: - Phase 4 (#290): Skew-T app wiring
//
// The extended thermodynamic indices (LCL/LFC/EL pressures, CIN, lifted index)
// the server already sends now decode and feed the RZSkewT marker/index render.

struct Phase4SkewTWiringTests {

    private func decodeProfile(indicesJSON: String, levelsJSON: String = "[]") throws -> SoundingProfileResponse {
        let json = """
        {
          "point_index": 1, "lat": 43.0, "lon": 5.0, "distance_from_origin_nm": 10.0,
          "waypoint_icao": null, "model": "gfs", "time": "2026-06-24T12:00:00Z",
          "cruise_altitude_ft": 8000,
          "indices": \(indicesJSON),
          "levels": \(levelsJSON)
        }
        """
        return try JSONDecoder.weatherBrief.decode(SoundingProfileResponse.self, from: Data(json.utf8))
    }

    @Test func thermodynamicIndicesDecodesExtendedFields() throws {
        let indices = """
        {
          "freezing_level_ft": 11000.0,
          "lcl_altitude_ft": 3000.0, "lcl_pressure_hpa": 905.0,
          "lfc_pressure_hpa": 820.0, "el_pressure_hpa": 300.0,
          "cape_surface_jkg": 850.0, "cin_surface_jkg": -45.0,
          "lifted_index": -3.5
        }
        """
        let profile = try decodeProfile(indicesJSON: indices)
        let idx = try #require(profile.indices)
        #expect(idx.lclPressureHpa == 905.0)
        #expect(idx.lfcPressureHpa == 820.0)
        #expect(idx.elPressureHpa == 300.0)
        #expect(idx.cinSurfaceJkg == -45.0)
        #expect(idx.liftedIndex == -3.5)
    }

    /// Altitude→pressure fallback interpolates linearly from the sounding levels.
    @Test func pressureInterpolationFromLevels() throws {
        let levels = """
        [
          {"pressure_hpa": 1000, "altitude_ft": 0.0, "temperature_c": 15.0},
          {"pressure_hpa": 900, "altitude_ft": 3000.0, "temperature_c": 9.0},
          {"pressure_hpa": 800, "altitude_ft": 6000.0, "temperature_c": 3.0}
        ]
        """
        let profile = try decodeProfile(indicesJSON: "null", levelsJSON: levels)
        // Midway between 3000ft (900 hPa) and 6000ft (800 hPa) → ~850 hPa.
        let p = try #require(SoundingProfileResponse.pressure(atAltitudeFt: 4500, levels: profile.levels))
        #expect(abs(p - 850.0) < 0.001)
        // Below the lowest level clamps to that level's pressure.
        #expect(SoundingProfileResponse.pressure(atAltitudeFt: -100, levels: profile.levels) == 1000.0)
        // nil altitude → nil.
        #expect(SoundingProfileResponse.pressure(atAltitudeFt: nil, levels: profile.levels) == nil)
    }
}
