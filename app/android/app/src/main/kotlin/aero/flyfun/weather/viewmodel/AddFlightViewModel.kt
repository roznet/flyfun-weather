package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.rzflight.Airport
import aero.flyfun.weather.data.AirportDatabaseService
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.TripRepository
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AutorouterRouteOption
import aero.flyfun.weather.model.CreateFlightRequest
import aero.flyfun.weather.model.CreateTripRequest
import aero.flyfun.weather.model.FlexibilityMode
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.FlightProfileResponse
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.TripSummaryResponse
import aero.flyfun.weather.model.UpdateFlightRequest
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for creating or editing a flight, mirroring iOS `AddFlightViewModel`.
 * Supports:
 * - Natural route text interpretation (`/api/flights/interpret-route`) + airport autocomplete
 * - ICAO FPL string paste parsing (`/api/flights/parse-fpl`)
 * - Great-circle distance + auto-calculated flight duration from cruise speed
 * - Autorouter route suggestions
 * - Aircraft selection (autofilling speed & ceiling) and Profile selection
 * - Flexibility mode (Fixed time, Day window, Multi-day window)
 * - Optional Trip assignment (existing trip or inline new trip creation)
 */
class AddFlightViewModel(
    private val repository: BriefingRepository,
    private val apiClient: ApiClient? = null,
    private val tripRepository: TripRepository? = null,
    private val airportDb: AirportDatabaseService? = null,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

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

    private val _flexibilityMode = MutableStateFlow(FlexibilityMode.FIXED)
    val flexibilityMode: StateFlow<FlexibilityMode> = _flexibilityMode.asStateFlow()

    private val _windowEndTime = MutableStateFlow(LocalTime.of(15, 0))
    val windowEndTime: StateFlow<LocalTime> = _windowEndTime.asStateFlow()

    private val _windowEndDate = MutableStateFlow(LocalDate.now(ZoneOffset.UTC).plusDays(2))
    val windowEndDate: StateFlow<LocalDate> = _windowEndDate.asStateFlow()

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

    val totalDurationHours: Double
        get() = FlightDuration.combine(_durationHours.value, _durationMinutes.value)

    val isValid: Boolean
        get() = _waypoints.value.size >= 2 && totalDurationHours > 0.0

    fun loadFormMetadata() {
        scope.launch {
            apiClient?.let { api ->
                runCatching { api.listAircraft() }.onSuccess { list ->
                    _aircraftList.value = list
                    if (_editingFlightId.value == null && _selectedAircraftId.value == null) {
                        val defaultAc = list.firstOrNull { it.isDefault } ?: list.firstOrNull()
                        if (defaultAc != null) {
                            selectAircraft(defaultAc.id)
                        }
                    }
                }
                runCatching { api.listProfiles() }.onSuccess { list ->
                    _profiles.value = list
                    if (_editingFlightId.value == null && _selectedProfileId.value == null) {
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
    }

    /** Populate the form from an existing [FlightResponse] for editing. */
    fun loadExistingFlight(flight: FlightResponse) {
        _editingFlightId.value = flight.id
        _waypoints.value = flight.waypoints
        _rawRouteText.value = flight.waypoints.joinToString(" ")
        _cruiseAltitudeFt.value = flight.cruiseAltitudeFt
        _flightCeilingFt.value = flight.flightCeilingFt
        _selectedAircraftId.value = flight.aircraftId
        _selectedProfileId.value = flight.profileId
        _selectedTripId.value = flight.tripId

        val parts = FlightDuration.split(flight.flightDurationHours)
        _durationHours.value = parts.hours
        _durationMinutes.value = parts.minutes
        _isDurationManual.value = true

        runCatching {
            val zdt = Instant.parse(flight.departureTime).atZone(ZoneOffset.UTC)
            _departureDate.value = zdt.toLocalDate()
            _departureTime.value = zdt.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
        }

        if (flight.departureWindowEnd != null) {
            runCatching {
                val endZdt = Instant.parse(flight.departureWindowEnd).atZone(ZoneOffset.UTC)
                _windowEndDate.value = endZdt.toLocalDate()
                _windowEndTime.value = endZdt.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
                _flexibilityMode.value = if (endZdt.toLocalDate() != _departureDate.value) {
                    FlexibilityMode.MULTI_DAY
                } else {
                    FlexibilityMode.DAY
                }
            }
        } else {
            _flexibilityMode.value = FlexibilityMode.FIXED
        }
    }

    /** Pre-populate route and date for adding a subsequent leg after a flight. */
    fun populateNextLeg(previousFlight: FlightResponse) {
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

    fun updateRouteText(text: String) {
        _rawRouteText.value = text
        val tokens = text.trim()
            .uppercase()
            .split(Regex("[\\s,\\->]+"))
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
            .split(Regex("[\\s,\\->]+"))
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

    fun interpretRouteFromServer() {
        val text = _rawRouteText.value.trim()
        if (text.isBlank()) return
        scope.launch {
            runCatching {
                apiClient?.interpretRoute(text, _cruiseSpeedKt.value.toDouble())
            }.onSuccess { resp ->
                if (resp != null && resp.waypoints.isNotEmpty()) {
                    val codes = resp.waypoints.map { it.code }
                    _waypoints.value = codes
                    _rawRouteText.value = codes.joinToString(" ")
                    _distanceNm.value = resp.totalDistanceNm
                    resp.estimatedDurationHours?.let { est ->
                        if (!_isDurationManual.value) {
                            val parts = FlightDuration.split(FlightDuration.clampToPickerRange(est))
                            _durationHours.value = parts.hours
                            _durationMinutes.value = parts.minutes
                        }
                    }
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
                apiClient?.parseFpl(fpl)
            }.onSuccess { resp ->
                if (resp != null) {
                    if (resp.waypoints.isNotEmpty()) {
                        _waypoints.value = resp.waypoints
                        _rawRouteText.value = resp.waypoints.joinToString(" ")
                    }
                    resp.altitudeFt?.let { _cruiseAltitudeFt.value = it }
                    resp.speedKt?.let { _cruiseSpeedKt.value = it }
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
        if (_windowEndDate.value.isBefore(date)) {
            _windowEndDate.value = date.plusDays(1)
        }
    }

    fun setDepartureTime(time: LocalTime) {
        _departureTime.value = time
    }

    fun setFlexibilityMode(mode: FlexibilityMode) {
        _flexibilityMode.value = mode
    }

    fun setWindowEndTime(time: LocalTime) {
        _windowEndTime.value = time
    }

    fun setWindowEndDate(date: LocalDate) {
        _windowEndDate.value = date
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

    fun buildDepartureIso(): String {
        val zdt = ZonedDateTime.of(_departureDate.value, _departureTime.value, ZoneOffset.UTC)
        return zdt.format(DateTimeFormatter.ISO_INSTANT)
    }

    fun buildDepartureWindowEndIso(): String? {
        return when (_flexibilityMode.value) {
            FlexibilityMode.FIXED -> null
            FlexibilityMode.DAY -> {
                val zdt = ZonedDateTime.of(_departureDate.value, _windowEndTime.value, ZoneOffset.UTC)
                zdt.format(DateTimeFormatter.ISO_INSTANT)
            }
            FlexibilityMode.MULTI_DAY -> {
                val zdt = ZonedDateTime.of(_windowEndDate.value, _windowEndTime.value, ZoneOffset.UTC)
                zdt.format(DateTimeFormatter.ISO_INSTANT)
            }
        }
    }

    fun saveFlight(onSaved: (FlightResponse) -> Unit = {}) {
        if (!isValid) {
            _errorMessage.value = "Please enter at least 2 waypoints (departure and destination)."
            return
        }
        _isSaving.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                var targetTripId = _selectedTripId.value
                val customTripName = _newTripName.value.trim()
                if (targetTripId == null && customTripName.isNotBlank() && tripRepository != null) {
                    val createdTrip = tripRepository.createTrip(
                        CreateTripRequest(name = customTripName, flightIds = emptyList()),
                    )
                    targetTripId = createdTrip.id
                }

                val editingId = _editingFlightId.value
                if (editingId != null) {
                    repository.updateFlight(
                        id = editingId,
                        req = UpdateFlightRequest(
                            waypoints = _waypoints.value,
                            departureTime = buildDepartureIso(),
                            departureWindowEnd = buildDepartureWindowEndIso(),
                            cruiseAltitudeFt = _cruiseAltitudeFt.value,
                            flightCeilingFt = _flightCeilingFt.value,
                            flightDurationHours = totalDurationHours,
                            profileId = _selectedProfileId.value,
                            aircraftId = _selectedAircraftId.value,
                            tripId = targetTripId,
                        ),
                    ).flight
                } else {
                    repository.createFlight(
                        CreateFlightRequest(
                            waypoints = _waypoints.value,
                            departureTime = buildDepartureIso(),
                            departureWindowEnd = buildDepartureWindowEndIso(),
                            cruiseAltitudeFt = _cruiseAltitudeFt.value,
                            flightCeilingFt = _flightCeilingFt.value,
                            flightDurationHours = totalDurationHours,
                            profileId = _selectedProfileId.value,
                            aircraftId = _selectedAircraftId.value,
                            tripId = targetTripId,
                        ),
                    )
                }
            }.onSuccess { saved ->
                _isSaving.value = false
                onSaved(saved)
            }.onFailure { err ->
                _isSaving.value = false
                _errorMessage.value = err.message ?: "Failed to save flight"
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
}
