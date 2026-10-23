import Foundation

/// Server calls of the first-run setup wizard (#900): the same endpoints the
/// web welcome wizard calls.
///
/// Its own protocol, like `TripRepository`, rather than more methods on
/// `BriefingRepository`: the wizard runs once per account, needs nothing from
/// the briefing cache, and its fixture (`FixtureSetupRepository`) can then
/// hold a profile + placeholder set of its own without changing what the
/// flight form's fixtures return to the other journeys.
protocol SetupRepository: Sendable {
    /// `setup_completed` from `/auth/me` (missing counts as not done).
    func setupCompleted() async throws -> Bool
    func profiles() async throws -> [SetupProfile]
    func systemTemplates(locale: String) async throws -> [SystemTemplate]
    func aircraft() async throws -> [AircraftResponse]
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse]
    /// `POST /api/aircraft`. With `is_default`, the server replaces the
    /// synthesised placeholder and re-points its flights (#777).
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse
    func updateAircraft(id: Int, request: UpdateAircraftRequest) async throws -> AircraftResponse
    func updateProfile(id: Int, request: SetupProfileUpdate) async throws
    /// `POST /api/user/preferences/setup-complete` (204): the wizard never
    /// shows again for this account, on any client.
    func completeSetup() async throws
}

final class OnlineSetupRepository: SetupRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func setupCompleted() async throws -> Bool {
        let me: AuthMeResponse = try await client.request("/auth/me")
        return me.setupCompleted ?? false
    }

    func profiles() async throws -> [SetupProfile] {
        try await client.request("/api/user/profiles")
    }

    func systemTemplates(locale: String) async throws -> [SystemTemplate] {
        try await client.requestURL(
            "/api/user/profiles/system-templates?locale=\(OnlineBriefingRepository.queryValueEncoded(locale))"
        )
    }

    func aircraft() async throws -> [AircraftResponse] {
        try await client.request("/api/aircraft")
    }

    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] {
        try await client.requestURL("/api/aircraft/types?q=\(OnlineBriefingRepository.queryValueEncoded(query))")
    }

    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/aircraft", method: "POST", body: body)
    }

    func updateAircraft(id: Int, request: UpdateAircraftRequest) async throws -> AircraftResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/aircraft/\(id)", method: "PUT", body: body)
    }

    func updateProfile(id: Int, request: SetupProfileUpdate) async throws {
        let body = try JSONEncoder.weatherBrief.encode(request)
        _ = try await client.requestData("/api/user/profiles/\(id)", method: "PUT", body: body)
    }

    func completeSetup() async throws {
        _ = try await client.requestData("/api/user/preferences/setup-complete", method: "POST")
    }
}

#if DEBUG
/// The wizard's fixture for UI tests (`FLYFUN_MOCK=1`): the seeded system
/// profiles, their templates and the server's placeholder aircraft, all from
/// the shared `app/fixtures/api/setup-*.json`. Setup counts as done unless
/// the launch sets `FLYFUN_MOCK_SETUP=1`, so the other journeys never see the
/// wizard. Writes are applied in memory, like the server would, so a journey
/// can read back what the wizard saved.
final class FixtureSetupRepository: SetupRepository {
    private(set) var isSetupCompleted: Bool
    private var storedProfiles: [SetupProfile]
    private var storedAircraft: [AircraftResponse]
    private var nextAircraftId = 300

    init(setupCompleted: Bool) {
        self.isSetupCompleted = setupCompleted
        self.storedProfiles = FixtureFiles.decode([SetupProfile].self, "setup-profiles")
        self.storedAircraft = FixtureFiles.decode([AircraftResponse].self, "setup-aircraft")
    }

    func setupCompleted() async throws -> Bool { isSetupCompleted }

    func profiles() async throws -> [SetupProfile] { storedProfiles }

    func systemTemplates(locale: String) async throws -> [SystemTemplate] {
        FixtureFiles.decode([SystemTemplate].self, "setup-system-templates")
    }

    func aircraft() async throws -> [AircraftResponse] { storedAircraft }

    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] {
        let q = query.uppercased()
        return [
            AircraftTypeResponse(icao: "SR22", manufacturer: "Cirrus", model: "SR22", category: "SEP"),
            AircraftTypeResponse(icao: "C172", manufacturer: "Cessna", model: "172 Skyhawk", category: "SEP"),
        ].filter { $0.icao.hasPrefix(q) }
    }

    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse {
        nextAircraftId += 1
        let created = AircraftResponse(
            id: nextAircraftId, icaoType: request.icaoType, typeName: request.icaoType,
            tailNumber: request.tailNumber, nickname: request.nickname,
            isIfr: request.isIfr, isFiki: request.isFiki,
            cruiseSpeedKt: request.cruiseSpeedKt, ceilingFt: request.ceilingFt,
            isDefault: request.isDefault, createdAt: "2026-10-23T00:00:00Z"
        )
        // Like the server (`api/aircraft.py`, #777): a default POST over a sole placeholder replaces it.
        if request.isDefault, storedAircraft.count == 1, storedAircraft[0].isPlaceholder {
            storedAircraft = [created]
        } else {
            storedAircraft.append(created)
        }
        return created
    }

    func updateAircraft(id: Int, request: UpdateAircraftRequest) async throws -> AircraftResponse {
        guard let index = storedAircraft.firstIndex(where: { $0.id == id }) else {
            throw APIError.notFound
        }
        let old = storedAircraft[index]
        let updated = AircraftResponse(
            id: old.id, icaoType: request.icaoType ?? old.icaoType, typeName: request.icaoType ?? old.typeName,
            tailNumber: request.tailNumber ?? old.tailNumber, nickname: request.nickname ?? old.nickname,
            isIfr: request.isIfr ?? old.isIfr, isFiki: request.isFiki ?? old.isFiki,
            cruiseSpeedKt: request.cruiseSpeedKt ?? old.cruiseSpeedKt, ceilingFt: request.ceilingFt ?? old.ceilingFt,
            isDefault: request.isDefault ?? old.isDefault, createdAt: old.createdAt
        )
        storedAircraft[index] = updated
        return updated
    }

    func updateProfile(id: Int, request: SetupProfileUpdate) async throws {
        storedProfiles = storedProfiles.map { profile in
            var settings = profile.settings
            if profile.id == id, let mission = request.settings {
                settings.flightRules = mission.flightRules
                settings.cruiseAltitudeFt = mission.cruiseAltitudeFt
                settings.flightCeilingFt = mission.flightCeilingFt
            }
            let isDefault = request.isDefault == true ? profile.id == id : profile.isDefault
            return SetupProfile(id: profile.id, name: profile.name, isDefault: isDefault,
                                systemTemplateKey: profile.systemTemplateKey, settings: settings)
        }
    }

    func completeSetup() async throws {
        isSetupCompleted = true
    }
}
#endif
