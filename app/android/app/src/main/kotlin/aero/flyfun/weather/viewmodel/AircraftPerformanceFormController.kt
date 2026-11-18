package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.PerformancePresetsResponse
import aero.flyfun.weather.model.rules.PerformanceFormState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Loads the presets for the aircraft form and applies the pilot's actions to
 * [PerformanceFormState]. Only the latest request may change the form.
 */
class AircraftPerformanceFormController(
    private val scope: CoroutineScope,
    private val fetch: suspend (icaoType: String, cruiseSpeedKt: Int?) -> PerformancePresetsResponse,
    private val typingDebounceMs: Long = TYPING_DEBOUNCE_MS,
) {
    private val _state = MutableStateFlow(PerformanceFormState())
    val state: StateFlow<PerformanceFormState> = _state.asStateFlow()

    private var aircraft: AircraftResponse? = null
    private var hasOpened = false
    private var loadSeq = 0
    private var loadJob: Job? = null
    private var lastQuery: Pair<String, Int?>? = null

    /**
     * Open for [aircraft], or a new one when null. [formIcaoType] and [formCruiseSpeedKt] are what
     * the sheet's fields start with, so its first [typeChanged] is not taken for an edit.
     */
    fun open(aircraft: AircraftResponse?, formIcaoType: String, formCruiseSpeedKt: Int?) {
        this.aircraft = aircraft
        hasOpened = false
        _state.value = PerformanceFormState()
        lastQuery = normalized(formIcaoType) to formCruiseSpeedKt
        // The placeholder's ZZZZ is not a type: it gets the unknown aircraft's suggestion.
        val icaoType = aircraft?.takeUnless { it.isPlaceholder }?.icaoType.orEmpty()
        load(icaoType, formCruiseSpeedKt, debounceMs = 0L)
    }

    /** The ICAO type or cruise speed field changed. */
    fun typeChanged(icaoType: String, cruiseSpeedKt: Int?) {
        val query = normalized(icaoType) to cruiseSpeedKt
        if (query == lastQuery) return
        lastQuery = query
        load(query.first, cruiseSpeedKt, typingDebounceMs)
    }

    fun pick(id: String) = _state.update { it.picked(id) }
    fun editClimbIas(text: String) = _state.update { it.copy(climbIasText = text) }
    fun editClimbRate(text: String) = _state.update { it.copy(climbRateText = text) }
    fun editDescentRate(text: String) = _state.update { it.editedDescentRate(text) }
    fun editDescentAngle(text: String) = _state.update { it.editedDescentAngle(text) }
    fun editTurbo(turbo: Boolean) = _state.update { it.copy(turbo = turbo) }

    private fun load(icaoType: String, cruiseSpeedKt: Int?, debounceMs: Long) {
        val seq = ++loadSeq
        loadJob?.cancel()
        loadJob = scope.launch {
            if (debounceMs > 0L) delay(debounceMs)
            val response = try {
                fetch(icaoType, cruiseSpeedKt)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                return@launch // The rest of the form still saves.
            }
            if (seq != loadSeq) return@launch
            if (hasOpened) {
                _state.update { it.refreshed(response) }
            } else {
                // Also after a failed open: the aircraft's own values, never a type's suggestion.
                hasOpened = true
                _state.update { it.opened(aircraft, response) }
            }
        }
    }

    private fun normalized(icaoType: String): String = icaoType.trim().uppercase()

    private companion object {
        const val TYPING_DEBOUNCE_MS = 500L
    }
}
