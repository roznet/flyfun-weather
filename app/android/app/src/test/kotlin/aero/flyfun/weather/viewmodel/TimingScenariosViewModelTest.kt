package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.TimeCandidate
import aero.flyfun.weather.model.TimeOptionsResponse
import aero.flyfun.weather.model.TimeScanJobStatus
import aero.flyfun.weather.model.TimeScanStatusDTO
import aero.flyfun.weather.model.TimeWindowScanDTO
import aero.flyfun.weather.model.UpdateFlightRequest
import aero.flyfun.weather.model.UpdateFlightResponse
import java.io.IOException
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The timing-scenario poll and actions (#893): per pack, like iOS, never per flight. */
class TimingScenariosViewModelTest {

    private fun status(s: TimeScanJobStatus) = TimeScanStatusDTO(status = s, flexibility = FlightFlexibility.SAME_DAY)

    private val running = TimeOptionsResponse(status = status(TimeScanJobStatus.RUNNING))
    private val done = TimeOptionsResponse(
        status = status(TimeScanJobStatus.DONE),
        scan = TimeWindowScanDTO(
            flexibility = FlightFlexibility.SAME_DAY,
            candidates = listOf(TimeCandidate(departureTime = "2099-06-30T12:00:00Z", assessment = "GREEN")),
        ),
    )

    /** Scripted server: answers [answers] in order (the last repeats); a Throwable is thrown. */
    private class ScanRepository(
        private val inner: BriefingRepository,
        private val flexibility: FlightFlexibility,
        private val answers: List<Any>,
    ) : BriefingRepository by inner {
        val polledTimestamps = mutableListOf<String>()
        val confirms = mutableListOf<Pair<String, String>>()
        val rescans = mutableListOf<String>()
        var confirmError: Throwable? = null
        var rescanError: Throwable? = null

        override suspend fun getFlight(id: String): FlightResponse =
            inner.getFlight(id).copy(flexibility = flexibility)

        override suspend fun timeOptions(flightId: String, timestamp: String): TimeOptionsResponse {
            val answer = answers[minOf(polledTimestamps.size, answers.size - 1)]
            polledTimestamps += timestamp
            if (answer is Throwable) throw answer
            return answer as TimeOptionsResponse
        }

        override suspend fun updateFlight(id: String, req: UpdateFlightRequest): UpdateFlightResponse {
            val resp = inner.updateFlight(id, req)
            return resp.copy(flight = resp.flight.copy(flexibility = flexibility))
        }

        override suspend fun confirmTimeOption(flightId: String, timestamp: String, departureTime: String) {
            confirms += timestamp to departureTime
            confirmError?.let { throw it }
        }

        override suspend fun rescanTimeOptions(flightId: String, timestamp: String) {
            rescans += timestamp
            rescanError?.let { throw it }
        }
    }

    private fun TestScope.openBriefing(repo: BriefingRepository): Pair<BriefingViewModel, TestScope> {
        val scope = TestScope(StandardTestDispatcher(testScheduler))
        val vm = BriefingViewModel(flightId = FixtureBriefingData.FLIGHT_ID, repository = repo, scopeOverride = scope)
        vm.loadInitialBriefing()
        return vm to scope
    }

    @Test
    fun pollsThePackUntilTheScanIsDone() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(running, running, done))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()

        // Running, running, done: at least three polls, all on the shown pack, then it stops.
        assertTrue(repo.polledTimestamps.size >= 3)
        assertTrue(repo.polledTimestamps.all { it == FixtureBriefingData.TIMESTAMP })
        assertEquals(done, vm.timeOptions.value)
        assertFalse(vm.timeOptionsOffline.value)
    }

    @Test
    fun flexibilityNoneNeverPolls() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.NONE, listOf(done))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()

        assertTrue(repo.polledTimestamps.isEmpty())
        assertNull(vm.timeOptions.value)
    }

    @Test
    fun notFoundHidesTheSection() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(ApiError.NotFound))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()

        assertTrue(repo.polledTimestamps.isNotEmpty())
        assertNull(vm.timeOptions.value)
    }

    @Test
    fun networkFailuresShowTheOfflineLineAfterTheBudget() = runTest {
        val offline = ApiError.NetworkError(IOException("offline"))
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(offline))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()

        // Three blips are tolerated, the fourth gives up (well before the 80-poll cap).
        assertTrue(repo.polledTimestamps.size in 4..8)
        assertTrue(vm.timeOptionsOffline.value)
    }

    @Test
    fun confirmQueuesTheCheckOnThePackAndPollsAgain() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(done))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()
        val pollsBefore = repo.polledTimestamps.size

        vm.confirmTimeOption("2099-06-30T12:00:00Z")
        scope.advanceUntilIdle()

        assertEquals(listOf(FixtureBriefingData.TIMESTAMP to "2099-06-30T12:00:00Z"), repo.confirms)
        assertEquals(pollsBefore + 1, repo.polledTimestamps.size)
        assertNull(vm.errorMessage.value)
    }

    @Test
    fun aBusyConfirmIsNotAnError() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(done))
        repo.confirmError = ApiError.ServerError(429, "A model check is already running")
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()
        val pollsBefore = repo.polledTimestamps.size

        vm.confirmTimeOption("2099-06-30T12:00:00Z")
        scope.advanceUntilIdle()

        assertNull(vm.errorMessage.value)
        assertEquals(pollsBefore + 1, repo.polledTimestamps.size)
    }

    @Test
    fun setAsAlternateRescansThePack() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(done))
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()

        vm.setScenarioAsAlternate("2099-06-30T12:00:00Z")
        scope.advanceUntilIdle()

        assertEquals(listOf(FixtureBriefingData.TIMESTAMP), repo.rescans)
        assertEquals("2099-06-30T12:00:00Z", vm.flight.value?.altDepartureTime)
    }

    @Test
    fun aFailedRescanAfterSavingTheAlternateStillPolls() = runTest {
        val repo = ScanRepository(FixtureBriefingRepository(), FlightFlexibility.SAME_DAY, listOf(done))
        repo.rescanError = ApiError.ServerError(500, "boom")
        val (vm, scope) = openBriefing(repo)
        scope.advanceUntilIdle()
        val pollsBefore = repo.polledTimestamps.size

        vm.setScenarioAsAlternate("2099-06-30T12:00:00Z")
        scope.advanceUntilIdle()

        // The PATCH landed, so the alternate is kept, the panel re-polls and the error says so.
        assertEquals("2099-06-30T12:00:00Z", vm.flight.value?.altDepartureTime)
        assertEquals(pollsBefore + 1, repo.polledTimestamps.size)
        assertTrue(vm.errorMessage.value.orEmpty().startsWith("Alternate time saved"))
    }
}
