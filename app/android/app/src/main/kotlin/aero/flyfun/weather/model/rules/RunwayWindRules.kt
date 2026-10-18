package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.EndComponents
import aero.flyfun.weather.model.RunwayEnd
import aero.flyfun.weather.model.RunwayEndInfo
import aero.flyfun.weather.model.RunwayInfo
import aero.flyfun.weather.model.WindAtAirport
import aero.flyfun.weather.model.WindSample
import java.util.Locale
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Computed wind components for a single runway end.
 */
data class RunwayComponents(
    val ident: String,
    val headingDeg: Double,
    /** Positive = headwind, negative = tailwind (knots). */
    val headwindKt: Double,
    /** Absolute crosswind magnitude (knots). */
    val crosswindKt: Double,
    /** True if the crosswind comes from the left of the runway heading. */
    val isLeftCrosswind: Boolean,
    /** Crosswind magnitude during gusts, if gust speed exceeds steady wind speed. */
    val gustCrosswindKt: Double?,
    /** True if this runway end is the favored end at the airport/runway. */
    val isFavored: Boolean,
)

/**
 * Pure trigonometric and formatting rules for runway wind components, best-runway selection,
 * crosswind severity classification, and dial/bar rendering.
 * Mirrors iOS `RunwayWindRules.swift`.
 */
object RunwayWindRules {

    /** Default crosswind limit (knots) when the pilot has not configured an aircraft-specific limit. */
    const val DEFAULT_CROSSWIND_LIMIT_KT: Int = 15

    /** Amber caution threshold as a fraction of the max crosswind limit (10 kt for default 15 kt limit). */
    const val AMBER_THRESHOLD_RATIO: Double = 2.0 / 3.0

    enum class WindSourceMode {
        METAR,
        TAF,
    }

    data class DialTransform(
        val stripRotationDeg: Double,
        val windRotationDeg: Double?,
        val showEndZeroAtTop: Boolean,
        val showWindArrow: Boolean,
    )

    data class PreferredComponent(
        val label: String,
        val valueKt: Int,
        val isTailwind: Boolean,
    )

    // -------------------------------------------------------------------------
    // Core Trigonometric Calculations
    // -------------------------------------------------------------------------

    /**
     * Normalize an angle difference into `(-180.0, 180.0]`.
     */
    fun normalizeAngleDeltaDeg(angleDeg: Double): Double {
        var delta = (angleDeg + 180.0) % 360.0
        if (delta <= 0.0) delta += 360.0
        return delta - 180.0
    }

    /**
     * Compute `(headwindKt, signedCrosswindKt)` for a given wind direction/speed and runway true heading.
     *
     * - `first` (`headwindKt`): positive = headwind, negative = tailwind.
     * - `second` (`signedCrosswindKt`): positive = wind from right, negative = wind from left.
     *
     * Returns `(0.0, 0.0)` when `windDirDeg` or `windSpeedKt` is null or non-positive.
     */
    fun computeComponents(
        windDirDeg: Int?,
        windSpeedKt: Int?,
        windGustKt: Int? = null,
        runwayHeadingDeg: Double,
    ): Pair<Double, Double> {
        if (windDirDeg == null || windSpeedKt == null || windSpeedKt <= 0) {
            return 0.0 to 0.0
        }
        val deltaDeg = normalizeAngleDeltaDeg(windDirDeg.toDouble() - runwayHeadingDeg)
        val deltaRad = deltaDeg * PI / 180.0
        val spd = windSpeedKt.toDouble()
        val headwind = spd * cos(deltaRad)
        val signedCrosswind = spd * sin(deltaRad)
        val cleanHeadwind = if (abs(headwind) < 1e-9) 0.0 else headwind
        val cleanCrosswind = if (abs(signedCrosswind) < 1e-9) 0.0 else signedCrosswind
        return cleanHeadwind to cleanCrosswind
    }

    /**
     * Select the best runway end identifier (`RunwayEnd.id`) for a given wind direction and speed.
     *
     * Prefers runway ends with non-negative headwind (`headwind >= 0`), selecting the one with
     * the smallest absolute crosswind and highest headwind as tiebreaker.
     */
    fun pickBestRunwayEnd(
        ends: List<RunwayEnd>,
        windDirDeg: Int?,
        windSpeedKt: Int?,
    ): String? {
        if (ends.isEmpty()) return null
        if (windDirDeg == null || windSpeedKt == null || windSpeedKt <= 0) {
            return ends.first().id
        }

        data class ScoredEnd(
            val id: String,
            val headwind: Double,
            val absCrosswind: Double,
        )

        val scored = ends.map { end ->
            val (hw, xw) = computeComponents(windDirDeg, windSpeedKt, null, end.headingDeg)
            ScoredEnd(id = end.id, headwind = hw, absCrosswind = abs(xw))
        }

        val intoWind = scored.filter { it.headwind >= -1e-6 }
        val candidates = intoWind.ifEmpty { scored }

        return candidates.minWithOrNull(
            compareBy<ScoredEnd> { it.absCrosswind }
                .thenByDescending { it.headwind },
        )?.id
    }

    /**
     * Overload accepting `RunwayEndInfo` DTOs from `BriefingModels`.
     */
    fun pickBestRunwayEndInfo(
        ends: List<RunwayEndInfo>,
        windDirDeg: Int?,
        windSpeedKt: Int?,
    ): String? = pickBestRunwayEnd(
        ends = ends.map { RunwayEnd(id = it.id, headingDeg = it.headingTrue) },
        windDirDeg = windDirDeg,
        windSpeedKt = windSpeedKt,
    )

