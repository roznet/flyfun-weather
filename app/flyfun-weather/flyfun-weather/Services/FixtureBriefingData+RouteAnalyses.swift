#if DEBUG
import Foundation

/// The `route-analyses` fixture blob for `fixture-1` — five points over 78 nm,
/// each carrying a full ECMWF + GFS sounding so the cross-section renderer has
/// real layers to draw (clouds, icing, convective towers, CAT, inversions) and
/// `extractVizData` has non-trivial data to map. GFS peaks the convective tower
/// near LFMD (point 0/1) while ECMWF stays quiet — the same disagreement the
/// convective advisory + drill-down narrate. Kept in its own file purely for
/// length; see `FixtureBriefingData` for the coherence rules.
extension FixtureBriefingData {
    static let routeAnalysesJSON = """
    {
      "route_name": "LFMD LFML",
      "target_date": "2099-07-01",
      "departure_time": "2099-07-01T09:00:00Z",
      "flight_duration_hours": 1.5,
      "total_distance_nm": 78.0,
      "cruise_altitude_ft": 8000,
      "models": ["ecmwf", "gfs"],
      "analyses": [
        {
          "point_index": 0, "lat": 43.542, "lon": 6.953, "distance_from_origin_nm": 0.0,
          "waypoint_icao": "LFMD", "waypoint_name": "Cannes-Mandelieu",
          "interpolated_time": "2099-07-01T09:00:00Z", "track_deg": 262.0,
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 8.0, "wind_direction_deg": 200.0, "track_deg": 262.0, "headwind_kt": 7.0, "crosswind_kt": 4.0 },
            "gfs": { "wind_speed_kt": 10.0, "wind_direction_deg": 210.0, "track_deg": 262.0, "headwind_kt": 8.0, "crosswind_kt": 5.0 }
          },
          "sounding": {
            "ecmwf": {
              "indices": { "freezing_level_ft": 11500.0, "minus10c_level_ft": 15500.0, "minus20c_level_ft": 19500.0, "lcl_altitude_ft": 3800.0, "lfc_altitude_ft": 6500.0, "el_altitude_ft": 24000.0, "cape_surface_jkg": 600.0, "sounding_ceiling_ft": 4500.0, "nwp_ceiling_ft": 4300.0, "cin_surface_jkg": -40.0, "lifted_index": -2.0 },
              "cloud_layers": [ { "base_ft": 3800.0, "top_ft": 6500.0, "coverage": "SCT", "mean_dewpoint_depression_c": 2.5, "mean_cloud_cover_pct": 40.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 4000.0, "top_ft": 6000.0, "coverage": "SCT", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 45.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [],
              "inversion_layers": [ { "base_ft": 2500.0, "top_ft": 3200.0, "strength_c": 2.0 } ],
              "convective": { "risk_level": "moderate", "base_ft": 6500.0, "top_ft": 24000.0, "cape_jkg": 600.0, "cin_jkg": -40.0, "cover_pct": 15.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 1.0, "method": "nwp" },
              "vertical_motion": { "classification": "weak_ascent", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 30.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 10.0
            },
            "gfs": {
              "indices": { "freezing_level_ft": 9000.0, "minus10c_level_ft": 13500.0, "minus20c_level_ft": 17500.0, "lcl_altitude_ft": 3500.0, "lfc_altitude_ft": 5500.0, "el_altitude_ft": 34000.0, "cape_surface_jkg": 1400.0, "sounding_ceiling_ft": 4300.0, "nwp_ceiling_ft": 4200.0, "cin_surface_jkg": -20.0, "lifted_index": -5.0 },
              "cloud_layers": [ { "base_ft": 3500.0, "top_ft": 5500.0, "coverage": "SCT", "mean_dewpoint_depression_c": 2.0, "mean_cloud_cover_pct": 45.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3800.0, "top_ft": 5200.0, "coverage": "SCT", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 40.0 } ],
              "icing_zones": [ { "base_ft": 9000.0, "top_ft": 12000.0, "risk": "light", "icing_type": "rime" } ],
              "icing_ogimet_nwp_zones": [], "sfip_zones": [],
              "inversion_layers": [],
              "convective": { "risk_level": "high", "base_ft": 5500.0, "top_ft": 34000.0, "cape_jkg": 1400.0, "cin_jkg": -20.0, "cover_pct": 25.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 2.0, "method": "nwp" },
              "vertical_motion": { "classification": "ascent", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 35.0, "cloud_cover_mid_pct": 25.0, "cloud_cover_high_pct": 15.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 12.0, "gfs": 11.0 }, "mean": 11.5, "spread": 1.0, "agreement": "good" },
            { "variable": "precipitation_mm", "model_values": { "ecmwf": 0.0, "gfs": 0.2 }, "mean": 0.1, "spread": 0.2, "agreement": "good" }
          ]
        },
        {
          "point_index": 1, "lat": 43.516, "lon": 6.520, "distance_from_origin_nm": 20.0,
          "waypoint_icao": null, "waypoint_name": null,
          "interpolated_time": "2099-07-01T09:22:00Z", "track_deg": 262.0,
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 9.0, "wind_direction_deg": 220.0, "track_deg": 262.0, "headwind_kt": 7.0, "crosswind_kt": 5.0 },
            "gfs": { "wind_speed_kt": 11.0, "wind_direction_deg": 225.0, "track_deg": 262.0, "headwind_kt": 9.0, "crosswind_kt": 5.0 }
          },
          "sounding": {
            "ecmwf": {
              "indices": { "freezing_level_ft": 11600.0, "minus10c_level_ft": 15600.0, "minus20c_level_ft": 19600.0, "lcl_altitude_ft": 4000.0, "lfc_altitude_ft": 7000.0, "el_altitude_ft": 22000.0, "cape_surface_jkg": 450.0, "sounding_ceiling_ft": 5000.0, "nwp_ceiling_ft": 4800.0, "cin_surface_jkg": -60.0, "lifted_index": -1.0 },
              "cloud_layers": [ { "base_ft": 4000.0, "top_ft": 7000.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.5, "mean_cloud_cover_pct": 60.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 4200.0, "top_ft": 6800.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 65.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "low", "base_ft": 7000.0, "top_ft": 22000.0, "cape_jkg": 450.0, "cin_jkg": -60.0, "cover_pct": 10.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "neutral", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 35.0, "cloud_cover_mid_pct": 30.0, "cloud_cover_high_pct": 10.0
            },
            "gfs": {
              "indices": { "freezing_level_ft": 9200.0, "minus10c_level_ft": 13700.0, "minus20c_level_ft": 17700.0, "lcl_altitude_ft": 3700.0, "lfc_altitude_ft": 6000.0, "el_altitude_ft": 31000.0, "cape_surface_jkg": 1100.0, "sounding_ceiling_ft": 4700.0, "nwp_ceiling_ft": 4600.0, "cin_surface_jkg": -30.0, "lifted_index": -4.0 },
              "cloud_layers": [ { "base_ft": 3700.0, "top_ft": 6200.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.5, "mean_cloud_cover_pct": 65.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3900.0, "top_ft": 6000.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 60.0 } ],
              "icing_zones": [ { "base_ft": 9200.0, "top_ft": 11500.0, "risk": "light", "icing_type": "rime" } ],
              "icing_ogimet_nwp_zones": [ { "base_ft": 9000.0, "top_ft": 11000.0, "risk": "light", "icing_type": "rime" } ],
              "sfip_zones": [ { "base_ft": 9000.0, "top_ft": 11000.0, "risk": "light", "icing_type": "rime", "mean_sfip100": 12.0, "variant": "nwp" } ],
              "inversion_layers": [],
              "convective": { "risk_level": "high", "base_ft": 6000.0, "top_ft": 31000.0, "cape_jkg": 1100.0, "cin_jkg": -30.0, "cover_pct": 20.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "low", "base_ft": 6000.0, "top_ft": 12000.0, "cape_jkg": null, "cin_jkg": null, "cover_pct": 8.0, "method": "nwp" },
              "vertical_motion": { "classification": "ascent", "cat_risk_layers": [ { "base_ft": 20000.0, "top_ft": 24000.0, "risk": "light" } ] },
              "cloud_cover_low_pct": 40.0, "cloud_cover_mid_pct": 35.0, "cloud_cover_high_pct": 15.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 10.0, "gfs": 8.5 }, "mean": 9.25, "spread": 1.5, "agreement": "moderate" },
            { "variable": "precipitation_mm", "model_values": { "ecmwf": 0.1, "gfs": 0.6 }, "mean": 0.35, "spread": 0.5, "agreement": "moderate" }
          ]
        },
        {
          "point_index": 2, "lat": 43.489, "lon": 6.084, "distance_from_origin_nm": 39.0,
          "waypoint_icao": null, "waypoint_name": null,
          "interpolated_time": "2099-07-01T09:45:00Z", "track_deg": 262.0,
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 10.0, "wind_direction_deg": 260.0, "track_deg": 262.0, "headwind_kt": 9.0, "crosswind_kt": 3.0 },
            "gfs": { "wind_speed_kt": 11.0, "wind_direction_deg": 265.0, "track_deg": 262.0, "headwind_kt": 10.0, "crosswind_kt": 3.0 }
          },
          "sounding": {
            "ecmwf": {
              "indices": { "freezing_level_ft": 11700.0, "minus10c_level_ft": 15700.0, "minus20c_level_ft": 19700.0, "lcl_altitude_ft": 4500.0, "lfc_altitude_ft": 8000.0, "el_altitude_ft": 18000.0, "cape_surface_jkg": 250.0, "sounding_ceiling_ft": 6000.0, "nwp_ceiling_ft": 5800.0, "cin_surface_jkg": -80.0, "lifted_index": 1.0 },
              "cloud_layers": [ { "base_ft": 4500.0, "top_ft": 8000.0, "coverage": "SCT", "mean_dewpoint_depression_c": 3.0, "mean_cloud_cover_pct": 40.0 } ],
              "nwp_cloud_layers": [], "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": 250.0, "cin_jkg": -80.0, "cover_pct": 0.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "neutral", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 20.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0
            },
            "gfs": {
              "indices": { "freezing_level_ft": 9500.0, "minus10c_level_ft": 14000.0, "minus20c_level_ft": 18000.0, "lcl_altitude_ft": 4200.0, "lfc_altitude_ft": 7500.0, "el_altitude_ft": 22000.0, "cape_surface_jkg": 500.0, "sounding_ceiling_ft": 5800.0, "nwp_ceiling_ft": 5600.0, "cin_surface_jkg": -50.0, "lifted_index": -1.0 },
              "cloud_layers": [ { "base_ft": 4200.0, "top_ft": 7500.0, "coverage": "SCT", "mean_dewpoint_depression_c": 2.5, "mean_cloud_cover_pct": 45.0 } ],
              "nwp_cloud_layers": [], "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "low", "base_ft": 7500.0, "top_ft": 22000.0, "cape_jkg": 500.0, "cin_jkg": -50.0, "cover_pct": 8.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "neutral", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 25.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 5.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 9.0, "gfs": 8.0 }, "mean": 8.5, "spread": 1.0, "agreement": "good" },
            { "variable": "precipitation_mm", "model_values": { "ecmwf": 0.0, "gfs": 0.0 }, "mean": 0.0, "spread": 0.0, "agreement": "good" }
          ]
        },
        {
          "point_index": 3, "lat": 43.463, "lon": 5.649, "distance_from_origin_nm": 59.0,
          "waypoint_icao": null, "waypoint_name": null,
          "interpolated_time": "2099-07-01T10:07:00Z", "track_deg": 262.0,
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 11.0, "wind_direction_deg": 290.0, "track_deg": 262.0, "headwind_kt": 9.0, "crosswind_kt": 6.0 },
            "gfs": { "wind_speed_kt": 12.0, "wind_direction_deg": 295.0, "track_deg": 262.0, "headwind_kt": 10.0, "crosswind_kt": 6.0 }
          },
          "sounding": {
            "ecmwf": {
              "indices": { "freezing_level_ft": 11800.0, "minus10c_level_ft": 15800.0, "minus20c_level_ft": 19800.0, "lcl_altitude_ft": 4200.0, "lfc_altitude_ft": null, "el_altitude_ft": null, "cape_surface_jkg": 100.0, "sounding_ceiling_ft": 4000.0, "nwp_ceiling_ft": 3800.0, "cin_surface_jkg": -120.0, "lifted_index": 3.0 },
              "cloud_layers": [ { "base_ft": 3200.0, "top_ft": 4200.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.0, "mean_cloud_cover_pct": 70.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3400.0, "top_ft": 4200.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 70.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [],
              "inversion_layers": [ { "base_ft": 4200.0, "top_ft": 5000.0, "strength_c": 3.0 } ],
              "convective": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": 100.0, "cin_jkg": -120.0, "cover_pct": 0.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "weak_descent", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 45.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0
            },
            "gfs": {
              "indices": { "freezing_level_ft": 10000.0, "minus10c_level_ft": 14200.0, "minus20c_level_ft": 18200.0, "lcl_altitude_ft": 4000.0, "lfc_altitude_ft": null, "el_altitude_ft": null, "cape_surface_jkg": 200.0, "sounding_ceiling_ft": 3800.0, "nwp_ceiling_ft": 3700.0, "cin_surface_jkg": -100.0, "lifted_index": 2.0 },
              "cloud_layers": [ { "base_ft": 3000.0, "top_ft": 4000.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.0, "mean_cloud_cover_pct": 72.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3200.0, "top_ft": 4000.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 72.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": 200.0, "cin_jkg": -100.0, "cover_pct": 0.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "weak_descent", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 50.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 8.0, "gfs": 7.0 }, "mean": 7.5, "spread": 1.0, "agreement": "good" },
            { "variable": "precipitation_mm", "model_values": { "ecmwf": 0.3, "gfs": 0.4 }, "mean": 0.35, "spread": 0.1, "agreement": "good" }
          ]
        },
        {
          "point_index": 4, "lat": 43.436, "lon": 5.215, "distance_from_origin_nm": 78.0,
          "waypoint_icao": "LFML", "waypoint_name": "Marseille Provence",
          "interpolated_time": "2099-07-01T10:30:00Z", "track_deg": 262.0,
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 12.0, "wind_direction_deg": 300.0, "track_deg": 262.0, "headwind_kt": 10.0, "crosswind_kt": 6.0 },
            "gfs": { "wind_speed_kt": 13.0, "wind_direction_deg": 305.0, "track_deg": 262.0, "headwind_kt": 11.0, "crosswind_kt": 6.0 }
          },
          "sounding": {
            "ecmwf": {
              "indices": { "freezing_level_ft": 11800.0, "minus10c_level_ft": 15800.0, "minus20c_level_ft": 19800.0, "lcl_altitude_ft": 4000.0, "lfc_altitude_ft": null, "el_altitude_ft": null, "cape_surface_jkg": 200.0, "sounding_ceiling_ft": 3800.0, "nwp_ceiling_ft": 3600.0, "cin_surface_jkg": -110.0, "lifted_index": 2.0 },
              "cloud_layers": [ { "base_ft": 3000.0, "top_ft": 3800.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.2, "mean_cloud_cover_pct": 65.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3200.0, "top_ft": 3800.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 65.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": 200.0, "cin_jkg": -110.0, "cover_pct": 0.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "neutral", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 40.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0
            },
            "gfs": {
              "indices": { "freezing_level_ft": 10500.0, "minus10c_level_ft": 14500.0, "minus20c_level_ft": 18500.0, "lcl_altitude_ft": 3800.0, "lfc_altitude_ft": null, "el_altitude_ft": null, "cape_surface_jkg": 350.0, "sounding_ceiling_ft": 3600.0, "nwp_ceiling_ft": 3500.0, "cin_surface_jkg": -90.0, "lifted_index": 1.0 },
              "cloud_layers": [ { "base_ft": 2800.0, "top_ft": 3600.0, "coverage": "BKN", "mean_dewpoint_depression_c": 1.0, "mean_cloud_cover_pct": 68.0 } ],
              "nwp_cloud_layers": [ { "base_ft": 3000.0, "top_ft": 3600.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 68.0 } ],
              "icing_zones": [], "icing_ogimet_nwp_zones": [], "sfip_zones": [], "inversion_layers": [],
              "convective": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": 350.0, "cin_jkg": -90.0, "cover_pct": 0.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 0.0, "method": "nwp" },
              "vertical_motion": { "classification": "neutral", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 45.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 5.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 9.5, "gfs": 8.5 }, "mean": 9.0, "spread": 1.0, "agreement": "good" },
            { "variable": "precipitation_mm", "model_values": { "ecmwf": 0.2, "gfs": 0.3 }, "mean": 0.25, "spread": 0.1, "agreement": "good" }
          ]
        }
      ]
    }
    """
}
#endif
