package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.TripRepository
import aero.flyfun.weather.model.CreateTripRequest
import aero.flyfun.weather.model.FlightGroup
import aero.flyfun.weather.model.FlightOrder
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FlightSection
import aero.flyfun.weather.model.MoveFlightRequest
import aero.flyfun.weather.model.SharedFlightCodeResponse
import aero.flyfun.weather.model.SharedFlightPreview
import aero.flyfun.weather.model.TripSummaryResponse
import aero.flyfun.weather.model.groupFlights
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for the main flight list screen, mirroring iOS `FlightListViewModel`.
 * Handles flight loading, section grouping (Upcoming, Recent, Past), sort order,
 * multi-select bulk actions (delete, move to trip, create trip), and shared flight preview.
 */
class FlightListViewModel(
    private var repository: BriefingRepository,
    private var tripRepository: TripRepository? = null,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _flights = MutableStateFlow<List<FlightResponse>>(emptyList())
    val flights: StateFlow<List<FlightResponse>> = _flights.asStateFlow()

    private val _trips = MutableStateFlow<List<TripSummaryResponse>>(emptyList())
    val trips: StateFlow<List<TripSummaryResponse>> = _trips.asStateFlow()

    private val _groups = MutableStateFlow<List<FlightGroup>>(emptyList())
    val groups: StateFlow<List<FlightGroup>> = _groups.asStateFlow()

    private val _order = MutableStateFlow(FlightOrder.SOONEST_FIRST)
    val order: StateFlow<FlightOrder> = _order.asStateFlow()

    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery.asStateFlow()

    private val _collapsedSections = MutableStateFlow<Set<FlightSection>>(setOf(FlightSection.PAST))
    val collapsedSections: StateFlow<Set<FlightSection>> = _collapsedSections.asStateFlow()

    private val _isSelectionMode = MutableStateFlow(false)
    val isSelectionMode: StateFlow<Boolean> = _isSelectionMode.asStateFlow()

    private val _selectedFlightIds = MutableStateFlow<Set<String>>(emptySet())
    val selectedFlightIds: StateFlow<Set<String>> = _selectedFlightIds.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    private val _sharedPreview = MutableStateFlow<SharedFlightPreview?>(null)
    val sharedPreview: StateFlow<SharedFlightPreview?> = _sharedPreview.asStateFlow()

    private val _activeShareCode = MutableStateFlow<SharedFlightCodeResponse?>(null)
    val activeShareCode: StateFlow<SharedFlightCodeResponse?> = _activeShareCode.asStateFlow()

    fun updateRepositories(newRepo: BriefingRepository, newTripRepo: TripRepository?) {
        repository = newRepo
        tripRepository = newTripRepo
    }

    fun loadFlights(nowEpochMs: Long = System.currentTimeMillis()) {
        _isLoading.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                val list = repository.fetchFlights()
                val tripList = runCatching { tripRepository?.listTrips() ?: emptyList() }.getOrDefault(emptyList())
                list to tripList
            }.onSuccess { (list, tripList) ->
                _flights.value = list
                _trips.value = tripList
                recomputeGroups(nowEpochMs)
                _isLoading.value = false
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to load flights"
                _isLoading.value = false
            }
        }
    }

    fun setOrder(newOrder: FlightOrder, nowEpochMs: Long = System.currentTimeMillis()) {
        _order.value = newOrder
        recomputeGroups(nowEpochMs)
    }

    fun toggleOrder(nowEpochMs: Long = System.currentTimeMillis()) {
        setOrder(_order.value.toggled, nowEpochMs)
    }

    fun setSearchQuery(query: String, nowEpochMs: Long = System.currentTimeMillis()) {
        _searchQuery.value = query
        recomputeGroups(nowEpochMs)
    }

    fun toggleSectionCollapsed(section: FlightSection) {
        val current = _collapsedSections.value.toMutableSet()
        if (!current.add(section)) {
            current.remove(section)
        }
        _collapsedSections.value = current
    }

    fun enterSelectionMode(initialFlightId: String? = null) {
        _isSelectionMode.value = true
        _selectedFlightIds.value = if (initialFlightId != null) setOf(initialFlightId) else emptySet()
    }

    fun exitSelectionMode() {
        _isSelectionMode.value = false
        _selectedFlightIds.value = emptySet()
    }

    fun toggleFlightSelection(flightId: String) {
        val current = _selectedFlightIds.value.toMutableSet()
        if (!current.add(flightId)) {
            current.remove(flightId)
        }
        _selectedFlightIds.value = current
    }

    fun selectAllFlights() {
        _selectedFlightIds.value = _flights.value.map { it.id }.toSet()
    }

    fun deleteFlight(flightId: String, nowEpochMs: Long = System.currentTimeMillis()) {
        scope.launch {
            runCatching {
                repository.deleteFlight(flightId)
            }.onSuccess {
                _flights.value = _flights.value.filterNot { it.id == flightId }
                _selectedFlightIds.value = _selectedFlightIds.value - flightId
                recomputeGroups(nowEpochMs)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to delete flight"
            }
        }
    }

    fun deleteSelectedFlights(nowEpochMs: Long = System.currentTimeMillis()) {
        val ids = _selectedFlightIds.value.toList()
        if (ids.isEmpty()) return
        scope.launch {
            runCatching {
                repository.bulkDeleteFlights(ids)
            }.onSuccess {
                val idSet = ids.toSet()
                _flights.value = _flights.value.filterNot { it.id in idSet }
                exitSelectionMode()
                recomputeGroups(nowEpochMs)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to delete selected flights"
            }
        }
    }

    fun moveSelectedFlightsToTrip(tripId: String?, nowEpochMs: Long = System.currentTimeMillis()) {
        val ids = _selectedFlightIds.value.toList()
        if (ids.isEmpty()) return
        scope.launch {
            runCatching {
                for (id in ids) {
                    repository.moveFlight(id, MoveFlightRequest(tripId = tripId))
                }
            }.onSuccess {
                exitSelectionMode()
                loadFlights(nowEpochMs)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to move flights"
            }
        }
    }

    fun createTripFromSelectedFlights(tripName: String, onCreated: (String) -> Unit = {}) {
        val ids = _selectedFlightIds.value.toList()
        val trimmed = tripName.trim()
        if (ids.isEmpty() || trimmed.isBlank()) return
        val repo = tripRepository ?: return
        scope.launch {
            runCatching {
                repo.createTrip(CreateTripRequest(name = trimmed, flightIds = ids))
            }.onSuccess { trip ->
                exitSelectionMode()
                loadFlights()
                onCreated(trip.id)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to create trip"
            }
        }
    }

    fun createShareLink(flightId: String, onShareUrlReady: (String) -> Unit = {}) {
        scope.launch {
            runCatching {
                repository.createFlightShare(flightId)
            }.onSuccess { resp ->
                _activeShareCode.value = resp
                onShareUrlReady(resp.shareUrl)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to create share link"
            }
        }
    }

    fun loadSharedFlightPreview(code: String) {
        scope.launch {
            runCatching {
                repository.previewSharedFlight(code)
            }.onSuccess { preview ->
                _sharedPreview.value = preview
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to preview shared flight"
            }
        }
    }

    fun acceptSharedFlight(code: String, onAccepted: (FlightResponse) -> Unit = {}) {
        scope.launch {
            runCatching {
                repository.acceptSharedFlight(code)
            }.onSuccess { flight ->
                _sharedPreview.value = null
                loadFlights()
                onAccepted(flight)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to save shared flight"
            }
        }
    }

    fun copySharedFlight(code: String, onCopied: (FlightResponse) -> Unit = {}) {
        scope.launch {
            runCatching {
                repository.copySharedFlight(code)
            }.onSuccess { flight ->
                _sharedPreview.value = null
                loadFlights()
                onCopied(flight)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to copy shared flight"
            }
        }
    }

    fun clearSharedPreview() {
        _sharedPreview.value = null
    }

    fun clearError() {
        _errorMessage.value = null
    }

    private fun recomputeGroups(nowEpochMs: Long = System.currentTimeMillis()) {
        val query = _searchQuery.value.trim().lowercase()
        val filtered = if (query.isEmpty()) {
            _flights.value
        } else {
            _flights.value.filter { flight ->
                flight.routeName.lowercase().contains(query) ||
                    flight.waypoints.any { it.lowercase().contains(query) } ||
                    (flight.tripName?.lowercase()?.contains(query) == true)
            }
        }
        _groups.value = groupFlights(
            flights = filtered,
            order = _order.value,
            nowEpochMs = nowEpochMs,
        )
    }
}
