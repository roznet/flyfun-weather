import Foundation
import OSLog

/// State and actions behind `AircraftFormSheet`: add (`POST`) or edit (`PUT`)
/// one saved aircraft, with search-as-you-type ICAO-type suggestions.
///
/// Shared by the flight form's aircraft picker (`AddFlightViewModel`) and the
/// Settings → Aircraft list (`AircraftListViewModel`, #778). It only edits the
/// one aircraft: `save()` hands the saved row back and the owner re-reads its
/// own list, since the server may have changed other rows too (a new default
/// clears the old one; a `POST` over a sole placeholder replaces it).
@Observable
@MainActor
final class AircraftFormModel {
    var icaoType: String = ""
    var tailNumber: String = ""
    var nickname: String = ""
    var cruiseSpeedKt: String = ""
    var ceilingFt: String = ""
    var isIfr: Bool = false
    var isFiki: Bool = false
    var isDefault: Bool = false

    // Climb / descent (#1005): the preset picker over the values it sets.
    var climbIasText: String = ""
    var climbRateText: String = ""
    /// A descent is a rate or an angle: filling one clears the other.
    var descentRateText: String = "" {
        didSet { if !descentRateText.isEmpty, descentRateText != oldValue { descentAngleText = "" } }
    }
    var descentAngleText: String = "" {
        didSet { if !descentAngleText.isEmpty, descentAngleText != oldValue { descentRateText = "" } }
    }
    var isTurbo: Bool = false
    /// Empty until the table loads; the section stays hidden and a save leaves
    /// the aircraft's climb and descent unchanged.
    private(set) var presets: [AircraftPerformancePreset] = []
    private(set) var suggestedPresetId: String = ""
    private(set) var basePresetId: String = ""
    /// The selection tracks the ICAO type's suggestion (not picked by hand).
    private var followsType = true
    /// The next table to arrive opens the form on this aircraft's values.
    private var needsPerformanceOpen = true
    private var presetLoadSeq = 0

    private(set) var selectedType: AircraftTypeResponse?
    private(set) var typeSuggestions: [AircraftTypeResponse] = []
    private(set) var isSearchingTypes: Bool = false
    private(set) var isSaving: Bool = false
    var errorMessage: String?
    /// The saved aircraft being edited (`PUT`), or nil when adding (`POST`).
    /// Set by `prepareEdit` / `prepareNew`.
    private(set) var editingAircraft: AircraftResponse?

    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AircraftForm")

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    /// The picker's value when the fields differ from the base preset.
    static let customPresetSelection = "custom"

    var basePreset: AircraftPerformancePreset? {
        presets.first { $0.id == basePresetId }
    }

    var performanceValues: AircraftPerformanceValues {
        AircraftPerformanceValues(
            climbIasKt: AircraftPerformanceForm.number(from: climbIasText),
            climbRateFpm: AircraftPerformanceForm.number(from: climbRateText),
            descentRateFpm: AircraftPerformanceForm.number(from: descentRateText),
            descentAngleDeg: AircraftPerformanceForm.number(from: descentAngleText),
            turbo: isTurbo
        )
    }

    var isPerformanceCustom: Bool {
        guard let basePreset else { return false }
        return AircraftPerformanceForm.isCustom(performanceValues, base: basePreset)
    }

    var presetSelection: String {
        isPerformanceCustom ? Self.customPresetSelection : basePresetId
    }

    var customPresetTitle: String {
        "Custom (based on \(AircraftPerformanceForm.presetName(basePresetId)))"
    }

    var performanceHint: String {
        let name = AircraftPerformanceForm.presetName(basePresetId)
        return isPerformanceCustom
            ? "Your own values, starting from \(name). Pick a preset again to restore its values."
            : "\(name) values, used for the briefing's climb and descent. Change any value to make it your own."
    }

    /// Picking a preset writes all its values back, turbo included.
    func selectPreset(_ id: String) {
        guard id != Self.customPresetSelection,
              let preset = presets.first(where: { $0.id == id }) else { return }
        basePresetId = id
        followsType = id == suggestedPresetId
        writePerformance(AircraftPerformanceForm.values(of: preset))
    }

