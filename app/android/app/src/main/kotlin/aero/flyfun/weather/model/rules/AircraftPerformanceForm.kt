package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.AircraftPerformanceResponse
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.PerformancePreset
import aero.flyfun.weather.model.PerformancePresetsResponse

/** What the four number fields and the turbo switch hold. Null is an empty field. */
data class PerformanceValues(
    val climbIasKt: Double?,
    val climbRateFpm: Double?,
    val descentRateFpm: Double?,
    val descentAngleDeg: Double?,
    val turbo: Boolean,
)

/** The save payload: the base preset and only the values that differ from it (0 = no override). */
data class PerformanceOverrides(
    val performancePreset: String,
    val isTurbo: Boolean,
    val climbIasKt: Int,
    val climbRateFpm: Int,
    val descentRateFpm: Int,
    val descentAngleDeg: Double,
)

/**
 * The climb / descent preset rules of the aircraft form (#1005).
 * SYNC: `web/ts/components/aircraft-performance-form.ts`. The preset table itself comes from
 * `GET /api/aircraft/presets` and is never copied here.
 */
object AircraftPerformanceForm {
    private const val MODE_RATE = "rate"
    private const val MODE_ANGLE = "angle"

    private val PRESET_NAMES = mapOf(
        "trainer_piston" to "Trainer piston",
        "hp_piston" to "High-performance piston",
        "turbo_piston" to "Turbocharged piston",
        "twin_piston" to "Twin piston",
        "single_turboprop" to "Single turboprop",
        "twin_turboprop" to "Twin turboprop",
        "light_jet" to "Light jet",
    )

    /** A preset's own values, as the form shows them (the unused descent field empty). */
    fun presetValues(preset: PerformancePreset): PerformanceValues = PerformanceValues(
        climbIasKt = preset.climbIasKt,
        climbRateFpm = preset.climbRateFpm,
        descentRateFpm = preset.descentRateFpm.takeIf { preset.descentMode == MODE_RATE },
        descentAngleDeg = preset.descentAngleDeg.takeIf { preset.descentMode == MODE_ANGLE },
        turbo = preset.turbo,
    )

    /** True when a filled field or the turbo switch differs. An empty field means "the preset's value". */
    fun isCustom(values: PerformanceValues, base: PerformancePreset): Boolean {
        val own = presetValues(base)
        if (values.turbo != own.turbo) return true
        return differs(values.climbIasKt, own.climbIasKt) ||
            differs(values.climbRateFpm, own.climbRateFpm) ||
            differs(values.descentRateFpm, own.descentRateFpm) ||
            differs(values.descentAngleDeg, own.descentAngleDeg)
    }

    private fun differs(value: Double?, own: Double?): Boolean = value != null && value != own

    fun overridesFor(values: PerformanceValues, base: PerformancePreset): PerformanceOverrides {
        val own = presetValues(base)
        fun diff(value: Double?, ownValue: Double?): Double =
            if (value != null && value > 0.0 && value != ownValue) value else 0.0
        return PerformanceOverrides(
            performancePreset = base.id,
            isTurbo = values.turbo,
            climbIasKt = Math.round(diff(values.climbIasKt, own.climbIasKt)).toInt(),
            climbRateFpm = Math.round(diff(values.climbRateFpm, own.climbRateFpm)).toInt(),
            descentRateFpm = Math.round(diff(values.descentRateFpm, own.descentRateFpm)).toInt(),
            descentAngleDeg = diff(values.descentAngleDeg, own.descentAngleDeg),
        )
    }

    /** What the server resolves a save to, for mock mode where there is no server. */
    fun resolved(overrides: PerformanceOverrides, base: PerformancePreset): AircraftPerformanceResponse {
        val angle = overrides.descentAngleDeg.takeIf { it > 0.0 }
        val rate = overrides.descentRateFpm.takeIf { it > 0 }?.toDouble()
        val mode = when {
            angle != null -> MODE_ANGLE
            rate != null -> MODE_RATE
            else -> base.descentMode
        }
        val hasOverride = angle != null || rate != null || overrides.climbIasKt > 0 ||
            overrides.climbRateFpm > 0 || overrides.isTurbo != base.turbo
        return AircraftPerformanceResponse(
            preset = base.id,
            source = if (hasOverride) "aircraft" else "preset",
            climbIasKt = overrides.climbIasKt.takeIf { it > 0 }?.toDouble() ?: base.climbIasKt,
            climbRateFpm = overrides.climbRateFpm.takeIf { it > 0 }?.toDouble() ?: base.climbRateFpm,
            descentMode = mode,
            descentRateFpm = (rate ?: base.descentRateFpm).takeIf { mode == MODE_RATE },
            descentAngleDeg = (angle ?: base.descentAngleDeg).takeIf { mode == MODE_ANGLE },
            turbo = overrides.isTurbo,
        )
    }

    fun presetName(id: String): String =
        PRESET_NAMES[id] ?: id.replace('_', ' ').replaceFirstChar { it.uppercase() }

    /** The picker's text: the preset, or "Custom (based on …)" once a value differs. */
    fun selectionLabel(state: PerformanceFormState): String {
        val base = state.base ?: return ""
        return if (state.isCustom) "Custom (based on ${presetName(base.id)})" else presetName(base.id)
    }

