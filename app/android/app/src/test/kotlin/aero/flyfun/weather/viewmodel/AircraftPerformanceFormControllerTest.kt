package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.AircraftPerformanceResponse
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.PerformancePresetsResponse
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** Loading the aircraft form's presets: stale replies, failures and saving (#1005). */
class AircraftPerformanceFormControllerTest {

    private val presets = FixtureBriefingData.aircraftPresets
    private fun suggesting(id: String): PerformancePresetsResponse = presets.copy(suggested = id)

    private val customised = AircraftResponse(
        id = 5,
        icaoType = "SR22",
        tailNumber = "ZZ-CUS",
        cruiseSpeedKt = 170,
        performancePreset = "hp_piston",
        climbRateFpm = 850,
        performance = AircraftPerformanceResponse(
            preset = "hp_piston", source = "aircraft", climbIasKt = 110.0, climbRateFpm = 850.0,
            descentMode = "rate", descentRateFpm = 500.0,
        ),
    )

    @Test
    fun aStaleReplyIsDropped() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val replies = mutableListOf<CompletableDeferred<PerformancePresetsResponse>>()
        val form = AircraftPerformanceFormController(testScope, { _, _ ->
            val reply = CompletableDeferred<PerformancePresetsResponse>().also { replies += it }
            withContext(NonCancellable) { reply.await() }
        })
        form.open(null, "", 130)
        testScope.testScheduler.advanceUntilIdle()
        replies[0].complete(presets)
        testScope.testScheduler.advanceUntilIdle()

        form.typeChanged("C172", 100)
        testScope.testScheduler.advanceUntilIdle()
        form.typeChanged("TBM9", 300)
        testScope.testScheduler.advanceUntilIdle()
        replies[2].complete(suggesting("single_turboprop"))
        testScope.testScheduler.advanceUntilIdle()
        replies[1].complete(suggesting("trainer_piston"))
        testScope.testScheduler.advanceUntilIdle()

        assertEquals("single_turboprop", form.state.value.baseId)
    }

    @Test
    fun theSheetsFirstValuesAreNotAnEdit() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        var calls = 0
        val form = AircraftPerformanceFormController(testScope, { _, _ ->
            calls++
            presets
        })
        form.open(customised, "SR22", 170)
        form.typeChanged("sr22", 170)
        testScope.testScheduler.advanceUntilIdle()
        assertEquals(1, calls)
    }

    @Test
    fun aPlaceholderAsksForTheUnknownAircraftsSuggestion() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val asked = mutableListOf<String>()
        val form = AircraftPerformanceFormController(testScope, { icao, _ ->
            asked += icao
            presets
        })
        form.open(AircraftResponse(icaoType = "ZZZZ", isPlaceholder = true), "ZZZZ", 130)
        testScope.testScheduler.advanceUntilIdle()
        assertEquals(listOf(""), asked)
    }

    @Test
    fun aFailedLoadLeavesNothingToSave() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val form = AircraftPerformanceFormController(testScope, { _, _ -> error("offline") })
        form.open(customised, "SR22", 170)
        testScope.testScheduler.advanceUntilIdle()
        assertNull(form.state.value.base)
        assertNull(form.state.value.overrides)
    }

    @Test
    fun aRetryAfterAFailedOpenKeepsTheAircraftsOwnValues() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        var online = false
        val form = AircraftPerformanceFormController(testScope, { _, _ ->
            check(online) { "offline" }
            suggesting("trainer_piston")
        })
        form.open(customised, "SR22", 170)
        testScope.testScheduler.advanceUntilIdle()

        online = true
        form.typeChanged("SR22", 165)
        testScope.testScheduler.advanceUntilIdle()

        assertEquals("hp_piston", form.state.value.baseId)
        assertEquals("850", form.state.value.climbRateText)
        assertEquals(850, form.state.value.overrides?.climbRateFpm)
    }

    @Test
    fun savingInMockModeKeepsWhatThePilotSet() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val vm = AircraftListViewModel(scopeOverride = testScope, repository = FixtureBriefingRepository())
        vm.openPerformanceForm(null)
        testScope.testScheduler.advanceUntilIdle()

        vm.performanceForm.pick("single_turboprop")
        vm.performanceForm.editClimbRate("1800")
        vm.createAircraft(
            icaoType = "PC12",
            tailNumber = "ZZ-TBP",
            nickname = null,
            cruiseSpeedKt = 260,
            ceilingFt = 30000,
            isIfr = true,
            isFiki = true,
            isDefault = false,
            performance = vm.performanceForm.state.value.overrides,
        )

        val saved = vm.aircraft.value.single()
        assertEquals("single_turboprop", saved.performancePreset)
        assertEquals(1800, saved.climbRateFpm)
        assertNull(saved.climbIasKt)
        val resolved = assertNotNullValue(saved.performance)
        assertEquals("aircraft", resolved.source)
        assertEquals(150.0, resolved.climbIasKt, 1e-9)
        assertEquals(3.0, resolved.descentAngleDeg ?: 0.0, 1e-9)
        assertFalse(saved.isTurbo)
    }

    private fun <T : Any> assertNotNullValue(value: T?): T {
        assertNotNull(value)
        return value!!
    }
}
