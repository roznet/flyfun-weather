package aero.flyfun.weather.rules

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.model.AircraftPerformanceResponse
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.PerformancePreset
import aero.flyfun.weather.model.PerformancePresetsResponse
import aero.flyfun.weather.model.UpdateAircraftRequest
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.AircraftPerformanceForm
import aero.flyfun.weather.model.rules.AircraftPerformanceForm.isCustom
import aero.flyfun.weather.model.rules.AircraftPerformanceForm.overridesFor
import aero.flyfun.weather.model.rules.AircraftPerformanceForm.presetValues
import aero.flyfun.weather.model.rules.PerformanceFormState
import aero.flyfun.weather.model.rules.PerformanceOverrides
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Port of `web/tests/unit/aircraft-performance-form.test.ts`, plus the form's transitions. */
class AircraftPerformanceFormTest {

    private val hp = PerformancePreset(
        id = "hp_piston", climbIasKt = 110.0, climbRateFpm = 1000.0,
        descentMode = "rate", descentRateFpm = 500.0,
    )
    private val turboprop = PerformancePreset(
        id = "single_turboprop", climbIasKt = 150.0, climbRateFpm = 2000.0,
        descentMode = "angle", descentAngleDeg = 3.0,
    )
    private val response = PerformancePresetsResponse(presets = listOf(hp, turboprop), suggested = "hp_piston")
    private val values = presetValues(hp)

    @Test
    fun isCustomIsFalseOnThePresetValuesAndOnEmptyFields() {
        assertFalse(isCustom(values, hp))
        assertFalse(isCustom(values.copy(climbIasKt = null, climbRateFpm = null), hp))
    }

    @Test
    fun isCustomIsTrueWhenAValueOrTheTurboBoxDiffers() {
        assertTrue(isCustom(values.copy(climbRateFpm = 900.0), hp))
        assertTrue(isCustom(values.copy(turbo = true), hp))
        // An angle on a rate preset is a different descent.
        assertTrue(isCustom(values.copy(descentRateFpm = null, descentAngleDeg = 3.0), hp))
    }

    @Test
    fun anAnglePresetWithTheRateFieldEmptyIsThePreset() {
        assertFalse(isCustom(presetValues(turboprop), turboprop))
    }

    @Test
    fun overridesSendThePresetAndZerosWhenNothingDiffers() {
        assertEquals(
            PerformanceOverrides("hp_piston", false, 0, 0, 0, 0.0),
            overridesFor(values, hp),
        )
    }

    @Test
    fun overridesSendOnlyTheDifferingValuesAndTurboAsIs() {
        assertEquals(
            PerformanceOverrides("hp_piston", true, 0, 850, 0, 0.0),
            overridesFor(values.copy(climbRateFpm = 850.0, turbo = true), hp),
        )
    }

    @Test
    fun overridesSwitchAnAnglePresetToARateDescent() {
        val v = presetValues(turboprop).copy(descentAngleDeg = null, descentRateFpm = 1500.0)
        val o = overridesFor(v, turboprop)
        assertEquals(1500, o.descentRateFpm)
        assertEquals(0.0, o.descentAngleDeg, 1e-9)
    }

    @Test
    fun openingShowsTheResolvedPerformanceWithTheUnusedDescentEmpty() {
        val aircraft = AircraftResponse(
            id = 1,
            tailNumber = "ZZ-ABC",
            performancePreset = "single_turboprop",
            performance = AircraftPerformanceResponse(
                preset = "single_turboprop", source = "aircraft", climbIasKt = 140.0, climbRateFpm = 1800.0,
                descentMode = "angle", descentRateFpm = 900.0, descentAngleDeg = 3.5, turbo = true,
            ),
        )
        val state = PerformanceFormState().opened(aircraft, response)
        assertEquals("single_turboprop", state.baseId)
        assertEquals("140", state.climbIasText)
        assertEquals("1800", state.climbRateText)
        assertEquals("", state.descentRateText)
        assertEquals("3.5", state.descentAngleText)
        assertTrue(state.turbo)
        assertTrue(state.isCustom)
        assertFalse("a picked preset that isn't the suggestion stays", state.followsType)
        assertEquals("Custom (based on Single turboprop)", AircraftPerformanceForm.selectionLabel(state))
    }

    @Test
    fun anAircraftOnTheSuggestedPresetFollowsTheType() {
        val picked = AircraftResponse(performancePreset = "hp_piston")
        assertTrue(PerformanceFormState().opened(picked, response).followsType)
        assertTrue(PerformanceFormState().opened(AircraftResponse(), response).followsType)
    }

