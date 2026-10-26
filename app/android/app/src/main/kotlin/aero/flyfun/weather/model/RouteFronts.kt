package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * The experimental front-detection artifact (`route_fronts.json`, #196), as far
 * as the cross-section needs it: the vertically-linked front chains the server
 * builds per model. Present only when the "Auto Front Detection" preference was
 * on at briefing time; the endpoint 404s otherwise.
 *
 * Only `front_chains` is decoded. The web falls back to the per-level crossings
 * on packs built before vertical linking, which means deduping them on the
 * client; Android draws only what the server linked.
 *
 * SYNC: `web/ts/types/fronts.ts`, iOS `RouteFrontsResponse.swift`;
 * `src/weatherbrief/models/fronts.py` defines the shape.
 */
@Serializable
data class RouteFrontsResponse(
    /** model → linked chains. Empty on pre-linking packs. */
    @SerialName("front_chains")
    val frontChains: Map<String, List<FrontChain>> = emptyMap(),
)

/** One air-mass boundary linked across 925/850/700 hPa. */
@Serializable
data class FrontChain(
    /** Ordered bottom → top (925 → 850 → 700). */
    val nodes: List<FrontChainNode> = emptyList(),
    /** Consensus kind: `cold` / `warm` / `quasi-stationary`. */
    val kind: String = "quasi-stationary",
    /** Depth: 1 = shallow / single level. */
    @SerialName("n_levels")
    val nLevels: Int = 1,
)

@Serializable
data class FrontChainNode(
    @SerialName("level_hPa")
    val levelHpa: Int = 850,
    @SerialName("distance_km")
    val distanceKm: Double = 0.0,
    val kind: String = "quasi-stationary",
    /** `significant` / `classical` / `sharp`. */
    val intensity: String = "significant",
    /** `dry` / `partly` / `wet` / `convective`; drives the convective glyph. */
    @SerialName("co_location")
    val coLocation: String? = null,
)
