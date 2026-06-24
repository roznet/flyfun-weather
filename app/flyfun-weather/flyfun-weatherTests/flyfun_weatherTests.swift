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

    @Test func advisoryDetailDecodesStructuredCrossCheckAndConvectiveBlock() throws {
        let json = """
        {
          "advisory_id": "convective",
          "aggregate_status": "red",
          "aggregate_detail": "High CAPE along the middle third of the route",
          "name": "Convective Activity",
          "description": "Can fly around convective activity.",
          "flight_id": "flight-1",
          "briefing_timestamp": "2026-06-24T12:00:00+00:00",
          "route_name": "egtf-eglf",
          "total_distance_nm": 50.0,
          "per_model": [
            {
              "model": "gfs",
              "status": "red",
              "detail": "HIGH risk near EGTF",
              "affected_pct": 44.0,
              "affected_nm": 22.0,
              "total_nm": 50.0,
              "cross_check": {"note": "NWP scheme remains quiet"}
            }
          ],
          "parameters_used": {"affected_pct_red": 40.0},
          "cross_check_note": "Cross-checks explain the grade.",
          "convective_note": "Convective split is explanatory.",
          "convective": {
            "gfs": {
              "assessment_method": "thermo",
              "method_counts": {"thermo": 1},
              "thermo": {
                "cape_range_jkg": [1840, 1840],
                "peak": {
                  "cape_jkg": 1840,
                  "el_top_ft": 27000,
                  "risk_level": "high",
                  "distance_nm": 19.5,
                  "waypoint_icao": "EGTF"
                }
              },
              "nwp": {"max_cover_pct": 0, "peak_top_ft": null}
            }
          }
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.weatherBrief.decode(AdvisoryDetailResponse.self, from: json)
        let model = try #require(response.perModel.first)
        let convective = try #require(response.convective?["gfs"])

        #expect(response.name == "Convective Activity")
        #expect(response.totalDistanceNm == 50.0)
        #expect(model.crossCheck == "note: NWP scheme remains quiet")
        #expect(convective.assessmentMethod == "thermo")
        #expect(convective.thermo?.peak?.elTopFt == 27000)
        #expect(convective.nwp?.maxCoverPct == 0)
    }

    @MainActor
    @Test func routeMapMetricsStyleSegmentsAndAltitudeLayers() throws {
        let json = """
        {
          "route_name": "egtf-eglf",
          "target_date": "2026-06-24",
          "departure_time": "2026-06-24T12:00:00Z",
          "flight_duration_hours": 1.0,
          "total_distance_nm": 20.0,
          "cruise_altitude_ft": 8000,
          "models": ["gfs"],
          "analyses": [
            {
              "point_index": 0,
              "lat": 51.3,
              "lon": -0.55,
              "distance_from_origin_nm": 0.0,
              "waypoint_icao": "EGTF",
              "waypoint_name": "Fairoaks",
              "interpolated_time": "2026-06-24T12:00:00Z",
              "track_deg": 120.0,
              "wind_components": {
                "gfs": {
                  "wind_speed_kt": 30.0,
                  "wind_direction_deg": 270.0,
                  "track_deg": 120.0,
                  "headwind_kt": 18.0,
                  "crosswind_kt": 12.0
                }
              },
              "sounding": {
                "gfs": {
                  "indices": {
                    "freezing_level_ft": 5000.0,
                    "minus10c_level_ft": 10000.0,
                    "minus20c_level_ft": 16000.0,
                    "cape_surface_jkg": 1800.0
                  },
                  "cloud_layers": [
                    {"base_ft": 7000.0, "top_ft": 9000.0, "coverage": "bkn"}
                  ],
                  "icing_zones": [
                    {"base_ft": 6500.0, "top_ft": 9500.0, "risk": "moderate", "icing_type": "rime"}
                  ],
                  "sfip_zones": [
                    {"base_ft": 6500.0, "top_ft": 9500.0, "risk": "moderate", "icing_type": "sfip", "mean_sfip100": 65.0, "variant": "sfip"}
                  ],
                  "vertical_motion": {
                    "classification": "moderate",
                    "cat_risk_layers": [
                      {"base_ft": 7500.0, "top_ft": 9000.0, "risk": "moderate"}
                    ]
                  },
                  "convective": {"risk_level": "high", "base_ft": 6000.0, "top_ft": 24000.0, "cape_jkg": 1800.0},
                  "cloud_cover_low_pct": 40.0,
                  "cloud_cover_mid_pct": 20.0,
                  "cloud_cover_high_pct": 10.0,
                  "nwp_cloud_diagnostics": {
                    "low": {"cover_pct": 40.0, "base_ft": 4000.0, "top_ft": 8000.0},
                    "mid": {"cover_pct": 20.0, "base_ft": 8000.0, "top_ft": 14000.0},
                    "high": {"cover_pct": 10.0, "base_ft": 18000.0, "top_ft": 24000.0},
                    "ceiling_ft": 2200.0
                  }
                }
              },
              "model_divergence": [{"variable": "temperature", "model_values": {"gfs": -6.0}, "mean": -6.0, "spread": 0.0, "agreement": "poor"}]
            },
            {
              "point_index": 1,
              "lat": 51.28,
              "lon": -0.76,
              "distance_from_origin_nm": 20.0,
              "waypoint_icao": "EGLF",
              "waypoint_name": "Farnborough",
              "interpolated_time": "2026-06-24T12:20:00Z",
              "track_deg": 120.0,
              "wind_components": {
                "gfs": {
                  "wind_speed_kt": 24.0,
                  "wind_direction_deg": 250.0,
                  "track_deg": 120.0,
                  "headwind_kt": 12.0,
                  "crosswind_kt": 9.0
                }
              },
              "sounding": {
                "gfs": {
                  "indices": {
                    "freezing_level_ft": 6000.0,
                    "minus10c_level_ft": 11000.0,
                    "minus20c_level_ft": 17000.0,
                    "cape_surface_jkg": 900.0
                  },
                  "cloud_layers": [],
                  "icing_zones": [],
                  "sfip_zones": [],
                  "vertical_motion": {"classification": "smooth", "cat_risk_layers": []},
                  "convective": {"risk_level": "moderate", "base_ft": 7000.0, "top_ft": 18000.0, "cape_jkg": 900.0},
                  "cloud_cover_low_pct": 10.0,
                  "cloud_cover_mid_pct": 10.0,
                  "cloud_cover_high_pct": 0.0
                }
              },
              "model_divergence": [{"variable": "temperature", "model_values": {"gfs": -4.0}, "mean": -4.0, "spread": 0.0, "agreement": "good"}]
            }
          ]
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder.weatherBrief.decode(RouteAnalysesResponse.self, from: json)
        let routeMap = RouteMapViewModel()
        routeMap.update(from: response)
        let firstPoint = try #require(response.analyses.first)
        let segments = routeMap.segments(colorMetric: .convectiveRisk, widthMetric: .headwind, model: "gfs", altitudeFt: 8000)

        #expect(segments.count == 1)
        #expect(segments.first?.valueDescription == "high")
        #expect((segments.first?.width ?? 0) > 3)
        #expect(RouteMapMetric.icingRiskAtLevel.value(for: firstPoint, model: "gfs", altitudeFt: 8000) == 2)
        #expect(RouteMapMetric.sfipAtLevel.value(for: firstPoint, model: "gfs", altitudeFt: 8000) == 65)
        #expect(RouteMapMetric.cloudAtLevel.value(for: firstPoint, model: "gfs", altitudeFt: 8000) == 70)
        #expect(RouteMapMetric.tempAtLevel.value(for: firstPoint, model: "gfs", altitudeFt: 8000) == -6)
    }

}
