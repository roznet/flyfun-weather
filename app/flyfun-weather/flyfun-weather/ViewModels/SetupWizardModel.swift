import Foundation
import OSLog

/// State and actions behind `SetupWizardView` (#900): the first-run flow,
/// a port of the web welcome wizard (`web/ts/components/welcome-wizard.ts`)
/// with the same steps, server calls and rules.
///
/// - Profile: the picked system profile becomes the default (`is_default`).
/// - Aircraft: "Your aircraft" is POSTed as the default aircraft (the server
///   replaces the placeholder), or PUT when a real default already exists;
///   "Your usual mission" is PUT into the picked profile's settings.
/// - Done / Skip: `setup-complete`, so it never shows again on any client.
///
/// Nothing is derived here that the server owns: the FIKI pre-tick reads the
/// profile the server seeded, with the server's own `profile_declares_fiki`
/// rule, and stays a suggestion the pilot can untick.
@Observable
@MainActor
final class SetupWizardModel {
    enum Step: Int, CaseIterable {
        case welcome, profile, aircraft, done
    }

    static let defaultCruiseAltitudeFt = 8000
    static let defaultFlightCeilingFt = 18000

    private(set) var step: Step = .welcome
    private(set) var isLoading = false
    private(set) var isSaving = false
    /// Profiles or aircraft couldn't be read. The wizard then stops at the
    /// profile step with a Retry: going on would skip the profile writes, or
    /// POST a second default aircraft over a real one it couldn't see.
    private(set) var loadFailed = false
    var errorMessage: String?

    // MARK: Profile step

    private(set) var profiles: [SetupProfile] = []
    private(set) var templates: [SystemTemplate] = []
    var selectedProfileId: Int?

    // MARK: Aircraft step — "Your aircraft"

    var nickname = ""
    var icaoType = ""
    var cruiseSpeedKt = ""
    var serviceCeilingFt = ""
    private(set) var selectedType: AircraftTypeResponse?
    private(set) var typeSuggestions: [AircraftTypeResponse] = []
    /// Shown under the type field after Next with no valid type.
    private(set) var icaoTypeMissing = false
    /// The pilot's FIKI answer; nil until they touch the toggle (or a real
    /// aircraft already answered it). Until then the box follows the profile.
    private var fikiAnswer: Bool?

    // MARK: Aircraft step — "Your usual mission"

    var flightRules = "vfr_ifr"
    var cruiseAltitudeFt = ""
    var flightCeilingFt = ""
    /// The profile the mission fields were last filled from.
    private var missionFilledFrom: Int?

    // MARK: Saved state

    /// The pilot's real aircraft once saved (or a real default that existed
    /// before the wizard). A second save PUTs it instead of POSTing again.
    private(set) var savedAircraft: AircraftResponse?
    /// `savedAircraft` existed before the wizard: its IFR flag is the pilot's
    /// earlier answer, so the wizard leaves it alone.
    private var aircraftPreExisted = false

