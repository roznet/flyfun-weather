import Foundation
import OSLog

/// Drives the post-flight **debrief** form (past owned flights): a decision
/// (flew / cancelled / monitoring) plus — depending on the decision —
/// cancel-reason chips or per-category outcome grading, and a free-text note.
///
/// Data-shape parity with the web debrief form: cancelled emits `reasons`,
/// flown emits `outcomes` for the flagged advisory categories only, monitoring
/// emits neither (the server's `_decision_shape` validator enforces that).
@Observable
@MainActor
final class DebriefViewModel {
    let flight: FlightResponse
    let taxonomy: DebriefTaxonomy
    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "DebriefVM")

    // Form state
    var decision: String
    var selectedReasons: Set<String> = []
    /// tag id → outcome value, populated for the flagged categories on the flown
    /// form (defaulting to "consistent" — the pilot flips only what differed).
    var outcomes: [String: String] = [:]
    var note: String = ""

    /// The advisory categories flagged AMBER/RED on the open briefing, in
    /// taxonomy order — the only outcome rows the flown form shows (matches web).
    let flaggedTagIds: [String]

    var submitState: LoadingState<DebriefResponse> = .idle
    var errorMessage: String?
    /// Whether a debrief already existed (drives "Edit" vs "Add" copy + delete).
    private(set) var isEditing: Bool

    init(flight: FlightResponse,
         taxonomy: DebriefTaxonomy,
         flaggedTagIds: [String],
         repository: any BriefingRepository) {
        self.flight = flight
        self.taxonomy = taxonomy
        self.flaggedTagIds = flaggedTagIds
        self.repository = repository

        // Seed from an existing debrief (inlined on the flight) if present.
        if let existing = flight.debrief {
            self.isEditing = true
            self.decision = existing.decision
            self.selectedReasons = Set(existing.reasons)
            self.note = existing.note ?? ""
            // Start every flagged category at its stored value, else "consistent".
            var seeded: [String: String] = [:]
            for tagId in flaggedTagIds { seeded[tagId] = existing.outcomes[tagId] ?? "consistent" }
            self.outcomes = seeded
        } else {
            self.isEditing = false
            // Flown is the common case — default to it (matches the web form).
            self.decision = "flown"
            var seeded: [String: String] = [:]
            for tagId in flaggedTagIds { seeded[tagId] = "consistent" }
            self.outcomes = seeded
        }
    }

    // MARK: - Derived

    /// Cancel-reason chips (all tags) — shown when decision == cancelled.
    var reasonTags: [DebriefTaxonomy.TagOption] { taxonomy.tags }

    /// Outcome rows (flagged categories only) — shown when decision == flown.
    var outcomeTags: [DebriefTaxonomy.TagOption] {
        taxonomy.tags.filter { flaggedTagIds.contains($0.id) }
    }

    var noteRemaining: Int { taxonomy.noteMaxLength - note.count }
    var noteTooLong: Bool { noteRemaining < 0 }

    var canSubmit: Bool {
        guard !noteTooLong else { return false }
        if case .loading = submitState { return false }
        return true
    }

    // MARK: - Actions

    func toggleReason(_ tagId: String) {
        if selectedReasons.contains(tagId) { selectedReasons.remove(tagId) }
        else { selectedReasons.insert(tagId) }
    }

    func setOutcome(_ tagId: String, _ value: String) {
        outcomes[tagId] = value
    }

    /// Build the decision-appropriate request and upsert it.
    func submit() async {
        guard canSubmit else { return }
        submitState = .loading
        errorMessage = nil

        let trimmedNote = note.trimmingCharacters(in: .whitespacesAndNewlines)
        let request: DebriefRequest
        switch decision {
        case "cancelled":
            request = DebriefRequest(
                decision: "cancelled",
                reasons: taxonomy.tags.map(\.id).filter { selectedReasons.contains($0) },
                outcomes: [:],
                note: trimmedNote.isEmpty ? nil : trimmedNote
            )
        case "monitoring":
            // Watch-only: neither reasons nor outcomes (validator enforces empty).
            request = DebriefRequest(decision: "monitoring", reasons: [], outcomes: [:],
                                     note: trimmedNote.isEmpty ? nil : trimmedNote)
        default: // "flown"
            // Only emit outcomes for the flagged categories.
            var flownOutcomes: [String: String] = [:]
            for tagId in flaggedTagIds { flownOutcomes[tagId] = outcomes[tagId] ?? "consistent" }
            request = DebriefRequest(
                decision: "flown",
                reasons: [],
                outcomes: flownOutcomes,
                note: trimmedNote.isEmpty ? nil : trimmedNote
            )
        }

        do {
            let saved = try await repository.upsertDebrief(flightId: flight.id, request: request)
            submitState = .loaded(saved)
            isEditing = true
            Self.logger.info("Debrief saved for flight \(self.flight.id): \(saved.decision)")
        } catch {
            submitState = .error(error)
            errorMessage = error.localizedDescription
            Self.logger.error("Debrief submit failed: \(error)")
        }
    }

    /// Remove the stored debrief (idempotent).
    func delete() async {
        submitState = .loading
        errorMessage = nil
        do {
            try await repository.deleteDebrief(flightId: flight.id)
            submitState = .idle
            isEditing = false
            selectedReasons = []
            note = ""
            Self.logger.info("Debrief deleted for flight \(self.flight.id)")
        } catch {
            submitState = .error(error)
            errorMessage = error.localizedDescription
            Self.logger.error("Debrief delete failed: \(error)")
        }
    }
}
