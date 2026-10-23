package aero.flyfun.weather.data

import aero.flyfun.weather.model.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.serialization.builtins.ListSerializer

/**
 * Canned, internally-consistent briefing fixtures for `fixture-1`
 * (LFMD -> LFML, D-1, AMBER overall with one RED convective advisory).
 * Decoded through the production [WbJson] serializer so any DTO contract mismatch fails loudly.
 */
object FixtureBriefingData {
    const val FLIGHT_ID = "fixture-1"
    const val TIMESTAMP = "2099-06-30T06:00:00Z"

    val pack: PackMetaResponse by lazy {
        WbJson.decodeFromString(PackMetaResponse.serializer(), PACK_JSON)
    }

    val packs: List<PackMetaResponse> by lazy { listOf(pack) }

    val advisories: AdvisoriesResponse by lazy {
        WbJson.decodeFromString(AdvisoriesResponse.serializer(), ADVISORIES_JSON)
    }

    val convectiveDetail: AdvisoryDetailResponse by lazy {
        WbJson.decodeFromString(AdvisoryDetailResponse.serializer(), CONVECTIVE_DETAIL_JSON)
    }

    val digest: DigestResponse by lazy {
        WbJson.decodeFromString(DigestResponse.serializer(), DIGEST_JSON)
    }

    val snapshot: SnapshotResponse by lazy {
        WbJson.decodeFromString(SnapshotResponse.serializer(), SNAPSHOT_JSON)
    }

    val nullLiveLayer: LiveLayerResponse by lazy {
        WbJson.decodeFromString(LiveLayerResponse.serializer(), NULL_LIVE_LAYER_JSON)
    }

    val liveLayer: LiveLayerResponse by lazy {
        WbJson.decodeFromString(LiveLayerResponse.serializer(), LIVE_LAYER_JSON)
    }

    val routeAnalyses: RouteAnalysesResponse by lazy {
        WbJson.decodeFromString(RouteAnalysesResponse.serializer(), ROUTE_ANALYSES_JSON)
    }

    val elevation: ElevationResponse by lazy {
        WbJson.decodeFromString(ElevationResponse.serializer(), ELEVATION_JSON)
    }

    val soundingProfile: SoundingProfileResponse by lazy {
        WbJson.decodeFromString(SoundingProfileResponse.serializer(), SOUNDING_PROFILE_JSON)
    }

    const val PACK_JSON = """
    {
      "flight_id": "fixture-1",
      "fetch_timestamp": "2099-06-30T06:00:00Z",
      "days_out": 1,
      "is_historical": false,
      "has_gramet": false,
      "has_skewt": true,
      "has_digest": true,
      "has_advisories": true,
      "assessment": "amber",
      "assessment_reason": "Isolated convective cell risk near Cannes on GFS; marginal cruise icing on the coast. VFR feasible with care and a timing eye on the afternoon build-up.",
      "model_init_times": { "ecmwf": 0, "gfs": 6 },
      "grib_init_times": { "ecmwf": 0, "gfs": 6 },
      "models_skipped_region": [],
      "data_status": {
        "fresh": true,
        "stale_models": [],
        "model_init_times": { "ecmwf": 0, "gfs": 6 },
        "next_expected_update": null,
        "next_expected_model": null
      }
    }
    """

    const val ADVISORIES_JSON = """
    {
      "advisories": [
        {
          "advisory_id": "convective",
          "aggregate_status": "red",
          "aggregate_detail": "Isolated convective cell near LFMD — GFS builds CAPE to ~1400 J/kg by mid-morning.",
          "parameters_used": { "cape_red_jkg": 1000.0, "affected_pct_red": 25.0 },
          "per_model": [
            {
              "model": "gfs",
              "status": "red",
              "detail": "CAPE ~1400 J/kg near LFMD, tops to FL340.",
              "affected_points": 2,
              "total_points": 5,
              "affected_pct": 40.0,
              "affected_nm": 20.0,
              "total_nm": 78.0,
              "cross_check": "High CAPE (~1400 J/kg) while the model's own convective cover is ~2% — a classic quiet-sky thermodynamic RED, not a wall of cloud."
            },
            {
              "model": "ecmwf",
              "status": "amber",
              "detail": "Marginal instability; CAPE ~600 J/kg.",
              "affected_points": 1,
              "total_points": 5,
              "affected_pct": 20.0,
              "affected_nm": 8.0,
              "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "icing",
          "aggregate_status": "amber",
          "aggregate_detail": "Light rime possible in coastal cloud between 6,000 and 9,000 ft.",
          "parameters_used": { "freezing_level_ft": 9000.0 },
          "per_model": [
            {
              "model": "gfs",
              "status": "amber",
              "detail": "Light rime 6–9 kft in stratiform cloud.",
              "affected_points": 2,
              "total_points": 5,
              "affected_pct": 40.0,
              "affected_nm": 18.0,
              "total_nm": 78.0
            },
            {
              "model": "ecmwf",
              "status": "green",
              "detail": "Freezing level above cloud tops.",
              "affected_points": 0,
              "total_points": 5,
              "affected_pct": 0.0,
              "affected_nm": 0.0,
              "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "turbulence",
          "aggregate_status": "green",
          "aggregate_detail": "No significant turbulence expected en route.",
          "parameters_used": {},
          "per_model": [
            {
              "model": "gfs", "status": "green", "detail": "Smooth.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            },
            {
              "model": "ecmwf", "status": "green", "detail": "Smooth.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            }
          ]
        },
        {
          "advisory_id": "winds_aloft",
          "aggregate_status": "green",
          "aggregate_detail": "Light westerly component, no trip-impacting headwind.",
          "parameters_used": {},
          "per_model": [
            {
              "model": "gfs", "status": "green", "detail": "10 kt headwind.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            },
            {
              "model": "ecmwf", "status": "green", "detail": "8 kt headwind.",
              "affected_points": 0, "total_points": 5, "affected_pct": 0.0,
              "affected_nm": 0.0, "total_nm": 78.0
            }
          ]
        }
      ],
      "catalog": [
        {
          "id": "convective", "name": "Convective", "short_description": "Thunderstorm / instability risk",
          "description": "Flags convective potential from model CAPE and native convective cloud fields, graded by the worst realizable scenario along the route.",
          "category": "convective", "default_enabled": true, "altitude_dependent": false, "parameters": []
        },
        {
          "id": "icing", "name": "Icing", "short_description": "Airframe icing risk",
          "description": "Airframe icing risk from freezing level, cloud layers, and Ogimet/NWP icing indices, gated to in-cloud layers.",
          "category": "icing", "default_enabled": true, "altitude_dependent": true, "parameters": []
        },
        {
          "id": "turbulence", "name": "Turbulence", "short_description": "En-route turbulence",
          "description": "Clear-air and mechanical turbulence risk from vertical motion and terrain-driven wind.",
          "category": "turbulence", "default_enabled": true, "altitude_dependent": true, "parameters": []
        },
        {
          "id": "winds_aloft", "name": "Winds Aloft", "short_description": "Trip-impacting wind",
          "description": "Winds-aloft trip impact from the headwind/tailwind component along the planned route and altitude.",
          "category": "winds", "default_enabled": true, "altitude_dependent": true, "parameters": []
        }
      ],
      "route_name": "LFMD LFML",
      "cruise_altitude_ft": 8000,
      "flight_ceiling_ft": 13000,
      "total_distance_nm": 78.0,
      "models": ["ecmwf", "gfs"],
      "aggregation": "worst",
      "airport_conditions": {
        "departure": {
          "icao": "LFMD", "name": "Cannes-Mandelieu",
          "runway_ends": [ { "id": "17", "heading_deg": 165.0 }, { "id": "35", "heading_deg": 345.0 } ],
          "conditions": [
            {
              "model": "ecmwf", "flight_category": "VFR", "ceiling_ft": 4500, "visibility_sm": 9.0,
              "wind_speed_kt": 8.0, "wind_direction_deg": 200.0, "wind_gust_kt": null,
              "best_runway": { "runway_id": "17", "heading_deg": 165.0, "crosswind_kt": 4.0, "headwind_kt": 7.0 },
              "all_runways": [
                { "runway_id": "17", "heading_deg": 165.0, "crosswind_kt": 4.0, "headwind_kt": 7.0 },
                { "runway_id": "35", "heading_deg": 345.0, "crosswind_kt": 4.0, "headwind_kt": -7.0 }
              ]
            }
          ]
        },
        "arrival": {
          "icao": "LFML", "name": "Marseille Provence",
          "runway_ends": [ { "id": "13L", "heading_deg": 130.0 }, { "id": "31R", "heading_deg": 310.0 } ],
          "conditions": [
            {
              "model": "ecmwf", "flight_category": "VFR", "ceiling_ft": 3800, "visibility_sm": 8.0,
              "wind_speed_kt": 12.0, "wind_direction_deg": 300.0, "wind_gust_kt": 18.0,
              "best_runway": { "runway_id": "31R", "heading_deg": 310.0, "crosswind_kt": 6.0, "headwind_kt": 10.0 },
              "all_runways": [
                { "runway_id": "31R", "heading_deg": 310.0, "crosswind_kt": 6.0, "headwind_kt": 10.0 },
                { "runway_id": "13L", "heading_deg": 130.0, "crosswind_kt": 6.0, "headwind_kt": -10.0 }
              ]
            }
          ]
        }
      }
    }
    """

