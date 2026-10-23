//
//  SetupWizardModelTests.swift
//  flyfun-weatherTests
//
//  First-run setup (#900): what the wizard sends the server at each step,
//  the FIKI pre-tick from the profile (#850), and the placeholder rules.
//

import Testing
import Foundation
@testable import flyfun_weather

@MainActor
final class StubSetupRepository: SetupRepository {
    var profilesResult: [SetupProfile] = []
    var aircraftResult: [AircraftResponse] = []
    var createAircraftError: Error?
    private(set) var profileUpdates: [(id: Int, request: SetupProfileUpdate)] = []
    private(set) var createRequests: [CreateAircraftRequest] = []
    private(set) var updateRequests: [(id: Int, request: UpdateAircraftRequest)] = []
    private(set) var completeCount = 0

    func setupCompleted() async throws -> Bool { false }
    func profiles() async throws -> [SetupProfile] { profilesResult }
    func systemTemplates(locale: String) async throws -> [SystemTemplate] {
        [SystemTemplate(key: "ifr_fiki", name: "IFR FIKI", description: "For FIKI-equipped IFR aircraft.")]
    }
    func aircraft() async throws -> [AircraftResponse] { aircraftResult }
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] { [] }
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse {
        if let createAircraftError { throw createAircraftError }
        createRequests.append(request)
        return makeAircraft(id: 50, icaoType: request.icaoType, typeName: request.icaoType, isDefault: true)
    }
    func updateAircraft(id: Int, request: UpdateAircraftRequest) async throws -> AircraftResponse {
        updateRequests.append((id, request))
        return makeAircraft(id: id, icaoType: request.icaoType ?? "C172", isDefault: true)
    }
    func updateProfile(id: Int, request: SetupProfileUpdate) async throws {
        profileUpdates.append((id, request))
    }
    func completeSetup() async throws { completeCount += 1 }
}

private func profile(_ id: Int, _ key: String, rules: String, isDefault: Bool = false, fikiCard: Bool = false) -> SetupProfile {
    SetupProfile(id: id, name: key, isDefault: isDefault, systemTemplateKey: key,
                 settings: SetupProfileSettings(flightRules: rules, cruiseAltitudeFt: 9000,
                                                flightCeilingFt: 20000, fikiIcingEnabled: fikiCard))
}

@MainActor
private func seededRepo() -> StubSetupRepository {
    let repo = StubSetupRepository()
    repo.profilesResult = [
        profile(1, "vfr_only", rules: "vfr_only", isDefault: true),
        profile(2, "ifr_conservative", rules: "vfr_ifr"),
        profile(3, "ifr_fiki", rules: "vfr_ifr"),
    ]
    repo.aircraftResult = [makeAircraft(id: 8, icaoType: "ZZZZ", typeName: "Unknown", nickname: "My aircraft",
                                        cruiseSpeedKt: 120, isDefault: true, isPlaceholder: true)]
    return repo
}

@MainActor
@Suite struct SetupWizardModelTests {

    /// The whole flow over the placeholder: profile made default, aircraft
    /// POSTed as the default (FIKI pre-ticked from IFR FIKI, IFR from the
    /// rules), mission PUT into the profile.
    @Test func finishingOverThePlaceholderPostsTheAircraftAndSavesTheMission() async throws {
        let repo = seededRepo()
        let model = SetupWizardModel(repository: repo)
        await model.load()
        #expect(model.selectedProfileId == 1)
        #expect(model.cruiseSpeedKt == "120")   // the placeholder's speed carries over
        #expect(model.icaoType.isEmpty)         // its ZZZZ does not

        await model.next()                       // welcome → profile
        model.selectedProfileId = 3
        await model.next()                       // profile → aircraft
        #expect(model.step == .aircraft)
        #expect(repo.profileUpdates.first?.id == 3)
        #expect(repo.profileUpdates.first?.request.isDefault == true)
        #expect(model.flightRules == "vfr_ifr")
        #expect(model.cruiseAltitudeFt == "9000")
        #expect(model.isFiki)                    // IFR FIKI pre-ticks the box

        model.icaoType = "sr22"
        #expect(await model.next())
        #expect(model.step == .done)

        let req = try #require(repo.createRequests.first)
        #expect(req.icaoType == "SR22")
        #expect(req.isFiki)
        #expect(req.isIfr)
        #expect(req.isDefault)
        #expect(req.cruiseSpeedKt == 120)
        let mission = try #require(repo.profileUpdates.last?.request.settings)
        #expect(repo.profileUpdates.last?.id == 3)
        #expect(mission == SetupMission(flightRules: "vfr_ifr", cruiseAltitudeFt: 9000, flightCeilingFt: 20000))

        await model.complete()
        #expect(repo.completeCount == 1)
    }