    /**
     * Evaluate full `RunwayComponents` for a list of runway ends, marking the favored end.
     */
    fun evaluateRunwayEnds(
        ends: List<RunwayEnd>,
        windDirDeg: Int?,
        windSpeedKt: Int?,
        windGustKt: Int? = null,
    ): List<RunwayComponents> {
        if (ends.isEmpty()) return emptyList()
        val bestId = pickBestRunwayEnd(ends, windDirDeg, windSpeedKt)
        return ends.map { end ->
            val (hw, signedXw) = computeComponents(windDirDeg, windSpeedKt, windGustKt, end.headingDeg)
            val gustXw = if (windGustKt != null && windSpeedKt != null && windGustKt > windSpeedKt && windDirDeg != null) {
                val (_, gx) = computeComponents(windDirDeg, windGustKt, null, end.headingDeg)
                abs(gx)
            } else {
                null
            }
            RunwayComponents(
                ident = end.id,
                headingDeg = end.headingDeg,
                headwindKt = hw,
                crosswindKt = abs(signedXw),
                isLeftCrosswind = signedXw < -1e-6,
                gustCrosswindKt = gustXw,
                isFavored = end.id == bestId,
            )
        }
    }

    // -------------------------------------------------------------------------
    // Crosswind Severity Assessment
    // -------------------------------------------------------------------------

    fun effectiveCrosswindLimit(userLimitKt: Int?): Double {
        if (userLimitKt != null && userLimitKt > 0) return userLimitKt.toDouble()
        return DEFAULT_CROSSWIND_LIMIT_KT.toDouble()
    }

    /**
     * Classify crosswind magnitude against a pilot/aircraft limit (default 15 kt):
     * - `< 2/3 limit` (e.g. `< 10 kt`): `Assessment.GREEN`
     * - `[2/3 limit, limit)` (e.g. `10..14.9 kt`): `Assessment.AMBER`
     * - `>= limit` (e.g. `>= 15 kt`): `Assessment.RED`
     */
    fun crosswindSeverity(
        crosswindKt: Double,
        maxCrosswindKt: Int? = null,
    ): Assessment {
        if (crosswindKt.isNaN()) return Assessment.UNAVAILABLE
        val absXw = abs(crosswindKt)
        val limit = effectiveCrosswindLimit(maxCrosswindKt)
        val amberThreshold = limit * AMBER_THRESHOLD_RATIO
        return when {
            absXw >= limit - 1e-6 -> Assessment.RED
            absXw >= amberThreshold - 1e-6 -> Assessment.AMBER
            else -> Assessment.GREEN
        }
    }

    // -------------------------------------------------------------------------
    // Dial & Bar Geometry / Formatting Helpers (iOS RunwayWindRules parity)
    // -------------------------------------------------------------------------

    fun selectActiveWind(winds: List<WindAtAirport>, mode: WindSourceMode): WindAtAirport? {
        val preferredSource = when (mode) {
            WindSourceMode.METAR -> "metar"
            WindSourceMode.TAF -> "taf"
        }
        return winds.firstOrNull { it.sample.source.equals(preferredSource, ignoreCase = true) }
            ?: winds.firstOrNull()
    }

    fun longestRunwayIndex(runways: List<RunwayInfo>): Int? {
        if (runways.isEmpty()) return null
        return runways.indices.maxByOrNull { idx -> runways[idx].lengthFt } ?: 0
    }

    fun dialTransform(
        headingDeg: Double,
        windDirDeg: Double?,
        favoredEndIndex: Int?,
    ): DialTransform {
        val stripRot = headingDeg - 90.0
        val windRot = windDirDeg?.let { it + 90.0 }
        val showZeroAtTop = favoredEndIndex == 1
        return DialTransform(
            stripRotationDeg = stripRot,
            windRotationDeg = windRot,
            showEndZeroAtTop = showZeroAtTop,
            showWindArrow = windRot != null,
        )
    }

    fun preferredComponent(end: EndComponents): PreferredComponent {
        val hw = end.headwindKt.roundToInt()
        return if (hw >= 0) {
            PreferredComponent(label = "HW", valueKt = max(0, hw), isTailwind = false)
        } else {
            PreferredComponent(label = "TW", valueKt = abs(hw), isTailwind = true)
        }
    }

    fun crosswindLabel(crosswindKt: Double): String {
        val xw = abs(crosswindKt).roundToInt()
        return "XW ${xw}kt"
    }

    fun windBadgeText(speedKt: Double?, gustKt: Double?, isVariable: Boolean = false): String {
        if (isVariable && (speedKt == null || speedKt <= 0.0)) return "VRB"
        val spd = (speedKt ?: 0.0).roundToInt()
        if (spd <= 0) return "CALM"
        val gst = gustKt?.roundToInt()
        val core = if (gst != null && gst > spd) "${spd}G${gst}kt" else "${spd}kt"
        return if (isVariable) "VRB $core" else core
    }

    fun windSummaryLine(wind: WindSample): String {
        val spd = wind.speedKt.roundToInt()
        if (spd <= 0) return "Calm"
        val dirPrefix = if (wind.directionDeg > 0.0) {
            String.format(Locale.US, "%03d°", wind.directionDeg.roundToInt())
        } else {
            "VRB"
        }
        val gst = wind.gustKt?.roundToInt()
        val spdPart = if (gst != null && gst > spd) "${spd}G${gst}kt" else "${spd}kt"
        return "$dirPrefix $spdPart"
    }

    fun bestEndSummary(bestEndId: String?, end: EndComponents?): String? {
        if (bestEndId.isNullOrBlank() || end == null) return null
        val pref = preferredComponent(end)
        val xw = crosswindLabel(end.crosswindKt)
        return "RWY $bestEndId · ${pref.label} ${pref.valueKt}kt · $xw"
    }
}
