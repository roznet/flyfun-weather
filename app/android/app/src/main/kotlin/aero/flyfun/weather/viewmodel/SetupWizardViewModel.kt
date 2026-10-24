package aero.flyfun.weather.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.SetupRepository
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AircraftTypeResponse
import aero.flyfun.weather.model.CreateAircraftRequest
import aero.flyfun.weather.model.ProfileResponse
import aero.flyfun.weather.model.SetupMission
import aero.flyfun.weather.model.SetupProfileUpdate
import aero.flyfun.weather.model.SystemTemplate
import aero.flyfun.weather.model.UpdateAircraftRequest
import aero.flyfun.weather.model.declaresFiki
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class SetupStep { WELCOME, PROFILE, AIRCRAFT, DONE }

/** Everything the setup screens show; one immutable value so Compose reads it whole. */
data class SetupWizardState(
    val step: SetupStep = SetupStep.WELCOME,
    val isLoading: Boolean = false,
    /** Profiles or aircraft failed to load: the profile step offers Retry and Next stays blocked. */
    val loadFailed: Boolean = false,
    val isSaving: Boolean = false,
    val errorMessage: String? = null,
    val profiles: List<ProfileResponse> = emptyList(),
    val templates: List<SystemTemplate> = emptyList(),
    val selectedProfileId: Int? = null,
    // "Your aircraft"
    val nickname: String = "",
    val icaoType: String = "",
    /** The type picked from the suggestions (its "manufacturer model"), cleared when the text diverges. */
    val selectedType: AircraftTypeResponse? = null,
    val typeSuggestions: List<AircraftTypeResponse> = emptyList(),
    val cruiseSpeedKt: String = "",
    val serviceCeilingFt: String = "",
    /** The pilot's FIKI answer; null until they touch the switch (or a real aircraft answered it). */
    val fikiAnswer: Boolean? = null,
    /** Shown under the type field after Next with no valid type. */
    val icaoTypeMissing: Boolean = false,
    // "Your usual mission"
    val flightRules: String = "vfr_ifr",
    val cruiseAltitudeFt: String = "",
    val flightCeilingFt: String = "",
    /** The profile the mission fields were last filled from (Back → Next keeps what was typed). */
    val missionFilledFrom: Int? = null,
    /** The pilot's real aircraft once saved, or a real default that existed before the wizard. */
    val savedAircraft: AircraftResponse? = null,
    /** [savedAircraft] existed before the wizard: its IFR flag is the pilot's earlier answer. */
    val aircraftPreExisted: Boolean = false,
) {
    /** The seeded system profiles, the ones the profile step offers. */
    val systemProfiles: List<ProfileResponse>
        get() = profiles.filter { it.systemTemplateKey != null }

    val selectedProfile: ProfileResponse?
        get() = profiles.firstOrNull { it.id == selectedProfileId } ?: profiles.firstOrNull()

    /** The FIKI switch: the pilot's answer, else what the picked profile says. */
    val isFiki: Boolean
        get() = fikiAnswer ?: (selectedProfile?.declaresFiki ?: false)

    fun templateDescription(profile: ProfileResponse): String =
        templates.firstOrNull { it.key == profile.systemTemplateKey }?.description.orEmpty()

    /** Warn (never clamp) when the mission ceiling is above the aircraft's service ceiling (#777 §4). */
    val ceilingWarning: String?
        get() {
            val service = positiveInt(serviceCeilingFt) ?: return null
            val ceiling = positiveInt(flightCeilingFt) ?: return null
            if (ceiling <= service) return null
            return "Ceiling ${groupedFeet(ceiling)} ft is above $aircraftWarningName's service ceiling (${groupedFeet(service)} ft)."
        }

    /** Tail number, else nickname, else the type (like the web wizard). */
    private val aircraftWarningName: String
        get() {
            savedAircraft?.tailNumber?.takeIf { it.isNotBlank() }?.let { return it }
            nickname.trim().takeIf { it.isNotEmpty() }?.let { return it }
            selectedType?.let { return "${it.manufacturer} ${it.model}".trim() }
            return icaoType.trim().uppercase().ifEmpty { "your aircraft" }
        }

    companion object {
        fun positiveInt(text: String): Int? = text.trim().toIntOrNull()?.takeIf { it > 0 }

        /** `25000` → `"25 000"` with a no-break space, as iOS, independent of the device locale. */
        fun groupedFeet(value: Int): String =
            value.toString().reversed().chunked(3).joinToString(" ").reversed()
    }
}

