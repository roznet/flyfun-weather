package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AircraftTypeResult
import aero.flyfun.weather.model.CreateAircraftRequest
import aero.flyfun.weather.model.UpdateAircraftRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Extension alias so UI callers using `serviceCeilingFt` or `ceilingFt` both compile cleanly. */
val AircraftResponse.serviceCeilingFt: Int?
    get() = ceilingFt

/**
 * ViewModel for listing, creating, editing, and deleting user aircraft,
 * plus ICAO type search (`/api/aircraft/types?q=...`).
 * Mirrors iOS `AircraftListViewModel`.
 */
class AircraftListViewModel(
    private val apiClient: ApiClient? = null,
    private val initialFixtureAircraft: List<AircraftResponse> = emptyList(),
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _aircraft = MutableStateFlow(initialFixtureAircraft)
    val aircraft: StateFlow<List<AircraftResponse>> = _aircraft.asStateFlow()

    private val _typeResults = MutableStateFlow<List<AircraftTypeResult>>(emptyList())
    val typeResults: StateFlow<List<AircraftTypeResult>> = _typeResults.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _isSaving = MutableStateFlow(false)
    val isSaving: StateFlow<Boolean> = _isSaving.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    fun loadAircraft() {
        val api = apiClient ?: return
        _isLoading.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                api.listAircraft()
            }.onSuccess { list ->
                _aircraft.value = list
                _isLoading.value = false
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to load aircraft"
                _isLoading.value = false
            }
        }
    }

    fun searchAircraftTypes(query: String) {
        val trimmed = query.trim()
        if (trimmed.length < 2) {
            _typeResults.value = emptyList()
            return
        }
        val api = apiClient
        if (api == null) {
            _typeResults.value = DEFAULT_AIRCRAFT_TYPES.filter {
                it.icao.contains(trimmed, ignoreCase = true) ||
                    it.manufacturer.contains(trimmed, ignoreCase = true) ||
                    it.model.contains(trimmed, ignoreCase = true)
            }
            return
        }
        scope.launch {
            runCatching {
                api.searchAircraftTypes(trimmed)
            }.onSuccess { results ->
                _typeResults.value = results
            }.onFailure {
                _typeResults.value = DEFAULT_AIRCRAFT_TYPES.filter {
                    it.icao.contains(trimmed, ignoreCase = true) ||
                        it.manufacturer.contains(trimmed, ignoreCase = true) ||
                        it.model.contains(trimmed, ignoreCase = true)
                }
            }
        }
    }

    fun createAircraft(
        icaoType: String,
        tailNumber: String?,
        nickname: String?,
        cruiseSpeedKt: Int?,
        ceilingFt: Int?,
        isIfr: Boolean,
        isFiki: Boolean,
        isDefault: Boolean,
        onSaved: (AircraftResponse) -> Unit = {},
    ) {
        val trimmedIcao = icaoType.trim().uppercase()
        if (trimmedIcao.isBlank()) {
            _errorMessage.value = "ICAO aircraft type designator is required (e.g. SR22, C172)."
            return
        }
        val req = CreateAircraftRequest(
            icaoType = trimmedIcao,
            tailNumber = tailNumber?.trim()?.takeIf { it.isNotEmpty() },
            nickname = nickname?.trim()?.takeIf { it.isNotEmpty() },
            cruiseSpeedKt = cruiseSpeedKt,
            ceilingFt = ceilingFt,
            isIfr = isIfr,
            isFiki = isFiki,
            isDefault = isDefault,
        )
        val api = apiClient
        if (api == null) {
            val mock = AircraftResponse(
                id = (System.currentTimeMillis() % 100000L).toInt(),
                icaoType = req.icaoType,
                typeName = req.icaoType,
                tailNumber = req.tailNumber,
                nickname = req.nickname,
                cruiseSpeedKt = req.cruiseSpeedKt,
                ceilingFt = req.ceilingFt,
                isIfr = req.isIfr,
                isFiki = req.isFiki,
                isDefault = req.isDefault,
            )
            val updated = if (req.isDefault) {
                _aircraft.value.map { it.copy(isDefault = false) } + mock
            } else {
                _aircraft.value + mock
            }
            _aircraft.value = updated
            onSaved(mock)
            return
        }

        _isSaving.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                api.createAircraft(req)
            }.onSuccess { created ->
                _isSaving.value = false
                loadAircraft()
                onSaved(created)
            }.onFailure { err ->
                _isSaving.value = false
                _errorMessage.value = err.message ?: "Failed to create aircraft"
            }
        }
    }

    fun updateAircraft(
        aircraftId: Int,
        icaoType: String?,
        tailNumber: String?,
        nickname: String?,
        cruiseSpeedKt: Int?,
        ceilingFt: Int?,
        isIfr: Boolean?,
        isFiki: Boolean?,
        isDefault: Boolean?,
        onSaved: (AircraftResponse) -> Unit = {},
    ) {
        val req = UpdateAircraftRequest(
            icaoType = icaoType?.trim()?.uppercase()?.takeIf { it.isNotEmpty() },
            tailNumber = tailNumber?.trim()?.takeIf { it.isNotEmpty() },
            nickname = nickname?.trim()?.takeIf { it.isNotEmpty() },
            cruiseSpeedKt = cruiseSpeedKt,
            ceilingFt = ceilingFt,
            isIfr = isIfr,
            isFiki = isFiki,
            isDefault = isDefault,
        )
        val api = apiClient
        if (api == null) {
            val existing = _aircraft.value.firstOrNull { it.id == aircraftId } ?: return
            val updatedItem = existing.copy(
                icaoType = req.icaoType ?: existing.icaoType,
                tailNumber = req.tailNumber,
                nickname = req.nickname,
                cruiseSpeedKt = req.cruiseSpeedKt,
                ceilingFt = req.ceilingFt,
                isIfr = req.isIfr ?: existing.isIfr,
                isFiki = req.isFiki ?: existing.isFiki,
                isDefault = req.isDefault ?: existing.isDefault,
            )
            _aircraft.value = _aircraft.value.map { item ->
                if (item.id == aircraftId) updatedItem
                else if (updatedItem.isDefault) item.copy(isDefault = false)
                else item
            }
            onSaved(updatedItem)
            return
        }

        _isSaving.value = true
        _errorMessage.value = null
        scope.launch {
            runCatching {
                api.updateAircraft(aircraftId, req)
            }.onSuccess { saved ->
                _isSaving.value = false
                loadAircraft()
                onSaved(saved)
            }.onFailure { err ->
                _isSaving.value = false
                _errorMessage.value = err.message ?: "Failed to update aircraft"
            }
        }
    }

    fun deleteAircraft(aircraftId: Int) {
        val api = apiClient
        if (api == null) {
            _aircraft.value = _aircraft.value.filterNot { it.id == aircraftId }
            return
        }
        scope.launch {
            runCatching {
                api.deleteAircraft(aircraftId)
            }.onSuccess {
                _aircraft.value = _aircraft.value.filterNot { it.id == aircraftId }
            }.onFailure { err ->
                _errorMessage.value = err.message ?: "Failed to delete aircraft"
            }
        }
    }

    fun clearError() {
        _errorMessage.value = null
    }

    companion object {
        val DEFAULT_AIRCRAFT_TYPES = listOf(
            AircraftTypeResult("SR22", "Cirrus", "SR22", "SEP", 175, 17500),
            AircraftTypeResult("SR20", "Cirrus", "SR20", "SEP", 145, 17500),
            AircraftTypeResult("C172", "Cessna", "172 Skyhawk", "SEP", 120, 14000),
            AircraftTypeResult("C182", "Cessna", "182 Skylane", "SEP", 145, 18000),
            AircraftTypeResult("PA28", "Piper", "PA-28 Cherokee / Archer", "SEP", 125, 14000),
            AircraftTypeResult("DA40", "Diamond", "DA40 Diamond Star", "SEP", 135, 16400),
            AircraftTypeResult("DA42", "Diamond", "DA42 Twin Star", "MEP", 170, 18000),
            AircraftTypeResult("TBM9", "Daher", "TBM 900/940/960", "SET", 330, 31000),
            AircraftTypeResult("PC12", "Pilatus", "PC-12", "SET", 280, 30000),
        )
    }
}
