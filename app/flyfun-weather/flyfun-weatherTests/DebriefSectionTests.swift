//
//  DebriefSectionTests.swift
//  flyfun-weatherTests
//
//  The Debrief section (#1024): the report DTO decodes the shared fixture
//  (`app/fixtures/api/debrief-report.json`, built by the #1023 server), the
//  answers body is the server's shape, and the section view model sends taps
//  back without deciding anything itself.
//

import Testing
import Foundation
@testable import flyfun_weather

@MainActor @Suite struct DebriefSectionTests {

    private var report: DebriefReport { FixtureFiles.decode(DebriefReport.self, "debrief-report") }

    private func answered(_ r: DebriefReport, _ id: String, _ answer: String, avoided: Bool = false) -> DebriefReport {
        DebriefReport(flightId: r.flightId, status: r.status, message: r.message, cancelled: r.cancelled,
                      summary: r.summary, questions: r.questions.map { q in
                          guard q.id == id else { return q }
                          return DebriefReport.Question(
                              id: q.id, kind: q.kind, text: q.text, options: q.options, avoidable: q.avoidable,
                              answer: .init(answer: answer, avoided: avoided, note: nil, answeredAt: nil))
                      })
    }

    @Test func theSharedFixtureDecodes() {
        let r = report
        #expect(r.headline == "No radar along the route.")
        #expect(r.questions.map(\.kind) == ["icing", "cloud", "showers"])
        #expect(r.questions.first?.options.map(\.id) == ["none", "light", "moderate_plus", "not_in_cloud"])
        #expect(r.questions.map(\.avoidable) == [true, false, true])
        #expect(r.questions.allSatisfy { $0.answer == nil })
        #expect((r.summary?.lines.count ?? 0) == 5)
    }

    @Test func theAnswersBodyIsSnakeCaseAndAClearOmitsTheAnswer() throws {
        let body = DebriefAnswersRequest(answers: [
            .init(questionId: "icing-110-192", answer: "light", avoided: true),
            .init(questionId: "cloud-55-165", answer: nil, avoided: false),
        ])
        let json = try JSONSerialization.jsonObject(with: JSONEncoder.weatherBrief.encode(body)) as? [String: Any]
        let answers = try #require(json?["answers"] as? [[String: Any]])
        #expect(answers[0]["question_id"] as? String == "icing-110-192")
        #expect(answers[0]["answer"] as? String == "light")
        #expect(answers[0]["avoided"] as? Bool == true)
        #expect(answers[1]["answer"] == nil)
    }

    @Test func theDebriefRequestCarriesTheQuestionsForm() throws {
        let req = DebriefRequest(decision: "flown", reasons: [], outcomes: [:], note: nil, form: "questions")
        let json = try JSONSerialization.jsonObject(with: req.encoded()) as? [String: Any]
        #expect(json?["form"] as? String == "questions")
        let legacy = DebriefRequest(decision: "flown", reasons: [], outcomes: [:], note: nil)
        let legacyJson = try JSONSerialization.jsonObject(with: legacy.encoded()) as? [String: Any]
        #expect(legacyJson?["form"] == nil)
    }

    @Test func flownTapStoresTheQuestionsFormAndReloadsTheReport() async {
        let mock = MockBriefingRepository()
        mock.debriefReportResult = .success(report)
        let vm = DebriefSectionViewModel(flightId: "ZZ-flt", debrief: nil, taxonomy: .bundledBaseline,
                                         repository: mock, report: report)
        #expect(vm.decision == nil)
        await vm.choose(.flown)
        #expect(mock.upsertedDebriefs.map(\.decision) == ["flown"])
        #expect(mock.upsertedDebriefs.first?.form == "questions")
        #expect(mock.upsertedDebriefs.first?.outcomes.isEmpty == true)
        #expect(vm.decision == .flown)
    }

    @Test func anOptionTapAnswersAndASecondTapClears() async {
        let mock = MockBriefingRepository()
        let r = report
        let icing = r.questions[0]
        mock.debriefReportResult = .success(answered(r, icing.id, "light"))
        let vm = DebriefSectionViewModel(flightId: "ZZ-flt", debrief: nil, taxonomy: .bundledBaseline,
                                         repository: mock, report: r)
        await vm.setAvoided(icing, true)  // before an answer: local, nothing sent
        #expect(mock.savedDebriefAnswers.isEmpty)
        await vm.answer(icing, option: "light")
        #expect(mock.savedDebriefAnswers.first?.answers
                == [.init(questionId: icing.id, answer: "light", avoided: true)])
        // An answer reads as flown even before a debrief row is read.
        #expect(vm.decision == .flown)
        let stored = vm.questions[0]
        await vm.answer(stored, option: "light")
        #expect(mock.savedDebriefAnswers.last?.answers.first?.answer == nil)
    }

    @Test func cancelReasonsAreSentInTaxonomyOrder() async {
        let mock = MockBriefingRepository()
        mock.debriefReportResult = .success(report)
        let vm = DebriefSectionViewModel(flightId: "ZZ-flt", debrief: nil, taxonomy: .bundledBaseline,
                                         repository: mock, report: report)
        await vm.toggleReason("WIND")
        await vm.toggleReason("IMC")
        #expect(mock.upsertedDebriefs.last?.decision == "cancelled")
        #expect(mock.upsertedDebriefs.last?.reasons == ["IMC", "WIND"])
    }

    @Test func theListGlyphFollowsTheServerCount() {
        var flight = makeFlight(id: "ZZ-flt", departureTime: "2026-01-01T09:00:00Z")
        flight.section = "past"
        #expect(!flight.needsDebrief)
        flight.questionsOpen = 2
        #expect(flight.needsDebrief)
        var shared = makeFlight(id: "ZZ-shared", departureTime: "2026-01-01T09:00:00Z", role: .subscriber)
        shared.questionsOpen = 2
        #expect(!shared.needsDebrief)
    }
}
