package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.CellFramesResponse
import aero.flyfun.weather.model.ObservedFlashesResponse
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.LatLonBox
import aero.flyfun.weather.model.rules.ObservedLightningRules
import aero.flyfun.weather.rules.CellsOverlayRulesTest
import aero.flyfun.weather.rules.ObservedLightningRulesTest
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The route map's cell and lightning models (#907), the iOS `RouteCellsModel` /
 * `RouteLightningModel` cases: one listing per TTL, display cached by key, a failure reads
 * unavailable and draws nothing, and the demo fixtures say "not available" rather than nothing.
 */
class RouteObservedLayerModelsTest {

    private class FakeRepository(
        var frames: () -> CellFramesResponse = { CellsOverlayRulesTest.listing() },
        var display: (String) -> CellDisplay = { CellsOverlayRulesTest.display() },
        var flashes: (LatLonBox) -> ObservedFlashesResponse = { ObservedLightningRulesTest.response() },
    ) : BriefingRepository by FixtureBriefingRepository() {
        var framesCalls = 0
        val displayRequests = mutableListOf<Pair<String, LatLonBox?>>()
        val flashRequests = mutableListOf<LatLonBox>()

        override suspend fun cellFrames(): CellFramesResponse {
            framesCalls++
            return frames()
        }

        override suspend fun observedCellDisplay(key: String, box: LatLonBox?): CellDisplay {
            displayRequests += key to box
            return display(key)
        }

        override suspend fun observedFlashes(box: LatLonBox): ObservedFlashesResponse {
            flashRequests += box
            return flashes(box)
        }
    }

    private val box = LatLonBox(south = 43.0, west = 4.0, north = 46.0, east = 7.0)

    @Test
    fun `cells - one listing per TTL, display cached by key, forced refresh skips the TTL`() = runTest {
        val repo = FakeRepository()
        var now = CellsOverlayRulesTest.NOW
        val model = RouteCellsModel(repo) { now }

        model.refresh(box)
        assertEquals("Cells 12 min", model.state.value.chip?.text)
        now += 30_000L
        model.refresh(box)
        assertEquals(1, repo.framesCalls)
        assertEquals(listOf("20261003T1405" to box), repo.displayRequests)
        assertEquals(3, model.state.value.display?.cells?.size)
        assertTrue(model.state.value.badge.orEmpty().startsWith("Cells 14:05Z"))

        now += 5_000L
        model.refresh(box, force = true)
        assertEquals(2, repo.framesCalls)
        assertEquals(1, repo.displayRequests.size) // still cached
    }

    @Test
    fun `cells - a failed display fetch reads unavailable, never a stale picture`() = runTest {
        val repo = FakeRepository(display = { error("410") })
        val model = RouteCellsModel(repo) { CellsOverlayRulesTest.NOW }
        model.refresh(box)
        assertNull(model.state.value.display)
        assertEquals(CellsOverlayRules.Match.Unavailable("2026-10-03T14:05:00+00:00"), model.state.value.match)
    }

    @Test
    fun `cells - a stale feed shows the unavailable note and fetches nothing`() = runTest {
        val repo = FakeRepository()
        val model = RouteCellsModel(repo) { CellsOverlayRulesTest.NOW + 60 * 60_000L }
        model.refresh(box)
        assertTrue(repo.displayRequests.isEmpty())
        assertEquals("Cell analysis unavailable since 14:05Z", model.state.value.badge)
        assertEquals(true, model.state.value.chip?.isWarning)
    }

    @Test
    fun `cells - demo fixtures read not available on this server`() = runTest {
        val model = RouteCellsModel(FixtureBriefingRepository()) { CellsOverlayRulesTest.NOW }
        model.refresh(box)
        assertEquals(CellsOverlayRules.Match.Disabled, model.state.value.match)
        assertEquals("Cells n/a", model.state.value.chip?.text)
    }

    @Test
    fun `lightning - requests the corridor box, a failure reads unavailable and draws nothing`() = runTest {
        val repo = FakeRepository()
        var now = ObservedLightningRulesTest.NOW
        val model = RouteLightningModel(repo) { now }

        assertNull(model.state.value.badge) // nothing asked yet: say nothing
        model.refresh(box)
        assertEquals(listOf(box), repo.flashRequests)
        assertEquals(2, model.state.value.drawable.size)
        assertTrue(model.state.value.badge.orEmpty().startsWith("Lightning 14:05Z"))

        repo.flashes = { error("offline") }
        now += 120_000L
        model.refresh(box)
        assertEquals(ObservedLightningRules.State.Unavailable, model.state.value.layer)
        assertTrue(model.state.value.drawable.isEmpty())
        assertEquals("Lightning unavailable", model.state.value.badge)

        model.reset()
        assertNull(model.state.value.chip)
    }
}
