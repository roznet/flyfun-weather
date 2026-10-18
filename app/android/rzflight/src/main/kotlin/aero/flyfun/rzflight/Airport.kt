package aero.flyfun.rzflight

import kotlinx.serialization.Serializable

/**
 * Airport record mirrored from `RZFlight.Airport`.
 */
@Serializable
data class Airport(
    val icao: String,
    val name: String,
    val latitude: Double,
    val longitude: Double,
    val elevationFt: Int = 0,
    val isoCountry: String = "",
    val municipality: String = "",
    val type: String = "",
) {
    val coord: Coord
        get() = Coord(latitude = latitude, longitude = longitude)
}
