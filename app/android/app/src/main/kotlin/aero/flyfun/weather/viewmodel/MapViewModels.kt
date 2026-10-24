// SYNC: iOS app/flyfun-weather/flyfun-weather/ViewModels/ForecastMapViewModel.swift
//       iOS app/flyfun-weather/flyfun-weather/ViewModels/HistoricalMapViewModel.swift
package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.MapsApi
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ForecastLinkedDate
import aero.flyfun.weather.model.ForecastMapAirport
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.ForecastModelMode
import aero.flyfun.weather.model.HistoricalMapAirport
import aero.flyfun.weather.model.HistoricalMapResponse
import aero.flyfun.weather.model.HistoricalRangeResponse
import aero.flyfun.weather.model.HistoricalSource
import aero.flyfun.weather.model.MapViewport
import aero.flyfun.weather.model.NavigationTarget
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.coroutines.cancellation.CancellationException
import kotlin.math.abs
import kotlin.math.ln
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * A one-shot camera move the map screen applies, then clears. [webZoom] is a Leaflet-style zoom
 * (the share links' unit; the screen adds the display density); null keeps the current zoom.
 */
data class MapFocusRequest(
    val lat: Double,
    val lon: Double,
    val webZoom: Double? = null,
    /** Bumped so two identical requests in a row both apply. */
    val seq: Long = nextSeq(),
) {
    companion object {
        private var counter = 0L

        @Synchronized
        private fun nextSeq(): Long = ++counter

        /** Leaflet zoom for a camera spanning [lonSpanDeg] of longitude (iOS spans → web zoom). */
        fun webZoomForLonSpan(lonSpanDeg: Double): Double = ln(360.0 / lonSpanDeg) / ln(2.0)

        fun at(view: MapViewport) = MapFocusRequest(view.lat, view.lon, view.zoom.toDouble())
    }
}

/** What a demo / guest session shows instead of a map: the payloads need a signed-in server. */
const val MAPS_DEMO_UNAVAILABLE = "The weather maps need a signed-in account; they aren't available in demo mode."

/**
 * State for the pan-European **forecast map** (#420): the day/hour/metric/model selection, the
 * per-(day, hour) payload with an in-memory LRU + adjacent-hour prefetch, and the tapped airport.
 *
 * Metric and model switches are a **pure recolour**; only a day/hour change hits the network. A
 * failed load shows the error. There is no stand-in data: the map shows the server's airports or
 * says why it can't.
 */
class ForecastMapViewModel(
    private val api: MapsApi? = null,
    private val isMockModeProvider: () -> Boolean = { false },
    private val scopeOverride: CoroutineScope? = null,
    private val clock: () -> Instant = { Instant.now() },
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private data class SlotKey(val day: Int, val hour: Int)

    /** The ragged day/hour/model grid, from `/maps/forecast/days` (near-days guess until then). */
    private val _days = MutableStateFlow(fallbackDays())
    val days: StateFlow<List<ForecastDayEntry>> = _days.asStateFlow()

    private val _selectedDay = MutableStateFlow(0)
    val selectedDay: StateFlow<Int> = _selectedDay.asStateFlow()

    private val _selectedHour = MutableStateFlow(12)
    val selectedHour: StateFlow<Int> = _selectedHour.asStateFlow()

    /** The selected day's sample hours (the hour menu and the ‹ › stepper). */
    private val _availableHours = MutableStateFlow(hoursFor(0, fallbackDays()))
    val availableHours: StateFlow<List<Int>> = _availableHours.asStateFlow()

    private val _metric = MutableStateFlow("flight_category")
    val metric: StateFlow<String> = _metric.asStateFlow()

    private val _mode = MutableStateFlow<ForecastModelMode>(ForecastModelMode.Worst)
    val mode: StateFlow<ForecastModelMode> = _mode.asStateFlow()

    private val _selectedIcao = MutableStateFlow<String?>(null)
    val selectedIcao: StateFlow<String?> = _selectedIcao.asStateFlow()

    /** The selected slot's payload (null until the first load). */
    private val _payload = MutableStateFlow<ForecastMapResponse?>(null)
    val payload: StateFlow<ForecastMapResponse?> = _payload.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _loadError = MutableStateFlow<String?>(null)
    val loadError: StateFlow<String?> = _loadError.asStateFlow()

    /** True once the grid + first payload have loaded (drives the full-screen placeholder). */
    private val _didLoadOnce = MutableStateFlow(false)
    val didLoadOnce: StateFlow<Boolean> = _didLoadOnce.asStateFlow()

    /**
     * Set when a linked `fc.date` couldn't be shown as is (#840): says which day is shown instead.
     * Cleared when the pilot picks a day.
     */
    private val _linkedDateNotice = MutableStateFlow<String?>(null)
    val linkedDateNotice: StateFlow<String?> = _linkedDateNotice.asStateFlow()

    private val _focusRequest = MutableStateFlow<MapFocusRequest?>(null)
    val focusRequest: StateFlow<MapFocusRequest?> = _focusRequest.asStateFlow()

    private val slots = LinkedHashMap<SlotKey, ForecastMapResponse>(16, 0.75f, true)
    private val inFlight = mutableSetOf<SlotKey>()

    /** True once the days request has finished (or was skipped), so [days] is final. */
    private var daysResolved = false

    /** The days request failed and [days] is the offline D+0…D+3 guess. */
    private var gridIsFallback = false

    /** A linked `fc.date` that arrived before the grid; resolved by [start]. */
    private var pendingLink: NavigationTarget.ForecastMap? = null

    /** A linked airport to open once its payload is on screen. */
    private var pendingOpenIcao: String? = null

    /** A link set the view (or an airport): no cold-open centring on the usual airport. */
    private var hasExplicitLocation = false

    /** The link pinned `fc.hour`; without it the cold-open nearest-hour default applies. */
    private var hourFromLink = false

    /** Cold-open target from the pilot's frequent airports, applied once its payload is there. */
    private var coldOpenIcao: String? = null

    /** The pilot moved the map: a late cold-open target must not yank the camera. */
    private var userDidInteract = false

    init {
        start()
    }

    private fun start() {
        scope.launch {
            _isLoading.value = true
            _loadError.value = null
            val client = api
            if (isMockModeProvider() || client == null) {
                // The demo grid stays as the grid; there is no payload to show
                daysResolved = true
                pendingLink?.let { link ->
                    pendingLink = null
                    applyLinkedDay(link)
                }
                snapSelectionIntoGrid(smartDefaultHour = !hourFromLink)
                _loadError.value = MAPS_DEMO_UNAVAILABLE
                _isLoading.value = false
                return@launch
            }
            // 1. The grid (best effort; a failure keeps the near-days guess)
            try {
                val resp = client.getForecastDays()
                if (resp.days.isNotEmpty()) {
                    _days.value = resp.days
                    gridIsFallback = false
                } else {
                    gridIsFallback = true
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                gridIsFallback = true
            }
            daysResolved = true
            // 2. A linked absolute date wins over `fc.day` (#840); then snap into the real grid
            pendingLink?.let { link ->
                pendingLink = null
                applyLinkedDay(link)
            }
            snapSelectionIntoGrid(smartDefaultHour = !hourFromLink)
            // 3. Cold-open centring on the pilot's usual departure (non-blocking, non-fatal)
            if (!hasExplicitLocation) {
                scope.launch { resolveColdOpenTarget(client) }
            }
            // 4. First payload
            loadSlot(_selectedDay.value, _selectedHour.value)
            _didLoadOnce.value = _payload.value != null
            openPendingAirport()
        }
    }

    /** Retry after a failed first load. */
    fun retry() {
        if (_isLoading.value) return
        loadSlotAsync(_selectedDay.value, _selectedHour.value)
    }

    // -------------------------------------------------------------------------
    // Selection
    // -------------------------------------------------------------------------

    fun selectDay(day: Int) {
        // The pilot's pick wins over a link still waiting for the grid
        pendingLink = null
        _linkedDateNotice.value = null
        if (_selectedDay.value == day && _payload.value != null) return
        _selectedDay.value = day
        syncHoursToSelectedDay()
        snapModeIntoDay()
        loadSlotAsync(day, _selectedHour.value)
    }

    fun selectHour(hour: Int) {
        val snapped = snapHour(hour, hoursFor(_selectedDay.value, _days.value))
        if (_selectedHour.value == snapped && _payload.value != null) return
        _selectedHour.value = snapped
        loadSlotAsync(_selectedDay.value, snapped)
    }

    /** Step the hour by ±1 within the day's sample hours (the ‹ › feel). */
    fun stepHour(delta: Int) {
        val hours = _availableHours.value
        val idx = hours.indexOf(_selectedHour.value)
        if (idx < 0) {
            hours.firstOrNull()?.let(::selectHour)
            return
        }
        hours.getOrNull(idx + delta)?.let(::selectHour)
    }

    fun canStepHour(delta: Int): Boolean {
        val hours = _availableHours.value
        val idx = hours.indexOf(_selectedHour.value)
        return idx >= 0 && (idx + delta) in hours.indices
    }

    fun selectMetric(metric: String) {
        _metric.value = metric
    }

    fun selectMode(mode: ForecastModelMode) {
        _mode.value = mode
    }

    /** Models present for a day (an unknown day → all three). */
    fun models(day: Int): List<String> =
        _days.value.firstOrNull { it.day == day }?.models?.takeIf { it.isNotEmpty() } ?: listOf("gfs", "icon", "ecmwf")

    /** Greyed-but-tappable: an unreachable model must explain itself, never read as agreement. */
    fun modelAvailable(model: String, day: Int = _selectedDay.value): Boolean = model in models(day)

    fun select(icao: String) {
        _selectedIcao.value = icao
        _payload.value?.airports?.firstOrNull { it.icao == icao }?.let { apt ->
            _focusRequest.value = MapFocusRequest(apt.lat, apt.lon)
        }
    }

    fun deselect() {
        _selectedIcao.value = null
    }

    fun selectedAirport(): ForecastMapAirport? {
        val icao = _selectedIcao.value ?: return null
        return _payload.value?.airports?.firstOrNull { it.icao == icao }
    }

    /** A located fix (the locate-me button) recentres the map at a regional zoom. */
    fun focusOn(lat: Double, lon: Double) {
        _focusRequest.value = MapFocusRequest(lat, lon, MapFocusRequest.webZoomForLonSpan(5.0))
    }

    fun focusApplied(request: MapFocusRequest) {
        if (_focusRequest.value == request) _focusRequest.value = null
    }

    /** The pilot panned or zoomed: freeze cold-open so it can't recentre later. */
    fun markUserInteracted() {
        userDidInteract = true
    }

    // -------------------------------------------------------------------------
    // Deep links
    // -------------------------------------------------------------------------

    fun applyDeepLinkTarget(target: NavigationTarget.ForecastMap) {
        target.metric?.let { _metric.value = it }
        target.model?.let { _mode.value = ForecastModelMode.fromToken(it) }
        target.hourUtc?.let {
            _selectedHour.value = it
            hourFromLink = true
        }
        target.airport?.let { pendingOpenIcao = it }
        target.view?.let {
            hasExplicitLocation = true
            _focusRequest.value = MapFocusRequest.at(it)
        }
        if (target.airport != null) hasExplicitLocation = true
        if (!daysResolved) {
            // `fc.date` resolves against the real grid: start() applies it once the days land
            pendingLink = target
            return
        }
        applyLinkedDay(target)
        snapSelectionIntoGrid(smartDefaultHour = false)
        loadSlotAsync(_selectedDay.value, _selectedHour.value, openPendingAirport = true)
    }

    /** `fc.date` wins over `fc.day` (#840); a malformed date or empty grid falls back to `fc.day`. */
    private fun applyLinkedDay(target: NavigationTarget.ForecastMap) {
        _linkedDateNotice.value = null
        // One clock reading for both the resolution and the notice's label, so a UTC-midnight
        // rollover in between can't mislabel the shown day.
        val today = clock().atZone(ZoneOffset.UTC).toLocalDate()
        val linked = target.date
        val resolved = linked?.let { date ->
            ForecastLinkedDate.resolve(date, offered = _days.value.filter { it.available }.map { it.day }, today = today)
        }
        if (linked == null || resolved == null) {
            target.dayOffset?.let { _selectedDay.value = it }
            return
        }
        _selectedDay.value = resolved.day
        _linkedDateNotice.value = ForecastLinkedDate.noticeFor(linked, resolved, gridIsFallback, today)
    }

    // -------------------------------------------------------------------------
    // Loading
    // -------------------------------------------------------------------------

    private fun loadSlotAsync(day: Int, hour: Int, openPendingAirport: Boolean = false) {
        scope.launch {
            loadSlot(day, hour)
            if (openPendingAirport) openPendingAirport()
        }
    }

    /** Fetch a slot from the LRU or the network; publish it only while it is the selected slot. */
    private suspend fun loadSlot(day: Int, hour: Int) {
        val key = SlotKey(day, hour)
        fun isCurrent() = _selectedDay.value == day && _selectedHour.value == hour
        val client = api
        if (isMockModeProvider() || client == null) return
        slots[key]?.let { cached ->
            _payload.value = cached
            _loadError.value = null
            if (isCurrent()) _isLoading.value = false
            prefetchAdjacentHours(client)
            return
        }
        if (key in inFlight) return // a prefetch already owns it; store() publishes it
        inFlight += key
        if (isCurrent()) _isLoading.value = true
        _loadError.value = null
        try {
            store(client.getForecastMap(day = day, hourUtc = hour), key)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // The previous slot stays on screen; a first load with nothing to show says why
            if (isCurrent() && _payload.value == null) _loadError.value = e.message ?: "The forecast map didn't load."
        } finally {
            inFlight -= key
        }
        if (isCurrent()) _isLoading.value = false
        _didLoadOnce.value = _didLoadOnce.value || _payload.value != null
        prefetchAdjacentHours(client)
    }

    /** Prefetch the day's neighbouring hours (server-cached, so cheap) so ‹ › feels instant. */
    private fun prefetchAdjacentHours(client: MapsApi) {
        val hours = _availableHours.value
        val idx = hours.indexOf(_selectedHour.value)
        if (idx < 0) return
        for (neighbour in listOf(idx - 1, idx + 1)) {
            val hour = hours.getOrNull(neighbour) ?: continue
            val key = SlotKey(_selectedDay.value, hour)
            if (key in slots || key in inFlight) continue
            inFlight += key
            scope.launch {
                try {
                    store(client.getForecastMap(day = key.day, hourUtc = key.hour), key)
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) {
                    // Best effort
                } finally {
                    inFlight -= key
                }
            }
        }
    }

    private fun store(resp: ForecastMapResponse, key: SlotKey) {
        slots[key] = resp
        val current = SlotKey(_selectedDay.value, _selectedHour.value)
        if (key == current) {
            _payload.value = resp
            _loadError.value = null
        }
        applyColdOpenIfPossible()
        // Evict least-recently used, never the on-screen slot
        val iter = slots.keys.iterator()
        while (slots.size > LRU_CAPACITY && iter.hasNext()) {
            if (iter.next() != current) iter.remove()
        }
    }

    private fun openPendingAirport() {
        val icao = pendingOpenIcao ?: return
        if (_payload.value?.airports?.any { it.icao == icao } == true) {
            pendingOpenIcao = null
            select(icao)
        }
    }

    private suspend fun resolveColdOpenTarget(client: MapsApi) {
        val freq = try {
            client.getFrequentAirports()
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            return
        }
        coldOpenIcao = freq.departures.firstOrNull()?.icao ?: freq.destinations.firstOrNull()?.icao
        applyColdOpenIfPossible()
    }

    private fun applyColdOpenIfPossible() {
        if (hasExplicitLocation || userDidInteract) return
        val icao = coldOpenIcao ?: return
        val apt = _payload.value?.airports?.firstOrNull { it.icao == icao } ?: return
        // iOS: an 8° × 10° region around the pilot's usual departure
        _focusRequest.value = MapFocusRequest(apt.lat, apt.lon, MapFocusRequest.webZoomForLonSpan(10.0))
        coldOpenIcao = null
    }

    // -------------------------------------------------------------------------
    // Grid
    // -------------------------------------------------------------------------

    private fun snapSelectionIntoGrid(smartDefaultHour: Boolean) {
        val maxDay = _days.value.maxOfOrNull { it.day } ?: 0
        _selectedDay.value = _selectedDay.value.coerceIn(0, maxDay)
        val hours = hoursFor(_selectedDay.value, _days.value)
        // Cold open: nearest sample hour to the current UTC hour, not always 12Z
        val wanted = if (smartDefaultHour) clock().atZone(ZoneOffset.UTC).hour else _selectedHour.value
        _selectedHour.value = snapHour(wanted, hours)
        _availableHours.value = hours
        snapModeIntoDay()
    }

    /**
     * Hour menu follows the selected day's sample hours (the last day often has only a few),
     * snapping the hour to the nearest one so the map never asks for an hour with no data.
     */
    private fun syncHoursToSelectedDay() {
        val hours = hoursFor(_selectedDay.value, _days.value)
        _availableHours.value = hours
        _selectedHour.value = snapHour(_selectedHour.value, hours)
    }

    /** Fall back to worst consensus when the active model can't reach the selected day (web). */
    private fun snapModeIntoDay() {
        val m = _mode.value
        if (m is ForecastModelMode.Model && !modelAvailable(m.name)) _mode.value = ForecastModelMode.Worst
    }

    companion object {
        private const val LRU_CAPACITY = 8

        /** Sample hours for a day from the grid; the fine set when the grid doesn't say. */
        fun hoursFor(day: Int, days: List<ForecastDayEntry>): List<Int> =
            days.firstOrNull { it.day == day }?.hours?.takeIf { it.isNotEmpty() }
                ?: if (day >= 6) listOf(6, 12, 18) else listOf(6, 9, 12, 15, 18)

        fun snapHour(hour: Int, hours: List<Int>): Int =
            if (hours.isEmpty() || hour in hours) hour else hours.minBy { abs(it - hour) }

        /** Near-days grid when `/maps/forecast/days` is unreachable (web `fallbackDayGrid`). */
        fun fallbackDays(): List<ForecastDayEntry> = (0..3).map { d ->
            ForecastDayEntry(day = d, date = "", available = true, hours = listOf(6, 9, 12, 15, 18), models = listOf("ecmwf", "gfs", "icon"))
        }
    }
}

/**
 * State for the **historical map** (#629): a past instant on the 30-min UTC grid, a model lead
 * (Latest / D-1…D-6), a source (METAR / TAF / Worst / Majority / GFS / ICON / ECMWF) and a metric.
 *
 * Every selection rule lives on the server (`tasks/historical_map.py`); this VM fetches on an
 * instant or lead change and recolours on a source or metric change, presenting the chosen source to
 * the shared marker layer as a [ForecastMapResponse] ([mapPayload]).
 */
class HistoricalMapViewModel(
    private val api: MapsApi? = null,
    private val isMockModeProvider: () -> Boolean = { false },
    private val scopeOverride: CoroutineScope? = null,
    private val now: () -> Instant = { Instant.now() },
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private data class SlotKey(val at: Instant, val lead: Int)

    private val _range = MutableStateFlow<HistoricalRangeResponse?>(null)
    val range: StateFlow<HistoricalRangeResponse?> = _range.asStateFlow()

    /** The selected instant, on the grid once the range has loaded. */
    private val _selectedInstant = MutableStateFlow<Instant?>(null)
    val selectedInstant: StateFlow<Instant?> = _selectedInstant.asStateFlow()

    private val _lead = MutableStateFlow(0)
    val lead: StateFlow<Int> = _lead.asStateFlow()

    private val _source = MutableStateFlow(HistoricalSource.METAR)
    val source: StateFlow<HistoricalSource> = _source.asStateFlow()

    private val _metric = MutableStateFlow("flight_category")
    val metric: StateFlow<String> = _metric.asStateFlow()

    private val _selectedIcao = MutableStateFlow<String?>(null)
    val selectedIcao: StateFlow<String?> = _selectedIcao.asStateFlow()

    private val _payload = MutableStateFlow<HistoricalMapResponse?>(null)
    val payload: StateFlow<HistoricalMapResponse?> = _payload.asStateFlow()

    /** The payload as the marker layer reads it: the airports that have [source], presenting it. */
    private val _mapPayload = MutableStateFlow<ForecastMapResponse?>(null)
    val mapPayload: StateFlow<ForecastMapResponse?> = _mapPayload.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _loadError = MutableStateFlow<String?>(null)
    val loadError: StateFlow<String?> = _loadError.asStateFlow()

    private val _didLoadOnce = MutableStateFlow(false)
    val didLoadOnce: StateFlow<Boolean> = _didLoadOnce.asStateFlow()

    /** Why a tapped source can't be shown; the screen presents it as a dialog. */
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice.asStateFlow()

    private val _focusRequest = MutableStateFlow<MapFocusRequest?>(null)
    val focusRequest: StateFlow<MapFocusRequest?> = _focusRequest.asStateFlow()

    /** Final instants only: a past instant stops changing once every input has landed. */
    private val cache = LinkedHashMap<SlotKey, HistoricalMapResponse>(16, 0.75f, true)
    private var requestedInstant: Instant? = null
    private var pendingOpenIcao: String? = null
    private var startJob: Job? = null

    /** The fetch for the current selection; cancelled when the selection moves on. */
    private var reloadJob: Job? = null

    init {
        start()
    }

    private fun start() {
        if (startJob?.isActive == true) return
        startJob = scope.launch {
            _isLoading.value = true
            _loadError.value = null
            val client = api
            if (isMockModeProvider() || client == null) {
                _loadError.value = MAPS_DEMO_UNAVAILABLE
                _isLoading.value = false
                return@launch
            }
            try {
                _range.value = client.getHistoricalRange()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _loadError.value = e.message ?: "The historical map didn't load."
                _isLoading.value = false
                return@launch
            }
            _selectedInstant.value = clamp(requestedInstant ?: latest)
            requestedInstant = null
            load()
            // A failed first payload keeps the full-screen error (with Retry) up
            _didLoadOnce.value = _payload.value != null
            if (_payload.value == null && _loadError.value == null) _loadError.value = "The historical map didn't load."
            openPendingAirport()
        }
    }

    /** Retry after a failed first load (range or payload). */
    fun retry() {
        if (_didLoadOnce.value) {
            retryReload()
        } else {
            start()
        }
    }

    /** Retry the current instant after a failed reload. */
    fun retryReload() {
        if (_selectedInstant.value != null) reload()
    }

    private fun reload() {
        reloadJob?.cancel()
        reloadJob = scope.launch { load() }
    }

    private suspend fun load() {
        val at = _selectedInstant.value ?: return
        val client = api ?: return
        val key = SlotKey(at, _lead.value)
        fun isCurrent() = _selectedInstant.value == key.at && _lead.value == key.lead
        cache[key]?.let { cached ->
            setPayload(cached)
            _isLoading.value = false
            return
        }
        _isLoading.value = true
        _loadError.value = null
        try {
            val resp = client.getHistoricalMap(at = DateTimeFormatter.ISO_INSTANT.format(at), lead = key.lead)
            if (at < now().minus(FINAL_AFTER)) store(resp, key)
            // The pilot may have stepped on while this was in flight
            if (isCurrent()) setPayload(resp)
        } catch (e: CancellationException) {
            // Superseded: the fetch that replaced this one owns isLoading
            throw e
        } catch (e: Exception) {
            if (isCurrent()) {
                // Don't leave the previous instant on screen under the new title
                _loadError.value = e.message ?: "The historical map didn't load."
                _payload.value = null
                rebuildMapPayload()
            }
        }
        if (isCurrent()) _isLoading.value = false
    }

    private fun setPayload(resp: HistoricalMapResponse) {
        _payload.value = resp
        _loadError.value = null
        _didLoadOnce.value = true
        rebuildMapPayload()
    }

    private fun rebuildMapPayload() {
        val payload = _payload.value
        val source = _source.value
        _mapPayload.value = payload?.let {
            ForecastMapResponse(
                forecastTime = it.at,
                modelInitTimes = emptyMap(),
                airports = it.airports.mapNotNull { apt -> apt.forecastAirport(source) },
            )
        }
        // Close the card when its airport isn't in the new payload rather than leave it empty
        val icao = _selectedIcao.value
        if (icao != null && payload?.airports?.any { it.icao == icao } != true) _selectedIcao.value = null
    }

    private fun store(resp: HistoricalMapResponse, key: SlotKey) {
        cache[key] = resp
        val iter = cache.keys.iterator()
        while (cache.size > LRU_CAPACITY && iter.hasNext()) {
            iter.next()
            iter.remove()
        }
    }

    // -------------------------------------------------------------------------
    // Time grid
    // -------------------------------------------------------------------------

    private val step: Duration get() = Duration.ofMinutes((_range.value?.stepMinutes ?: 30).toLong())

    /** Latest selectable instant (the server's floor of "now"); now before the range loads. */
    val latest: Instant
        get() = _range.value?.latest?.let(HistoricalTime::parse) ?: floorToStep(now())

    /** Earliest selectable instant: the oldest observation, else the oldest model. */
    val earliest: Instant
        get() = (_range.value?.earliestObservation ?: _range.value?.earliestModel)?.let(HistoricalTime::parse) ?: latest

    /** First instant with model data; days before it are METAR/TAF only. */
    val earliestModel: Instant?
        get() = _range.value?.earliestModel?.let(HistoricalTime::parse)

    private fun floorToStep(instant: Instant): Instant {
        val stepS = step.seconds
        val s = instant.epochSecond
        return Instant.ofEpochSecond(s - Math.floorMod(s, stepS))
    }

    /** Snap an instant into [earliest, latest] on the grid (web `clampState`). */
    fun clamp(instant: Instant): Instant {
        var at = instant
        if (at > latest) at = latest
        if (at < earliest) at = earliest
        return floorToStep(at)
    }

    /** Every slot of the selected UTC day, for the time menu. */
    fun timeSlots(): List<Instant> {
        val at = _selectedInstant.value ?: return emptyList()
        val start = HistoricalTime.startOfUtcDay(at)
        val count = (Duration.ofDays(1).seconds / step.seconds).toInt()
        return (0 until count).map { start.plus(step.multipliedBy(it.toLong())) }
    }

    /** Future and pre-history slots stay listed but disabled, as on the web. */
    fun isSelectable(slot: Instant): Boolean = slot <= latest && slot >= floorToStep(earliest)

    /** Whether a UTC day predates the model history (METAR/TAF only). */
    fun isObservationOnly(day: Instant): Boolean {
        val model = earliestModel ?: return true
        return HistoricalTime.startOfUtcDay(day) < HistoricalTime.startOfUtcDay(model)
    }

    fun selectInstant(instant: Instant) {
        val at = clamp(instant)
        if (at == _selectedInstant.value) return
        _selectedInstant.value = at
        reload()
    }

    /** Pick a UTC day, keeping the time of day (then clamping, so today lands on the latest slot). */
    fun selectDay(day: LocalDate) {
        val at = _selectedInstant.value ?: return
        val offset = Duration.between(HistoricalTime.startOfUtcDay(at), at)
        selectInstant(day.atStartOfDay(ZoneOffset.UTC).toInstant().plus(offset))
    }

    /** Step by ±1 slot, crossing midnight into the adjacent day. */
    fun step(delta: Int) {
        val at = _selectedInstant.value ?: return
        val next = at.plus(step.multipliedBy(delta.toLong()))
        if (isSelectable(next)) selectInstant(next)
    }

    fun canStep(delta: Int): Boolean =
        _selectedInstant.value?.let { isSelectable(it.plus(step.multipliedBy(delta.toLong()))) } ?: false

    fun selectLead(lead: Int) {
        if (lead == _lead.value || lead !in LEADS) return
        _lead.value = lead
        reload()
    }

    /** Models the server stores at a lead (a model past its map horizon isn't). */
    fun models(lead: Int): List<String> =
        _range.value?.leads?.firstOrNull { it.leadDays == lead }?.models ?: listOf("gfs", "icon", "ecmwf")

    fun selectMetric(metric: String) {
        _metric.value = metric
    }

    // -------------------------------------------------------------------------
    // Source
    // -------------------------------------------------------------------------

    /** Switch source (a pure recolour). An unavailable source doesn't switch; it says why. */
    fun selectSource(source: HistoricalSource) {
        if (!sourceAvailable(source)) {
            _notice.value = unavailableText(source)
            return
        }
        if (source == _source.value) return
        _source.value = source
        rebuildMapPayload()
    }

    fun dismissNotice() {
        _notice.value = null
    }

    /** Unknown until loaded, so nothing is greyed out before the first payload. */
    fun sourceAvailable(source: HistoricalSource): Boolean {
        val payload = _payload.value ?: return true
        if (source.isConsensus) return HistoricalSource.nwpModels.any { payload.sources[it.token]?.available == true }
        return payload.sources[source.token]?.available ?: false
    }

    /** Why a source has nothing at this instant and lead, in words. */
    fun unavailableText(source: HistoricalSource): String {
        if (source.isObserved) return "No ${source.label} reports at this time."
        if (source.isConsensus) return "No model has data at this time and lead."
        val lead = _lead.value
        return when (_payload.value?.sources?.get(source.token)?.reason) {
            "beyond_horizon" -> "${source.label} isn't stored $lead day${if (lead == 1) "" else "s"} ahead. Its map horizon is shorter."
            "no_valid_time" -> "No model sample at this time. Models are sampled every 3 h from 06Z to 18Z."
            "no_run" -> "No ${source.label} run was stored for this time and lead."
            else -> "No ${source.label} data at this time."
        }
    }

    /** One line under the pickers: why the map is empty, or what it shows. */
    fun statusLine(): String? {
        _loadError.value?.let { return "Couldn't load this time: $it" }
        val payload = _payload.value ?: return null
        val at = _selectedInstant.value
        // The previous instant stays on screen while the next one loads; say so
        if (_isLoading.value && at != null && HistoricalTime.parse(payload.at) != at) {
            return "Loading ${HistoricalTime.timeLabel(at)}… showing ${HistoricalTime.timeLabel(payload.at)}"
        }
        val source = _source.value
        if (!sourceAvailable(source)) return unavailableText(source)
        val shown = _mapPayload.value?.airports?.size ?: 0
        return "$shown airport${if (shown == 1) "" else "s"} · " + provenance(source, payload)
    }

    private fun provenance(source: HistoricalSource, payload: HistoricalMapResponse): String = when (source) {
        HistoricalSource.METAR -> "reports ≤${payload.sources["metar"]?.maxAgeMin ?: 90} min old"
        HistoricalSource.TAF -> "TAFs read at ${HistoricalTime.timeLabel(payload.at)}"
        HistoricalSource.WORST, HistoricalSource.MAJORITY -> {
            val present = HistoricalSource.nwpModels.filter { payload.sources[it.token]?.available == true }
            "${source.label.lowercase(Locale.US)} of " + present.joinToString(" · ") { it.label }
        }
        HistoricalSource.GFS, HistoricalSource.ICON, HistoricalSource.ECMWF -> {
            val info = payload.sources[source.token]
            if (info == null) {
                source.label
            } else {
                var text = "run ${HistoricalTime.runLabel(info.modelInitTime)} → ${HistoricalTime.timeLabel(info.validTime)}"
                info.leadHours?.let { text += " (+${it}h)" }
                text
            }
        }
    }

    // -------------------------------------------------------------------------
    // Selection
    // -------------------------------------------------------------------------

    fun select(icao: String) {
        _selectedIcao.value = icao
        _payload.value?.airports?.firstOrNull { it.icao == icao }?.let { apt ->
            _focusRequest.value = MapFocusRequest(apt.lat, apt.lon)
        }
    }

    fun deselect() {
        _selectedIcao.value = null
    }

    /** The tapped airport, from the full payload (it stays open when the source has nothing for it). */
    fun selectedAirport(): HistoricalMapAirport? {
        val icao = _selectedIcao.value ?: return null
        return _payload.value?.airports?.firstOrNull { it.icao == icao }
    }

    fun focusOn(lat: Double, lon: Double) {
        _focusRequest.value = MapFocusRequest(lat, lon, MapFocusRequest.webZoomForLonSpan(5.0))
    }

    fun focusApplied(request: MapFocusRequest) {
        if (_focusRequest.value == request) _focusRequest.value = null
    }

    private fun openPendingAirport() {
        val icao = pendingOpenIcao ?: return
        pendingOpenIcao = null
        if (_payload.value?.airports?.any { it.icao == icao } == true) select(icao)
    }

    // -------------------------------------------------------------------------
    // Deep links
    // -------------------------------------------------------------------------

    fun applyDeepLinkTarget(target: NavigationTarget.HistoricalMap) {
        target.lead?.takeIf { it in LEADS }?.let { _lead.value = it }
        HistoricalSource.fromToken(target.source)?.let { _source.value = it }
        target.metric?.let { _metric.value = it }
        target.view?.let { _focusRequest.value = MapFocusRequest.at(it) }
        pendingOpenIcao = target.airport
        // The linked instant applies only when both date and time are given, as on the web
        val instant = HistoricalTime.instant(target.date, target.time)
        if (_range.value == null) {
            requestedInstant = instant
            start()
            return
        }
        instant?.let { _selectedInstant.value = clamp(it) }
        scope.launch {
            reloadJob?.cancel()
            load()
            openPendingAirport()
        }
    }

    companion object {
        /** Leads the server stores (web `HISTORICAL_LEADS`). */
        val LEADS = 0..6
        private const val LRU_CAPACITY = 8

        /** Server `FINAL_AFTER`: only instants older than this are cached. */
        private val FINAL_AFTER: Duration = Duration.ofHours(2)
    }
}

/** Parsing and labels for the historical map's UTC instants (iOS `HistoricalTime`). */
object HistoricalTime {
    private fun formatter(pattern: String) = DateTimeFormatter.ofPattern(pattern, Locale.US).withZone(ZoneOffset.UTC)

    private val hhmm = formatter("HH:mm")
    private val run = formatter("dd HH")
    private val longDate = formatter("EEE d MMM yyyy")
    private val shortDate = formatter("EEE d MMM")
    private val isoDayFormatter = formatter("yyyy-MM-dd")

    /** Server timestamps are Python `isoformat()` (`+00:00`, sometimes microseconds); a naive one is UTC. */
    fun parse(iso: String?): Instant? {
        if (iso.isNullOrBlank()) return null
        return runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull()
            ?: runCatching { LocalDateTime.parse(iso).toInstant(ZoneOffset.UTC) }.getOrNull()
    }

    fun startOfUtcDay(instant: Instant): Instant =
        instant.atZone(ZoneOffset.UTC).toLocalDate().atStartOfDay(ZoneOffset.UTC).toInstant()

    /** "14:30Z"; empty when absent or unparseable. */
    fun timeLabel(iso: String?): String = parse(iso)?.let(::timeLabel) ?: ""

    fun timeLabel(instant: Instant): String = hhmm.format(instant) + "Z"

    /** "28 00Z": day-of-month + hour, the compact model-run notation. */
    fun runLabel(iso: String?): String = parse(iso)?.let { run.format(it) + "Z" } ?: ""

    /** "Mon 28 Sep 2026". */
    fun dateLabel(instant: Instant): String = longDate.format(instant)

    /** "Mon 28 Sep". */
    fun shortDateLabel(instant: Instant): String = shortDate.format(instant)

    /** "2026-09-28" (the `hist.date` token). */
    fun isoDay(instant: Instant): String = isoDayFormatter.format(instant)

    /** Rebuild an instant from `hist.date` + `hist.time` tokens; null unless both parse. */
    fun instant(date: String?, time: String?): Instant? {
        if (date == null || time == null) return null
        return runCatching {
            LocalDateTime.parse("${date}T$time").toInstant(ZoneOffset.UTC)
        }.getOrNull()
    }
}
