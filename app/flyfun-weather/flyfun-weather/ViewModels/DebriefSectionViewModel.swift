import Foundation
import OSLog

/// Drives the **Debrief** section at the top of the Observed tab after the
/// live window (#1024). The server builds the report (#1023); this holds it,
/// sends the pilot's taps back, and swaps in what the server returns.
///
/// Reading order (web twin: `web/ts/components/debrief-section.ts`): the
/// headline, the flown / cancelled tap, then either the report's questions
/// (one-tap options, the "I changed level or route" toggle) or the cancel
/// reasons, a short note, and the report's detail lines folded.
///
/// Nothing is decided here: which questions are asked, their wording and the
/// options all come from the report. The only local state is a toggle ticked
/// before its question has an answer, and the note being typed.
@Observable
@MainActor
final class DebriefSectionViewModel {
    let flightId: String
    let taxonomy: DebriefTaxonomy
    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "DebriefSection")

    private(set) var report: DebriefReport?
    private(set) var debrief: DebriefResponse?
    private(set) var isBusy = false
    private(set) var errorMessage: String?
    /// "I changed level or route" ticked before a chip was picked: sent with
    /// the answer.
    private(set) var pendingAvoided: [String: Bool] = [:]
    var note: String
    /// Called after each save with the stored debrief (the briefing card and
    /// the flight list re-sync from it).
    var onSaved: (@MainActor (DebriefResponse?) -> Void)?

    init(flightId: String, debrief: DebriefResponse?, taxonomy: DebriefTaxonomy,
         repository: any BriefingRepository, report: DebriefReport? = nil) {
        self.flightId = flightId
        self.debrief = debrief
        self.taxonomy = taxonomy
        self.repository = repository
        self.report = report
        self.note = debrief?.note ?? ""
    }

    enum Decision: String { case flown, cancelled }

    /// The stored debrief's decision, else flown once any question has an
    /// answer (answering stores the flight as flown). Monitoring shows neither.
    var decision: Decision? {
        switch debrief?.decision {
        case "flown": return .flown
        case "cancelled": return .cancelled
        default: break
        }
        if report?.questions.contains(where: { $0.answer != nil }) == true { return .flown }
        return nil
    }

    var questions: [DebriefReport.Question] { report?.questions ?? [] }
    var detailLines: [DebriefReport.Line] { report?.summary?.lines ?? [] }
    var selectedReasons: Set<String> {
        debrief?.decision == "cancelled" ? Set(debrief?.reasons ?? []) : []
    }
    var noteTooLong: Bool { note.count > taxonomy.noteMaxLength }

    func isAvoided(_ question: DebriefReport.Question) -> Bool {
        question.answer?.avoided ?? pendingAvoided[question.id] ?? false
    }

    // MARK: - Load

    func load() async {
        do {
            report = try await repository.debriefReport(flightId: flightId)
        } catch {
            Self.logger.error("Debrief report failed for \(self.flightId): \(error)")
        }
    }

    // MARK: - Actions

    func choose(_ decision: Decision) async {
        guard decision != self.decision else { return }
        await saveDecision(decision, reasons: [])
    }

    /// Tap an option: answer it, or clear it when it is the stored answer.
    func answer(_ question: DebriefReport.Question, option: String) async {
        let clearing = question.answer?.answer == option
        await send(.init(questionId: question.id, answer: clearing ? nil : option,
                         avoided: isAvoided(question)))
    }

    func setAvoided(_ question: DebriefReport.Question, _ avoided: Bool) async {
        if let stored = question.answer {
            await send(.init(questionId: question.id, answer: stored.answer, avoided: avoided))
        } else {
            pendingAvoided[question.id] = avoided
        }
    }

    func toggleReason(_ tagId: String) async {
        var reasons = selectedReasons
        if reasons.contains(tagId) { reasons.remove(tagId) } else { reasons.insert(tagId) }
        await saveDecision(.cancelled, reasons: taxonomy.tags.map(\.id).filter { reasons.contains($0) })
    }

    func saveNote() async {
        guard let decision, !noteTooLong else { return }
        await saveDecision(decision, reasons: decision == .cancelled ? Array(debrief?.reasons ?? []) : [])
    }

    // MARK: - Saving

    private var trimmedNote: String? {
        let t = note.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }

    private func saveDecision(_ decision: Decision, reasons: [String]) async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            let request = DebriefRequest(decision: decision.rawValue, reasons: reasons, outcomes: [:],
                                         note: noteTooLong ? debrief?.note : trimmedNote, form: "questions")
            let saved = try await repository.upsertDebrief(flightId: flightId, request: request)
            debrief = saved
            onSaved?(saved)
        } catch {
            errorMessage = "Could not save. Check your connection and try again."
            Self.logger.error("Debrief save failed: \(error)")
            return
        }
        // The report reads the decision: a cancelled flight asks nothing. The
        // decision is stored by now, so a failed refetch keeps the old report
        // instead of showing the save as failed.
        do {
            report = try await repository.debriefReport(flightId: flightId)
        } catch {
            Self.logger.error("Debrief report refetch failed: \(error)")
        }
    }

    private func send(_ item: DebriefAnswersRequest.Item) async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do {
            report = try await repository.saveDebriefAnswers(
                flightId: flightId, request: DebriefAnswersRequest(answers: [item]))
            pendingAvoided[item.questionId] = nil
            // The server stored the flight as flown with this answer.
            if let d = debrief, d.decision != "flown" {
                debrief = DebriefResponse(flightId: d.flightId, decision: "flown", reasons: [], outcomes: d.outcomes,
                                          note: d.note, form: "questions", createdAt: d.createdAt,
                                          updatedAt: d.updatedAt)
            }
            onSaved?(debrief)
        } catch {
            errorMessage = "Could not save. Check your connection and try again."
            Self.logger.error("Debrief answer failed: \(error)")
        }
    }
}