    @Test func untickingFikiSendsNonFiki() async throws {
        let repo = seededRepo()
        let model = SetupWizardModel(repository: repo)
        await model.load()
        await model.next()
        model.selectedProfileId = 3
        await model.next()
        model.isFiki = false
        model.icaoType = "C172"
        await model.next()
        #expect(try #require(repo.createRequests.first).isFiki == false)
    }

    /// The retired FIKI Icing card also says FIKI, like the server's rule.
    @Test func theFikiIcingCardPreTicksToo() async {
        let repo = seededRepo()
        repo.profilesResult = [profile(4, "custom", rules: "vfr_ifr", isDefault: true, fikiCard: true)]
        let model = SetupWizardModel(repository: repo)
        await model.load()
        #expect(model.isFiki)
    }

    @Test func vfrOnlyCreatesANonIfrAircraft() async throws {
        let repo = seededRepo()
        let model = SetupWizardModel(repository: repo)
        await model.load()
        await model.next()
        await model.next()                       // keeps VFR Only
        #expect(model.isFiki == false)
        model.icaoType = "C172"
        await model.next()
        #expect(try #require(repo.createRequests.first).isIfr == false)
    }

    @Test func noTypeStaysOnTheAircraftStep() async {
        let repo = seededRepo()
        let model = SetupWizardModel(repository: repo)
        await model.load()
        await model.next()
        await model.next()
        #expect(await model.next() == false)
        #expect(model.step == .aircraft)
        #expect(model.icaoTypeMissing)
        #expect(repo.createRequests.isEmpty)
    }

    @Test func aFailedAircraftSaveStaysAndSaysSo() async {
        let repo = seededRepo()
        repo.createAircraftError = MockError.injected("offline")
        let model = SetupWizardModel(repository: repo)
        await model.load()
        await model.next()
        await model.next()
        model.icaoType = "C172"
        #expect(await model.next() == false)
        #expect(model.step == .aircraft)
        #expect(model.errorMessage?.hasPrefix("Couldn't save your aircraft") == true)
    }

    /// A real default aircraft already exists: it is PUT, its IFR flag left
    /// alone, and its FIKI answer kept rather than re-derived.
    @Test func anExistingRealAircraftIsUpdatedNotDuplicated() async throws {
        let repo = seededRepo()
        repo.aircraftResult = [makeAircraft(id: 9, icaoType: "PA28", typeName: "Piper PA-28",
                                            nickname: "Archie", isDefault: true)]
        let model = SetupWizardModel(repository: repo)
        await model.load()
        #expect(model.icaoType == "PA28")
        #expect(model.nickname == "Archie")
        await model.next()
        model.selectedProfileId = 3
        await model.next()
        #expect(model.isFiki == false)           // the aircraft's answer wins
        await model.next()

        #expect(repo.createRequests.isEmpty)
        let update = try #require(repo.updateRequests.first)
        #expect(update.id == 9)
        #expect(update.request.isIfr == nil)
        #expect(update.request.isFiki == false)
        #expect(update.request.isDefault == nil) // already the default
    }

    @Test func backKeepsTheMissionTyped() async {
        let repo = seededRepo()
        let model = SetupWizardModel(repository: repo)
        await model.load()
        await model.next()
        await model.next()
        model.cruiseAltitudeFt = "6500"
        model.back()
        #expect(model.step == .profile)
        await model.next()
        #expect(model.cruiseAltitudeFt == "6500")
    }

    @Test func ceilingWarningNamesTheAircraft() {
        let model = SetupWizardModel(repository: StubSetupRepository())
        model.nickname = "Archie"
        model.serviceCeilingFt = "14000"
        model.flightCeilingFt = "18000"
        #expect(model.ceilingWarning == "Ceiling 18\u{00A0}000 ft is above Archie's service ceiling (14\u{00A0}000 ft).")
        model.flightCeilingFt = "12000"
        #expect(model.ceilingWarning == nil)
    }

    /// The settings blob carries many other keys; the wizard reads its few and
    /// survives odd values elsewhere.
    @Test func profileSettingsDecodeTheFikiCardAndTolerateOddValues() throws {
        let json = """
        {"id": 7, "name": "Mine", "is_default": true, "system_template_key": null,
         "settings": {"flight_rules": "vfr_ifr", "cruise_altitude_ft": "high",
                      "advisories": {"enabled": {"fiki_icing": true, "icing": false}, "params": {}}}}
        """
        let profile = try JSONDecoder.weatherBrief.decode(SetupProfile.self, from: Data(json.utf8))
        #expect(profile.settings.flightRules == "vfr_ifr")
        #expect(profile.settings.cruiseAltitudeFt == nil)
        #expect(profile.settings.fikiIcingEnabled)
        #expect(profile.declaresFiki)
    }

    @Test func missingSetupCompletedMeansNotDone() throws {
        let me = try JSONDecoder.weatherBrief.decode(AuthMeResponse.self, from: Data(#"{"id": "u1"}"#.utf8))
        #expect(me.setupCompleted == nil)
    }
}
