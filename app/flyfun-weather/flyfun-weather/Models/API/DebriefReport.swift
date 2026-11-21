import Foundation

/// The post-flight debrief report (#1023), `GET /api/flights/{id}/debrief/report`
/// and the reply of `PUT /api/flights/{id}/debrief/answers`. Built by the server
/// on each call (`debriefs/report.py`); the app draws it as served (#1024): the
/// headline, the detail lines, the question text and the option labels are the
/// server's words. Nothing here is computed on the phone.
///
/// Decoded with `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`), so no
/// snake `CodingKeys`. Only the fields the Debrief section draws are decoded;
/// the strip, airports and `orphan_answers` (keyed by question ids, which the
/// key strategy would rewrite) are left out on purpose.
nonisolated struct DebriefReport: Codable, Sendable, Equatable {
    let flightId: String?
    /// `ok`, or why the recap could not be built (`not_recorded`, …).
    let status: String?
    let message: String?
    let cancelled: Bool?
    let summary: Summary?
    /// One to three questions, in the server's order. Empty when the
    /// instruments covered the flight, or it was cancelled.
    let questions: [Question]

    nonisolated struct Summary: Codable, Sendable, Equatable {
        let headline: String
        let differsFromForecast: Bool?
        let lines: [Line]
    }

    nonisolated struct Line: Codable, Sendable, Equatable {
        let kind: String
        let text: String
    }

    nonisolated struct Question: Codable, Sendable, Equatable, Identifiable {
        /// `kind-from_nm-to_nm`, e.g. `icing-110-192`.
        let id: String
        /// `icing` | `cloud` | `turbulence` | `showers`.
        let kind: String
        let text: String
        let options: [Option]
        /// Whether "I changed level or route to stay out of it" is offered.
        let avoidable: Bool
        let answer: Answer?
    }

    nonisolated struct Option: Codable, Sendable, Equatable, Identifiable {
        let id: String
        let label: String
    }

    /// A stored answer (`storage/debriefs.py` `_answer_dict`).
    nonisolated struct Answer: Codable, Sendable, Equatable {
        let answer: String
        let avoided: Bool
        let note: String?
        let answeredAt: String?
    }

    /// The headline to show: the server's, else its message (a report that
    /// could not be built says why).
    var headline: String? {
        if let h = summary?.headline, !h.isEmpty { return h }
        return message
    }
}

/// Body of `PUT /api/flights/{id}/debrief/answers`. Encoded with
/// `JSONEncoder.weatherBrief` (`.convertToSnakeCase`: `questionId` →
/// `question_id`). `answer` nil clears the answer: the synthesized encoder
/// omits it and the server reads the missing field as null.
nonisolated struct DebriefAnswersRequest: Encodable, Sendable, Equatable {
    let answers: [Item]

    nonisolated struct Item: Encodable, Sendable, Equatable {
        let questionId: String
        let answer: String?
        let avoided: Bool
        var note: String? = nil
    }
}
