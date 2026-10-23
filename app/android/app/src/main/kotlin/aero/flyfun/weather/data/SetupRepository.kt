package aero.flyfun.weather.data

import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.AircraftTypeResponse
import aero.flyfun.weather.model.CreateAircraftRequest
import aero.flyfun.weather.model.ProfileResponse
import aero.flyfun.weather.model.SetupProfileUpdate
import aero.flyfun.weather.model.SystemTemplate
import aero.flyfun.weather.model.UpdateAircraftRequest
import kotlinx.serialization.builtins.ListSerializer

/**
 * Server calls of the first-run setup wizard (#900): the same endpoints the web welcome wizard
 * calls. Its own interface, like iOS `SetupRepository`, rather than more methods on
 * [BriefingRepository]: the wizard runs once per account, needs nothing from the briefing cache, and
 * its fixture ([FixtureSetupRepository]) can hold a profile + placeholder set of its own without
 * changing what the other journeys' fixtures return.
 */
interface SetupRepository {
    /** `setup_completed` from `/auth/me` (missing counts as not done). */
    suspend fun setupCompleted(): Boolean
    suspend fun profiles(): List<ProfileResponse>
    suspend fun systemTemplates(locale: String): List<SystemTemplate>
    suspend fun aircraft(): List<AircraftResponse>
    suspend fun searchAircraftTypes(query: String): List<AircraftTypeResponse>

    /** `POST /api/aircraft`. With `is_default`, the server replaces the placeholder and re-points its flights (#777). */
    suspend fun createAircraft(req: CreateAircraftRequest): AircraftResponse
    suspend fun updateAircraft(id: Int, req: UpdateAircraftRequest): AircraftResponse
    suspend fun updateProfile(id: Int, req: SetupProfileUpdate)

    /** `POST /api/user/preferences/setup-complete` (204): the wizard never shows again, on any client. */
    suspend fun completeSetup()
}

class OnlineSetupRepository(private val api: ApiClient) : SetupRepository {
    override suspend fun setupCompleted(): Boolean = api.getAuthMe().setupCompleted ?: false
    override suspend fun profiles(): List<ProfileResponse> = api.listProfiles()
    override suspend fun systemTemplates(locale: String): List<SystemTemplate> = api.listSystemTemplates(locale)
    override suspend fun aircraft(): List<AircraftResponse> = api.listAircraft()
    override suspend fun searchAircraftTypes(query: String): List<AircraftTypeResponse> =
        api.searchAircraftTypes(query)
    override suspend fun createAircraft(req: CreateAircraftRequest): AircraftResponse = api.createAircraft(req)
    override suspend fun updateAircraft(id: Int, req: UpdateAircraftRequest): AircraftResponse =
        api.updateAircraft(id, req)
    override suspend fun updateProfile(id: Int, req: SetupProfileUpdate) {
        api.updateProfile(id, req)
    }
    override suspend fun completeSetup() = api.completeSetup()
}

/**
 * The wizard's fixture for mock launches (`FLYFUN_MOCK=1`): the seeded system profiles, their
 * templates and the server's placeholder aircraft, from the shared `app/fixtures/api/setup-*.json`.
 * Setup counts as done unless the UI test launch sets `FLYFUN_MOCK_SETUP=1`, so the other journeys
 * (and demo mode, which has no account) never see the wizard. Writes are applied in memory, like
 * the server would, so a journey reads back what the wizard saved.
 */
class FixtureSetupRepository(setupCompleted: Boolean) : SetupRepository {
    @Volatile
    var isSetupCompleted: Boolean = setupCompleted
        private set

    /** The account's rows, read on first use: the app holds one of these from launch. */
    private class Store(var profiles: List<ProfileResponse>, var aircraft: List<AircraftResponse>)

    private val store by lazy {
        Store(
            profiles = FixtureFiles.decode(ListSerializer(ProfileResponse.serializer()), "setup-profiles"),
            aircraft = FixtureFiles.decode(ListSerializer(AircraftResponse.serializer()), "setup-aircraft"),
        )
    }
    private var storedProfiles: List<ProfileResponse>
        get() = store.profiles
        set(value) { store.profiles = value }
    private var storedAircraft: List<AircraftResponse>
        get() = store.aircraft
        set(value) { store.aircraft = value }
    private var nextAircraftId = 300

    override suspend fun setupCompleted(): Boolean = isSetupCompleted

    override suspend fun profiles(): List<ProfileResponse> = storedProfiles

    override suspend fun systemTemplates(locale: String): List<SystemTemplate> =
        FixtureFiles.decode(ListSerializer(SystemTemplate.serializer()), "setup-system-templates")

    override suspend fun aircraft(): List<AircraftResponse> = storedAircraft

    override suspend fun searchAircraftTypes(query: String): List<AircraftTypeResponse> =
        listOf(
            AircraftTypeResponse(icao = "SR22", manufacturer = "Cirrus", model = "SR22", category = "SEP"),
            AircraftTypeResponse(icao = "C172", manufacturer = "Cessna", model = "172 Skyhawk", category = "SEP"),
        ).filter { it.icao.startsWith(query.trim().uppercase()) }

    override suspend fun createAircraft(req: CreateAircraftRequest): AircraftResponse {
        nextAircraftId += 1
        val created = AircraftResponse(
            id = nextAircraftId,
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
        // Like the server: a default POST over a sole placeholder replaces it.
        storedAircraft = if (req.isDefault && storedAircraft.size == 1 && storedAircraft[0].isPlaceholder) {
            listOf(created)
        } else {
            storedAircraft + created
        }
        return created
    }

    override suspend fun updateAircraft(id: Int, req: UpdateAircraftRequest): AircraftResponse {
        val old = storedAircraft.firstOrNull { it.id == id } ?: throw ApiError.NotFound
        val updated = old.copy(
            icaoType = req.icaoType ?: old.icaoType,
            typeName = req.icaoType ?: old.typeName,
            nickname = req.nickname ?: old.nickname,
            cruiseSpeedKt = req.cruiseSpeedKt ?: old.cruiseSpeedKt,
            ceilingFt = req.ceilingFt ?: old.ceilingFt,
            isIfr = req.isIfr ?: old.isIfr,
            isFiki = req.isFiki ?: old.isFiki,
            isDefault = req.isDefault ?: old.isDefault,
            isPlaceholder = false,
        )
        storedAircraft = storedAircraft.map { if (it.id == id) updated else it }
        return updated
    }

    override suspend fun updateProfile(id: Int, req: SetupProfileUpdate) {
        storedProfiles = storedProfiles.map { profile ->
            val mission = req.settings
            val settings = if (profile.id == id && mission != null) {
                profile.settings.copy(
                    flightRules = mission.flightRules,
                    cruiseAltitudeFt = mission.cruiseAltitudeFt,
                    flightCeilingFt = mission.flightCeilingFt,
                )
            } else {
                profile.settings
            }
            val isDefault = if (req.isDefault == true) profile.id == id else profile.isDefault
            profile.copy(settings = settings, isDefault = isDefault)
        }
    }

    override suspend fun completeSetup() {
        isSetupCompleted = true
    }
}