    @Test
    fun aPlaceholderOpensOnTheSuggestedPresetsValues() {
        val placeholder = AircraftResponse(
            isPlaceholder = true,
            performance = AircraftPerformanceResponse(preset = "hp_piston", climbIasKt = 99.0, climbRateFpm = 111.0),
        )
        val state = PerformanceFormState().opened(placeholder, response)
        assertEquals("110", state.climbIasText)
        assertEquals("1000", state.climbRateText)
        assertEquals("500", state.descentRateText)
        assertFalse(state.isCustom)
    }

    @Test
    fun pickingAPresetWritesAllItsValuesIncludingTurbo() {
        val turbo = hp.copy(id = "turbo_piston", climbIasKt = 120.0, descentRateFpm = 700.0, turbo = true)
        val all = response.copy(presets = response.presets + turbo)
        val state = PerformanceFormState().opened(null, all).copy(climbRateText = "750").picked("turbo_piston")
        assertEquals("120", state.climbIasText)
        assertEquals("1000", state.climbRateText)
        assertEquals("700", state.descentRateText)
        assertTrue(state.turbo)
        assertFalse(state.isCustom)
        assertFalse(state.followsType)
        assertTrue(state.picked("hp_piston").followsType)
    }

    @Test
    fun aFollowingPresetMovesToTheNewSuggestion() {
        val state = PerformanceFormState().opened(null, response)
            .refreshed(response.copy(suggested = "single_turboprop"))
        assertEquals("single_turboprop", state.baseId)
        assertEquals("150", state.climbIasText)
        assertEquals("", state.descentRateText)
        assertEquals("3", state.descentAngleText)
    }

    @Test
    fun aPickedOrCustomPresetStaysOnANewSuggestion() {
        val moved = response.copy(suggested = "single_turboprop")
        val hand = PerformanceFormState().opened(null, response.copy(suggested = "single_turboprop")).picked("hp_piston")
        assertEquals("hp_piston", hand.refreshed(moved.copy(suggested = "other")).baseId)

        val custom = PerformanceFormState().opened(null, response).copy(climbRateText = "850").refreshed(moved)
        assertEquals("hp_piston", custom.baseId)
        assertEquals("850", custom.climbRateText)
    }

    @Test
    fun typingADescentRateClearsTheAngleAndTheReverse() {
        val state = PerformanceFormState(descentRateText = "500")
        val angle = state.editedDescentAngle("3")
        assertEquals("", angle.descentRateText)
        val rate = angle.editedDescentRate("600")
        assertEquals("", rate.descentAngleText)
        // Emptying a field leaves the other one alone.
        assertEquals("600", rate.editedDescentAngle("").descentRateText)
    }

    @Test
    fun withoutPresetsThereIsNothingToSave() {
        assertNull(PerformanceFormState().overrides)
    }

    @Test
    fun aRequestWithoutOverridesSendsNoPerformanceKeys() {
        val body = WbJson.encodeToString(UpdateAircraftRequest.serializer(), UpdateAircraftRequest(nickname = "Club"))
        listOf("performance_preset", "is_turbo", "climb_ias_kt", "climb_rate_fpm", "descent_rate_fpm", "descent_angle_deg")
            .forEach { assertFalse(it, body.contains(it)) }
    }

    @Test
    fun aRequestSendsZeroToClearAnOverride() {
        val body = WbJson.encodeToString(
            UpdateAircraftRequest.serializer(),
            UpdateAircraftRequest(performancePreset = "hp_piston", isTurbo = false, climbIasKt = 0, descentAngleDeg = 0.0),
        )
        assertTrue(body.contains("\"performance_preset\":\"hp_piston\""))
        assertTrue(body.contains("\"is_turbo\":false"))
        assertTrue(body.contains("\"climb_ias_kt\":0"))
        assertTrue(body.contains("\"descent_angle_deg\":0.0"))
    }

    @Test
    fun theSharedPresetsFixtureDecodes() {
        val fixture = FixtureBriefingData.aircraftPresets
        assertEquals("hp_piston", fixture.suggested)
        assertEquals(6, fixture.presets.size)
        assertTrue(fixture.presets.first { it.id == "turbo_piston" }.turbo)
        assertEquals(3.0, requireNotNull(fixture.presets.first { it.id == "twin_turboprop" }.descentAngleDeg), 1e-9)
    }
}