/**
 * First-run setup (#900), the port of the web welcome wizard (`web/ts/components/welcome-wizard.ts`)
 * and of iOS `SetupWizardModel`: welcome → profile → aircraft → done, with the same server calls.
 *
 * - Profile: the picked system profile becomes the default (`is_default`).
 * - Aircraft: "Your aircraft" is POSTed as the default aircraft (the server replaces the
 *   placeholder), or PUT when a real default already exists; "Your usual mission" is PUT into the
 *   picked profile's settings.
 * - Done / Skip: `setup-complete`, so it never shows again on any client.
 *
 * Nothing is derived here that the server owns: the FIKI pre-tick reads the profile the server
 * seeded with the server's own `profile_declares_fiki` rule, and stays a suggestion.
 */
class SetupWizardViewModel(
    private val repository: SetupRepository,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _state = MutableStateFlow(SetupWizardState())
    val state: StateFlow<SetupWizardState> = _state.asStateFlow()

    private var searchJob: Job? = null

    // ---- Load -------------------------------------------------------------------------------

    fun start() {
        scope.launch { load() }
    }

    /**
     * Profiles, templates and the user's aircraft. The server always returns at least the
     * placeholder; a placeholder only contributes its speed (its name and `ZZZZ` are stand-ins).
     */
    suspend fun load() {
        _state.update { it.copy(isLoading = true) }
        // Without the profiles the profile writes would be skipped, and without the aircraft the
        // save would POST a second default next to a real one: stop rather than guess.
        val loaded = runCatching { repository.profiles() to repository.aircraft() }
        val (profiles, aircraft) = loaded.getOrElse { err ->
            logError("Failed to load the wizard's profiles or aircraft", err)
            _state.update { it.copy(isLoading = false, loadFailed = true) }
            return
        }
        // Descriptions only: the cards still work without them. The app is English-only, so the
        // template descriptions are too.
        val templates = runCatching { repository.systemTemplates("en") }.getOrDefault(emptyList())
        val current = aircraft.firstOrNull { it.isDefault }
        val real = current?.takeIf { !it.isPlaceholder }
        _state.update { s ->
            var next = s.copy(
                isLoading = false,
                loadFailed = false,
                profiles = profiles,
                templates = templates,
                selectedProfileId = (profiles.firstOrNull { it.isDefault } ?: profiles.firstOrNull())?.id,
                cruiseSpeedKt = current?.cruiseSpeedKt?.toString().orEmpty(),
            )
            if (real != null) {
                next = next.copy(
                    savedAircraft = real,
                    aircraftPreExisted = true,
                    nickname = real.nickname.orEmpty(),
                    icaoType = real.icaoType,
                    selectedType = AircraftTypeResponse(icao = real.icaoType, manufacturer = real.typeName),
                    serviceCeilingFt = real.ceilingFt?.toString().orEmpty(),
                    fikiAnswer = real.isFiki,
                )
            }
            next
        }
    }

    // ---- Field edits ------------------------------------------------------------------------

    fun selectProfile(id: Int) = _state.update { it.copy(selectedProfileId = id) }
    fun setNickname(value: String) = _state.update { it.copy(nickname = value) }
    fun setCruiseSpeed(value: String) = _state.update { it.copy(cruiseSpeedKt = value) }
    fun setServiceCeiling(value: String) = _state.update { it.copy(serviceCeilingFt = value) }
    fun setFiki(value: Boolean) = _state.update { it.copy(fikiAnswer = value) }
    fun setFlightRules(value: String) = _state.update { it.copy(flightRules = value) }
    fun setCruiseAltitude(value: String) = _state.update { it.copy(cruiseAltitudeFt = value) }
    fun setFlightCeiling(value: String) = _state.update { it.copy(flightCeilingFt = value) }

    /** Type text, with a debounced type search (250 ms, superseded searches cancelled). */
    fun setIcaoType(value: String) {
        _state.update { s ->
            val keepType = s.selectedType?.takeIf { it.icao == value.trim().uppercase() }
            s.copy(
                icaoType = value,
                selectedType = keepType,
                icaoTypeMissing = s.icaoTypeMissing && value.isBlank(),
            )
        }
        searchJob?.cancel()
        val query = value.trim()
        if (query.isEmpty() || query.length > 20 || _state.value.selectedType != null) {
            _state.update { it.copy(typeSuggestions = emptyList()) }
            return
        }
        searchJob = scope.launch {
            delay(250)
            val results = runCatching { repository.searchAircraftTypes(query) }.getOrDefault(emptyList())
            _state.update { it.copy(typeSuggestions = results) }
        }
    }

    fun selectType(type: AircraftTypeResponse) {
        searchJob?.cancel()
        _state.update {
            it.copy(icaoType = type.icao, selectedType = type, typeSuggestions = emptyList(), icaoTypeMissing = false)
        }
    }

    // ---- Navigation -------------------------------------------------------------------------

    fun onNext() {
        scope.launch { next() }
    }

    /**
     * Save the current step, then move on. Returns false (staying put) when the load failed or the
     * aircraft step can't be saved (aircraft or mission): those are what the briefing runs on. The
     * default-profile write that fails is logged and skipped, like the web.
     */
    suspend fun next(): Boolean {
        if (_state.value.loadFailed && _state.value.step != SetupStep.WELCOME) return false
        // A second tap while a step saves must not repeat its writes (a second aircraft POST, a
        // second profile PUT): claimed atomically, held for the whole step.
        var claimed = false
        _state.update {
            claimed = !it.isSaving
            if (claimed) it.copy(isSaving = true, errorMessage = null) else it
        }
        if (!claimed) return false
        try {
            return advance()
        } finally {
            _state.update { it.copy(isSaving = false) }
        }
    }

    private suspend fun advance(): Boolean {
        when (_state.value.step) {
            SetupStep.WELCOME -> _state.update { it.copy(step = SetupStep.PROFILE) }
            SetupStep.PROFILE -> {
                applyProfileSelection()
                _state.update { fillMissionIfNeeded(it).copy(step = SetupStep.AIRCRAFT) }
            }
            SetupStep.AIRCRAFT -> {
                if (!saveAircraftStep()) return false
                _state.update { it.copy(step = SetupStep.DONE) }
            }
            SetupStep.DONE -> return false
        }
        return true
    }

    fun back() {
        _state.update { s ->
            val previous = SetupStep.entries.getOrNull(s.step.ordinal - 1) ?: return@update s
            s.copy(step = previous, errorMessage = null)
        }
    }

    /** Finish or Skip: mark setup done so the flow never returns. A failure is logged only. */
    suspend fun complete() {
        runCatching { repository.completeSetup() }
            .onFailure { logError("setup-complete failed", it) }
    }

    // ---- Private ----------------------------------------------------------------------------

    private suspend fun applyProfileSelection() {
        val id = _state.value.selectedProfile?.id ?: return
        runCatching { repository.updateProfile(id, SetupProfileUpdate(isDefault = true)) }
            .onFailure { logError("Failed to set the default profile", it) }
    }

    private fun fillMissionIfNeeded(s: SetupWizardState): SetupWizardState {
        val profile = s.selectedProfile ?: return s
        if (s.missionFilledFrom == profile.id) return s
        return s.copy(
            missionFilledFrom = profile.id,
            flightRules = profile.settings.flightRules ?: "vfr_ifr",
            cruiseAltitudeFt = (profile.settings.cruiseAltitudeFt ?: DEFAULT_CRUISE_ALTITUDE_FT).toString(),
            flightCeilingFt = (profile.settings.flightCeilingFt ?: DEFAULT_FLIGHT_CEILING_FT).toString(),
        )
    }

    /**
     * The ICAO type is required: the server needs one to create the aircraft, and none would leave
     * the placeholder in place. `ZZZZ` (the ICAO code for a type without a designator) is the escape.
     */
    private fun resolvedIcaoType(s: SetupWizardState): String? =
        s.icaoType.trim().uppercase().takeIf { ICAO_TYPE.matches(it) }

    private suspend fun saveAircraftStep(): Boolean {
        val s = _state.value
        val icao = resolvedIcaoType(s)
        if (icao == null) {
            _state.update { it.copy(icaoTypeMissing = true) }
            return false
        }
        val nickname = s.nickname.trim()
        val speed = SetupWizardState.positiveInt(s.cruiseSpeedKt)
        val ceiling = SetupWizardState.positiveInt(s.serviceCeilingFt)
        // Mirrors the placeholder rule: IFR-capable unless the pilot flies VFR only.
        val isIfr = s.flightRules != "vfr_only"
        val existing = s.savedAircraft
        val saved = runCatching {
            if (existing != null) {
                repository.updateAircraft(
                    existing.id,
                    UpdateAircraftRequest(
                        icaoType = icao,
                        nickname = nickname,
                        cruiseSpeedKt = speed,
                        ceilingFt = ceiling,
                        isIfr = if (s.aircraftPreExisted) null else isIfr,
                        isFiki = s.isFiki,
                        isDefault = if (existing.isDefault) null else true,
                    ),
                )
            } else {
                repository.createAircraft(
                    CreateAircraftRequest(
                        icaoType = icao,
                        nickname = nickname.ifEmpty { null },
                        cruiseSpeedKt = speed,
                        ceilingFt = ceiling,
                        isIfr = isIfr,
                        // Icing is graded for the aircraft (#827): the switch, pre-ticked from the
                        // profile, so picking "IFR FIKI" isn't lost (#850).
                        isFiki = s.isFiki,
                        isDefault = true,
                    ),
                )
            }
        }
        val aircraft = saved.getOrElse { err ->
            logError("Failed to save the wizard aircraft", err)
            _state.update { it.copy(errorMessage = "Couldn't save your aircraft: ${err.message ?: err}") }
            return false
        }
        _state.update { it.copy(savedAircraft = aircraft) }

        s.selectedProfile?.let { profile ->
            val mission = SetupMission(
                flightRules = s.flightRules,
                cruiseAltitudeFt = SetupWizardState.positiveInt(s.cruiseAltitudeFt) ?: DEFAULT_CRUISE_ALTITUDE_FT,
                flightCeilingFt = SetupWizardState.positiveInt(s.flightCeilingFt) ?: DEFAULT_FLIGHT_CEILING_FT,
            )
            runCatching { repository.updateProfile(profile.id, SetupProfileUpdate(settings = mission)) }
                .onFailure { err ->
                    // The aircraft is saved (and now PUT on a retry); stay so the mission isn't lost.
                    logError("Failed to save the wizard mission", err)
                    _state.update { it.copy(errorMessage = "Couldn't save your usual mission: ${err.message ?: err}") }
                    return false
                }
        }
        return true
    }

    private fun logError(message: String, err: Throwable) {
        // android.util.Log is a stub in JVM unit tests; never let logging fail the flow.
        runCatching { Log.w(TAG, "$message: ${err.message}") }
    }

    companion object {
        private const val TAG = "SetupWizard"
        const val DEFAULT_CRUISE_ALTITUDE_FT = 8000
        const val DEFAULT_FLIGHT_CEILING_FT = 18000
        private val ICAO_TYPE = Regex("^[A-Z0-9]{1,4}$")
    }
}