    const val CONVECTIVE_DETAIL_JSON = """
    {
      "advisory_id": "convective",
      "aggregate_status": "red",
      "aggregate_detail": "Isolated convective cell near LFMD — GFS builds CAPE to ~1400 J/kg by mid-morning.",
      "parameters_used": { "cape_red_jkg": 1000.0, "affected_pct_red": 25.0, "el_top_ft": 34000.0 },
      "cross_check_note": "This is an explainer, not an alert: the RED is driven by realizable CAPE, even though the model paints a nearly clear sky. Watch the timing — instability grows through the morning.",
      "name": "Convective",
      "category": "convective",
      "description": "Convective potential from model CAPE and native convective cloud fields, graded by the worst realizable scenario along the route.",
      "convective_note": "GFS and ECMWF disagree on magnitude; treat the coast near Cannes as the focus area.",
      "per_model": [
        {
          "model": "gfs",
          "status": "red",
          "detail": "CAPE ~1400 J/kg near LFMD, parcel equilibrium level ~FL340.",
          "affected_pct": 40.0,
          "affected_nm": 20.0,
          "total_nm": 78.0,
          "cross_check": "High CAPE (~1400 J/kg) while the model's own convective cover is ~2% — a quiet-sky thermodynamic RED."
        },
        {
          "model": "ecmwf",
          "status": "amber",
          "detail": "Marginal instability; CAPE ~600 J/kg, tops near FL240.",
          "affected_pct": 20.0,
          "affected_nm": 8.0,
          "total_nm": 78.0
        }
      ],
      "convective": {
        "gfs": {
          "assessment_method": "thermo",
          "method_counts": { "thermo": 2, "nwp": 0 },
          "thermo": {
            "cape_range_jkg": [300.0, 1400.0],
            "peak": {
              "cape_jkg": 1400.0,
              "el_top_ft": 34000.0,
              "risk_level": "high",
              "distance_nm": 8.0,
              "waypoint_icao": "LFMD",
              "valid_time": "2099-07-01T10:00:00Z",
              "eta": "2099-07-01T09:15:00Z"
            }
          },
          "nwp": { "max_cover_pct": 2.0, "peak_top_ft": null, "note": "Native convective cover ~2% — blue sky." }
        },
        "ecmwf": {
          "assessment_method": "thermo",
          "method_counts": { "thermo": 1, "nwp": 0 },
          "thermo": {
            "cape_range_jkg": [150.0, 600.0],
            "peak": {
              "cape_jkg": 600.0,
              "el_top_ft": 24000.0,
              "risk_level": "moderate",
              "distance_nm": 12.0,
              "waypoint_icao": "LFMD",
              "valid_time": "2099-07-01T10:00:00Z",
              "eta": "2099-07-01T09:18:00Z"
            }
          },
          "nwp": { "max_cover_pct": 1.0, "peak_top_ft": null, "note": "Native convective cover ~1%." }
        }
      }
    }
    """

