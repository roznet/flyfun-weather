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