    private let repository: any SetupRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "SetupWizard")

    init(repository: any SetupRepository) {
        self.repository = repository
    }

    // MARK: - Derived

    /// The seeded system profiles, the ones the profile step offers.
    var systemProfiles: [SetupProfile] {
        profiles.filter { $0.systemTemplateKey != nil }
    }

    var selectedProfile: SetupProfile? {
        profiles.first { $0.id == selectedProfileId } ?? profiles.first
    }

    func templateDescription(for profile: SetupProfile) -> String {
        templates.first { $0.key == profile.systemTemplateKey }?.description ?? ""
    }

    var isFiki: Bool {
        get { fikiAnswer ?? (selectedProfile?.declaresFiki ?? false) }
        set { fikiAnswer = newValue }
    }

    /// Warn (never clamp) when the mission ceiling is above the aircraft's
    /// service ceiling, worded like the flight form's warning (#777 §4).
    var ceilingWarning: String? {
        guard let service = Self.positiveInt(serviceCeilingFt),
              let ceiling = Self.positiveInt(flightCeilingFt),
              ceiling > service else { return nil }
        return "Ceiling \(AddFlightViewModel.groupedFeet(ceiling)) ft is above \(aircraftWarningName)'s service ceiling (\(AddFlightViewModel.groupedFeet(service)) ft)."
    }

    /// Tail number, else nickname, else the type (like the web wizard).
    private var aircraftWarningName: String {
        if let tail = savedAircraft?.tailNumber, !tail.isEmpty { return tail }
        let name = nickname.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { return name }
        if let selectedType { return selectedType.displayName }
        let icao = icaoType.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        return icao.isEmpty ? "your aircraft" : icao
    }

    // MARK: - Load

    /// Profiles, templates and the user's aircraft. The server always returns
    /// at least the synthesised "My aircraft" placeholder; a placeholder only
    /// contributes its speed (its name and `ZZZZ` type are stand-ins).
    func load() async {
        isLoading = true
        defer { isLoading = false }
        let aircraft: [AircraftResponse]
        do {
            profiles = try await repository.profiles()
            aircraft = try await repository.aircraft()
            loadFailed = false
        } catch {
            Self.logger.error("Failed to load the wizard's profiles or aircraft: \(error)")
            loadFailed = true
            return
        }
        // Descriptions only: the cards still work without them. The app is
        // English-only, so the descriptions are too.
        templates = (try? await repository.systemTemplates(locale: "en")) ?? []

        selectedProfileId = (profiles.first { $0.isDefault } ?? profiles.first)?.id

        guard let current = aircraft.first(where: { $0.isDefault }) else { return }
        cruiseSpeedKt = current.cruiseSpeedKt.map { String($0) } ?? ""
        guard !current.isPlaceholder else { return }
        savedAircraft = current
        aircraftPreExisted = true
        nickname = current.nickname ?? ""
        icaoType = current.icaoType
        selectedType = AircraftTypeResponse(icao: current.icaoType, manufacturer: current.typeName, model: "", category: nil)
        serviceCeilingFt = current.ceilingFt.map { String($0) } ?? ""
        fikiAnswer = current.isFiki
    }

    // MARK: - Navigation

    /// Save the current step, then move on. Returns false (staying put) only
    /// when the aircraft step can't be saved: an unsaved aircraft is the one
    /// thing the pilot must know about. Profile writes that fail are logged
    /// and skipped, like the web; Settings stays the place to fix them.
    @discardableResult
    func next() async -> Bool {
        // A second tap while a step saves must not repeat its writes (a second
        // aircraft POST, a second profile PUT): held for the whole step.
        guard !isSaving, !(loadFailed && step != .welcome) else { return false }
        isSaving = true
        defer { isSaving = false }
        errorMessage = nil
        switch step {
        case .welcome:
            step = .profile
        case .profile:
            await applyProfileSelection()
            fillMissionIfNeeded()
            step = .aircraft
        case .aircraft:
            guard await saveAircraftStep() else { return false }
            step = .done
        case .done:
            return false
        }
        return true
    }

    func back() {
        errorMessage = nil
        guard let previous = Step(rawValue: step.rawValue - 1) else { return }
        step = previous
    }

    /// Finish or Skip: mark setup done so the flow never returns. A failure
    /// is logged only; the next sign-in would offer the wizard again.
    func complete() async {
        do {
            try await repository.completeSetup()
        } catch {
            Self.logger.error("setup-complete failed: \(error)")
        }
    }

    // MARK: - Type search

    /// Search aircraft types as the pilot types (the view debounces).
    func searchTypes() async {
        let query = icaoType.trimmingCharacters(in: .whitespacesAndNewlines)
        if let selectedType, query.uppercased() != selectedType.icao {
            self.selectedType = nil
        }
        if !query.isEmpty { icaoTypeMissing = false }
        guard selectedType == nil, !query.isEmpty, query.count <= 20, !Task.isCancelled else {
            typeSuggestions = []
            return
        }
        do {
            let results = try await repository.searchAircraftTypes(query)
            guard !Task.isCancelled else { return }
            typeSuggestions = results
        } catch {
            typeSuggestions = []
        }
    }

    func selectType(_ type: AircraftTypeResponse) {
        selectedType = type
        icaoType = type.icao
        typeSuggestions = []
        icaoTypeMissing = false
    }

    // MARK: - Private

    private func applyProfileSelection() async {
        guard let id = selectedProfile?.id else { return }
        do {
            try await repository.updateProfile(id: id, request: SetupProfileUpdate(isDefault: true))
        } catch {
            Self.logger.error("Failed to set the default profile: \(error)")
        }
    }

    /// Prefill "Your usual mission" from the picked profile, once per pick so
    /// Back → Next keeps what the pilot typed.
    private func fillMissionIfNeeded() {
        guard let profile = selectedProfile, missionFilledFrom != profile.id else { return }
        missionFilledFrom = profile.id
        flightRules = profile.settings.flightRules ?? "vfr_ifr"
        cruiseAltitudeFt = String(profile.settings.cruiseAltitudeFt ?? Self.defaultCruiseAltitudeFt)
        flightCeilingFt = String(profile.settings.flightCeilingFt ?? Self.defaultFlightCeilingFt)
    }

    /// The ICAO type is required: the server needs one to create the
    /// aircraft, and none would leave the placeholder in place. `ZZZZ` (the
    /// ICAO code for a type without a designator) is the escape.
    private var resolvedIcaoType: String? {
        let value = icaoType.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard value.range(of: #"^[A-Z0-9]{1,4}$"#, options: .regularExpression) != nil else { return nil }
        return value
    }

    private func saveAircraftStep() async -> Bool {
        guard let icao = resolvedIcaoType else {
            icaoTypeMissing = true
            return false
        }

        let nick = nickname.trimmingCharacters(in: .whitespacesAndNewlines)
        let speed = Self.positiveInt(cruiseSpeedKt)
        let ceiling = Self.positiveInt(serviceCeilingFt)
        // Mirrors the placeholder rule: IFR-capable unless the pilot flies VFR only.
        let isIfr = flightRules != "vfr_only"
        do {
            if let existing = savedAircraft {
                savedAircraft = try await repository.updateAircraft(id: existing.id, request: UpdateAircraftRequest(
                    icaoType: icao,
                    nickname: nick,
                    isIfr: aircraftPreExisted ? nil : isIfr,
                    isFiki: isFiki,
                    cruiseSpeedKt: speed,
                    ceilingFt: ceiling,
                    isDefault: existing.isDefault ? nil : true
                ))
            } else {
                savedAircraft = try await repository.createAircraft(CreateAircraftRequest(
                    icaoType: icao,
                    nickname: nick.isEmpty ? nil : nick,
                    isIfr: isIfr,
                    // Icing is graded for the aircraft (#827): the box, pre-ticked
                    // from the profile, so picking "IFR FIKI" isn't lost (#850).
                    isFiki: isFiki,
                    cruiseSpeedKt: speed,
                    ceilingFt: ceiling,
                    isDefault: true
                ))
            }
        } catch {
            Self.logger.error("Failed to save the wizard aircraft: \(error)")
            errorMessage = "Couldn't save your aircraft: \(error.localizedDescription)"
            return false
        }

        if let profile = selectedProfile {
            let mission = SetupMission(
                flightRules: flightRules,
                cruiseAltitudeFt: Self.positiveInt(cruiseAltitudeFt) ?? Self.defaultCruiseAltitudeFt,
                flightCeilingFt: Self.positiveInt(flightCeilingFt) ?? Self.defaultFlightCeilingFt
            )
            do {
                try await repository.updateProfile(id: profile.id, request: SetupProfileUpdate(settings: mission))
            } catch {
                // Stay on the step: Next again PUTs the (now saved) aircraft and retries this.
                Self.logger.error("Failed to save the wizard mission: \(error)")
                errorMessage = "Couldn't save your usual mission: \(error.localizedDescription)"
                return false
            }
        }
        return true
    }

    private static func positiveInt(_ text: String) -> Int? {
        guard let value = Int(text.trimmingCharacters(in: .whitespacesAndNewlines)), value > 0 else { return nil }
        return value
    }
}