    const val DIGEST_JSON = """
    {
      "assessment": "amber",
      "assessment_reason": "VFR feasible with attention to the afternoon convective build-up near the coast.",
      "synoptic": "A weak upper trough drifts across the Côte d'Azur, steepening lapse rates through the morning. Surface flow is a light onshore westerly with a shallow marine layer along the Marseille approach.",
      "specific_concerns": "Isolated convective cells could pop near Cannes after 10Z. Keep a diversion plan for the coastal leg.",
      "winds": "Light westerly aloft, 8–12 kt at cruise. No trip-impacting headwind on the direct route.",
      "icing": "A thin band of light rime is possible where the cruise level clips coastal stratiform cloud between 6,000 and 9,000 ft — GFS flags it, ECMWF keeps the freezing level above the tops.",
      "turbulence": "Smooth away from any convective cell. Expect light mechanical turbulence on the Marseille approach in the westerly.",
      "precipitation": "Dry for most of the route; a brief shower is possible under any cell that develops near LFMD.",
      "visibility": "Good, 8–10 SM, reducing only briefly in a shower.",
      "trend": "Instability increases through the afternoon — an earlier departure keeps the convective risk lower.",
      "model_agreement": "Good on winds and visibility; GFS is more bullish than ECMWF on convective CAPE near the coast.",
      "recommendations": "Depart on the earlier side, brief a coastal diversion, and re-check the D-0 pack for cell initiation.",
      "watch_items": [
        "Convective initiation near LFMD after 10Z",
        "Marine layer ceiling on the LFML approach",
        "Light rime in coastal cloud at cruise"
      ]
    }
    """

    const val SNAPSHOT_JSON = """
    {
      "route": {
        "name": "LFMD LFML",
        "waypoints": [
          { "icao": "LFMD", "name": "Cannes-Mandelieu", "lat": 43.542, "lon": 6.953 },
          { "icao": "LFML", "name": "Marseille Provence", "lat": 43.436, "lon": 5.215 }
        ],
        "cruise_altitude_ft": 8000,
        "flight_ceiling_ft": 13000,
        "flight_duration_hours": 1.5
      },
      "target_date": "2099-07-01",
      "days_out": 1,
      "departure_time": "2099-07-01T09:00:00Z",
      "analyses": [
        {
          "waypoint": { "icao": "LFMD", "name": "Cannes-Mandelieu", "lat": 43.542, "lon": 6.953 },
          "target_time": "2099-07-01T09:00:00Z",
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 8.0, "wind_direction_deg": 200.0, "track_deg": 262.0, "headwind_kt": 7.0, "crosswind_kt": 4.0 },
            "gfs": { "wind_speed_kt": 10.0, "wind_direction_deg": 210.0, "track_deg": 262.0, "headwind_kt": 8.0, "crosswind_kt": 5.0 }
          },
          "sounding": {
            "ecmwf": { "indices": { "freezing_level_ft": 11500.0, "cape_surface_jkg": 600.0, "sounding_ceiling_ft": 4500.0, "nwp_ceiling_ft": 4300.0 }, "cloud_cover_low_pct": 30.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 10.0 },
            "gfs": { "indices": { "freezing_level_ft": 9000.0, "cape_surface_jkg": 1400.0, "sounding_ceiling_ft": 4300.0, "nwp_ceiling_ft": 4200.0 }, "cloud_cover_low_pct": 35.0, "cloud_cover_mid_pct": 25.0, "cloud_cover_high_pct": 15.0 }
          }
        },
        {
          "waypoint": { "icao": "LFML", "name": "Marseille Provence", "lat": 43.436, "lon": 5.215 },
          "target_time": "2099-07-01T10:30:00Z",
          "wind_components": {
            "ecmwf": { "wind_speed_kt": 12.0, "wind_direction_deg": 300.0, "track_deg": 262.0, "headwind_kt": 10.0, "crosswind_kt": 6.0 },
            "gfs": { "wind_speed_kt": 13.0, "wind_direction_deg": 305.0, "track_deg": 262.0, "headwind_kt": 11.0, "crosswind_kt": 6.0 }
          },
          "sounding": {
            "ecmwf": { "indices": { "freezing_level_ft": 11800.0, "cape_surface_jkg": 200.0, "sounding_ceiling_ft": 3800.0, "nwp_ceiling_ft": 3600.0 }, "cloud_cover_low_pct": 40.0, "cloud_cover_mid_pct": 15.0, "cloud_cover_high_pct": 5.0 },
            "gfs": { "indices": { "freezing_level_ft": 10500.0, "cape_surface_jkg": 350.0, "sounding_ceiling_ft": 3600.0, "nwp_ceiling_ft": 3500.0 }, "cloud_cover_low_pct": 45.0, "cloud_cover_mid_pct": 20.0, "cloud_cover_high_pct": 5.0 }
          }
        }
      ],
      "route_observations": {
        "corridor_nm": 15.0,
        "fetch_time": "2099-06-30T05:50:00Z",
        "airports_found": 3,
        "airports_with_metar": 3,
        "airports_with_taf": 3,
        "worst_metar_category": "IFR",
        "worst_taf_category": "IFR",
        "has_conflicts": true,
        "phenomena_along_route": ["TSRA", "BR"],
        "airports": [
          {
            "icao": "LFMD", "name": "Cannes-Mandelieu", "distance_from_route_nm": 0.0,
            "enroute_distance_nm": 0.0, "nearest_waypoint_icao": "LFMD",
            "metar_raw": "LFMD 300550Z 20008KT 9999 FEW045 SCT090 21/15 Q1016",
            "metar_time": "2099-06-30T05:50:00Z",
            "metar_flight_category": "VFR", "metar_ceiling_ft": 9000, "metar_visibility_m": 9999,
            "metar_wind_dir": 200, "metar_wind_speed_kt": 8,
            "metar_wind_gust_kt": null, "metar_weather": [], "metar_temperature_c": 21, "metar_dewpoint_c": 15,
            "metar_qnh": 1016.0,
            "taf_raw": "LFMD 300500Z 3006/3018 20010KT 9999 SCT040\nTEMPO 3012/3016 VRB15G25KT 4000 TSRA BKN030CB",
            "taf_flight_category_at_eta": "MVFR", "taf_trend_type": "TEMPO",
            "taf_wind_dir": 200, "taf_wind_speed_kt": 10, "taf_wind_gust_kt": null,
            "taf_applicable_lines": [0, 1],
            "metar_wind_advisory": "green", "metar_best_runway_id": "17", "metar_crosswind_kt": 3.0,
            "metar_headwind_kt": 7.4,
            "taf_wind_advisory": "green", "taf_best_runway_id": "17", "taf_crosswind_kt": 4.0,
            "taf_headwind_kt": 9.2,
            "has_metar": true, "has_taf": true, "eta_hour_offset": 0
          },
          {
            "icao": "LFML", "name": "Marseille Provence", "distance_from_route_nm": 2.0,
            "enroute_distance_nm": 78.0, "nearest_waypoint_icao": "LFML",
            "metar_raw": "LFML 300600Z 30012G18KT 9999 BKN038 20/13 Q1017",
            "metar_time": "2099-06-30T06:00:00Z",
            "metar_flight_category": "VFR", "metar_ceiling_ft": 3800, "metar_visibility_m": 9999,
            "metar_wind_dir": 300, "metar_wind_speed_kt": 12,
            "metar_wind_gust_kt": 18, "metar_weather": [], "metar_temperature_c": 20, "metar_dewpoint_c": 13,
            "metar_qnh": 1017.0,
            "taf_raw": "LFML 300500Z 3006/3018 30012KT 9999 BKN035",
            "taf_flight_category_at_eta": "VFR", "taf_trend_type": null,
            "taf_wind_dir": 300, "taf_wind_speed_kt": 12, "taf_wind_gust_kt": null,
            "taf_applicable_lines": [0],
            "metar_wind_advisory": "amber", "metar_best_runway_id": "31L", "metar_crosswind_kt": 11.0,
            "metar_headwind_kt": 4.8,
            "taf_wind_advisory": "green", "taf_best_runway_id": "31L", "taf_crosswind_kt": 6.0,
            "taf_headwind_kt": 10.4,
            "has_metar": true, "has_taf": true, "eta_hour_offset": 1
          }
        ],
        "comparisons": [
          {
            "icao": "LFMD", "obs_category": "VFR", "model_category": "VFR",
            "category_match": "CONFIRMING",
            "ceiling_delta_ft": -400, "visibility_delta_m": 0.0, "wind_speed_delta_kt": 1.0,
            "model_wind_dir": 205.0, "model_wind_speed_kt": 9.0, "model_wind_gust_kt": null,
            "model_wind_advisory": "green", "model_best_runway_id": "17", "model_crosswind_kt": 4.0,
            "wind_advisory_match": "CONFIRMING",
            "detail": "Model agrees with the observation."
          }
        ]
      },
      "route_sigmets": {
        "corridor_nm": 50.0,
        "fetch_time": "2099-06-30T05:52:00Z",
        "altitude_low_ft": 0,
        "altitude_high_ft": 13000,
        "time_window_from": "2099-06-30T05:52:00Z",
        "time_window_to": "2099-06-30T23:59:59Z",
        "route_firs": ["LFMM", "LFFF"],
        "count": 1,
        "hazards": ["TURB"],
        "has_severe": true,
        "sigmets": [
          {
            "fir_id": "LFMM", "fir_name": "LFMM MARSEILLE", "hazard": "TURB", "qualifier": "SEV",
            "base_ft": 5000, "top_ft": 18000,
            "valid_from": "2026-06-30T06:00:00Z", "valid_to": "2099-06-30T12:00:00Z",
            "direction": null, "speed_kt": null,
            "raw_text": "WSFR31 LFPW 300551\nLFMM SIGMET T04 VALID 300600/301200 LFPW-",
            "matched_firs": ["LFMM"],
            "min_distance_nm": 0.0,
            "enroute_distance_from_nm": 30.0, "enroute_distance_to_nm": 78.0,
            "coords": [[4.75, 43.25], [6.0, 44.0], [6.25, 42.75], [4.75, 43.25]]
          }
        ]
      }
    }
    """

