package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.FixtureBriefingData
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The UI-test launch extras (#895): parsed like the iOS launch environment, applied without a token. */
class UiTestLaunchTest {

    @Test
    fun `no FLYFUN_UITEST is not a UI-test launch`() {
        assertNull(UiTestLaunch.from { null })
        assertNull(UiTestLaunch.from { if (it == UiTestLaunch.EXTRA_MOCK) "1" else null })
    }

    @Test
    fun `extras map to the launch the test asked for`() {
        val extras = mapOf(
            UiTestLaunch.EXTRA_UITEST to "1",
            UiTestLaunch.EXTRA_MOCK to "1",
            UiTestLaunch.EXTRA_MOCK_OFFLINE to "1",
        )
        assertEquals(UiTestLaunch(mock = true, offline = true, mockLive = false), UiTestLaunch.from(extras::get))
        assertEquals(UiTestLaunch(), UiTestLaunch.from(mapOf(UiTestLaunch.EXTRA_UITEST to "1")::get))
    }

    @Test
    fun `a mock launch renders past the gate on fresh fixtures`() = runTest {
        val appState = AppStateViewModel(context = null, scopeOverride = TestScope(testScheduler))
        assertFalse(appState.isAuthenticated.value)

        appState.startUiTestSession(UiTestLaunch(mock = true))

        assertTrue(appState.isAuthenticated.value)
        assertTrue(appState.isMockMode.value)
        assertFalse(appState.isGuestBrowsing.value)
        assertNull(appState.tokenStore.getToken())
        val seeded = appState.repository.listFlights().map { it.id }
        assertTrue(FixtureBriefingData.FLIGHT_ID in seeded)

        // A journey's writes don't leak into the next launch.
        appState.repository.deleteFlight(FixtureBriefingData.FLIGHT_ID)
        appState.startUiTestSession(UiTestLaunch(mock = true))
        assertEquals(seeded, appState.repository.listFlights().map { it.id })
    }

    @Test
    fun `a live-dev launch leaves the auth gate alone`() = runTest {
        val appState = AppStateViewModel(context = null, scopeOverride = TestScope(testScheduler))
        appState.startUiTestSession(UiTestLaunch(mock = false))
        assertFalse(appState.isAuthenticated.value)
        assertFalse(appState.isMockMode.value)
    }
}
