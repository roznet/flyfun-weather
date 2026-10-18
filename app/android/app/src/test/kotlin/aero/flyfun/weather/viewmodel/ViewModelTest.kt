package aero.flyfun.weather.viewmodel

import aero.flyfun.common.InMemoryTokenStore
import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureTripRepository
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FlexibilityMode
import aero.flyfun.weather.model.FlightOrder
import aero.flyfun.weather.model.FlightSection
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.NavigationTarget
import java.time.LocalDate
import java.time.LocalTime
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelTest {

    @Test
    fun appStateViewModel_handlesAuthDemoAndDeepLinks() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val tokenStore = InMemoryTokenStore()
        val appState = AppStateViewModel(
            context = null,
            baseUrl = "https://weather.flyfun.aero",
            tokenStoreOverride = tokenStore,
            initialMockMode = false,
            scopeOverride = testScope,
        )

        assertFalse(appState.isAuthenticated.value)

        // Start Google login generates a state nonce and authorize URL
        val googleUrl = appState.startGoogleLogin()
        assertTrue(googleUrl.contains("/auth/login/google"))
        assertNotNull(appState.pendingOAuthState.value)

        // Enter Demo Mode switches to authenticated + mock mode
        appState.enterDemoMode()
        assertTrue(appState.isAuthenticated.value)
        assertTrue(appState.isMockMode.value)
        assertTrue(appState.isGuestBrowsing.value)

        // Universal link routing for briefing
        appState.handleIncomingUri("flyfunweather://briefing?flight_id=fixture-1&pack_timestamp=2099-06-30T06:00:00Z&open_observed=1")
        val target = appState.consumePendingNavigation()
        assertTrue(target is NavigationTarget.Briefing)
        val briefingTarget = target as NavigationTarget.Briefing
        assertEquals("fixture-1", briefingTarget.flightId)
        assertEquals("2099-06-30T06:00:00Z", briefingTarget.packTimestamp)
        assertTrue(briefingTarget.openObserved)

        // Logout clears token and resets flags
        appState.logout()
        assertFalse(appState.isAuthenticated.value)
        assertFalse(appState.isMockMode.value)
    }

    @Test
    fun flightListViewModel_loadsGroupsSelectsAndDeletesFlights() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository(mockLive = true)
        val tripRepo = FixtureTripRepository()
        val vm = FlightListViewModel(
            repository = repo,
            tripRepository = tripRepo,
            scopeOverride = testScope,
        )

        vm.loadFlights(nowEpochMs = 1_700_000_000_000L)
        testScope.advanceUntilIdle()

        assertEquals(2, vm.flights.value.size)
        assertEquals(1, vm.trips.value.size)
        assertTrue(vm.groups.value.any { it.section == FlightSection.FUTURE })

        // Toggle sort order
        assertEquals(FlightOrder.SOONEST_FIRST, vm.order.value)
        vm.toggleOrder(nowEpochMs = 1_700_000_000_000L)
        assertEquals(FlightOrder.FURTHEST_FIRST, vm.order.value)

        // Search filter
        vm.setSearchQuery("LFMD", nowEpochMs = 1_700_000_000_000L)
        assertEquals(1, vm.groups.value.sumOf { it.flights.size })
        vm.setSearchQuery("", nowEpochMs = 1_700_000_000_000L)
        assertEquals(2, vm.groups.value.sumOf { it.flights.size })

        // Multi-select bulk delete
        vm.enterSelectionMode("fixture-2")
        assertTrue(vm.isSelectionMode.value)
        assertEquals(setOf("fixture-2"), vm.selectedFlightIds.value)

        vm.deleteSelectedFlights(nowEpochMs = 1_700_000_000_000L)
        testScope.advanceUntilIdle()

        assertFalse(vm.isSelectionMode.value)
        assertEquals(1, vm.flights.value.size)
        assertEquals("fixture-1", vm.flights.value.first().id)
    }

    @Test
    fun addFlightViewModel_parsesWaypointsAndCreatesFlight() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository(mockLive = true)
        val tripRepo = FixtureTripRepository()
        val vm = AddFlightViewModel(
            repository = repo,
            apiClient = null,
            tripRepository = tripRepo,
            airportDb = null,
            scopeOverride = testScope,
        )

        vm.updateRouteText("egtk -> lfpb lfmd")
        assertEquals(listOf("EGTK", "LFPB", "LFMD"), vm.waypoints.value)
        assertTrue(vm.isValid)

        vm.setDepartureDate(LocalDate.of(2099, 7, 10))
        vm.setDepartureTime(LocalTime.of(8, 30))
        vm.setFlexibilityMode(FlexibilityMode.DAY)
        vm.setWindowEndTime(LocalTime.of(14, 0))
        vm.setCruiseAltitudeFt(9500)
        vm.setFlightCeilingFt(16000)
        vm.setDuration(2, 15)

        assertEquals("2099-07-10T08:30:00Z", vm.buildDepartureIso())
        assertEquals("2099-07-10T14:00:00Z", vm.buildDepartureWindowEndIso())

        var createdId: String? = null
        vm.saveFlight { saved ->
            createdId = saved.id
        }
        testScope.advanceUntilIdle()

        assertNotNull(createdId)
        val allFlights = repo.fetchFlights()
        assertTrue(allFlights.any { it.id == createdId && it.waypoints == listOf("EGTK", "LFPB", "LFMD") })
    }

    @Test
    fun briefingViewModel_loadsFixturePackSwitchesModelsAndHandlesFocusIntent() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository(mockLive = true)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.loadInitialBriefing()
        testScope.advanceUntilIdle()

        assertNotNull(vm.selectedPack.value)
        assertNotNull(vm.snapshot.value)
        assertNotNull(vm.advisories.value)
        assertNotNull(vm.digest.value)
        assertNotNull(vm.elevation.value)
        assertNotNull(vm.liveLayer.value)
        assertTrue(BriefingTab.OBSERVED in vm.availableTabs.value)

        // Model selection
        vm.selectModel("gfs")
        assertEquals("gfs", vm.selectedModel.value)

        // Focus intent deep-link into Cross-Section tab
        val intent = FocusIntent(
            tab = BriefingTab.CROSS_SECTION,
            waypointIcao = "LFML",
            distanceNm = 95.0,
        )
        vm.applyFocusIntent(intent)
        assertEquals(BriefingTab.CROSS_SECTION, vm.selectedTab.value)
        assertEquals(intent, vm.focusIntent.value)

        vm.clearFocusIntent()
        assertNull(vm.focusIntent.value)

        // Advisory recalculation with updated cruise altitude
        vm.recalculateAdvisories(cruiseAltitudeFt = 10000)
        testScope.advanceUntilIdle()
        assertEquals(10000, vm.flight.value?.cruiseAltitudeFt)
    }

    @Test
    fun aircraftAndTripsViewModels_performCrudOperations() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val aircraftVm = AircraftListViewModel(
            apiClient = null,
            scopeOverride = testScope,
        )

        aircraftVm.searchAircraftTypes("SR22")
        assertTrue(aircraftVm.typeResults.value.any { it.icao == "SR22" })

        aircraftVm.createAircraft(
            icaoType = "sr22",
            tailNumber = "n123ab",
            nickname = "Cirrus",
            cruiseSpeedKt = 175,
            ceilingFt = 17500,
            isIfr = true,
            isFiki = true,
            isDefault = true,
        )
        assertEquals(1, aircraftVm.aircraft.value.size)
        val createdAc = aircraftVm.aircraft.value.first()
        assertEquals("SR22", createdAc.icaoType)
        assertEquals(17500, createdAc.serviceCeilingFt)

        aircraftVm.deleteAircraft(createdAc.id)
        assertTrue(aircraftVm.aircraft.value.isEmpty())

        // TripsViewModel
        val tripRepo = FixtureTripRepository()
        val tripsVm = TripsViewModel(
            tripRepository = tripRepo,
            scopeOverride = testScope,
        )

        tripsVm.loadTripDetail("trip-fixture-1")
        testScope.advanceUntilIdle()

        assertNotNull(tripsVm.currentTrip.value)
        assertNotNull(tripsVm.aiSummary.value?.summary)

        tripsVm.renameTrip("trip-fixture-1", "Provence Weekend")
        testScope.advanceUntilIdle()
        assertEquals("Provence Weekend", tripsVm.currentTrip.value?.name)
    }
}
