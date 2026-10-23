package aero.flyfun.weather.viewmodel

import aero.flyfun.common.InMemoryTokenStore
import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureTripRepository
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.FlightOrder
import aero.flyfun.weather.model.FlightSection
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.ActiveRefreshResponse
import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.model.RefreshEvent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import java.io.IOException
import java.time.LocalDate
import java.time.LocalTime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.launch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
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
        vm.setFlexibility(FlightFlexibility.SAME_DAY)
        vm.setCruiseAltitudeFt(9500)
        vm.setFlightCeilingFt(16000)
        vm.setDuration(2, 15)

        assertEquals("2099-07-10T08:30:00Z", vm.buildDepartureIso())

        var createdId: String? = null
        vm.saveFlight { saved ->
            createdId = saved.id
        }
        testScope.advanceUntilIdle()

        assertNotNull(createdId)
        val allFlights = repo.fetchFlights()
        assertTrue(allFlights.any { it.id == createdId && it.waypoints == listOf("EGTK", "LFPB", "LFMD") })
        // The server field is `flexibility`; the old `flexibility_mode` was silently dropped (#885).
        assertEquals(FlightFlexibility.SAME_DAY, repo.lastCreatedFlight?.flexibility)
        assertEquals("egtk -> lfpb lfmd", repo.lastCreatedFlight?.rawRoute)
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

    @Test
    fun appStateViewModel_isolatesPerUserCacheScopeOnAccountSwitch() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val tokenStore = InMemoryTokenStore()
        val tempRoot = java.nio.file.Files.createTempDirectory("flyfun_cache_scope_test").toFile()
        try {
            val appState = AppStateViewModel(
                context = null,
                baseUrl = "https://weather.flyfun.aero",
                tokenStoreOverride = tokenStore,
                initialMockMode = false,
                scopeOverride = testScope,
                cacheRootOverride = tempRoot,
            )

            // Craft two distinct JWTs with different `sub` claims
            fun fakeJwt(sub: String): String {
                val header = java.util.Base64.getUrlEncoder().withoutPadding()
                    .encodeToString("""{"alg":"HS256","typ":"JWT"}""".toByteArray())
                val payload = java.util.Base64.getUrlEncoder().withoutPadding()
                    .encodeToString("""{"sub":"$sub"}""".toByteArray())
                return "$header.$payload.sig"
            }

            val jwtAlice = fakeJwt("user-alice")
            val jwtBob = fakeJwt("user-bob")

            appState.completeLogin(jwtAlice)
            val aliceScope = aero.flyfun.common.JwtClaims.cacheScope(jwtAlice)
            assertEquals(aliceScope, appState.cacheScope.value)
            val aliceCache = appState.briefingCache
            assertNotNull(aliceCache)
            aliceCache!!.writeData(
                bytes = """{"test":"alice"}""".toByteArray(),
                flightId = "flight-1",
                timestamp = "2099-06-30T06:00:00Z",
                endpoint = "advisories",
            )
            assertNotNull(aliceCache.readData("flight-1", "2099-06-30T06:00:00Z", "advisories"))

            // Sign out and sign in as Bob in the same process session
            appState.logout()
            assertEquals("anon", appState.cacheScope.value)

            appState.completeLogin(jwtBob)
            val bobScope = aero.flyfun.common.JwtClaims.cacheScope(jwtBob)
            assertEquals(bobScope, appState.cacheScope.value)
            assertFalse(aliceScope == bobScope)
            val bobCache = appState.briefingCache
            assertNotNull(bobCache)
            // Bob must not see Alice's cached pack data
            assertNull(bobCache!!.readData("flight-1", "2099-06-30T06:00:00Z", "advisories"))
        } finally {
            tempRoot.deleteRecursively()
        }
    }

    @Test
    fun mapTileCache_selectsStadiaAndOsmStylesMatchingProjectConvention() {
        assertEquals("stadia-dark", aero.flyfun.weather.ui.map.MapTileCache.baseTileStyle(dark = true, muted = false))
        assertEquals(
            "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/6/32/21.png",
            aero.flyfun.weather.ui.map.MapTileCache.baseTileUrl(z = 6, x = 32, y = 21, dark = true, muted = false),
        )
        assertEquals("stadia-muted", aero.flyfun.weather.ui.map.MapTileCache.baseTileStyle(dark = false, muted = true))
        assertEquals(
            "https://tiles.stadiamaps.com/tiles/alidade_smooth/6/32/21.png",
            aero.flyfun.weather.ui.map.MapTileCache.baseTileUrl(z = 6, x = 32, y = 21, dark = false, muted = true),
        )
        assertEquals("osm-light", aero.flyfun.weather.ui.map.MapTileCache.baseTileStyle(dark = false, muted = false))
        assertEquals(
            "https://tile.openstreetmap.org/6/32/21.png",
            aero.flyfun.weather.ui.map.MapTileCache.baseTileUrl(z = 6, x = 32, y = 21, dark = false, muted = false),
        )
    }

    @Test
    fun androidTokenStore_readsLegacyPlaintextAndClearsCleanly() {
        val tempDir = java.nio.file.Files.createTempDirectory("flyfun_token_store_test").toFile()
        try {
            val legacyFile = java.io.File(tempDir, aero.flyfun.weather.data.AndroidTokenStore.LEGACY_FILE_NAME)
            legacyFile.writeText("  legacy-jwt-token  ")

            val store = aero.flyfun.weather.data.AndroidTokenStore(tempDir)
            assertEquals("legacy-jwt-token", store.readToken())

            store.writeToken("updated-jwt-token")
            assertEquals("updated-jwt-token", store.readToken())

            store.clearToken()
            assertNull(store.readToken())
            assertFalse(legacyFile.exists())
            assertFalse(java.io.File(tempDir, aero.flyfun.weather.data.AndroidTokenStore.ENCRYPTED_FILE_NAME).exists())
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun briefingViewModel_applyLaunchOptionsUpdatesExistingInstance() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository(mockLive = true)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions(preferredTimestamp = null, openObserved = false)
        testScope.advanceUntilIdle()
        assertEquals(BriefingTab.ADVISORY, vm.selectedTab.value)

        // Re-invoking applyLaunchOptions with openObserved = true on an already-loaded ViewModel switches tabs
        vm.applyLaunchOptions(preferredTimestamp = FixtureBriefingData.TIMESTAMP, openObserved = true)
        testScope.advanceUntilIdle()
        assertEquals(BriefingTab.OBSERVED, vm.selectedTab.value)
    }

    @Test
    fun viewModelScopes_clearDroppedOwnersAndKeepRetainedOnes() {
        val scopes = ViewModelScopes()
        val session = ViewModelScopes.sessionKey("scope-a:false:false")
        val briefing = ViewModelScopes.briefingKey("scope-a:false:false", "flight-1")
        val sessionVm = ViewModelProvider.create(scopes.owner(session), clearTrackingFactory)[ClearTrackingViewModel::class]
        val briefingVm = ViewModelProvider.create(scopes.owner(briefing), clearTrackingFactory)[ClearTrackingViewModel::class]

        // Rotation recomposes with the same keys: same owners, same ViewModels
        scopes.retainOnly(setOf(session, briefing))
        assertFalse(briefingVm.cleared)
        assertSame(
            briefingVm,
            ViewModelProvider.create(scopes.owner(briefing), clearTrackingFactory)[ClearTrackingViewModel::class],
        )

        // Closing the briefing clears its ViewModel; reopening gets a fresh one
        scopes.retainOnly(setOf(session))
        assertTrue(briefingVm.cleared)
        assertFalse(sessionVm.cleared)
        assertEquals(setOf(session), scopes.keys)
        val reopened = ViewModelProvider.create(scopes.owner(briefing), clearTrackingFactory)[ClearTrackingViewModel::class]
        assertNotSame(briefingVm, reopened)

        // A session change clears everything from the old session
        scopes.retainOnly(setOf(ViewModelScopes.sessionKey("scope-b:false:false")))
        assertTrue(sessionVm.cleared)
        assertTrue(reopened.cleared)
    }

    @Test
    fun viewModelScopes_clearingCancelsBriefingWork() {
        // Held like MainActivity holds it: in the activity's ViewModelStore
        val activityStore = ViewModelStore()
        val scopes = ViewModelProvider.create(
            activityStore,
            viewModelFactory { initializer { ViewModelScopes() } },
        )[ViewModelScopes::class]
        val key = ViewModelScopes.briefingKey("scope-a:false:false", "flight-1")
        val vm = ViewModelProvider.create(scopes.owner(key), clearTrackingFactory)[ClearTrackingViewModel::class]
        val job = vm.startWork()
        assertTrue(job.isActive)

        // The activity going away for good clears every owner it holds
        activityStore.clear()
        assertTrue(vm.cleared)
        assertTrue(job.isCancelled)
        assertTrue(scopes.keys.isEmpty())
    }

    private class ClearTrackingViewModel : ViewModel() {
        private val scope = CoroutineScope(SupervisorJob()).also { s -> addCloseable { s.cancel() } }
        var cleared = false
            private set

        fun startWork(): Job = scope.launch { awaitCancellation() }

        override fun onCleared() {
            cleared = true
        }
    }

    private val clearTrackingFactory = viewModelFactory { initializer { ClearTrackingViewModel() } }

    @Test
    fun briefingViewModel_newFlightStartsFirstBriefing() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = NewFlightRepository(FixtureBriefingRepository(), activePolls = 0)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()

        assertEquals(1, repo.refreshPackCalls)
        assertNotNull(vm.selectedPack.value)
        assertFalse(vm.isRefreshing.value)
    }

    @Test
    fun briefingViewModel_newFlightAdoptsRefreshAlreadyRunning() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // Started on the web: running for the open's check plus two polls, then done.
        val repo = NewFlightRepository(FixtureBriefingRepository(), activePolls = 3)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.runCurrent()
        assertTrue(vm.isRefreshing.value)
        assertEquals("Fetching · 40%", vm.refreshProgressText.value)

        testScope.advanceUntilIdle()
        // Followed the server's run instead of starting a second one (which would 409).
        assertEquals(0, repo.refreshPackCalls)
        assertNotNull(vm.selectedPack.value)
        assertFalse(vm.isRefreshing.value)
        assertNull(vm.refreshProgressText.value)
    }

    @Test
    fun briefingViewModel_shownPackFollowsRefreshStartedElsewhere() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // Existing briefing, and a web refresh running for the check plus one poll.
        val repo = NewFlightRepository(FixtureBriefingRepository(), activePolls = 2, hasPack = true)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.runCurrent()
        val shown = vm.selectedPack.value
        assertNotNull(shown)
        assertTrue(vm.isRefreshing.value)
        val listPacksAfterLoad = repo.listPacksCalls

        testScope.advanceUntilIdle()
        assertEquals(0, repo.refreshPackCalls)
        assertEquals(3, repo.refreshStatusCalls)
        assertFalse(vm.isRefreshing.value)
        assertNull(vm.refreshProgressText.value)
        assertNull(vm.errorMessage.value)
        // The run's latest pack is the one already shown: not reloaded under the reader.
        assertEquals(listPacksAfterLoad, repo.listPacksCalls)
        assertEquals(shown?.fetchTimestamp, vm.selectedPack.value?.fetchTimestamp)
    }

    @Test
    fun briefingViewModel_newFlightReportsLostRefresh() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // Running for the check plus one poll, then the status call fails.
        val repo = NewFlightRepository(FixtureBriefingRepository(), activePolls = 2, failWhenDone = true)
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()

        // Not left on a blank briefing with no explanation.
        assertNull(vm.selectedPack.value)
        assertNotNull(vm.errorMessage.value)
        assertFalse(vm.isRefreshing.value)
        assertEquals(0, repo.refreshPackCalls)
    }

    @Test
    fun briefingViewModel_sseDisconnectHandsOffToActiveRefreshPolling() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // Initial load sees no active refresh (0), so it starts SSE refreshPack;
        // SSE stream drops after progress, while the server is still active for 2 status checks.
        val repo = InterruptedSseRepository(
            inner = FixtureBriefingRepository(),
            activePollsAfterStreamDrop = 2,
        )
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()

        assertEquals(1, repo.refreshPackCalls)
        assertNotNull(vm.selectedPack.value)
        assertNull(vm.errorMessage.value)
        assertFalse(vm.isRefreshing.value)
    }

    @Test
    fun briefingViewModel_sseTeardownFailureRecoversCompletedServerPack() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // SSE stream throws at close right as the server finishes saving the pack (0 active polls left).
        val repo = InterruptedSseRepository(
            inner = FixtureBriefingRepository(),
            activePollsAfterStreamDrop = 0,
        )
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()

        assertEquals(1, repo.refreshPackCalls)
        assertNotNull(vm.selectedPack.value)
        assertNull(vm.errorMessage.value)
        assertFalse(vm.isRefreshing.value)
    }

    @Test
    fun briefingViewModel_sseDropWithTransientStatusFailureRetriesViaPolling() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // SSE stream drops and the first two status checks also fail (e.g. screen-off socket abort),
        // then status succeeds once as active and finishes.
        val repo = InterruptedSseRepository(
            inner = FixtureBriefingRepository(),
            activePollsAfterStreamDrop = 1,
            statusFailuresAfterStreamDrop = 2,
        )
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()

        assertEquals(1, repo.refreshPackCalls)
        assertNotNull(vm.selectedPack.value)
        assertNull(vm.errorMessage.value)
        assertFalse(vm.isRefreshing.value)
    }

    @Test
    fun briefingViewModel_onForegroundResumeRecoversAfterBackgroundAbort() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        // Simulate a long screen-off where all status polls fail while asleep, then
        // network comes back when the user unlocks the phone (`onForegroundResume`).
        val repo = InterruptedSseRepository(
            inner = FixtureBriefingRepository(),
            activePollsAfterStreamDrop = 0,
            statusFailuresAfterStreamDrop = 10,
            failLatestUntilUnlocked = true,
        )
        val vm = BriefingViewModel(
            flightId = FixtureBriefingData.FLIGHT_ID,
            repository = repo,
            scopeOverride = testScope,
        )

        // Calling onForegroundResume before initial load is a no-op
        vm.onForegroundResume()
        testScope.advanceUntilIdle()
        assertEquals(0, repo.refreshPackCalls)

        vm.applyLaunchOptions()
        testScope.advanceUntilIdle()
        // While phone remained offline across all background retries, no pack could be loaded
        assertNull(vm.selectedPack.value)
        assertNotNull(vm.errorMessage.value)

        // Phone wakes up and network is restored
        repo.unlockNetworkWithFinishedPack()
        vm.onForegroundResume()
        testScope.advanceUntilIdle()

        assertNotNull(vm.selectedPack.value)
        assertNull(vm.errorMessage.value)
        assertFalse(vm.isRefreshing.value)
    }

    private class InterruptedSseRepository(
        private val inner: BriefingRepository,
        private var activePollsAfterStreamDrop: Int,
        private var statusFailuresAfterStreamDrop: Int = 0,
        private var failLatestUntilUnlocked: Boolean = false,
    ) : BriefingRepository by inner {
        var refreshPackCalls = 0
        private var streamStarted = false
        private var packReady = false

        fun unlockNetworkWithFinishedPack() {
            statusFailuresAfterStreamDrop = 0
            failLatestUntilUnlocked = false
            packReady = true
        }

        override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse {
            if (!streamStarted) {
                return ActiveRefreshResponse(active = false)
            }
            if (statusFailuresAfterStreamDrop > 0) {
                statusFailuresAfterStreamDrop--
                throw IOException("Software caused connection abort")
            }
            if (activePollsAfterStreamDrop > 0) {
                activePollsAfterStreamDrop--
                return ActiveRefreshResponse(active = true, label = "Generating digest", progress = 0.85)
            }
            packReady = true
            return ActiveRefreshResponse(active = false)
        }

        override suspend fun latestPack(flightId: String): PackMetaResponse {
            if (failLatestUntilUnlocked) throw IOException("Software caused connection abort")
            return if (packReady) inner.latestPack(flightId) else throw ApiError.NotFound
        }

        override suspend fun listPacks(flightId: String): List<PackMetaResponse> =
            if (packReady && !failLatestUntilUnlocked) inner.listPacks(flightId) else emptyList()

        override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> =
            kotlinx.coroutines.flow.flow {
                refreshPackCalls++
                streamStarted = true
                if (activePollsAfterStreamDrop == 0 && statusFailuresAfterStreamDrop == 0 && !failLatestUntilUnlocked) {
                    packReady = true
                }
                emit(RefreshEvent(type = "progress", label = "Fetching weather", progress = 0.5))
                throw ApiError.NetworkError(IOException("SSE stream reset"))
            }
    }

    /** A flight whose server refresh state is scripted: [activePolls] "running" answers, then done. */
    private class NewFlightRepository(
        private val inner: BriefingRepository,
        private var activePolls: Int,
        private var hasPack: Boolean = false,
        private val failWhenDone: Boolean = false,
    ) : BriefingRepository by inner {
        var refreshPackCalls = 0
        var refreshStatusCalls = 0
        var listPacksCalls = 0
        private var sawActive = false

        override suspend fun refreshStatus(flightId: String): ActiveRefreshResponse {
            refreshStatusCalls++
            if (activePolls > 0) {
                activePolls--
                sawActive = true
                return ActiveRefreshResponse(active = true, label = "Fetching", progress = 0.4)
            }
            if (failWhenDone) throw IOException("offline")
            // The server's run finished: its pack is now there.
            if (sawActive) hasPack = true
            return ActiveRefreshResponse(active = false)
        }

        override suspend fun latestPack(flightId: String): PackMetaResponse =
            if (hasPack) inner.latestPack(flightId) else throw ApiError.NotFound

        override suspend fun listPacks(flightId: String): List<PackMetaResponse> {
            listPacksCalls++
            return if (hasPack) inner.listPacks(flightId) else emptyList()
        }

        override fun refreshPack(flightId: String, force: Boolean, notifyEmail: Boolean): Flow<RefreshEvent> {
            refreshPackCalls++
            hasPack = true
            return inner.refreshPack(flightId, force, notifyEmail)
        }
    }
}