    /// Load the preset table for the type and cruise speed in the form. The first
    /// table opens the form; later ones move a preset that follows the type to
    /// the new suggestion. A picked or custom preset stays.
    func refreshPresets() async {
        presetLoadSeq += 1
        let seq = presetLoadSeq
        let response: AircraftPresetsResponse
        do {
            response = try await repository.aircraftPresets(
                icaoType: presetQueryType, cruiseSpeedKt: presetQueryCruiseSpeed)
        } catch {
            Self.logger.debug("Aircraft presets unavailable: \(error)")
            return
        }
        guard seq == presetLoadSeq, !Task.isCancelled else { return }
        presets = response.presets
        suggestedPresetId = response.suggested
        if needsPerformanceOpen {
            openPerformance()
        } else if followsType, !isPerformanceCustom, response.suggested != basePresetId,
                  let preset = basePresetCandidate(response.suggested) {
            basePresetId = preset.id
            writePerformance(AircraftPerformanceForm.values(of: preset))
        }
    }

    /// The sheet is editing a saved aircraft rather than adding one.
    var isEditing: Bool { editingAircraft != nil }

    var title: String {
        guard let editingAircraft else { return "Add Aircraft" }
        return editingAircraft.isPlaceholder ? "Set Up Your Aircraft" : "Edit Aircraft"
    }

