package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.FixtureSetupRepository
import aero.flyfun.weather.data.SetupRepository
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AuthMeResponse
import aero.flyfun.weather.model.ProfileAdvisoriesConfig
import aero.flyfun.weather.model.ProfileResponse
import aero.flyfun.weather.model.ProfileSettings
import aero.flyfun.weather.model.SetupMission
import aero.flyfun.weather.model.SetupProfileUpdate
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.declaresFiki
import java.io.IOException
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * First-run setup (#900) against the fixture account (`app/fixtures/api/setup-*.json`: the three
 * seeded system profiles, VFR Only the default, and the server's placeholder aircraft). The fixture
 * applies writes like the server, so each case reads back what the wizard saved.
 */
class SetupWizardViewModelTest {

    /** The wizard on a fresh fixture account, loaded; its type searches run on the test's scheduler. */
    private suspend fun TestScope.loaded(): Pair<FixtureSetupRepository, SetupWizardViewModel> {
        val repo = FixtureSetupRepository(setupCompleted = false)
        val vm = SetupWizardViewModel(repo, scopeOverride = this)
        vm.load()
        return repo to vm
    }

    @Test
    fun `finishing over the placeholder saves a FIKI aircraft and the mission`() = runTest {
        val (repo, vm) = loaded()
        assertEquals(101, vm.state.value.selectedProfileId)
        assertEquals("110", vm.state.value.cruiseSpeedKt) // the placeholder's speed carries over
        assertEquals("", vm.state.value.icaoType) // its ZZZZ does not

        assertTrue(vm.next()) // welcome -> profile
        vm.selectProfile(103) // IFR FIKI
        assertTrue(vm.next()) // profile -> aircraft
        val atAircraft = vm.state.value
        assertEquals(SetupStep.AIRCRAFT, atAircraft.step)
        assertEquals("vfr_ifr", atAircraft.flightRules)
        assertEquals("10000", atAircraft.cruiseAltitudeFt)
        assertTrue("IFR FIKI pre-ticks FIKI", atAircraft.isFiki)

        vm.setIcaoType("sr22")
        assertTrue(vm.next())
        assertEquals(SetupStep.DONE, vm.state.value.step)

        val aircraft = repo.aircraft()
        assertEquals(1, aircraft.size) // the placeholder was replaced
        val saved = aircraft.single()
        assertEquals("SR22", saved.icaoType)
        assertTrue(saved.isFiki)
        assertTrue(saved.isIfr)
        assertTrue(saved.isDefault)
        assertFalse(saved.isPlaceholder)

        val profiles = repo.profiles()
        assertEquals(103, profiles.single { it.isDefault }.id)
        assertEquals("vfr_ifr", profiles.single { it.id == 103 }.settings.flightRules)

        assertFalse(repo.isSetupCompleted)
        vm.complete()
        assertTrue(repo.isSetupCompleted)
    }

    @Test
    fun `unticking FIKI saves a non-FIKI aircraft`() = runTest {
        val (repo, vm) = loaded()
        vm.next()
        vm.selectProfile(103)
        vm.next()
        vm.setFiki(false)
        vm.setIcaoType("C172")
        vm.next()
        assertFalse(repo.aircraft().single().isFiki)
    }

    @Test
    fun `VFR only saves a non-IFR, non-FIKI aircraft`() = runTest {
        val (repo, vm) = loaded()
        vm.next()
        vm.next() // keeps VFR Only
        assertFalse(vm.state.value.isFiki)
        vm.setIcaoType("C172")
        vm.next()
        val saved = repo.aircraft().single()
        assertFalse(saved.isIfr)
        assertFalse(saved.isFiki)
    }

    @Test
    fun `no type stays on the aircraft step`() = runTest {
        val (repo, vm) = loaded()
        vm.next()
        vm.next()
        assertFalse(vm.next())
        assertEquals(SetupStep.AIRCRAFT, vm.state.value.step)
        assertTrue(vm.state.value.icaoTypeMissing)
        assertTrue(repo.aircraft().single().isPlaceholder)
    }

    @Test
    fun `a failed load blocks the wizard until Retry, and never completes over it`() = runTest {
        val repo = FlakySetupRepository(FixtureSetupRepository(setupCompleted = false))
        repo.failAircraft = true
        val vm = SetupWizardViewModel(repo, scopeOverride = this)
        vm.load()
        assertTrue(vm.state.value.loadFailed)
        assertTrue(vm.next()) // the welcome copy needs nothing loaded
        assertFalse(vm.next()) // but the profile step does
        assertEquals(SetupStep.PROFILE, vm.state.value.step)

        repo.failAircraft = false
        vm.load() // Retry
        assertFalse(vm.state.value.loadFailed)
        assertEquals("110", vm.state.value.cruiseSpeedKt)
        assertTrue(vm.next())
        assertEquals(SetupStep.AIRCRAFT, vm.state.value.step)
    }

    @Test
    fun `a failed mission save stays on the aircraft step, and a retry updates the aircraft`() = runTest {
        val repo = FlakySetupRepository(FixtureSetupRepository(setupCompleted = false))
        val vm = SetupWizardViewModel(repo, scopeOverride = this)
        vm.load()
        vm.next()
        vm.next()
        vm.setIcaoType("C172")
        repo.failMission = true
        assertFalse(vm.next())
        assertEquals(SetupStep.AIRCRAFT, vm.state.value.step)
        assertTrue(vm.state.value.errorMessage!!.startsWith("Couldn't save your usual mission"))
        assertFalse(vm.state.value.isSaving)

        repo.failMission = false
        assertTrue(vm.next())
        assertEquals(SetupStep.DONE, vm.state.value.step)
        assertEquals(1, repo.aircraft().size) // the retry PUT the saved aircraft, no second POST
    }

    @Test
    fun `back keeps the mission typed`() = runTest {
        val (_, vm) = loaded()
        vm.next()
        vm.next()
        vm.setCruiseAltitude("6500")
        vm.back()
        assertEquals(SetupStep.PROFILE, vm.state.value.step)
        vm.next()
        assertEquals("6500", vm.state.value.cruiseAltitudeFt)
    }

    @Test
    fun `the FIKI Icing card also says FIKI, like the server rule`() {
        val custom = ProfileResponse(
            id = 9,
            name = "Mine",
            settings = ProfileSettings(advisories = ProfileAdvisoriesConfig(enabled = mapOf("fiki_icing" to true))),
        )
        assertTrue(custom.declaresFiki)
        assertFalse(custom.copy(settings = ProfileSettings()).declaresFiki)
        assertTrue(ProfileResponse(systemTemplateKey = "ifr_fiki").declaresFiki)
    }

    @Test
    fun `ceiling warning names the aircraft and never clamps`() {
        val state = SetupWizardState(nickname = "Archie", serviceCeilingFt = "14000", flightCeilingFt = "18000")
        assertEquals(
            "Ceiling 18 000 ft is above Archie's service ceiling (14 000 ft).",
            state.ceilingWarning,
        )
        assertNull(state.copy(flightCeilingFt = "12000").ceilingWarning)
    }

    @Test
    fun `wire shapes match the server`() {
        assertNull(WbJson.decodeFromString(AuthMeResponse.serializer(), """{"id": "u1"}""").setupCompleted)
        assertEquals(
            """{"is_default":true}""",
            WbJson.encodeToString(SetupProfileUpdate.serializer(), SetupProfileUpdate(isDefault = true)),
        )
        assertEquals(
            """{"settings":{"flight_rules":"vfr_only","cruise_altitude_ft":5500,"flight_ceiling_ft":10000}}""",
            WbJson.encodeToString(
                SetupProfileUpdate.serializer(),
                SetupProfileUpdate(settings = SetupMission("vfr_only", 5500, 10000)),
            ),
        )
        val placeholder = WbJson.decodeFromString(AircraftResponse.serializer(), """{"id": 1, "is_placeholder": true}""")
        assertTrue(placeholder.isPlaceholder)
    }

    @Test
    fun `a UI-test launch shows the wizard only when asked`() = runTest {
        val appState = AppStateViewModel(context = null, scopeOverride = TestScope(testScheduler))
        appState.startUiTestSession(UiTestLaunch(mock = true))
        testScheduler.advanceUntilIdle()
        assertFalse(appState.showSetupWizard.value)

        appState.startUiTestSession(UiTestLaunch(mock = true, setupPending = true))
        testScheduler.advanceUntilIdle()
        assertTrue(appState.showSetupWizard.value)

        appState.dismissSetupWizard()
        assertFalse(appState.showSetupWizard.value)
    }

    @Test
    fun `demo mode has no account to set up`() = runTest {
        val appState = AppStateViewModel(context = null, scopeOverride = TestScope(testScheduler))
        appState.enterDemoMode()
        assertNull(appState.setupRepository)
    }
}

/** The fixture account with switchable failures, for the wizard's error paths. */
private class FlakySetupRepository(private val inner: FixtureSetupRepository) : SetupRepository by inner {
    var failAircraft = false
    var failMission = false

    override suspend fun aircraft() = if (failAircraft) throw IOException("offline") else inner.aircraft()

    override suspend fun updateProfile(id: Int, req: SetupProfileUpdate) {
        if (failMission && req.settings != null) throw IOException("offline")
        inner.updateProfile(id, req)
    }
}
