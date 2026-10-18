package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.TripRepository
import aero.flyfun.weather.model.AddTripLegsRequest
import aero.flyfun.weather.model.CreateTripRequest
import aero.flyfun.weather.model.TripAiSummaryResponse
import aero.flyfun.weather.model.TripRefreshStatus
import aero.flyfun.weather.model.TripResponse
import aero.flyfun.weather.model.TripSummaryResponse
import aero.flyfun.weather.model.UpdateTripRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for managing multi-leg trips, trip refresh status polling,
 * and cross-leg AI trip digest generation. Mirrors iOS `TripDetailView` logic.
 */
class TripsViewModel(
    private var tripRepository: TripRepository,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _trips = MutableStateFlow<List<TripSummaryResponse>>(emptyList())
    val trips: StateFlow<List<TripSummaryResponse>> = _trips.asStateFlow()

    private val _currentTrip = MutableStateFlow<TripResponse?>(null)
    val currentTrip: StateFlow<TripResponse?> = _currentTrip.asStateFlow()

    private val _aiSummary = MutableStateFlow<TripAiSummaryResponse?>(null)
    val aiSummary: StateFlow<TripAiSummaryResponse?> = _aiSummary.asStateFlow()

    private val _refreshStatus = MutableStateFlow<TripRefreshStatus?>(null)
    val refreshStatus: StateFlow<TripRefreshStatus?> = _refreshStatus.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _isGeneratingAi = MutableStateFlow(false)
    val isGeneratingAi: StateFlow<Boolean> = _isGeneratingAi.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    fun updateRepository(newRepo: TripRepository) {
        tripRepository = newRepo
    }

    fun loadTrips() {
        _isLoading.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                tripRepository.listTrips()
            }.onSuccess { list ->
                _trips.value = list
                _isLoading.value = false
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to load trips"
                _isLoading.value = false
            }
        }
    }

    fun loadTripDetail(tripId: String) {
        _isLoading.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                tripRepository.getTrip(tripId)
            }.onSuccess { trip ->
                _currentTrip.value = trip
                _isLoading.value = false
                loadAiSummary(tripId, generate = false)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to load trip"
                _isLoading.value = false
            }
        }
    }

    fun createTrip(name: String, flightIds: List<String>, onCreated: (TripResponse) -> Unit = {}) {
        val trimmed = name.trim()
        if (trimmed.isBlank()) {
            _errorMessage.value = "Trip name is required."
            return
        }
        scope.launch {
            runCatching {
                tripRepository.createTrip(CreateTripRequest(name = trimmed, flightIds = flightIds))
            }.onSuccess { created ->
                _currentTrip.value = created
                loadTrips()
                onCreated(created)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to create trip"
            }
        }
    }

    fun renameTrip(tripId: String, newName: String) {
        val trimmed = newName.trim()
        if (trimmed.isBlank()) return
        scope.launch {
            runCatching {
                tripRepository.updateTrip(tripId, UpdateTripRequest(name = trimmed))
            }.onSuccess { updated ->
                _currentTrip.value = updated
                loadTrips()
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to rename trip"
            }
        }
    }

    fun addLegs(tripId: String, flightIds: List<String>) {
        if (flightIds.isEmpty()) return
        scope.launch {
            runCatching {
                tripRepository.addTripLegs(tripId, AddTripLegsRequest(flightIds))
            }.onSuccess { updated ->
                _currentTrip.value = updated
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to add legs"
            }
        }
    }

    fun removeLeg(tripId: String, flightId: String) {
        scope.launch {
            runCatching {
                tripRepository.removeTripLeg(tripId, flightId)
            }.onSuccess { updated ->
                _currentTrip.value = updated
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to remove leg from trip"
            }
        }
    }

    fun deleteTrip(tripId: String, deleteFlights: Boolean = false, onDeleted: () -> Unit = {}) {
        scope.launch {
            runCatching {
                tripRepository.deleteTrip(tripId, deleteFlights)
            }.onSuccess {
                _trips.value = _trips.value.filterNot { it.id == tripId }
                if (_currentTrip.value?.id == tripId) {
                    _currentTrip.value = null
                }
                onDeleted()
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to delete trip"
            }
        }
    }

    fun refreshAllLegs(tripId: String, force: Boolean = false) {
        scope.launch {
            runCatching {
                tripRepository.refreshTrip(tripId, force)
            }.onSuccess { initialStatus ->
                _refreshStatus.value = initialStatus
                var current = initialStatus
                var attempts = 0
                while ((current.status == "running" || current.status == "queued") && attempts < 60) {
                    delay(1500)
                    attempts++
                    current = runCatching { tripRepository.tripRefreshStatus(tripId) }.getOrNull() ?: break
                    _refreshStatus.value = current
                }
                loadTripDetail(tripId)
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to refresh trip"
            }
        }
    }

    fun loadAiSummary(tripId: String, generate: Boolean = false) {
        if (generate) _isGeneratingAi.value = true
        scope.launch {
            runCatching {
                tripRepository.tripAiSummary(tripId, generate = generate)
            }.onSuccess { summary ->
                _aiSummary.value = summary
                _isGeneratingAi.value = false
            }.onFailure { err ->
                if (generate) {
                    _errorMessage.value = err.message ?: "Failed to generate trip AI summary"
                }
                _isGeneratingAi.value = false
            }
        }
    }

    fun clearError() {
        _errorMessage.value = null
    }
}