    var canSave: Bool {
        guard !isSaving else { return false }
        // Editing may leave the type blank: it is then omitted from the PUT
        // and the server keeps the stored one (the placeholder's `ZZZZ` is
        // deliberately not prefilled).
        if isEditing, icaoType.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return true
        }
        return resolvedIcaoType != nil
    }

    /// Reset the sheet for a new aircraft. `existing` is the owner's current
    /// list: "Default aircraft" starts on for the first aircraft, and when the
    /// only one is the server's placeholder (the server replaces the
    /// placeholder with this aircraft, which then has to be the default).
    func prepareNew(existing: [AircraftResponse]) {
        editingAircraft = nil
        icaoType = ""
        tailNumber = ""
        nickname = ""
        cruiseSpeedKt = ""
        ceilingFt = ""
        isIfr = false
        isFiki = false
        isDefault = existing.isEmpty || (existing.count == 1 && existing[0].isPlaceholder)
        selectedType = nil
        typeSuggestions = []
        errorMessage = nil
        resetPerformance()
    }

    /// Prefill the sheet from a saved aircraft for an edit (`PUT`). A
    /// placeholder's `ZZZZ` type is left blank so the field shows its prompt;
    /// a blank type is omitted from the PUT, keeping the stored one.
    func prepareEdit(_ aircraft: AircraftResponse) {
        editingAircraft = aircraft
        if aircraft.isPlaceholder {
            icaoType = ""
            selectedType = nil
        } else {
            icaoType = aircraft.icaoType
            // Marks the prefilled type as already chosen, so opening the sheet
            // doesn't fire a type search for it.
            selectedType = AircraftTypeResponse(
                icao: aircraft.icaoType, manufacturer: aircraft.typeName, model: "", category: nil
            )
        }
        tailNumber = aircraft.tailNumber ?? ""
        nickname = aircraft.nickname ?? ""
        cruiseSpeedKt = aircraft.cruiseSpeedKt.map { String($0) } ?? ""
        ceilingFt = aircraft.ceilingFt.map { String($0) } ?? ""
        isIfr = aircraft.isIfr
        isFiki = aircraft.isFiki
        isDefault = aircraft.isDefault
        typeSuggestions = []
        errorMessage = nil
        resetPerformance()
    }

    /// Save the sheet: `PUT` when editing, `POST` when adding. Returns the
    /// saved aircraft, or nil with `errorMessage` set.
    func save() async -> AircraftResponse? {
        if let editingAircraft {
            return await update(editingAircraft)
        }
        return await create()
    }

    /// Search aircraft types as the user types. The caller (the sheet's
    /// `.task(id:)`) debounces and cancels superseded searches; we also bail
    /// out if the task was cancelled before the network call so a stale query
    /// never overwrites results.
    func searchTypes() async {
        let query = icaoType.trimmingCharacters(in: .whitespacesAndNewlines)
        // Clear a previously-picked type once the text diverges from it.
        if let selectedType, query.uppercased() != selectedType.icao {
            self.selectedType = nil
        }
        if let selectedType, query.uppercased() == selectedType.icao {
            typeSuggestions = []
            isSearchingTypes = false
            return
        }
        guard !query.isEmpty else {
            typeSuggestions = []
            isSearchingTypes = false
            return
        }
        guard query.count <= 20 else { return }

        // Defensive cancellation guard: even though the debounce in the view
        // cancels superseded `.task(id:)` runs, never fire a stale search.
        guard !Task.isCancelled else { return }

        isSearchingTypes = true
        defer { isSearchingTypes = false }
        do {
            let results = try await repository.searchAircraftTypes(query)
            // The query may have been superseded while the request was in flight.
            guard !Task.isCancelled else { return }
            typeSuggestions = results
        } catch {
            typeSuggestions = []
            Self.logger.debug("Aircraft type search unavailable: \(error)")
        }
    }

    func selectType(_ type: AircraftTypeResponse) {
        selectedType = type
        icaoType = type.icao
        typeSuggestions = []
        errorMessage = nil
    }

    // MARK: - Private

    /// The server's query limit is four characters: a longer or half-typed entry
    /// asks for the table without a type rather than getting a 422.
    private var presetQueryType: String {
        let value = icaoType.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        return value.range(of: #"^[A-Z0-9]{1,4}$"#, options: .regularExpression) != nil ? value : ""
    }

    private var presetQueryCruiseSpeed: Int? {
        guard let speed = Int(cruiseSpeedKt.trimmingCharacters(in: .whitespacesAndNewlines)),
              speed > 0, speed <= 600 else { return nil }
        return speed
    }

    private func basePresetCandidate(_ id: String) -> AircraftPerformancePreset? {
        presets.first { $0.id == id }
    }

    private func resetPerformance() {
        presetLoadSeq += 1  // a table still on its way belongs to the previous aircraft
        presets = []
        suggestedPresetId = ""
        basePresetId = ""
        followsType = true
        needsPerformanceOpen = true
        writePerformance(AircraftPerformanceValues(turbo: false))
    }

    /// Picker = the resolved preset; values = the resolved performance.
    private func openPerformance() {
        needsPerformanceOpen = false
        let aircraft = editingAircraft
        let performance = aircraft?.performance
        if let performance, basePresetCandidate(performance.preset) != nil {
            basePresetId = performance.preset
        } else {
            basePresetId = suggestedPresetId
        }
        let picked = aircraft?.performancePreset ?? ""
        followsType = picked.isEmpty || picked == suggestedPresetId
        if let performance, aircraft?.isPlaceholder == false, basePreset != nil {
            writePerformance(AircraftPerformanceForm.values(of: performance))
        } else if let basePreset {
            writePerformance(AircraftPerformanceForm.values(of: basePreset))
        }
    }

    private func writePerformance(_ values: AircraftPerformanceValues) {
        climbIasText = AircraftPerformanceForm.text(from: values.climbIasKt)
        climbRateText = AircraftPerformanceForm.text(from: values.climbRateFpm)
        descentRateText = AircraftPerformanceForm.text(from: values.descentRateFpm)
        descentAngleText = AircraftPerformanceForm.text(from: values.descentAngleDeg)
        isTurbo = values.turbo
    }

    /// The save payload, or nil when the table never loaded (leave unchanged).
    private var performanceOverrides: AircraftPerformanceOverrides? {
        basePreset.map { AircraftPerformanceForm.overrides(for: performanceValues, base: $0) }
    }

    /// The server's limits, said in the form's words rather than as a 422.
    private func validatePerformance() {
        guard basePreset != nil else { return }
        let values = performanceValues
        if (values.climbIasKt ?? 0) > 400 {
            errorMessage = "Climb speed must be 400 kt or less."
        } else if (values.climbRateFpm ?? 0) > 8000 {
            errorMessage = "Climb rate must be 8,000 ft/min or less."
        } else if (values.descentRateFpm ?? 0) > 8000 {
            errorMessage = "Descent rate must be 8,000 ft/min or less."
        } else if (values.descentAngleDeg ?? 0) > 6 {
            errorMessage = "Descent angle must be 6\u{00b0} or less."
        }
    }

    private func create() async -> AircraftResponse? {
        guard let icaoType = resolvedIcaoType else {
            errorMessage = "Enter a valid ICAO aircraft type, for example C172."
            return nil
        }
        errorMessage = nil
        let cruiseSpeed = optionalPositiveInt(cruiseSpeedKt, fieldName: "Cruise speed")
        guard errorMessage == nil else { return nil }
        let ceiling = optionalPositiveInt(ceilingFt, fieldName: "Service ceiling")
        guard errorMessage == nil else { return nil }
        validatePerformance()
        guard errorMessage == nil else { return nil }

        isSaving = true
        defer { isSaving = false }

        let performance = performanceOverrides
        let request = CreateAircraftRequest(
            icaoType: icaoType,
            tailNumber: optionalText(tailNumber)?.uppercased(),
            nickname: optionalText(nickname),
            isIfr: isIfr,
            isFiki: isFiki,
            cruiseSpeedKt: cruiseSpeed,
            ceilingFt: ceiling,
            isDefault: isDefault,
            performancePreset: performance?.performancePreset,
            isTurbo: performance?.isTurbo,
            climbIasKt: performance?.climbIasKt,
            climbRateFpm: performance?.climbRateFpm,
            descentRateFpm: performance?.descentRateFpm,
            descentAngleDeg: performance?.descentAngleDeg
        )

        do {
            return try await repository.createAircraft(request)
        } catch {
            errorMessage = error.localizedDescription
            Self.logger.error("Create aircraft failed: \(error)")
            return nil
        }
    }

    private func update(_ original: AircraftResponse) async -> AircraftResponse? {
        let typedType = icaoType.trimmingCharacters(in: .whitespacesAndNewlines)
        var icaoTypeToSend: String?
        if !typedType.isEmpty {
            guard let resolved = resolvedIcaoType else {
                errorMessage = "Enter a valid ICAO aircraft type, for example C172."
                return nil
            }
            icaoTypeToSend = resolved
        }
        errorMessage = nil
        let cruiseSpeed = optionalPositiveInt(cruiseSpeedKt, fieldName: "Cruise speed")
        guard errorMessage == nil else { return nil }
        let ceiling = optionalPositiveInt(ceilingFt, fieldName: "Service ceiling")
        guard errorMessage == nil else { return nil }
        validatePerformance()
        guard errorMessage == nil else { return nil }

        isSaving = true
        defer { isSaving = false }

        let performance = performanceOverrides
        let request = UpdateAircraftRequest(
            icaoType: icaoTypeToSend,
            // Always sent: "" clears a tail number / nickname the pilot erased.
            tailNumber: optionalText(tailNumber)?.uppercased() ?? "",
            nickname: optionalText(nickname) ?? "",
            isIfr: isIfr,
            isFiki: isFiki,
            // nil = unchanged server-side (a blank field can't clear these).
            cruiseSpeedKt: cruiseSpeed,
            ceilingFt: ceiling,
            // Only ever *promote*: the server keeps one default per account, so
            // un-defaulting is done by making another aircraft the default.
            isDefault: (isDefault && !original.isDefault) ? true : nil,
            performancePreset: performance?.performancePreset,
            isTurbo: performance?.isTurbo,
            climbIasKt: performance?.climbIasKt,
            climbRateFpm: performance?.climbRateFpm,
            descentRateFpm: performance?.descentRateFpm,
            descentAngleDeg: performance?.descentAngleDeg
        )

        do {
            return try await repository.updateAircraft(id: original.id, request: request)
        } catch {
            errorMessage = error.localizedDescription
            Self.logger.error("Update aircraft failed: \(error)")
            return nil
        }
    }

    private var resolvedIcaoType: String? {
        let value = icaoType
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased()
        if let selectedType, value == selectedType.icao {
            return selectedType.icao
        }
        guard value.range(of: #"^[A-Z0-9]{1,4}$"#, options: .regularExpression) != nil else {
            return nil
        }
        return value
    }

    private func optionalText(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func optionalPositiveInt(_ value: String, fieldName: String) -> Int? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard let intValue = Int(trimmed), intValue > 0 else {
            errorMessage = "\(fieldName) must be a positive number."
            return nil
        }
        return intValue
    }
}

extension [AircraftResponse] {
    /// Default first, then by title: the order of the flight form's picker and
    /// the Settings list.
    func sortedForPicker() -> [AircraftResponse] {
        sorted { lhs, rhs in
            if lhs.isDefault != rhs.isDefault {
                return lhs.isDefault && !rhs.isDefault
            }
            return lhs.pickerTitle.localizedCaseInsensitiveCompare(rhs.pickerTitle) == .orderedAscending
        }
    }
}
