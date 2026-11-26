package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.PastArchiveResponse
import aero.flyfun.weather.model.PastChips
import aero.flyfun.weather.model.PastFilter
import aero.flyfun.weather.model.WbJson
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Past flights finder (#1032): archive decoding, filter → query, paging and month grouping. */
@OptIn(ExperimentalCoroutinesApi::class)
class PastFlightsViewModelTest {

    private val payload = """
    {
      "view": "list", "total": 3, "limit": 2, "offset": 0,
      "facets": {
        "source": "forecast",
        "tags": {"ICE": 1, "TS": 0, "IMC": 1, "WIND": 0, "TURB": 0, "FRZ": 0, "VIS": 0},
        "outcomes": {"flown": 1, "cancelled": 1, "monitoring": 0, "debriefed": 2, "differs": 1},
        "not_recorded": 1
      },
      "rows": [
        {"id": "f1", "departure_time": "2026-11-20T09:00:00+00:00", "departure": "EGTK",
         "destination": "LFAT", "route_name": "Oxford to Le Touquet",
         "trip": {"id": "t1", "name": "Autumn trip"},
         "forecast_tags": {"ICE": "red", "IMC": "amber"}, "forecast_worst": "red",
         "tags_recorded": true, "reported_tags": ["ICE"], "decision": "flown",
         "debriefed": true, "differs_from_forecast": true, "distance_nm": 112.4},
        {"id": "f2", "departure_time": "2026-10-02T15:30:00+00:00", "departure": null,
         "destination": null, "route_name": "Local", "trip": null,
         "forecast_tags": {}, "forecast_worst": null, "tags_recorded": false,
         "reported_tags": [], "decision": "cancelled", "debriefed": true,
         "differs_from_forecast": false, "distance_nm": null}
      ]
    }
    """.trimIndent()

    private fun decode(): PastArchiveResponse = WbJson.decodeFromString(PastArchiveResponse.serializer(), payload)

    @Test
    fun decodesRowsAndFacets() {
        val resp = decode()
        assertEquals(3, resp.total)
        assertEquals(1, resp.facets.tags["ICE"])
        assertEquals(1, resp.facets.outcomes["differs"])
        assertEquals(1, resp.facets.notRecorded)
        val first = resp.rows[0]
        assertEquals(mapOf("ICE" to "red", "IMC" to "amber"), first.forecastTags)
        assertEquals("EGTK → LFAT", first.title)
        assertEquals("Autumn trip", first.trip?.name)
        assertTrue(first.departureEpochMs != null)
        assertEquals("Local", resp.rows[1].title)
        assertFalse(resp.rows[1].tagsRecorded)
    }

    @Test
    fun filterMapsToServerParams() {
        assertEquals(listOf("limit" to "30"), PastFilter().queryItems(limit = 30, offset = 0))
        assertFalse(PastFilter().isActive)
        val f = PastFilter(query = " EGTK ", tags = listOf("ICE", "TS"), reported = true,
            outcomes = listOf("flown"), hideInactive = true)
        assertTrue(f.isActive)
        assertEquals(
            listOf("q" to "EGTK", "tags" to "ICE,TS", "source" to "reported", "outcome" to "flown",
                "hide" to "cancelled,monitoring", "limit" to "30", "offset" to "60"),
            f.queryItems(limit = 30, offset = 60),
        )
        assertEquals("Icing", PastChips.labelForTag("ICE"))
        assertEquals("XYZ", PastChips.labelForTag("XYZ"))
        assertEquals("Monitor only", PastChips.labelForOutcome("monitoring"))
    }

    @Test
    fun viewModelLoadsPagesAndGroupsByMonth() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository()
        repo.pastArchiveFixture = decode()
        val vm = PastFlightsViewModel(repository = repo, scopeOverride = testScope)

        vm.toggleTag("ICE")
        testScope.advanceUntilIdle()
        assertEquals(listOf("f1", "f2"), vm.rows.value.map { it.id })
        assertEquals("ICE", repo.pastArchiveRequests.last().toMap()["tags"])
        assertTrue(vm.hasMore)

        // The next page asks from the loaded count; a repeated row is not duplicated.
        vm.loadMoreIfNeeded(vm.rows.value.last())
        testScope.advanceUntilIdle()
        assertEquals("2", repo.pastArchiveRequests.last().toMap()["offset"])
        assertEquals(2, vm.rows.value.size)

        assertEquals(listOf(1, 1), PastFlightsViewModel.monthSections(vm.rows.value).map { it.rows.size })
    }
}
