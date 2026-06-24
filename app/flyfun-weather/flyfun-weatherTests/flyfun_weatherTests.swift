//
//  flyfun_weatherTests.swift
//  flyfun-weatherTests
//
//  Created by Brice Rosenzweig on 01/03/2026.
//

import Foundation
import Testing
@testable import flyfun_weather

struct flyfun_weatherTests {

    @Test func soundingProfileDecodesExtendedLevelFields() throws {
        let json = """
        {
          "point_index": 3,
          "lat": 51.1,
          "lon": -0.6,
          "distance_from_origin_nm": 42.5,
          "waypoint_icao": "EGTF",
          "model": "gfs",
          "time": "2026-06-24T12:00:00Z",
          "levels": [
            {
              "pressure_hpa": 850,
              "altitude_ft": 4780.0,
              "temperature_c": 4.2,
              "dewpoint_c": 1.4,
              "wind_speed_kt": 27.0,
              "wind_direction_deg": 245.0,
              "relative_humidity_pct": 82.0,
              "dewpoint_depression_c": 2.8,
              "wet_bulb_c": 2.9,
              "theta_e_k": 303.4,
              "lapse_rate_c_per_km": 6.1,
              "icing_index": 21.0,
              "icing_index_nwp": 37.0,
              "sfip_100": 44.0,
              "cloud_liquid_water_g_m3": 0.18,
              "ice_mixing_ratio_g_kg": 0.05,
              "cloud_area_fraction_pct": 73.0,
              "richardson_number": 0.21,
              "omega_pa_s": -0.32,
              "w_fpm": 52.0
            }
          ],
          "cruise_altitude_ft": 8000,
          "track_deg": 132.0,
          "label": "EGTF",
          "indices": {
            "freezing_level_ft": 5200.0,
            "minus10c_level_ft": 11800.0,
            "minus20c_level_ft": 17200.0,
            "lcl_pressure_hpa": 910.0,
            "lcl_altitude_ft": 2900.0,
            "lfc_pressure_hpa": 710.0,
            "lfc_altitude_ft": 9800.0,
            "el_pressure_hpa": 360.0,
            "el_altitude_ft": 27000.0,
            "cape_surface_jkg": 850.0,
            "cape_most_unstable_jkg": 920.0,
            "cape_mixed_layer_jkg": 610.0,
            "cin_surface_jkg": -42.0,
            "lifted_index": -3.2,
            "showalter_index": 0.8,
            "k_index": 28.5,
            "total_totals": 49.0,
            "precipitable_water_mm": 24.0,
            "nwp_cape_jkg": 700.0,
            "nwp_cape_type": "ml",
            "nwp_cin_jkg": -20.0,
            "nwp_lifted_index": -2.1,
            "nwp_freezing_level_ft": 5400.0,
            "cape_raw_vs_calc_divergent": true
          },
          "parcel_path": [{"pressure_hpa": 850.0, "temperature_c": 7.0}],
          "cloud_layers": [],
          "nwp_cloud_layers": [],
          "icing_zones": [],
          "icing_ogimet_nwp_zones": [],
          "sfip_zones": [],
          "inversion_layers": [],
          "convective": {"risk_level": "low", "base_ft": 9000.0, "top_ft": 25000.0}
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.weatherBrief.decode(SoundingProfileResponse.self, from: json)
        let level = try #require(response.levels.first)

        #expect(response.trackDeg == 132.0)
        #expect(response.label == "EGTF")
        #expect(response.parcelPath?.first?.pressureHpa == 850.0)
        #expect(response.indices?.lclPressureHpa == 910.0)
        #expect(response.indices?.liftedIndex == -3.2)
        #expect(response.indices?.nwpCapeType == "ml")
        #expect(response.indices?.capeRawVsCalcDivergent == true)
        #expect(response.convective?.riskLevel == "low")
        #expect(level.relativeHumidityPct == 82.0)
        #expect(level.dewpointDepressionC == 2.8)
        #expect(level.wetBulbC == 2.9)
        #expect(level.thetaEK == 303.4)
        #expect(level.lapseRateCPerKm == 6.1)
        #expect(level.icingIndex == 21.0)
        #expect(level.icingIndexNwp == 37.0)
        #expect(level.sfip100 == 44.0)
        #expect(level.cloudLiquidWaterGM3 == 0.18)
        #expect(level.iceMixingRatioGKg == 0.05)
        #expect(level.cloudAreaFractionPct == 73.0)
        #expect(level.richardsonNumber == 0.21)
        #expect(level.omegaPaS == -0.32)
        #expect(level.wFpm == 52.0)
    }

    @Test func advisoriesDecodeParametersAndCrossCheck() throws {
        let json = """
        {
          "advisories": [
            {
              "advisory_id": "convective",
              "aggregate_status": "red",
              "aggregate_detail": "High CAPE near EGTF",
              "per_model": [
                {
                  "model": "gfs",
                  "status": "red",
                  "detail": "HIGH risk over 20nm",
                  "affected_points": 3,
                  "total_points": 5,
                  "affected_pct": 60.0,
                  "affected_nm": 20.0,
                  "total_nm": 33.3,
                  "cross_check": "Thermodynamic risk high while NWP cover is quiet"
                }
              ],
              "parameters_used": {
                "affected_pct_red": 50.0,
                "min_risk": 2.0
              }
            }
          ],
          "catalog": [
            {
              "id": "convective",
              "name": "Convective Activity",
              "short_description": "Can fly around convective activity",
              "description": "Uses convective risk assessment per point.",
              "category": "convective",
              "default_enabled": true,
              "altitude_dependent": false,
              "parameters": [
                {
                  "key": "affected_pct_red",
                  "label": "Route % (red)",
                  "description": "Route percentage affected for red",
                  "type": "percent",
                  "unit": "%",
                  "default": 50.0,
                  "min": 10.0,
                  "max": 100.0,
                  "step": 5.0
                }
              ]
            }
          ],
          "route_name": "egtf-eglf",
          "cruise_altitude_ft": 8000,
          "flight_ceiling_ft": 18000,
          "total_distance_nm": 33.3,
          "models": ["gfs"],
          "aggregation": "worst",
          "airport_conditions": null
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.weatherBrief.decode(AdvisoriesResponse.self, from: json)
        let advisory = try #require(response.advisories.first)
        let model = try #require(advisory.perModel.first)

        #expect(advisory.parametersUsed["affected_pct_red"] == 50.0)
        #expect(response.catalog.first?.parameters.first?.key == "affected_pct_red")
        #expect(model.crossCheck == "Thermodynamic risk high while NWP cover is quiet")
    }

}
