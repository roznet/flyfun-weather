package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.rzflight.Airport
import aero.flyfun.weather.data.AirportDatabaseService
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.TripRepository
import aero.flyfun.weather.model.AddTripLegsRequest
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AutorouterRouteOption
import aero.flyfun.weather.model.CreateFlightRequest
import aero.flyfun.weather.model.CreateTripRequest
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.FlightEditRules
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.FlightInvalidation
import aero.flyfun.weather.model.FlightProfileResponse
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.MoveFlightRequest
import aero.flyfun.weather.model.TripSummaryResponse
import aero.flyfun.weather.model.UpdateFlightRequest
import aero.flyfun.weather.model.departureInstant
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for creating, editing or duplicating a flight, mirroring iOS `AddFlightViewModel`.
 * Supports:
 * - Natural route text interpretation (`/api/flights/interpret-route`) + airport autocomplete
 * - ICAO FPL string paste parsing (`/api/flights/parse-fpl`)
 * - Great-circle distance + auto-calculated flight duration from cruise speed
 * - Autorouter route suggestions
 * - Aircraft selection (autofilling speed & ceiling) and Profile selection
 * - Flexibility (the server's timing-scenario mode, incl. a pinned alternate departure)
 * - Optional Trip assignment (existing trip or inline new trip creation)
 *
 * Editing follows the web's rules ([FlightEditRules], #552, #885): a change of UTC date or of
 * origin/destination is a different flight, so Save asks for Move or Duplicate instead of a
 * PATCH the server would refuse; any other edit is a PATCH whose `invalidation` decides
 * whether the advisories are recomputed or the briefing regenerated.
 */
class AddFlightViewModel(
    private val repository: BriefingRepository,
    private val apiClient: ApiClient? = null,
    private val tripRepository: TripRepository? = null,
    private val airportDb: AirportDatabaseService? = null,
    private val scopeOverride: CoroutineScope? = null,
    /** Outlives the sheet: the 409 refresh retry keeps going after it closes. */
    private val backgroundScope: CoroutineScope? = null,
) : ViewModel() {

    /** What the sheet is doing. */
    enum class Mode { CREATE, EDIT, DUPLICATE }

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _mode = MutableStateFlow(Mode.CREATE)
    val mode: StateFlow<Mode> = _mode.asStateFlow()

    /** The flight being edited ([Mode.EDIT]) or duplicated ([Mode.DUPLICATE]). */
    private val _originalFlight = MutableStateFlow<FlightResponse?>(null)
    val originalFlight: StateFlow<FlightResponse?> = _originalFlight.asStateFlow()

    private val _editingFlightId = MutableStateFlow<String?>(null)
    val editingFlightId: StateFlow<String?> = _editingFlightId.asStateFlow()

    private val _rawRouteText = MutableStateFlow("")
    val rawRouteText: StateFlow<String> = _rawRouteText.asStateFlow()

    private val _waypoints = MutableStateFlow<List<String>>(emptyList())
    val waypoints: StateFlow<List<String>> = _waypoints.asStateFlow()

    private val _fplText = MutableStateFlow("")
    val fplText: StateFlow<String> = _fplText.asStateFlow()

    private val _airportSuggestions = MutableStateFlow<List<Airport>>(emptyList())
    val airportSuggestions: StateFlow<List<Airport>> = _airportSuggestions.asStateFlow()

    private val _departureDate = MutableStateFlow(LocalDate.now(ZoneOffset.UTC).plusDays(1))
    val departureDate: StateFlow<LocalDate> = _departureDate.asStateFlow()

    private val _departureTime = MutableStateFlow(LocalTime.of(9, 0))
    val departureTime: StateFlow<LocalTime> = _departureTime.asStateFlow()

    private val _flexibility = MutableStateFlow(FlightFlexibility.NONE)
    val flexibility: StateFlow<FlightFlexibility> = _flexibility.asStateFlow()

    /** Time of day (UTC) of the pinned alternate departure, for [FlightFlexibility.ALTERNATE]. */
    private val _altDepartureTime = MutableStateFlow(LocalTime.of(14, 0))
    val altDepartureTime: StateFlow<LocalTime> = _altDepartureTime.asStateFlow()

    /**
     * Whether the pilot touched the alternate time. Only a real edit is sent: re-dating the
     * stored alternate onto a moved departure's day would otherwise always read as a change.
     */
    private var altDepartureEdited = false

    private val _cruiseAltitudeFt = MutableStateFlow(8000)
    val cruiseAltitudeFt: StateFlow<Int> = _cruiseAltitudeFt.asStateFlow()

    private val _flightCeilingFt = MutableStateFlow(18000)
    val flightCeilingFt: StateFlow<Int> = _flightCeilingFt.asStateFlow()

    private val _cruiseSpeedKt = MutableStateFlow(120)
    val cruiseSpeedKt: StateFlow<Int> = _cruiseSpeedKt.asStateFlow()

    private val _durationHours = MutableStateFlow(1)
    val durationHours: StateFlow<Int> = _durationHours.asStateFlow()

    private val _durationMinutes = MutableStateFlow(30)
    val durationMinutes: StateFlow<Int> = _durationMinutes.asStateFlow()

    private val _isDurationManual = MutableStateFlow(false)
    val isDurationManual: StateFlow<Boolean> = _isDurationManual.asStateFlow()

    private val _distanceNm = MutableStateFlow<Double?>(null)
    val distanceNm: StateFlow<Double?> = _distanceNm.asStateFlow()

    private val _aircraftList = MutableStateFlow<List<AircraftResponse>>(emptyList())
    val aircraftList: StateFlow<List<AircraftResponse>> = _aircraftList.asStateFlow()

    private val _selectedAircraftId = MutableStateFlow<Int?>(null)
    val selectedAircraftId: StateFlow<Int?> = _selectedAircraftId.asStateFlow()

    private val _profiles = MutableStateFlow<List<FlightProfileResponse>>(emptyList())
    val profiles: StateFlow<List<FlightProfileResponse>> = _profiles.asStateFlow()

    private val _selectedProfileId = MutableStateFlow<Int?>(null)
    val selectedProfileId: StateFlow<Int?> = _selectedProfileId.asStateFlow()

    private val _trips = MutableStateFlow<List<TripSummaryResponse>>(emptyList())
    val trips: StateFlow<List<TripSummaryResponse>> = _trips.asStateFlow()

    private val _selectedTripId = MutableStateFlow<String?>(null)
    val selectedTripId: StateFlow<String?> = _selectedTripId.asStateFlow()

    private val _newTripName = MutableStateFlow("")
    val newTripName: StateFlow<String> = _newTripName.asStateFlow()

    private val _autorouterRoutes = MutableStateFlow<List<AutorouterRouteOption>>(emptyList())
    val autorouterRoutes: StateFlow<List<AutorouterRouteOption>> = _autorouterRoutes.asStateFlow()

    private val _isFetchingRoutes = MutableStateFlow(false)
    val isFetchingRoutes: StateFlow<Boolean> = _isFetchingRoutes.asStateFlow()

    private val _isSaving = MutableStateFlow(false)
    val isSaving: StateFlow<Boolean> = _isSaving.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    /** Set when a save went through but part of it didn't (shown after the sheet closes). */
    private val _saveWarning = MutableStateFlow<String?>(null)
    val saveWarning: StateFlow<String?> = _saveWarning.asStateFlow()

    /**
     * How many briefings the edited flight has, for what Move discards. Null while unknown
     * (not loaded, or the load failed): "couldn't read it" is not "there are none".
     */
    private val _existingPackCount = MutableStateFlow<Int?>(null)
    val existingPackCount: StateFlow<Int?> = _existingPackCount.asStateFlow()

    /** True while the Move / Keep both choice is shown for a structural edit. */
    private val _showStructuralChoice = MutableStateFlow(false)
    val showStructuralChoice: StateFlow<Boolean> = _showStructuralChoice.asStateFlow()

    /** Move keeps the flight in its trip unless unticked (web default). */
    private val _keepInTripOnMove = MutableStateFlow(true)
    val keepInTripOnMove: StateFlow<Boolean> = _keepInTripOnMove.asStateFlow()

    /** Keep both (duplicate) joins the trip only when ticked (web default). */
    private val _addDuplicateToTrip = MutableStateFlow(false)
    val addDuplicateToTrip: StateFlow<Boolean> = _addDuplicateToTrip.asStateFlow()

    val totalDurationHours: Double
        get() = FlightDuration.combine(_durationHours.value, _durationMinutes.value)

    val isValid: Boolean
        get() = _waypoints.value.size >= 2 && totalDurationHours > 0.0

    /** The trip-picker applies to create and duplicate; an edit moves trips from the list. */
    val showsTripPicker: Boolean
        get() = _mode.value != Mode.EDIT

    /** Alternate needs an alt time that only PATCH can set, so it is offered on edit only. */
    val flexibilityOptions: List<FlightFlexibility>
        get() = if (_mode.value == Mode.EDIT) {
            FlightFlexibility.entries
        } else {
            FlightFlexibility.entries.filter { it != FlightFlexibility.ALTERNATE }
        }

    fun loadFormMetadata() {
        scope.launch {
            apiClient?.let { api ->
                runCatching { api.listAircraft() }.onSuccess { list ->
                    _aircraftList.value = list
                    if (_mode.value == Mode.CREATE && _selectedAircraftId.value == null) {
                        val defaultAc = list.firstOrNull { it.isDefault } ?: list.firstOrNull()
                        if (defaultAc != null) {
                            selectAircraft(defaultAc.id)
                        }
                    }
                }
                runCatching { api.listProfiles() }.onSuccess { list ->
                    _profiles.value = list
                    if (_mode.value == Mode.CREATE && _selectedProfileId.value == null) {
                        _selectedProfileId.value = list.firstOrNull { it.isDefault }?.id ?: list.firstOrNull()?.id
                    }
                }
            }
            tripRepository?.let { repo ->
                runCatching { repo.listTrips() }.onSuccess { list ->
                    _trips.value = list
                }
            }
        }
        loadPackCount()
    }

    /** Load the edited flight's pack count for the Move copy. Best-effort. */
    fun loadPackCount() {
        val flight = _originalFlight.value ?: return
        if (_mode.value != Mode.EDIT) return
        scope.launch {
            runCatching { repository.listPacks(flight.id) }
                .onSuccess { _existingPackCount.value = it.size }
        }
    }

    /** Populate the form from an existing [FlightResponse] for editing. */
    fun loadExistingFlight(flight: FlightResponse) {
        _mode.value = Mode.EDIT
        _editingFlightId.value = flight.id
        prefillFrom(flight)
        _selectedTripId.value = flight.tripId
        runCatching {
            val zdt = Instant.parse(flight.departureTime).atZone(ZoneOffset.UTC)
            _departureDate.value = zdt.toLocalDate()
            _departureTime.value = zdt.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
        }
        _flexibility.value = flight.flexibility
        flight.altDepartureTime?.let { alt ->
            runCatching {
                _altDepartureTime.value = Instant.parse(alt).atZone(ZoneOffset.UTC)
                    .toLocalTime().truncatedTo(ChronoUnit.MINUTES)
            }
        }
    }

    /**
     * Pre-fill a *new* flight from [flight] (#885 "Duplicate…"): the route as typed, altitude,
     * ceiling, duration, aircraft, profile and description. The date is left for the pilot to
     * pick: the next day by default, keeping the time of day. Not added to the trip unless the
     * pilot picks one, the same as the web's Duplicate.
     */
    fun loadDuplicateOf(flight: FlightResponse, today: LocalDate = LocalDate.now(ZoneOffset.UTC)) {
        _mode.value = Mode.DUPLICATE
        _editingFlightId.value = null
        prefillFrom(flight)
        _selectedTripId.value = null
        runCatching {
            val zdt = Instant.parse(flight.departureTime).atZone(ZoneOffset.UTC)
            _departureTime.value = zdt.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
            _departureDate.value = maxOf(zdt.toLocalDate(), today).plusDays(1)
        }
        // Alternate is edit-only; the day modes carry over.
        _flexibility.value = flight.flexibility.takeIf { it != FlightFlexibility.ALTERNATE }
            ?: FlightFlexibility.NONE
    }

    private fun prefillFrom(flight: FlightResponse) {
        _originalFlight.value = flight
        _waypoints.value = flight.waypoints
        _rawRouteText.value = FlightEditRules.baselineRouteInput(flight)
        _cruiseAltitudeFt.value = flight.cruiseAltitudeFt
        _flightCeilingFt.value = flight.flightCeilingFt
        _selectedAircraftId.value = flight.aircraftId
        _selectedProfileId.value = flight.profileId
        val parts = FlightDuration.split(flight.flightDurationHours)
        _durationHours.value = parts.hours
        _durationMinutes.value = parts.minutes
        _isDurationManual.value = true
    }

    /** Pre-populate route and date for adding a subsequent leg after a flight. */
    fun populateNextLeg(previousFlight: FlightResponse) {
        _mode.value = Mode.CREATE
        _editingFlightId.value = null
        val lastWp = previousFlight.waypoints.lastOrNull()
        if (lastWp != null) {
            _waypoints.value = listOf(lastWp)
            _rawRouteText.value = "$lastWp "
        }
        _selectedAircraftId.value = previousFlight.aircraftId
        _selectedProfileId.value = previousFlight.profileId
        _selectedTripId.value = previousFlight.tripId
        _cruiseAltitudeFt.value = previousFlight.cruiseAltitudeFt
        _flightCeilingFt.value = previousFlight.flightCeilingFt
        runCatching {
            val prevDep = Instant.parse(previousFlight.departureTime).atZone(ZoneOffset.UTC)
            val nextDep = prevDep.plusMinutes((previousFlight.flightDurationHours * 60).toLong() + 60)
            _departureDate.value = nextDep.toLocalDate()
            _departureTime.value = nextDep.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
        }
    }

    // MARK: - Edit rules

    /** The departure the form holds, as a UTC instant. */
    val departureInstant: Instant
        get() = ZonedDateTime.of(_departureDate.value, _departureTime.value, ZoneOffset.UTC).toInstant()

    /** Whether the route text differs from the flight being edited or duplicated. */
    val routeChangedFromOriginal: Boolean
        get() {
            val original = _originalFlight.value ?: return true
            return FlightEditRules.routeInputChanged(_rawRouteText.value, original)
        }

    /** The picker rounds durations to the quarter hour, so compare against that rounding. */
    private val durationChangedFromOriginal: Boolean
        get() {
            val original = _originalFlight.value ?: return true
            return FlightDuration.split(original.flightDurationHours) !=
                FlightDuration.Parts(_durationHours.value, _durationMinutes.value)
        }

    private fun editedValues(): FlightEditRules.EditedValues = FlightEditRules.EditedValues(
        routeInput = _rawRouteText.value,
        waypoints = _waypoints.value,
        departure = departureInstant,
        cruiseAltitudeFt = _cruiseAltitudeFt.value,
        flightCeilingFt = _flightCeilingFt.value,
        durationChanged = durationChangedFromOriginal,
        profileId = _selectedProfileId.value,
    )

    /** What saving this edit does to the briefing; null unless editing. */
    val editImpact: FlightEditRules.Impact?
        get() {
            val original = _originalFlight.value ?: return null
            if (_mode.value != Mode.EDIT) return null
            return FlightEditRules.impact(editedValues(), original)
        }

    /** True when the edit changes the flight's identity (UTC date or route endpoints). */
    val hasStructuralChange: Boolean
        get() = editImpact == FlightEditRules.Impact.STRUCTURAL

    /** Any edited field differs from the original (gates the Save button on edit). */
    val hasChanges: Boolean
        get() {
            val original = _originalFlight.value ?: return true
            if (_mode.value != Mode.EDIT) return true
            if (editImpact != FlightEditRules.Impact.NONE) return true
            if (_selectedAircraftId.value != null && _selectedAircraftId.value != original.aircraftId) return true
            if (_flexibility.value != original.flexibility) return true
            return _flexibility.value == FlightFlexibility.ALTERNATE && altDepartureEdited
        }

    /** Whether the flight being edited is in its live window, for the Move warning. */
    fun isLive(nowEpochMs: Long = System.currentTimeMillis()): Boolean =
        _originalFlight.value?.isInLiveObservationWindow(nowEpochMs) == true

    // MARK: - Form inputs

    fun updateRouteText(text: String) {
        _rawRouteText.value = text
        val tokens = text.trim()
            .uppercase()
            .split(FlightEditRules.ROUTE_SEPARATORS)
            .filter { it.length >= 2 }
        _waypoints.value = tokens

        // Update airport autocomplete for the last token
        val lastToken = tokens.lastOrNull()
        if (lastToken != null && lastToken.length >= 2 && airportDb != null) {
            _airportSuggestions.value = airportDb.search(lastToken, limit = 8)
        } else {
            _airportSuggestions.value = emptyList()
        }

        if (tokens.size >= 2) {
            recomputeDistanceAndDuration(tokens)
        } else {
            _distanceNm.value = null
        }
    }

    fun selectAirportSuggestion(airport: Airport) {
        val currentTokens = _rawRouteText.value.trim()
            .uppercase()
            .split(FlightEditRules.ROUTE_SEPARATORS)
            .filter { it.isNotBlank() }
            .toMutableList()
        if (currentTokens.isEmpty()) {
            currentTokens.add(airport.icao)
        } else {
            currentTokens[currentTokens.lastIndex] = airport.icao
        }
        val updated = currentTokens.joinToString(" ") + " "
        _rawRouteText.value = updated
        _waypoints.value = currentTokens
        _airportSuggestions.value = emptyList()
        if (currentTokens.size >= 2) {
            recomputeDistanceAndDuration(currentTokens)
        }
    }

    /**
     * Resolve the typed route on the server and show the waypoints it keeps. The text stays as
     * typed: it is what is sent as `raw_route`.
     */
    fun interpretRouteFromServer() {
        val text = _rawRouteText.value.trim()
        if (text.isBlank()) return
        scope.launch {
            runCatching { repository.interpretRoute(text) }
                .onSuccess { resp ->
                    if (resp.interpreted.isNotEmpty()) {
                        _waypoints.value = resp.interpreted
                        if (resp.interpreted.size >= 2) recomputeDistanceAndDuration(resp.interpreted)
                    }
                    if (resp.skipped.isNotEmpty()) {
                        _errorMessage.value = "Not recognised: ${resp.skipped.joinToString(" ")}"
                    }
                }.onFailure { err ->
                    _errorMessage.value = err.message ?: "Could not interpret route"
                }
        }
    }

    fun updateFplText(text: String) {
        _fplText.value = text
    }

    fun parseIcaoFpl() {
        val fpl = _fplText.value.trim()
        if (fpl.isBlank()) return
        scope.launch {
            runCatching {
                repository.parseFpl(fpl)
            }.onSuccess { resp ->
                resp.error?.takeIf { it.isNotBlank() }?.let {
                    _errorMessage.value = it
                    return@onSuccess
                }
                if (resp.waypoints.isNotEmpty()) {
                    _waypoints.value = resp.waypoints
                    _rawRouteText.value = resp.rawRoute?.takeIf { it.isNotBlank() }
                        ?: resp.waypoints.joinToString(" ")
                    if (resp.waypoints.size >= 2) recomputeDistanceAndDuration(resp.waypoints)
                }
                resp.altitudeFt?.let { _cruiseAltitudeFt.value = it }
                resp.durationHours?.let { dur ->
                    val parts = FlightDuration.split(FlightDuration.clampToPickerRange(dur))
                    _durationHours.value = parts.hours
                    _durationMinutes.value = parts.minutes
                    _isDurationManual.value = true
                }
                resp.departureTime?.let { depStr ->
                    runCatching {
                        val zdt = Instant.parse(depStr).atZone(ZoneOffset.UTC)
                        _departureDate.value = zdt.toLocalDate()
                        _departureTime.value = zdt.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
                    }
                }
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to parse ICAO flight plan"
            }
        }
    }

    fun fetchAutorouterSuggestions() {
        val wps = _waypoints.value
        if (wps.size < 2 || apiClient == null) return
        _isFetchingRoutes.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                apiClient.fetchAutorouterRoutes(
                    departure = wps.first(),
                    destination = wps.last(),
                    departureTime = buildDepartureIso(),
                    cruiseAltitudeFt = _cruiseAltitudeFt.value,
                    durationHours = totalDurationHours,
                    aircraftId = _selectedAircraftId.value,
                )
            }.onSuccess { resp ->
                _autorouterRoutes.value = resp.routes
                _isFetchingRoutes.value = false
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Autorouter route fetch failed"
                _isFetchingRoutes.value = false
            }
        }
    }

    fun applyAutorouterRoute(option: AutorouterRouteOption) {
        if (option.waypoints.isNotEmpty()) {
            _waypoints.value = option.waypoints
            _rawRouteText.value = option.waypoints.joinToString(" ")
        }
        option.distanceNm?.let { _distanceNm.value = it }
        option.durationHours?.let { dur ->
            val parts = FlightDuration.split(FlightDuration.clampToPickerRange(dur))
            _durationHours.value = parts.hours
            _durationMinutes.value = parts.minutes
        }
    }

    fun selectAircraft(aircraftId: Int?) {
        _selectedAircraftId.value = aircraftId
        val ac = _aircraftList.value.firstOrNull { it.id == aircraftId } ?: return
        ac.cruiseSpeedKt?.let { speed ->
            _cruiseSpeedKt.value = speed
            if (_waypoints.value.size >= 2 && !_isDurationManual.value) {
                recomputeDistanceAndDuration(_waypoints.value)
            }
        }
        ac.ceilingFt?.let { ceiling ->
            _flightCeilingFt.value = ceiling
            if (_cruiseAltitudeFt.value > ceiling) {
                _cruiseAltitudeFt.value = ceiling
            }
        }
    }

    fun selectProfile(profileId: Int?) {
        _selectedProfileId.value = profileId
        val profile = _profiles.value.firstOrNull { it.id == profileId } ?: return
        profile.settings.cruiseAltitudeFt?.let { _cruiseAltitudeFt.value = it }
        profile.settings.flightCeilingFt?.let { _flightCeilingFt.value = it }
        profile.settings.speedKt?.let { _cruiseSpeedKt.value = it }
    }

    fun selectTrip(tripId: String?) {
        _selectedTripId.value = tripId
    }

    fun setNewTripName(name: String) {
        _newTripName.value = name
    }

    fun setDepartureDate(date: LocalDate) {
        _departureDate.value = date
    }

    fun setDepartureTime(time: LocalTime) {
        _departureTime.value = time
    }

    fun setFlexibility(value: FlightFlexibility) {
        _flexibility.value = value
    }

    fun setAltDepartureTime(time: LocalTime) {
        _altDepartureTime.value = time
        altDepartureEdited = true
    }

    fun setCruiseAltitudeFt(altitudeFt: Int) {
        _cruiseAltitudeFt.value = altitudeFt.coerceIn(1000, 45000)
        if (_flightCeilingFt.value < _cruiseAltitudeFt.value) {
            _flightCeilingFt.value = _cruiseAltitudeFt.value
        }
    }

    fun setFlightCeilingFt(ceilingFt: Int) {
        _flightCeilingFt.value = ceilingFt.coerceIn(_cruiseAltitudeFt.value, 45000)
    }

    fun setDuration(hours: Int, minutes: Int, manual: Boolean = true) {
        _durationHours.value = hours.coerceIn(0, 12)
        _durationMinutes.value = minutes.coerceIn(0, 55)
        _isDurationManual.value = manual
    }

    fun setKeepInTripOnMove(keep: Boolean) {
        _keepInTripOnMove.value = keep
    }

    fun setAddDuplicateToTrip(add: Boolean) {
        _addDuplicateToTrip.value = add
    }

    fun dismissStructuralChoice() {
        _showStructuralChoice.value = false
    }

    fun buildDepartureIso(): String = iso(departureInstant)

    /** The alternate on the departure's UTC day, which the server requires. */
    fun buildAltDepartureIso(): String {
        val alt = ZonedDateTime.of(_departureDate.value, _altDepartureTime.value, ZoneOffset.UTC).toInstant()
        return iso(FlightEditRules.alignUtcDay(alt, departureInstant))
    }

    /**
     * The `alt_departure_time` a PATCH sends: the pinned time in alternate mode, `""` when
     * leaving it (to clear the stale value), and nothing otherwise, so a day-scan flight's
     * "your alternate" is not wiped by an unrelated edit.
     */
    private fun altDepartureTimePayload(original: FlightResponse): String? = when {
        _flexibility.value == FlightFlexibility.ALTERNATE -> buildAltDepartureIso()
        original.flexibility == FlightFlexibility.ALTERNATE -> ""
        else -> null
    }

    // MARK: - Save

    /**
     * Save the form. Create and duplicate post a new flight. An edit PATCHes, unless it
     * changes the flight's identity: then the Move / Keep both choice is shown instead.
     */
    fun saveFlight(onSaved: (FlightResponse) -> Unit = {}) {
        if (!isValid) {
            _errorMessage.value = "Please enter at least 2 waypoints (departure and destination)."
            return
        }
        if (_mode.value == Mode.EDIT && hasStructuralChange) {
            _showStructuralChoice.value = true
            return
        }
        submit(onSaved) {
            when (_mode.value) {
                Mode.EDIT -> saveEdit()
                Mode.CREATE, Mode.DUPLICATE -> createNew(tripId = resolveTripForCreate())
            }
        }
    }

    /** Replace the flight with the edited one, discarding its briefings (structural edit). */
    fun moveFlight(onSaved: (FlightResponse) -> Unit = {}) {
        _showStructuralChoice.value = false
        submit(onSaved) { move() }
    }

    /** Keep the original and create a new flight from the edited values (structural edit). */
    fun duplicateEditedFlight(onSaved: (FlightResponse) -> Unit = {}) {
        _showStructuralChoice.value = false
        val tripId = _originalFlight.value?.tripId?.takeIf { _addDuplicateToTrip.value }
        submit(onSaved) { createNew(tripId = tripId) }
    }

    private fun submit(onSaved: (FlightResponse) -> Unit, block: suspend () -> FlightResponse) {
        _isSaving.value = true
        _errorMessage.value = null
        _saveWarning.value = null
        scope.launch {
            try {
                val saved = block()
                _isSaving.value = false
                onSaved(saved)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                _isSaving.value = false
                _errorMessage.value = submitErrorMessage(e)
            }
        }
    }

    /** The waypoints and `raw_route` a request carries. */
    private data class RoutePayload(val waypoints: List<String>, val rawRoute: String?, val changed: Boolean)

    /**
     * An untouched route keeps the flight's stored waypoints and Field-15 annotation (no
     * `raw_route` on PATCH, so `parser_version` keeps its meaning). A changed or new route
     * is resolved by the server and sent with the text as typed.
     */
    private suspend fun resolveRoute(): RoutePayload {
        val original = _originalFlight.value
        if (original != null && !routeChangedFromOriginal) {
            return RoutePayload(original.waypoints, original.rawRoute, changed = false)
        }
        val text = _rawRouteText.value.trim()
        // A failed check is a failed save: the local token split is not a resolved route.
        val resolved = try {
            repository.interpretRoute(text)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            throw IllegalStateException("Couldn't check the route with the server. ${e.message ?: ""}".trim(), e)
        }
        if (resolved.skipped.isNotEmpty()) {
            throw IllegalArgumentException("Not recognised in the route: ${resolved.skipped.joinToString(" ")}")
        }
        val waypoints = resolved.interpreted.takeIf { it.size >= 2 }
            ?: throw IllegalArgumentException("The route needs a departure and a destination.")
        _waypoints.value = waypoints
        return RoutePayload(waypoints, text, changed = true)
    }

    private suspend fun resolveTripForCreate(): String? {
        val picked = _selectedTripId.value
        val name = _newTripName.value.trim()
        if (picked == null && name.isNotBlank() && tripRepository != null) {
            return tripRepository.createTrip(CreateTripRequest(name = name, flightIds = emptyList())).id
        }
        return picked
    }

    private suspend fun createNew(tripId: String?): FlightResponse {
        val route = resolveRoute()
        val source = _originalFlight.value
        val created = repository.createFlight(
            CreateFlightRequest(
                waypoints = route.waypoints,
                rawRoute = route.rawRoute,
                description = source?.description,
                departureTime = buildDepartureIso(),
                cruiseAltitudeFt = _cruiseAltitudeFt.value,
                flightCeilingFt = _flightCeilingFt.value,
                flightDurationHours = totalDurationHours,
                profileId = _selectedProfileId.value,
                aircraftId = _selectedAircraftId.value,
                flexibility = _flexibility.value.takeIf {
                    it != FlightFlexibility.NONE && it != FlightFlexibility.ALTERNATE
                },
                tripId = tripId,
            ),
        )
        // Create refuses "alternate"; restore it with the same create-then-patch the web does.
        if (_flexibility.value != FlightFlexibility.ALTERNATE) return created
        return try {
            repository.updateFlight(
                created.id,
                UpdateFlightRequest(
                    flexibility = FlightFlexibility.ALTERNATE,
                    altDepartureTime = buildAltDepartureIso(),
                ),
            ).flight
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            _saveWarning.value = "The flight was created, but the alternate time didn't save. Open Edit to apply it again."
            created
        }
    }

    private suspend fun saveEdit(): FlightResponse {
        val original = _originalFlight.value ?: error("No flight to edit")
        val route = resolveRoute()
        val aircraftId = _selectedAircraftId.value?.takeIf { it != original.aircraftId }
        val request = UpdateFlightRequest(
            waypoints = route.waypoints.takeIf { route.changed },
            rawRoute = route.rawRoute.takeIf { route.changed },
            departureTime = buildDepartureIso().takeIf { departureInstant != original.departureInstant },
            cruiseAltitudeFt = _cruiseAltitudeFt.value.takeIf { it != original.cruiseAltitudeFt },
            flightCeilingFt = _flightCeilingFt.value.takeIf { it != original.flightCeilingFt },
            flightDurationHours = totalDurationHours.takeIf { durationChangedFromOriginal },
            profileId = _selectedProfileId.value?.takeIf { it != original.profileId },
            aircraftId = aircraftId,
            flexibility = _flexibility.value.takeIf {
                it != original.flexibility || (it == FlightFlexibility.ALTERNATE && altDepartureEdited)
            },
            altDepartureTime = altDepartureTimePayload(original).takeIf {
                _flexibility.value != original.flexibility || altDepartureEdited
            },
        )
        val response = repository.updateFlight(original.id, request)
        applyInvalidation(response.flight, response.invalidation)
        return response.flight
    }

    private suspend fun move(): FlightResponse {
        val original = _originalFlight.value ?: error("No flight to move")
        val route = resolveRoute()
        val moved = repository.moveFlight(
            original.id,
            MoveFlightRequest(
                departureTime = buildDepartureIso(),
                // Omitted on an untouched route: the server then keeps its raw_route too.
                waypoints = route.waypoints.takeIf { route.changed },
                cruiseAltitudeFt = _cruiseAltitudeFt.value,
                flightCeilingFt = _flightCeilingFt.value,
                flightDurationHours = if (durationChangedFromOriginal) totalDurationHours else original.flightDurationHours,
                rawRoute = route.rawRoute.takeIf { route.changed },
                keepInTrip = original.tripId == null || _keepInTripOnMove.value,
            ),
        )
        return applyResidualEdits(original, moved)
    }

    /**
     * `/move` has no aircraft, profile or Flexibility field: send what the pilot changed there
     * as a PATCH on the moved flight. Retried once for a transient failure; the move itself has
     * already committed, so a failure is reported rather than undoing it.
     */
    private suspend fun applyResidualEdits(original: FlightResponse, moved: FlightResponse): FlightResponse {
        val flexChanged = _flexibility.value != original.flexibility
        val altChanged = _flexibility.value == FlightFlexibility.ALTERNATE && altDepartureEdited
        val request = UpdateFlightRequest(
            aircraftId = _selectedAircraftId.value?.takeIf { it != original.aircraftId },
            profileId = _selectedProfileId.value?.takeIf { it != original.profileId },
            flexibility = _flexibility.value.takeIf { flexChanged },
            altDepartureTime = altDepartureTimePayload(original).takeIf { flexChanged || altChanged },
        )
        if (request == UpdateFlightRequest()) return moved
        repeat(2) { attempt ->
            try {
                return repository.updateFlight(moved.id, request).flight
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                if (attempt == 0 && isWorthRetrying(e)) {
                    delay(RESIDUAL_RETRY_MS)
                } else {
                    _saveWarning.value = residualFailureMessage(request)
                    return moved
                }
            }
        }
        return moved
    }

    /**
     * Act on the server's `invalidation` after a PATCH, as iOS does: advisories are recomputed
     * in place (fast), a refetch is *queued* (`POST /packs/refresh`, 202) so the sheet can close
     * while the server works; the briefing screen adopts the running refresh when it reloads.
     */
    private suspend fun applyInvalidation(flight: FlightResponse, invalidation: FlightInvalidation) {
        when (invalidation) {
            FlightInvalidation.NONE -> Unit
            FlightInvalidation.ADVISORIES_ONLY -> {
                val pack = runCatching { repository.latestPack(flight.id) }.getOrNull()
                if (pack == null) {
                    queueRefresh(flight.id)
                } else {
                    runCatching { repository.recalculateAdvisories(flight.id, pack.fetchTimestamp) }
                        .onFailure { _saveWarning.value = BRIEFING_NOT_UPDATED_WARNING }
                }
            }
            FlightInvalidation.REFETCH_NEEDED -> queueRefresh(flight.id)
        }
    }

    /**
     * Queue a refresh. A 409 means one is already running on the *old* values, so retry in the
     * background until it clears (the briefing's own ↻ is the backstop if it never does). Any
     * other failure means no refresh is coming, so the pilot is told to use ↻.
     */
    private suspend fun queueRefresh(flightId: String) {
        val first = runCatching { repository.triggerRefresh(flightId) }
        if (first.isSuccess) return
        if (!isConflict(first.exceptionOrNull())) {
            _saveWarning.value = BRIEFING_NOT_UPDATED_WARNING
            return
        }
        (backgroundScope ?: scope).launch {
            repeat(QUEUE_REFRESH_ATTEMPTS) {
                delay(QUEUE_REFRESH_RETRY_MS)
                val retry = runCatching { repository.triggerRefresh(flightId) }
                if (retry.isSuccess || !isConflict(retry.exceptionOrNull())) return@launch
            }
        }
    }

    fun clearError() {
        _errorMessage.value = null
    }

    private fun recomputeDistanceAndDuration(tokens: List<String>) {
        // First try local airport DB for instant great-circle calculation
        val localAirports = tokens.mapNotNull { airportDb?.lookup(it) }
        if (localAirports.size == tokens.size && localAirports.size >= 2) {
            var totalNm = 0.0
            for (i in 0 until localAirports.size - 1) {
                totalNm += localAirports[i].coord.distanceNmTo(localAirports[i + 1].coord)
            }
            _distanceNm.value = totalNm
            updateDurationFromDistance(totalNm)
            return
        }

        // Fall back to server route-distance endpoint
        val api = apiClient ?: return
        scope.launch {
            runCatching {
                api.routeDistance(tokens)
            }.onSuccess { resp ->
                _distanceNm.value = resp.totalDistanceNm
                updateDurationFromDistance(resp.totalDistanceNm)
            }
        }
    }

    private fun updateDurationFromDistance(distanceNm: Double) {
        if (_isDurationManual.value) return
        val speed = _cruiseSpeedKt.value.coerceAtLeast(40)
        // Add 15 minutes (0.25h) taxi/climb allowance
        val estHours = FlightDuration.clampToPickerRange((distanceNm / speed) + 0.25)
        val parts = FlightDuration.split(estHours)
        _durationHours.value = parts.hours
        _durationMinutes.value = parts.minutes
    }

    companion object {
        private const val QUEUE_REFRESH_ATTEMPTS = 6
        private const val QUEUE_REFRESH_RETRY_MS = 30_000L
        private const val RESIDUAL_RETRY_MS = 2_000L

        /** Names what the follow-up PATCH after a move carried, for the failure warning. */
        fun residualFailureMessage(request: UpdateFlightRequest): String {
            val parts = listOfNotNull(
                "aircraft".takeIf { request.aircraftId != null },
                "profile".takeIf { request.profileId != null },
                "flexibility".takeIf { request.flexibility != null || request.altDepartureTime != null },
            )
            val what = when (parts.size) {
                0, 1 -> parts.firstOrNull() ?: "edit"
                else -> parts.dropLast(1).joinToString(", ") + " and " + parts.last()
            }
            return "The flight moved, but the $what change didn't save. Open Edit to apply it again."
        }

        private fun iso(instant: Instant): String = DateTimeFormatter.ISO_INSTANT.format(instant)

        const val BRIEFING_NOT_UPDATED_WARNING =
            "The flight saved, but its briefing didn't update. Tap ↻ on the briefing to refresh it."

        private fun isConflict(error: Throwable?): Boolean =
            (error as? ApiError.ServerError)?.statusCode == 409

        /** A 4xx is the server's considered answer to this body; only 429 means "later". */
        fun isWorthRetrying(error: Throwable): Boolean = when (error) {
            is ApiError.Unauthorized, is ApiError.Forbidden, is ApiError.NotFound -> false
            is ApiError.ServerError -> error.statusCode !in 400..499 || error.statusCode == 429
            else -> true
        }

        /** Pilot-facing copy for a failed save; the server's `detail` reads well verbatim. */
        fun submitErrorMessage(error: Throwable): String = when {
            error is ApiError.ServerError && error.statusCode == 409 ->
                "You already have a flight on that date for this route. Pick a different time, or edit the existing flight."
            else -> error.message ?: "Failed to save flight"
        }
    }
}
