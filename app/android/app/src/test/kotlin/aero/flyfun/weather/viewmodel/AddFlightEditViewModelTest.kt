package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.BriefingStatusInfo
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.FlightInvalidation
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.TripLegRef
import java.time.LocalDate
import java.time.LocalTime
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android edit flow (#885) against the fixture repository: PATCH only what changed,
 * Move / Keep both for a structural edit, the residual PATCH after Move, trip defaults, and
 * acting on the server's `invalidation`.
 */
class AddFlightEditViewModelTest {

    private fun setUp(testScope: TestScope): Pair<FixtureBriefingRepository, AddFlightViewModel> {
        val repo = FixtureBriefingRepository(mockLive = true)
        // This suite's own two flights instead of the shared UI-test fixtures (app/fixtures/api),
        // so those can follow the journeys without rewriting these cases. `fixture-1` keeps its
        // id because that is the flight the fixture repository serves a pack for.
        repo.flights.clear()
        repo.flights += listOf(BRIEFED_TRIP_LEG, ROUTED_FLIGHT)
        val vm = AddFlightViewModel(
            repository = repo,
            apiClient = null,
            airportDb = null,
            scopeOverride = testScope,
        )
        return repo to vm
    }

    private fun FixtureBriefingRepository.flight(id: String): FlightResponse = flights.first { it.id == id }

    private companion object {
        /** Briefed (the repository has its pack) and leg 1 of a trip. */
        val BRIEFED_TRIP_LEG = FlightResponse(
            id = "fixture-1",
            userId = "ui-test",
            routeName = "LFMD LFML",
            waypoints = listOf("LFMD", "LFML"),
            departureTime = "2099-07-01T09:00:00Z",
            targetDate = "2099-07-01",
            targetTimeUtc = 9,
            cruiseAltitudeFt = 8000,
            flightCeilingFt = 13000,
            flightDurationHours = 1.5,
            createdAt = "2099-06-30T00:00:00Z",
            briefingStatus = BriefingStatusInfo(lastUpdated = "2099-06-30T06:00:00Z", assessment = "amber", daysOut = 1, hasDigest = true),
            trip = TripLegRef(legacyTripId = "trip-fixture-1", title = "Riviera Hop", role = "owner", legacyLegIndex = 1, legacyLegCount = 2),
        )

        /** No pack, not in a trip, with the Field-15 text the pilot typed. */
        val ROUTED_FLIGHT = FlightResponse(
            id = "fixture-2",
            userId = "ui-test",
            routeName = "EGTK LFPB",
            waypoints = listOf("EGTK", "LFPB"),
            rawRoute = "EGTK DCT LFPB",
            departureTime = "2099-07-03T10:30:00Z",
            targetDate = "2099-07-03",
            targetTimeUtc = 10,
            cruiseAltitudeFt = 9000,
            flightCeilingFt = 18000,
            flightDurationHours = 2.0,
            createdAt = "2099-06-30T00:00:00Z",
        )
    }

    @Test
    fun editPrefillsTheTypedRouteAndPatchesOnlyWhatChanged() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-2"))

        assertEquals("EGTK DCT LFPB", vm.rawRouteText.value)
        assertFalse(vm.hasChanges)

        vm.setCruiseAltitudeFt(9500)
        assertTrue(vm.hasChanges)
        assertFalse(vm.hasStructuralChange)

        var saved: FlightResponse? = null
        vm.saveFlight { saved = it }
        testScope.testScheduler.advanceUntilIdle()

        assertEquals("fixture-2", saved?.id)
        val (id, req) = requireNotNull(repo.lastUpdatedFlight)
        assertEquals("fixture-2", id)
        assertEquals(9500, req.cruiseAltitudeFt)
        // An untouched route keeps its stored waypoints and Field-15 text: neither is sent.
        assertNull(req.waypoints)
        assertNull(req.rawRoute)
        assertNull(req.departureTime)
        assertNull(req.flightDurationHours)
    }

    @Test
    fun aTimeChangeQueuesTheRefreshTheServerAskedFor() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        repo.nextInvalidation = FlightInvalidation.REFETCH_NEEDED
        vm.loadExistingFlight(repo.flight("fixture-2"))

        vm.setDepartureTime(LocalTime.of(12, 0))
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        assertEquals("2099-07-03T12:00:00Z", repo.lastUpdatedFlight?.second?.departureTime)
        assertEquals(listOf("fixture-2"), repo.refreshTriggeredFor)
    }

    @Test
    fun aRefreshThatFailsToQueueWarnsAfterTheSave() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        repo.nextInvalidation = FlightInvalidation.REFETCH_NEEDED
        repo.triggerRefreshError = ApiError.ServerError(500, "boom")
        vm.loadExistingFlight(repo.flight("fixture-2"))

        vm.setDepartureTime(LocalTime.of(12, 0))
        var saved: FlightResponse? = null
        vm.saveFlight { saved = it }
        testScope.testScheduler.advanceUntilIdle()

        // The PATCH committed, so the sheet closes; the stale briefing is not silent.
        assertNotNull(saved)
        assertEquals(AddFlightViewModel.BRIEFING_NOT_UPDATED_WARNING, vm.saveWarning.value)
    }

    @Test
    fun aFailedAdvisoryRecalculationWarnsAfterTheSave() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        repo.nextInvalidation = FlightInvalidation.ADVISORIES_ONLY
        repo.recalculateAdvisoriesError = java.io.IOException("offline")
        // fixture-1 has a pack, so this takes the recalculate path rather than a refresh.
        vm.loadExistingFlight(repo.flight("fixture-1"))

        vm.setCruiseAltitudeFt(9500)
        var saved: FlightResponse? = null
        vm.saveFlight { saved = it }
        testScope.testScheduler.advanceUntilIdle()

        assertNotNull(saved)
        assertEquals(AddFlightViewModel.BRIEFING_NOT_UPDATED_WARNING, vm.saveWarning.value)
    }

    @Test
    fun aMidRouteEditSendsTheResolvedWaypointsAndTheTextAsTyped() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-2"))

        vm.updateRouteText("EGTK DCT LFAT DCT LFPB")
        assertFalse(vm.hasStructuralChange)
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        val req = requireNotNull(repo.lastUpdatedFlight).second
        assertEquals(listOf("EGTK", "LFAT", "LFPB"), req.waypoints)
        assertEquals("EGTK DCT LFAT DCT LFPB", req.rawRoute)
    }

    @Test
    fun aDateChangeAsksBeforeMovingAndMoveKeepsTheTrip() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-1"))

        vm.setDepartureDate(LocalDate.of(2099, 7, 2))
        assertTrue(vm.hasStructuralChange)
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        // No PATCH the server would refuse: the choice is shown instead.
        assertTrue(vm.showStructuralChoice.value)
        assertNull(repo.lastUpdatedFlight)

        var moved: FlightResponse? = null
        vm.moveFlight { moved = it }
        testScope.testScheduler.advanceUntilIdle()

        val (sourceId, req) = requireNotNull(repo.lastMovedFlight)
        assertEquals("fixture-1", sourceId)
        assertEquals("2099-07-02T09:00:00Z", req.departureTime)
        assertNull(req.waypoints)
        assertTrue(req.keepInTrip)
        assertNotNull(moved)
        assertNotEquals("fixture-1", moved?.id)
        assertEquals("trip-fixture-1", moved?.tripId)
        // Nothing /move can't carry was edited: no follow-up PATCH.
        assertNull(repo.lastUpdatedFlight)
    }

    @Test
    fun moveSendsTheProfileChangeAsAFollowUpPatch() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-1"))

        vm.updateRouteText("LFMD LFMN")
        vm.selectProfile(42)
        vm.setKeepInTripOnMove(false)
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()
        assertTrue(vm.showStructuralChoice.value)

        var moved: FlightResponse? = null
        vm.moveFlight { moved = it }
        testScope.testScheduler.advanceUntilIdle()

        val moveReq = requireNotNull(repo.lastMovedFlight).second
        assertEquals(listOf("LFMD", "LFMN"), moveReq.waypoints)
        assertFalse(moveReq.keepInTrip)
        val (patchedId, patch) = requireNotNull(repo.lastUpdatedFlight)
        assertEquals(moved?.id, patchedId)
        assertEquals(42, patch.profileId)
    }

    @Test
    fun keepBothLeavesTheOriginalAndJoinsTheTripOnlyWhenTicked() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-1"))
        vm.setDepartureDate(LocalDate.of(2099, 7, 8))
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        vm.duplicateEditedFlight()
        testScope.testScheduler.advanceUntilIdle()
        assertNull(repo.lastCreatedFlight?.tripId)
        assertTrue(repo.flights.any { it.id == "fixture-1" })
        assertNull(repo.lastMovedFlight)

        vm.setAddDuplicateToTrip(true)
        vm.setDepartureDate(LocalDate.of(2099, 7, 9))
        vm.duplicateEditedFlight()
        testScope.testScheduler.advanceUntilIdle()
        assertEquals("trip-fixture-1", repo.lastCreatedFlight?.tripId)
    }

    @Test
    fun duplicatePrefillsFromTheFlightAndLeavesTheDateToPick() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadDuplicateOf(repo.flight("fixture-2"), today = LocalDate.of(2099, 6, 1))

        assertEquals(AddFlightViewModel.Mode.DUPLICATE, vm.mode.value)
        assertEquals("EGTK DCT LFPB", vm.rawRouteText.value)
        assertEquals(LocalDate.of(2099, 7, 4), vm.departureDate.value)
        assertEquals(LocalTime.of(10, 30), vm.departureTime.value)
        assertNull(vm.selectedTripId.value)

        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        val req = requireNotNull(repo.lastCreatedFlight)
        assertEquals(listOf("EGTK", "LFPB"), req.waypoints)
        assertEquals("EGTK DCT LFPB", req.rawRoute)
        assertEquals(9000, req.cruiseAltitudeFt)
        assertEquals("2099-07-04T10:30:00Z", req.departureTime)
        assertNull(req.tripId)
    }

    @Test
    fun aRouteTheServerCouldNotCheckFailsTheSave() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        repo.interpretRouteError = java.io.IOException("offline")
        vm.loadExistingFlight(repo.flight("fixture-2"))

        vm.updateRouteText("EGTK DCT LFAT DCT LFPB")
        var saved: FlightResponse? = null
        vm.saveFlight { saved = it }
        testScope.testScheduler.advanceUntilIdle()

        // The local token split is never sent as a resolved route.
        assertNull(saved)
        assertNull(repo.lastUpdatedFlight)
        assertTrue(vm.errorMessage.value.orEmpty().startsWith("Couldn't check the route"))
    }

    @Test
    fun aFailedFollowUpPatchAfterMoveWarnsWithWhatDidNotSave() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-1"))
        vm.setDepartureDate(LocalDate.of(2099, 7, 2))
        vm.selectProfile(42)
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        repo.updateFlightError = ApiError.NotFound
        var moved: FlightResponse? = null
        vm.moveFlight { moved = it }
        testScope.testScheduler.advanceUntilIdle()

        // The move committed, so the sheet still closes; the warning is shown after it.
        assertNotNull(moved)
        assertEquals(
            "The flight moved, but the profile change didn't save. Open Edit to apply it again.",
            vm.saveWarning.value,
        )
    }

    @Test
    fun keepBothWarnsWhenTheAlternateFollowUpFails() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val (repo, vm) = setUp(testScope)
        vm.loadExistingFlight(repo.flight("fixture-1"))
        vm.setDepartureDate(LocalDate.of(2099, 7, 8))
        vm.setFlexibility(FlightFlexibility.ALTERNATE)
        vm.setAltDepartureTime(LocalTime.of(14, 0))
        vm.saveFlight()
        testScope.testScheduler.advanceUntilIdle()

        // Create refuses "alternate", so it follows as a PATCH; its failure is not silent.
        repo.updateFlightError = ApiError.NotFound
        var saved: FlightResponse? = null
        vm.duplicateEditedFlight { saved = it }
        testScope.testScheduler.advanceUntilIdle()

        assertNotNull(saved)
        assertNull(repo.lastCreatedFlight?.flexibility)
        assertEquals(
            "The flight was created, but the alternate time didn't save. Open Edit to apply it again.",
            vm.saveWarning.value,
        )
    }
}
