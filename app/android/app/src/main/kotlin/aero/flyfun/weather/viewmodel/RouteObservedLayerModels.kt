package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.CellFramesResponse
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.LatLonBox
import aero.flyfun.weather.model.rules.ObservedLightningRules
import aero.flyfun.weather.model.rules.ObservedSummaryChip
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The route map's experimental radar-cell overlay (#907), port of iOS `RouteCellsModel`.
 * The frame listing is fetched at most once a minute (a new frame lands every 5), the display
 * file for the chosen frame + box is immutable so it is cached by key, and nothing is drawn
 * unless the match is current: never a stale picture. Online only, nothing is written to the
 * briefing cache. The map drives [poll] from a `LaunchedEffect`, so polling stops when the map
 * leaves the screen.
 */
class RouteCellsModel(
    private val repository: BriefingRepository,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    data class State(
        /** Null until the first refresh finished. */
        val match: CellsOverlayRules.Match? = null,
        /** The overlay to draw, or null (not loaded, unavailable, disabled). */
        val display: CellDisplay? = null,
        val nowMs: Long = 0L,
    ) {
        val badge: String? get() = match?.let { CellsOverlayRules.badge(it, display, nowMs) }
        val chip: ObservedSummaryChip? get() = match?.let { CellsOverlayRules.chip(it, nowMs) }
    }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    private var frames: CellFramesResponse? = null
    private var framesFetchedAtMs: Long? = null
    private val displayCache = ArrayDeque<Pair<String, CellDisplay>>()

    /** Keep the overlay fresh until the calling coroutine is cancelled. */
    suspend fun poll(box: LatLonBox?): Nothing {
        while (true) { // delay() ends the loop on cancellation
            refresh(box)
            delay(POLL_INTERVAL_MS)
        }
    }

    /** Fetch the listing (TTL; [force] skips it) and the newest current overlay for [box]. */
    suspend fun refresh(box: LatLonBox?, force: Boolean = false) {
        val now = clock()
        val fetchedAt = framesFetchedAtMs
        if (force || frames == null || fetchedAt == null || now - fetchedAt >= FRAMES_TTL_MS) {
            try {
                frames = repository.cellFrames()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // Keep a held listing over a failed refresh; with none, the match reads "unavailable".
            }
            framesFetchedAtMs = now
        }
        val listing = frames
        // No radar pairing on Android: the route map's radar tiles are not keyed to a cell
        // frame, so the overlay is always the newest current one (iOS with no radar drawn).
        val match = CellsOverlayRules.match(listing, radarStamp = null, nowMs = now)
        if (match !is CellsOverlayRules.Match.Ok || listing == null) {
            _state.value = State(match, null, now)
            return
        }
        val key = match.frame.displayKey
        val cacheKey = "$key|${CellsOverlayRules.boxQuery(box)}"
        displayCache.firstOrNull { it.first == cacheKey }?.let {
            _state.value = State(match, it.second, now)
            return
        }
        try {
            val display = repository.observedCellDisplay(key, box)
            displayCache.addLast(cacheKey to display)
            if (displayCache.size > DISPLAY_CACHE_SIZE) displayCache.removeFirst()
            _state.value = State(match, display, now)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Purged (410) or unreadable: say unavailable since its own time.
            _state.value = State(CellsOverlayRules.Match.Unavailable(match.frame.validTime), null, now)
        }
    }

    companion object {
        const val FRAMES_TTL_MS: Long = 60_000L
        const val POLL_INTERVAL_MS: Long = 120_000L
        const val DISPLAY_CACHE_SIZE: Int = 6
    }
}

/**
 * Lightning flashes for the route map's corridor box (#907), port of iOS `RouteLightningModel`:
 * polled every [ObservedLightningRules.POLL_INTERVAL_MS] while the map is on screen, faded at
 * each refresh. Online only.
 */
class RouteLightningModel(
    private val repository: BriefingRepository,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    data class State(
        val layer: ObservedLightningRules.State = ObservedLightningRules.State.Loading,
        /** Flashes to draw now, faded at [nowMs]. */
        val drawable: List<ObservedLightningRules.DrawableFlash> = emptyList(),
        val nowMs: Long = 0L,
    ) {
        val badge: String? get() = ObservedLightningRules.badge(layer, nowMs)
        val chip: ObservedSummaryChip? get() = ObservedLightningRules.chip(layer, nowMs)
    }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** Keep the flashes fresh until the calling coroutine is cancelled. */
    suspend fun poll(box: LatLonBox): Nothing {
        while (true) { // delay() ends the loop on cancellation
            refresh(box)
            delay(ObservedLightningRules.POLL_INTERVAL_MS)
        }
    }

    suspend fun refresh(box: LatLonBox) {
        val next = try {
            ObservedLightningRules.State.Loaded(repository.observedFlashes(box))
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Say so on the map rather than drawing nothing silently.
            ObservedLightningRules.State.Unavailable
        }
        val now = clock()
        val drawable = (next as? ObservedLightningRules.State.Loaded)
            ?.let { ObservedLightningRules.drawable(it.response.flashes, now) }
            .orEmpty()
        _state.value = State(next, drawable, now)
    }

    /** Back to the not-yet-loaded state, so a layer toggled back on shows no stale bolts. */
    fun reset() {
        _state.value = State()
    }
}
