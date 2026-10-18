package aero.flyfun.weather.model

import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

/**
 * Pure conversion helpers between the wire format (`Double` decimal hours on
 * `FlightResponse` / `CreateFlightRequest` / `UpdateFlightRequest`) and the
 * hours + quarter-hour minutes picker UI.
 */
object FlightDuration {
    val minuteOptions: List<Int> = listOf(0, 15, 30, 45)
    const val maxHours: Int = 12

    /** Maximum value representable by the picker (`12h 45m` = `12.75` h). */
    val maxDecimalHours: Double = maxHours + (minuteOptions.lastOrNull() ?: 0) / 60.0

    data class Parts(val hours: Int, val minutes: Int)

    /**
     * Split a decimal hour value into `(hours, minutes)` where `minutes` is one
     * of `minuteOptions` (`0, 15, 30, 45`), rounding UP to the next quarter-hour.
     *
     * Negative inputs clamp to `0h 00m`. Values above [maxDecimalHours] are not
     * clamped here so [label] and [format] can still format any server value accurately;
     * use [clampToPickerRange] when binding to the picker.
     */
    fun split(decimalHours: Double): Parts {
        val clamped = max(0.0, decimalHours)
        val totalMinutes = clamped * 60.0
        // Subtract a tiny epsilon before ceil so exact multiples like 1.25h (75.0 min)
        // don't round up due to floating-point representation noise.
        val quarterHours = ceil(totalMinutes / 15.0 - 1e-9).toInt()
        val roundedMinutes = quarterHours * 15
        return Parts(hours = roundedMinutes / 60, minutes = roundedMinutes % 60)
    }

    /** Recombine picker parts into decimal hours for API requests. */
    fun combine(hours: Int, minutes: Int): Double =
        max(0, hours).toDouble() + max(0, minutes).toDouble() / 60.0

    /** Clamp a decimal hour value to the range representable by the picker (`0 ... 12.75`). */
    fun clampToPickerRange(decimalHours: Double): Double =
        min(max(0.0, decimalHours), maxDecimalHours)

    /** Compact human label matching the quarter-hour rounding, e.g. `"2h"` or `"2h 30m"`. */
    fun label(decimalHours: Double): String {
        val parts = split(decimalHours)
        return if (parts.minutes == 0) {
            "${parts.hours}h"
        } else {
            "${parts.hours}h ${parts.minutes}m"
        }
    }

    /** Alias for [label] (`"Xh"` or `"Xh YYm"`). */
    fun format(decimalHours: Double): String = label(decimalHours)

    /** Compact `"XhYY"` format, e.g. `"2h"` or `"2h30"`. */
    fun formatCompact(decimalHours: Double): String {
        val parts = split(decimalHours)
        return if (parts.minutes == 0) {
            "${parts.hours}h"
        } else {
            "%dh%02d".format(parts.hours, parts.minutes)
        }
    }
}
