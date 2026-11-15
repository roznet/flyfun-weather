package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.MapsApi
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.CellFramesResponse
import aero.flyfun.weather.model.ObservedFramesResponse
import aero.flyfun.weather.model.rules.ObservedImageryRules
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.LatLonBox
import aero.flyfun.weather.model.rules.ObservedLightningRules
import aero.flyfun.weather.model.rules.ObservedSummaryChip
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.time.Instant
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit
import kotlin.math.abs

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

/**
 * Frame listings for the route map's tiled observed imagery (#998: radar over satellite IR), port
 * of iOS `RouteObservedImageryModel`. The map polls only the sources it draws, only while composed;
 * a failed refresh keeps the listing already held, and a source with none is reported as failed so
 * the map can say "unavailable" instead of drawing nothing silently. Online only.
 */
class RouteObservedImageryModel(
    private val repository: BriefingRepository,
) {
    data class State(
        val frames: Map<String, ObservedFramesResponse> = emptyMap(),
        val failed: Set<String> = emptySet(),
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** Keep [sources] fresh until the calling coroutine is cancelled. */
    suspend fun poll(sources: Set<String>): Nothing {
        while (true) { // delay() ends the loop on cancellation
            refresh(sources)
            delay(ObservedImageryRules.POLL_INTERVAL_MS)
        }
    }

    suspend fun refresh(sources: Set<String>) {
        for (source in sources) {
            val listing = try {
                repository.observedFrames(source)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                null
            }
            val current = _state.value
            _state.value = if (listing != null) {
                current.copy(frames = current.frames + (source to listing), failed = current.failed - source)
            } else if (source !in current.frames) {
                current.copy(failed = current.failed + source)
            } else {
                current
            }
        }
    }
}

/**
 * The briefing route map's airport-forecast overlay (#428 → Android #998): the same per-airport
 * markers the full forecast map draws, for the grid slot nearest the flight's departure. Port of
 * iOS `RouteForecastOverlayModel` / `RouteForecastOverlay`. The day grid loads once; a slot's
 * payload holds every model, so a model or metric change is a recolour, never a refetch.
 */
class RouteAirportOverlayModel(
    private val api: MapsApi?,
    private val clock: () -> Instant = { Instant.now() },
) {
    /** The grid slot for this flight, plus the models that carry airport data for it. */
    data class Slot(val day: Int, val hour: Int, val models: List<String>) {
        val key: String get() = "$day:$hour"
    }

    data class State(
        val days: List<ForecastDayEntry>? = null,
        val payloads: Map<String, ForecastMapResponse> = emptyMap(),
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun slot(departureIso: String?): Slot? =
        _state.value.days?.let { resolveSlot(departureIso, it, clock()) }

    /** Load the grid (once) and the payload for [departureIso]'s slot (once per slot). */
    suspend fun load(departureIso: String?) {
        val client = api ?: return
        if (_state.value.days == null) {
            val days = try {
                client.getForecastDays().days
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                return
            }
            _state.value = _state.value.copy(days = days)
        }
        val slot = slot(departureIso) ?: return
        if (slot.key in _state.value.payloads) return
        val payload = try {
            client.getForecastMap(slot.day, slot.hour)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            return
        }
        _state.value = _state.value.copy(payloads = _state.value.payloads + (slot.key to payload))
    }

    companion object {
        /** The individual models the full map's `fc.model` accepts (web `FORECAST_INDIVIDUAL_MODELS`). */
        val INDIVIDUAL_MODELS = setOf("gfs", "icon", "ecmwf")

        /** Relative (day, hour) in UTC for a departure, as the forecast endpoint labels days. */
        fun relativeDayHour(departureIso: String?, now: Instant): Pair<Int, Int>? {
            val ms = IsoTime.parseEpochMs(departureIso) ?: return null
            val dep = Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC)
            val today = now.atZone(ZoneOffset.UTC).toLocalDate()
            val day = ChronoUnit.DAYS.between(today, dep.toLocalDate()).toInt()
            return day to dep.hour
        }

        /** Nearest hour in [hours]; ties go to the earlier one. */
        fun nearestHour(hours: List<Int>, h: Int): Int? = hours.minByOrNull { abs(it - h) }

        /** Null outside the advertised horizon or on a day without data: the overlay is not offered. */
        fun resolveSlot(departureIso: String?, days: List<ForecastDayEntry>, now: Instant): Slot? {
            val (day, hour) = relativeDayHour(departureIso, now) ?: return null
            val entry = days.firstOrNull { it.day == day && it.available && it.hours.isNotEmpty() } ?: return null
            val h = nearestHour(entry.hours.sorted(), hour) ?: return null
            return Slot(day, h, entry.models)
        }
    }
}