    const val NULL_LIVE_LAYER_JSON = """
    {
      "flight_id": "fixture-1",
      "pack_timestamp": "2099-06-30T06:00:00Z",
      "live_updated_at": null,
      "route_observations": null,
      "route_sigmets": null,
      "observed_conditions": null,
      "changes": null,
      "last_refresh_delta": null
    }
    """

    const val LIVE_LAYER_JSON = """
    {
      "flight_id": "fixture-1",
      "pack_timestamp": "2099-06-30T06:00:00+00:00",
      "live_updated_at": "2099-06-30T08:10:00Z",
      "route_observations": null,
      "route_sigmets": null,
      "observed_conditions": null,
      "changes": {
        "baseline_at": "2099-06-30T06:00:00Z",
        "computed_at": "2099-06-30T08:10:00Z",
        "changes": [
          {
            "key": "metar:LFMD", "kind": "metar_category", "source": "SPECI",
            "direction": "worse", "tier": "alert", "role": "departure",
            "icao": "LFMD", "station_id": null, "from_value": "VFR", "to_value": "IFR",
            "observed_at": "2099-06-30T08:05:00Z", "enroute_distance_nm": 0.0,
            "message": "LFMD SPECI: VFR → IFR", "new_alert": true,
            "trail": {
              "spans": [
                { "start": "2099-06-30T07:00:00Z", "end": "2099-06-30T07:30:00Z" },
                { "start": "2099-06-30T08:10:00Z", "end": null }
              ],
              "times_today": 2,
              "reports": [
                { "at": "2099-06-30T07:00:00Z", "category": "MVFR", "report_type": "METAR" },
                { "at": "2099-06-30T08:05:00Z", "category": "IFR", "report_type": "SPECI" }
              ],
              "baseline_source": "briefing"
            }
          }
        ],
        "worsened_count": 1, "improved_count": 0, "alert_count": 1,
        "recently_cleared": []
      },
      "last_refresh_delta": { "worsened": true, "messages": ["LFMD SPECI: VFR → IFR"], "computed_at": "2099-06-30T08:10:00Z" }
    }
    """

    const val ROUTE_ANALYSES_JSON = """
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
              "icing_ogimet_nwp_zones": [], "sfip_zones": [ { "base_ft": 9000.0, "top_ft": 11000.0, "risk": "light", "icing_type": "rime", "mean_sfip100": 12.0, "variant": "nwp" } ],
              "inversion_layers": [],
              "convective": { "risk_level": "high", "base_ft": 5500.0, "top_ft": 34000.0, "cape_jkg": 1400.0, "cin_jkg": -20.0, "cover_pct": 25.0, "method": "thermo" },
              "convective_nwp": { "risk_level": "none", "base_ft": null, "top_ft": null, "cape_jkg": null, "cin_jkg": null, "cover_pct": 2.0, "method": "nwp" },
              "vertical_motion": { "classification": "ascent", "cat_risk_layers": [] },
              "cloud_cover_low_pct": 35.0, "cloud_cover_mid_pct": 25.0, "cloud_cover_high_pct": 15.0
            }
          },
          "model_divergence": [
            { "variable": "temperature_c", "model_values": { "ecmwf": 12.0, "gfs": 11.0 }, "mean": 11.5, "spread": 1.0, "agreement": "good" }
          ]
        }
      ]
    }
    """

