package aero.flyfun.rzflight

import kotlinx.serialization.Serializable

/**
 * Geographic coordinate in decimal degrees (WGS84).
 */
@Serializable
data class Coord(
    val latitude: Double,
    val longitude: Double,
) {
    fun distanceNmTo(other: Coord): Double = RouteGeometry.directDistanceNm(this, other)
}
