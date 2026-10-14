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

        isSaving = true
        defer { isSaving = false }

        let request = CreateAircraftRequest(
            icaoType: icaoType,
            tailNumber: optionalText(tailNumber)?.uppercased(),
            nickname: optionalText(nickname),
            isIfr: isIfr,
            isFiki: isFiki,
            cruiseSpeedKt: cruiseSpeed,
            ceilingFt: ceiling,
            isDefault: isDefault
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

        isSaving = true
        defer { isSaving = false }

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
            isDefault: (isDefault && !original.isDefault) ? true : nil
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