    const val ELEVATION_JSON = """
    {
      "route_name": "LFMD LFML",
      "max_elevation_ft": 2400.0,
      "total_distance_nm": 78.0,
      "points": [
        { "distance_nm": 0.0, "elevation_ft": 12.0, "lat": 43.542, "lon": 6.953 },
        { "distance_nm": 20.0, "elevation_ft": 1800.0, "lat": 43.516, "lon": 6.520 },
        { "distance_nm": 39.0, "elevation_ft": 2400.0, "lat": 43.489, "lon": 6.084 },
        { "distance_nm": 59.0, "elevation_ft": 900.0, "lat": 43.463, "lon": 5.649 },
        { "distance_nm": 78.0, "elevation_ft": 74.0, "lat": 43.436, "lon": 5.215 }
      ]
    }
    """

    const val SOUNDING_PROFILE_JSON = """
    {
      "point_index": 0,
      "lat": 43.542,
      "lon": 6.953,
      "distance_from_origin_nm": 0.0,
      "waypoint_icao": "LFMD",
      "waypoint_name": "Cannes-Mandelieu",
      "interpolated_time": "2099-07-01T09:00:00Z",
      "model": "ecmwf",
      "cruise_altitude_ft": 8000,
      "track_deg": 262.0,
      "terrain_elevation_ft": 12.0,
      "levels": [
        {
          "pressure_hpa": 1000.0, "altitude_ft": 364.0,
          "temperature_c": 22.0, "dewpoint_c": 15.0,
          "wind_speed_kt": 8.0, "wind_direction_deg": 200.0,
          "relative_humidity_pct": 64.0, "dewpoint_depression_c": 7.0,
          "theta_e_k": 328.5, "lapse_rate_c_per_km": 6.5,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.0, "cloud_liquid_water_g_m3": 0.0,
          "ice_mixing_ratio_g_kg": 0.0, "cloud_area_fraction_pct": 5.0,
          "richardson_number": 4.2, "omega_pa_s": -0.05, "w_fpm": 10.0
        },
        {
          "pressure_hpa": 925.0, "altitude_ft": 2500.0,
          "temperature_c": 17.0, "dewpoint_c": 12.5,
          "wind_speed_kt": 12.0, "wind_direction_deg": 215.0,
          "relative_humidity_pct": 75.0, "dewpoint_depression_c": 4.5,
          "theta_e_k": 326.0, "lapse_rate_c_per_km": 6.8,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.02, "cloud_liquid_water_g_m3": 0.02,
          "ice_mixing_ratio_g_kg": 0.0, "cloud_area_fraction_pct": 25.0,
          "richardson_number": 3.5, "omega_pa_s": -0.12, "w_fpm": 24.0
        },
        {
          "pressure_hpa": 850.0, "altitude_ft": 4780.0,
          "temperature_c": 11.5, "dewpoint_c": 9.5,
          "wind_speed_kt": 16.0, "wind_direction_deg": 235.0,
          "relative_humidity_pct": 88.0, "dewpoint_depression_c": 2.0,
          "theta_e_k": 324.2, "lapse_rate_c_per_km": 6.2,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.18, "cloud_liquid_water_g_m3": 0.19,
          "ice_mixing_ratio_g_kg": 0.0, "cloud_area_fraction_pct": 65.0,
          "richardson_number": 2.1, "omega_pa_s": -0.25, "w_fpm": 52.0
        },
        {
          "pressure_hpa": 700.0, "altitude_ft": 9880.0,
          "temperature_c": -1.5, "dewpoint_c": -3.5,
          "wind_speed_kt": 24.0, "wind_direction_deg": 255.0,
          "relative_humidity_pct": 86.0, "dewpoint_depression_c": 2.0,
          "theta_e_k": 321.0, "lapse_rate_c_per_km": 6.9,
          "icing_index": 35.0, "icing_index_nwp": 42.0, "sfip_100": 38.0,
          "cloud_liquid_water_g_kg": 0.22, "cloud_liquid_water_g_m3": 0.20,
          "ice_mixing_ratio_g_kg": 0.04, "cloud_area_fraction_pct": 70.0,
          "richardson_number": 1.4, "omega_pa_s": -0.35, "w_fpm": 85.0
        },
        {
          "pressure_hpa": 600.0, "altitude_ft": 13800.0,
          "temperature_c": -9.5, "dewpoint_c": -13.0,
          "wind_speed_kt": 32.0, "wind_direction_deg": 265.0,
          "relative_humidity_pct": 76.0, "dewpoint_depression_c": 3.5,
          "theta_e_k": 320.4, "lapse_rate_c_per_km": 7.2,
          "icing_index": 22.0, "icing_index_nwp": 28.0, "sfip_100": 25.0,
          "cloud_liquid_water_g_kg": 0.10, "cloud_liquid_water_g_m3": 0.08,
          "ice_mixing_ratio_g_kg": 0.09, "cloud_area_fraction_pct": 45.0,
          "richardson_number": 1.1, "omega_pa_s": -0.28, "w_fpm": 74.0
        },
        {
          "pressure_hpa": 500.0, "altitude_ft": 18280.0,
          "temperature_c": -18.5, "dewpoint_c": -25.0,
          "wind_speed_kt": 42.0, "wind_direction_deg": 270.0,
          "relative_humidity_pct": 56.0, "dewpoint_depression_c": 6.5,
          "theta_e_k": 321.5, "lapse_rate_c_per_km": 7.5,
          "icing_index": 5.0, "icing_index_nwp": 8.0, "sfip_100": 6.0,
          "cloud_liquid_water_g_kg": 0.02, "cloud_liquid_water_g_m3": 0.01,
          "ice_mixing_ratio_g_kg": 0.12, "cloud_area_fraction_pct": 25.0,
          "richardson_number": 0.9, "omega_pa_s": -0.18, "w_fpm": 55.0
        },
        {
          "pressure_hpa": 400.0, "altitude_ft": 23570.0,
          "temperature_c": -30.0, "dewpoint_c": -40.0,
          "wind_speed_kt": 54.0, "wind_direction_deg": 275.0,
          "relative_humidity_pct": 38.0, "dewpoint_depression_c": 10.0,
          "theta_e_k": 324.0, "lapse_rate_c_per_km": 7.8,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.0, "cloud_liquid_water_g_m3": 0.0,
          "ice_mixing_ratio_g_kg": 0.08, "cloud_area_fraction_pct": 15.0,
          "richardson_number": 0.7, "omega_pa_s": -0.08, "w_fpm": 28.0
        },
        {
          "pressure_hpa": 300.0, "altitude_ft": 30065.0,
          "temperature_c": -44.5, "dewpoint_c": -56.0,
          "wind_speed_kt": 68.0, "wind_direction_deg": 280.0,
          "relative_humidity_pct": 28.0, "dewpoint_depression_c": 11.5,
          "theta_e_k": 328.0, "lapse_rate_c_per_km": 7.4,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.0, "cloud_liquid_water_g_m3": 0.0,
          "ice_mixing_ratio_g_kg": 0.03, "cloud_area_fraction_pct": 10.0,
          "richardson_number": 0.6, "omega_pa_s": 0.02, "w_fpm": -8.0
        },
        {
          "pressure_hpa": 250.0, "altitude_ft": 33985.0,
          "temperature_c": -52.5, "dewpoint_c": -65.0,
          "wind_speed_kt": 75.0, "wind_direction_deg": 282.0,
          "relative_humidity_pct": 22.0, "dewpoint_depression_c": 12.5,
          "theta_e_k": 332.0, "lapse_rate_c_per_km": 6.0,
          "icing_index": 0.0, "icing_index_nwp": 0.0, "sfip_100": 0.0,
          "cloud_liquid_water_g_kg": 0.0, "cloud_liquid_water_g_m3": 0.0,
          "ice_mixing_ratio_g_kg": 0.01, "cloud_area_fraction_pct": 5.0,
          "richardson_number": 1.2, "omega_pa_s": 0.05, "w_fpm": -22.0
        }
      ],
      "parcel_path": [
        { "pressure_hpa": 1000.0, "temperature_c": 22.0 },
        { "pressure_hpa": 925.0, "temperature_c": 15.2 },
        { "pressure_hpa": 885.0, "temperature_c": 11.4 },
        { "pressure_hpa": 850.0, "temperature_c": 9.8 },
        { "pressure_hpa": 795.0, "temperature_c": 7.1 },
        { "pressure_hpa": 700.0, "temperature_c": 1.5 },
        { "pressure_hpa": 600.0, "temperature_c": -5.8 },
        { "pressure_hpa": 500.0, "temperature_c": -15.2 },
        { "pressure_hpa": 400.0, "temperature_c": -28.5 },
        { "pressure_hpa": 390.0, "temperature_c": -30.2 },
        { "pressure_hpa": 300.0, "temperature_c": -46.0 },
        { "pressure_hpa": 250.0, "temperature_c": -55.0 }
      ],
      "indices": {
        "lcl_altitude_ft": 3800.0,
        "lcl_pressure_hpa": 885.0,
        "lfc_altitude_ft": 6500.0,
        "lfc_pressure_hpa": 795.0,
        "el_altitude_ft": 24000.0,
        "el_pressure_hpa": 390.0,
        "cape_surface_jkg": 600.0,
        "cin_surface_jkg": -40.0,
        "lifted_index": -2.0,
        "k_index": 28.0,
        "total_totals": 48.0,
        "precipitable_water_mm": 24.5,
        "freezing_level_ft": 9500.0,
        "minus10c_level_ft": 14000.0,
        "minus20c_level_ft": 19000.0,
        "sounding_ceiling_ft": 4500.0,
        "nwp_ceiling_ft": 4300.0
      },
      "cloud_layers": [
        { "base_ft": 3800.0, "top_ft": 10500.0, "base_pressure_hpa": 885.0, "top_pressure_hpa": 685.0, "coverage": "BKN", "mean_dewpoint_depression_c": 2.0, "mean_cloud_cover_pct": 68.0 }
      ],
      "nwp_cloud_layers": [
        { "base_ft": 4000.0, "top_ft": 10200.0, "base_pressure_hpa": 878.0, "top_pressure_hpa": 692.0, "coverage": "BKN", "mean_dewpoint_depression_c": null, "mean_cloud_cover_pct": 68.0 }
      ],
      "icing_zones": [
        { "base_ft": 9500.0, "top_ft": 13800.0, "base_pressure_hpa": 710.0, "top_pressure_hpa": 600.0, "risk": "light", "icing_type": "rime", "mean_icing_index": 30.0 }
      ],
      "icing_ogimet_nwp_zones": [
        { "base_ft": 9500.0, "top_ft": 13800.0, "base_pressure_hpa": 710.0, "top_pressure_hpa": 600.0, "risk": "moderate", "icing_type": "rime", "mean_icing_index": 38.0 }
      ],
      "sfip_zones": [
        { "base_ft": 9500.0, "top_ft": 13000.0, "base_pressure_hpa": 710.0, "top_pressure_hpa": 620.0, "risk": "light", "icing_type": "rime", "mean_sfip_100": 35.0, "variant": "nwp" }
      ],
      "inversion_layers": [
        { "base_ft": 2500.0, "top_ft": 3200.0, "base_pressure_hpa": 925.0, "top_pressure_hpa": 900.0, "strength_c": 2.0, "surface_based": false }
      ],
      "convective": {
        "risk_level": "moderate", "base_ft": 6500.0, "top_ft": 24000.0,
        "cape_jkg": 600.0, "cin_jkg": -40.0, "cover_pct": 15.0, "method": "thermo"
      }
    }
    """
}