    fun hint(state: PerformanceFormState): String {
        val base = state.base ?: return ""
        val name = presetName(base.id)
        return if (state.isCustom) {
            "Your own values, starting from $name. Pick a preset again to restore its values."
        } else {
            "$name values, used for the briefing's climb and descent. Change any value to make it your own."
        }
    }

    /** A field's text for a value, to one decimal; empty for null. */
    fun formatValue(value: Double?): String {
        if (value == null) return ""
        val rounded = Math.round(value * 10.0) / 10.0
        return if (rounded % 1.0 == 0.0) rounded.toLong().toString() else rounded.toString()
    }

    /** A field's value: a positive number, else null (empty). A decimal comma is accepted. */
    fun parseValue(text: String): Double? =
        text.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it.isFinite() && it > 0.0 }

    /** Keeps the digits and the first decimal separator of what was typed. */
    fun sanitizeNumber(text: String): String {
        var seenSeparator = false
        return text.filter { ch ->
            when {
                ch.isDigit() -> true
                (ch == '.' || ch == ',') && !seenSeparator -> {
                    seenSeparator = true
                    true
                }
                else -> false
            }
        }
    }
}

/**
 * The form's state, with each user action as a pure transition. Empty [presets] means they have
 * not loaded: [overrides] is then null and a save leaves the aircraft's performance unchanged.
 */
data class PerformanceFormState(
    val presets: List<PerformancePreset> = emptyList(),
    val suggested: String = "",
    val baseId: String = "",
    /** The selection tracks the ICAO type's suggestion (not picked by hand). */
    val followsType: Boolean = true,
    val climbIasText: String = "",
    val climbRateText: String = "",
    val descentRateText: String = "",
    val descentAngleText: String = "",
    val turbo: Boolean = false,
) {
    val base: PerformancePreset?
        get() = presets.firstOrNull { it.id == baseId }

    val values: PerformanceValues
        get() = PerformanceValues(
            climbIasKt = AircraftPerformanceForm.parseValue(climbIasText),
            climbRateFpm = AircraftPerformanceForm.parseValue(climbRateText),
            descentRateFpm = AircraftPerformanceForm.parseValue(descentRateText),
            descentAngleDeg = AircraftPerformanceForm.parseValue(descentAngleText),
            turbo = turbo,
        )

    val isCustom: Boolean
        get() = base?.let { AircraftPerformanceForm.isCustom(values, it) } ?: false

    val overrides: PerformanceOverrides?
        get() = base?.let { AircraftPerformanceForm.overridesFor(values, it) }

    /** Open on [aircraft] (null for a new one) with the presets just loaded. */
    fun opened(aircraft: AircraftResponse?, response: PerformancePresetsResponse): PerformanceFormState {
        val performance = aircraft?.performance
        val startId = performance?.preset?.takeIf { id -> response.presets.any { it.id == id } }
            ?: response.suggested
        val loaded = copy(
            presets = response.presets,
            suggested = response.suggested,
            baseId = startId,
            followsType = aircraft?.performancePreset.isNullOrEmpty() ||
                aircraft?.performancePreset == response.suggested,
        )
        val startBase = loaded.base
        return when {
            performance != null && startBase != null && aircraft?.isPlaceholder == false -> loaded.writing(
                PerformanceValues(
                    climbIasKt = performance.climbIasKt,
                    climbRateFpm = performance.climbRateFpm,
                    descentRateFpm = performance.descentRateFpm.takeIf { performance.descentMode == "rate" },
                    descentAngleDeg = performance.descentAngleDeg.takeIf { performance.descentMode == "angle" },
                    turbo = performance.turbo,
                ),
            )
            startBase != null -> loaded.writing(AircraftPerformanceForm.presetValues(startBase))
            else -> loaded
        }
    }

    /** The type or cruise speed changed: a following preset moves; a picked or custom one stays. */
    fun refreshed(response: PerformancePresetsResponse): PerformanceFormState {
        val moves = followsType && !isCustom && response.suggested != baseId
        val loaded = copy(presets = response.presets, suggested = response.suggested)
        if (!moves) return loaded
        val moved = loaded.copy(baseId = response.suggested)
        return moved.base?.let { moved.writing(AircraftPerformanceForm.presetValues(it)) } ?: moved
    }

    /** Picking a preset writes all its values back, turbo included. */
    fun picked(id: String): PerformanceFormState {
        val moved = copy(baseId = id, followsType = id == suggested)
        return moved.base?.let { moved.writing(AircraftPerformanceForm.presetValues(it)) } ?: moved
    }

    /** A descent is a rate or an angle: typing one clears the other. */
    fun editedDescentRate(text: String): PerformanceFormState =
        copy(descentRateText = text, descentAngleText = if (text.isNotEmpty()) "" else descentAngleText)

    fun editedDescentAngle(text: String): PerformanceFormState =
        copy(descentAngleText = text, descentRateText = if (text.isNotEmpty()) "" else descentRateText)

    private fun writing(v: PerformanceValues): PerformanceFormState = copy(
        climbIasText = AircraftPerformanceForm.formatValue(v.climbIasKt),
        climbRateText = AircraftPerformanceForm.formatValue(v.climbRateFpm),
        descentRateText = AircraftPerformanceForm.formatValue(v.descentRateFpm),
        descentAngleText = AircraftPerformanceForm.formatValue(v.descentAngleDeg),
        turbo = v.turbo,
    )
}
