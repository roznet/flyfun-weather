import Foundation

/// A 👍 / 👎 rating on a briefing's AI digest, posted to `POST /api/feedback`
/// with `category = "digest_rating"` (mirrors the web digest-feedback widget).
///
/// The comment is optional for a thumb — the server only requires text when
/// there's no `sentiment`. Encoded with `JSONEncoder.weatherBrief`
/// (`.convertToSnakeCase`), so the camelCase properties become `flight_id`,
/// `pack_timestamp`, `contact_ok` on the wire. No dictionary keys here, so the
/// snake strategy is safe (unlike `DebriefRequest`).
struct DigestFeedbackRequest: Encodable, Sendable, Equatable {
    /// The rated flight.
    let flightId: String
    /// The rated briefing pack's `fetch_timestamp` (ISO 8601) — the rating is
    /// per-pack-version, so this pins which briefing the pilot judged.
    let packTimestamp: String
    /// Always `"digest_rating"` for the thumb path.
    let category: String
    /// Optional free-text (≤ 2000 chars). Empty is valid for a bare thumb.
    let comment: String
    /// `"up"` | `"down"`.
    let sentiment: String
    /// Always `"digest"` for the thumb path.
    let target: String
    /// Whether the pilot allows follow-up contact about this feedback.
    let contactOk: Bool

    init(flightId: String, packTimestamp: String, sentiment: String,
         comment: String, contactOk: Bool) {
        self.flightId = flightId
        self.packTimestamp = packTimestamp
        self.category = "digest_rating"
        self.comment = comment
        self.sentiment = sentiment
        self.target = "digest"
        self.contactOk = contactOk
    }
}

/// Free-text feedback with a category, posted to `POST /api/feedback` with
/// `target = "general"` — the iOS twin of the web help page's "Submit Feedback"
/// modal (`help-main.ts::showFeedbackModal`). No `sentiment`, so the server
/// requires a non-empty `comment` (`FeedbackRequest.require_comment_unless_thumb`).
///
/// `flightId` / `packTimestamp` stay empty for app-level feedback (the web sends
/// the same empty strings); they exist so a future in-briefing entry point can
/// pin the report to a pack without a second request type.
struct GeneralFeedbackRequest: Encodable, Sendable, Equatable {
    let flightId: String
    let packTimestamp: String
    /// One of the server's `ALLOWED_CATEGORIES` minus `digest_rating` — see
    /// `FeedbackCategory`, which is the only thing that builds this.
    let category: String
    /// Free-text (≤ 2000 chars). Must be non-empty.
    let comment: String
    /// Always `"general"` for this path.
    let target: String
    /// Whether the pilot allows a reply by email.
    let contactOk: Bool

    init(category: FeedbackCategory, comment: String, contactOk: Bool,
         flightId: String = "", packTimestamp: String = "") {
        self.flightId = flightId
        self.packTimestamp = packTimestamp
        self.category = category.rawValue
        self.comment = comment
        self.target = "general"
        self.contactOk = contactOk
    }
}

/// The categories the feedback form offers. Raw values mirror the server's
/// `ALLOWED_CATEGORIES` and the labels mirror the web `feedback.cat.*` strings,
/// so a report filed from iOS lands in the same admin bucket as a web one.
enum FeedbackCategory: String, CaseIterable, Identifiable, Sendable {
    case dataIssue = "data_issue"
    case tooConservative = "too_conservative"
    case tooOptimistic = "too_optimistic"
    case incorrectInterpretation = "incorrect_interpretation"
    case other = "other"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .dataIssue: return "Briefing Data Issue"
        case .tooConservative: return "Briefing Too Conservative"
        case .tooOptimistic: return "Briefing Too Optimistic"
        case .incorrectInterpretation: return "Briefing Incorrect Interpretation"
        case .other: return "Other Bug/Issue"
        }
    }
}