/**
 * In-memory [BriefingRepository] serving deterministic fixtures for tests and offline UI previews.
 * Mirrors `FixtureBriefingRepository` on iOS.
 */
open class FixtureBriefingRepository(
    val mockLive: Boolean = false,
) : BriefingRepository {

    val flights: MutableList<FlightResponse> = decodeDefaultFlights().toMutableList()
    var lastMovedFlight: Pair<String, MoveFlightRequest>? = null
    var lastUpdatedFlight: Pair<String, UpdateFlightRequest>? = null
    var lastCreatedFlight: CreateFlightRequest? = null
    val refreshTriggeredFor = mutableListOf<String>()

    /** Invalidation the next [updateFlight] reports, so tests can drive the post-save paths. */
    var nextInvalidation: FlightInvalidation = FlightInvalidation.NONE
    /** When set, `updateFlight` / `interpretRoute` throw it (tests). */
    var updateFlightError: Throwable? = null
    var interpretRouteError: Throwable? = null
    var savedDebrief: DebriefResponse? = null

    override suspend fun listFlights(): List<FlightResponse> = flights.toList()

    override suspend fun getFlight(id: String): FlightResponse =
        flights.firstOrNull { it.id == id } ?: throw ApiError.NotFound

    override suspend fun createFlight(req: CreateFlightRequest): FlightResponse {
        lastCreatedFlight = req
        val id = "fixture-created-${flights.size + 1}"
        val routeName = req.routeName ?: req.waypoints.joinToString(" ")
        val created = FlightResponse(
            id = id,
            userId = "ui-test",
            routeName = routeName,
            waypoints = req.waypoints,
            departureTime = req.departureTime,
            targetDate = req.departureTime.take(10),
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: 8000,
            flightCeilingFt = req.flightCeilingFt ?: 18000,
            flightDurationHours = req.flightDurationHours ?: 2.0,
            createdAt = "2099-06-30T00:00:00Z",
            profileId = req.profileId,
            aircraftId = req.aircraftId,
            flexibility = req.flexibility ?: FlightFlexibility.NONE,
            rawRoute = req.rawRoute,
            description = req.description,
        )
        flights.add(0, created)
        return created
    }

    override suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse {
        lastUpdatedFlight = id to req
        updateFlightError?.let { throw it }
        val idx = flights.indexOfFirst { it.id == id }
        if (idx < 0) throw ApiError.NotFound
        val current = flights[idx]
        val updated = current.copy(
            waypoints = req.waypoints ?: current.waypoints,
            departureTime = req.departureTime ?: current.departureTime,
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: current.cruiseAltitudeFt,
            flightCeilingFt = req.flightCeilingFt ?: current.flightCeilingFt,
            flightDurationHours = req.flightDurationHours ?: current.flightDurationHours,
            profileId = req.profileId ?: current.profileId,
            aircraftId = req.aircraftId ?: current.aircraftId,
            flexibility = req.flexibility ?: current.flexibility,
            altDepartureTime = when (req.altDepartureTime) {
                null -> current.altDepartureTime
                "" -> null
                else -> req.altDepartureTime
            },
            rawRoute = if (req.waypoints != null) req.rawRoute else current.rawRoute,
        )
        flights[idx] = updated
        return UpdateFlightResponse(flight = updated, invalidation = nextInvalidation)
    }

    override suspend fun deleteFlight(id: String) {
        flights.removeAll { it.id == id }
    }

    override suspend fun bulkDeleteFlights(ids: List<String>): BulkDeleteResponse {
        val idSet = ids.toSet()
        flights.removeAll { it.id in idSet }
        return BulkDeleteResponse(deleted = ids)
    }

    override suspend fun moveFlight(id: String, req: MoveFlightRequest): FlightResponse {
        lastMovedFlight = id to req
        val idx = flights.indexOfFirst { it.id == id }
        if (idx < 0) throw ApiError.NotFound
        val source = flights[idx]
        val waypoints = req.waypoints ?: source.waypoints
        val departure = req.departureTime ?: source.departureTime
        // Like the server, the id follows the route endpoints and the UTC date.
        val sameIdentity = departure.take(10) == source.departureTime.take(10) &&
            waypoints.firstOrNull() == source.waypoints.firstOrNull() &&
            waypoints.lastOrNull() == source.waypoints.lastOrNull()
        val moved = source.copy(
            id = if (sameIdentity) source.id else "fixture-moved-${flights.size + 1}",
            waypoints = waypoints,
            departureTime = departure,
            targetDate = departure.take(10),
            cruiseAltitudeFt = req.cruiseAltitudeFt ?: source.cruiseAltitudeFt,
            flightCeilingFt = req.flightCeilingFt ?: source.flightCeilingFt,
            flightDurationHours = req.flightDurationHours ?: source.flightDurationHours,
            rawRoute = if (req.waypoints != null) req.rawRoute else source.rawRoute,
            trip = if (req.keepInTrip) source.trip else null,
        )
        flights[idx] = moved
        return moved
    }

    override suspend fun parseFpl(fplText: String): ParseFplResponse =
        ParseFplResponse(
            waypoints = listOf("LFMD", "LFML"),
            altitudeFt = 8000,
            flightRules = "V",
            durationHours = 1.5,
            rawRoute = "LFMD DCT LFML",
        )

    /** Keeps the 4-letter idents, drops Field-15 syntax (DCT, VFR, N0120F065...). */
    override suspend fun interpretRoute(rawRoute: String): InterpretRouteResponse {
        interpretRouteError?.let { throw it }
        val tokens = rawRoute.uppercase().split(Regex("[\\s,\\->]+")).filter { it.isNotBlank() }
        val syntax = setOf("DCT", "VFR", "IFR")
        val idents = tokens.filter { it.length == 4 && it.all(Char::isLetter) && it !in syntax }
        return InterpretRouteResponse(originalTokens = tokens, interpreted = idents)
    }

    override suspend fun routeDistance(waypoints: List<String>): RouteDistanceResponse =
        RouteDistanceResponse(
            waypoints = listOf(
                Waypoint(icao = "LFMD", name = "Cannes-Mandelieu", lat = 43.542, lon = 6.953),
                Waypoint(icao = "LFML", name = "Marseille Provence", lat = 43.436, lon = 5.215),
            ),
            totalDistanceNm = 78.0,
        )

    override suspend fun fetchAutorouterRoutes(
        departure: String,
        destination: String,
        departureTime: String,
        cruiseAltitudeFt: Int,
        durationHours: Double,
        aircraftId: Int?,
    ): AutorouterRoutesResponse = AutorouterRoutesResponse()

    override suspend fun listPacks(flightId: String): List<PackMetaResponse> =
        if (flightId == FixtureBriefingData.FLIGHT_ID) FixtureBriefingData.packs else emptyList()

    override suspend fun latestPack(flightId: String): PackMetaResponse {
        if (flightId == FixtureBriefingData.FLIGHT_ID) return FixtureBriefingData.pack
        throw ApiError.NotFound
    }

    override suspend fun freshness(flightId: String): DataStatus =
        FixtureBriefingData.pack.dataStatus ?: DataStatus(fresh = true)

    override suspend fun advisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun altAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun recalculateAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun recalculateAltAdvisories(flightId: String, timestamp: String): AdvisoriesResponse =
        FixtureBriefingData.advisories

    override suspend fun advisoryDetail(
        flightId: String,
        timestamp: String,
        advisoryId: String,
        alt: Boolean,
    ): AdvisoryDetailResponse = FixtureBriefingData.convectiveDetail

    override suspend fun alternates(flightId: String, timestamp: String): AlternatesResponse =
        AlternatesResponse()

    override suspend fun digest(flightId: String, timestamp: String): DigestResponse =
        FixtureBriefingData.digest

    override suspend fun snapshot(flightId: String, timestamp: String): SnapshotResponse =
        FixtureBriefingData.snapshot

    override suspend fun routeAnalyses(flightId: String, timestamp: String): RouteAnalysesResponse =
        FixtureBriefingData.routeAnalyses

    override suspend fun elevation(flightId: String, timestamp: String): ElevationResponse =
        FixtureBriefingData.elevation

    override suspend fun soundingProfile(
        flightId: String,
        timestamp: String,
        pointIndex: Int,
        model: String,
    ): SoundingProfileResponse = FixtureBriefingData.soundingProfile

    override suspend fun gramet(flightId: String, timestamp: String): ByteArray =
        throw ApiError.NotFound

    override suspend fun skewT(flightId: String, timestamp: String, icao: String, model: String): ByteArray =
        throw ApiError.NotFound

    override suspend fun routeSkewT(flightId: String, timestamp: String, pointIndex: Int, model: String): ByteArray =
        throw ApiError.NotFound

    open override suspend fun chartImage(chart: PackChart): ByteArray =
        throw ApiError.NotFound

    override suspend fun chartOverlay(
        urlPath: String,
        flightId: String,
        timestamp: String,
        source: String,
    ): ChartOverlay = emptyMap()

    override suspend fun liveLayer(flightId: String): LiveLayerResponse =
        if (mockLive && flightId == FixtureBriefingData.FLIGHT_ID) {
            FixtureBriefingData.liveLayer
        } else {
            FixtureBriefingData.nullLiveLayer
        }

    override suspend fun triggerRefresh(flightId: String, force: Boolean): RefreshStatusResponse {
        refreshTriggeredFor += flightId
        return RefreshStatusResponse(status = "queued", flightId = flightId)
    }

    override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse =
        ActiveRefreshResponse(active = false)

    override suspend fun activeRefreshes(): Map<String, ActiveRefreshResponse> =
        emptyMap()

    override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> =
        flowOf(RefreshEvent(type = "complete", pack = FixtureBriefingData.pack))

    override suspend fun timeOptions(flightId: String): TimeOptionsResponse =
        TimeOptionsResponse(flightId = flightId)

    override suspend fun triggerTimeScan(flightId: String): TimeScanStatusDTO =
        TimeScanStatusDTO(status = TimeScanJobStatus.READY)

    override suspend fun confirmTimeOption(flightId: String, req: ConfirmTimeOptionRequest): FlightResponse =
        moveFlight(flightId, MoveFlightRequest(departureTime = req.departureTime))

    override suspend fun observedStatus(): ObservedImageryStatusResponse =
        ObservedImageryStatusResponse()

    override suspend fun observedFrames(layer: String, bbox: String?): ObservedFramesResponse =
        ObservedFramesResponse()

    override suspend fun observedFlashes(bbox: String, validTime: String?): ObservedFlashesResponse =
        ObservedFlashesResponse()

    override suspend fun cellFrames(bbox: String): CellFramesResponse =
        CellFramesResponse()

    override suspend fun observedCells(bbox: String, validTime: String?): CellDisplay =
        CellDisplay()

    override suspend fun debrief(flightId: String): DebriefResponse =
        savedDebrief ?: DebriefResponse(flightId = flightId)

    override suspend fun saveDebrief(flightId: String, req: DebriefRequest): DebriefResponse {
        val resp = DebriefResponse(
            flightId = flightId,
            outcomes = req.outcomes,
            otherText = req.otherText,
            ratingOverall = req.ratingOverall,
            createdAt = "2099-06-30T12:00:00Z",
            updatedAt = "2099-06-30T12:00:00Z",
        )
        savedDebrief = resp
        return resp
    }

    override suspend fun deleteDebrief(flightId: String) {
        savedDebrief = null
    }

    override suspend fun submitPirep(req: SubmitPirepRequest): PirepResponse =
        PirepResponse(
            id = 1,
            clientUuid = req.clientUuid,
            flightId = req.flightId,
            observedAt = req.observedAt,
            latitude = req.latitude,
            longitude = req.longitude,
            isOwn = true,
        )

    override suspend fun listPireps(flightId: String): PirepListResponse =
        PirepListResponse()

    override suspend fun resolveSharedFlight(code: String): FlightResponse =
        flights.first()

    override suspend fun subscribeToSharedFlight(code: String): FlightResponse =
        flights.first()

    override suspend fun unsubscribeFromFlight(flightId: String) = Unit

    override suspend fun resolveSharedTrip(code: String): TripResponse =
        FixtureTripData.trip

    override suspend fun subscribeToSharedTrip(code: String): TripSubscribeResponse =
        TripSubscribeResponse(
            subscribed = true,
            tripId = FixtureTripData.TRIP_ID,
            title = FixtureTripData.trip.title,
            legCount = FixtureTripData.trip.legCount,
        )

    override suspend fun unsubscribeFromTrip(tripId: String) = Unit

    companion object {
        fun decodeDefaultFlights(): List<FlightResponse> =
            WbJson.decodeFromString(ListSerializer(FlightResponse.serializer()), DEFAULT_FLIGHTS_JSON)

        const val DEFAULT_FLIGHTS_JSON = """
        [
          {
            "id": "fixture-1",
            "user_id": "ui-test",
            "route_name": "LFMD LFML",
            "waypoints": ["LFMD", "LFML"],
            "departure_time": "2099-07-01T09:00:00Z",
            "target_date": "2099-07-01",
            "target_time_utc": 9,
            "cruise_altitude_ft": 8000,
            "flight_ceiling_ft": 13000,
            "flight_duration_hours": 1.5,
            "private": false,
            "auto_refresh": true,
            "created_at": "2099-06-30T00:00:00Z",
            "latest_briefing": {
              "last_updated": "2099-06-30T06:00:00Z",
              "assessment": "amber",
              "days_out": 1,
              "has_digest": true
            },
            "advisory_summary": {
              "green": 2,
              "amber": 1,
              "red": 1,
              "chips": [
                { "id": "convective", "name": "Convective", "status": "red" },
                { "id": "icing", "name": "Icing", "status": "amber" }
              ]
            },
            "trip": {
              "trip_id": "trip-fixture-1",
              "title": "Riviera Hop",
              "role": "owner",
              "leg_index": 1,
              "leg_count": 2,
              "continuity_warning": null
            }
          },
          {
            "id": "fixture-2",
            "user_id": "ui-test",
            "route_name": "EGTK LFPB",
            "waypoints": ["EGTK", "LFPB"],
            "raw_route": "EGTK DCT LFPB",
            "departure_time": "2099-07-03T10:30:00Z",
            "target_date": "2099-07-03",
            "target_time_utc": 10,
            "cruise_altitude_ft": 9000,
            "flight_ceiling_ft": 18000,
            "flight_duration_hours": 2.0,
            "private": false,
            "auto_refresh": true,
            "created_at": "2099-06-30T00:00:00Z",
            "briefing_status": {
              "last_updated": "2099-06-30T06:00:00Z",
              "assessment": "green",
              "days_out": 3,
              "has_digest": true
            },
            "advisory_summary": {
              "green": 4,
              "amber": 0,
              "red": 0,
              "chips": []
            }
          }
        ]
        """
    }
}
