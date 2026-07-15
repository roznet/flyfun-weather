import Foundation

/// A stored pilot debrief for a past flight (wire shape of
/// `GET/PUT /api/flights/{id}/debrief`, and inlined on `/api/flights`).
///
/// ## Key-strategy note
/// Decoded with the shared `JSONDecoder.weatherBrief` (`.convertFromSnakeCase`),
/// both standalone and when inlined on `FlightResponse`. The scalar keys
/// (`flight_id`, `created_at`, …) map to the camelCase properties via that
/// strategy, so this type carries **no explicit snake `CodingKeys`** — adding
/// them would break the nested decode (the strategy rewrites the wire key first,
/// then fails to match a snake CodingKey).
///
/// `outcomes` is keyed by condition-tag ids (`IMC`, `ICE`, …). Those have no
/// underscores, and `.convertFromSnakeCase` leaves single-word keys untouched,
/// so the dict decodes verbatim. (Encoding is different — see `DebriefRequest`.)
struct DebriefResponse: Codable, Sendable, Equatable {
    let flightId: String
    /// `flown` | `cancelled` | `monitoring`.
    let decision: String
    /// Cancel-reason tag ids (cancelled only).
    let reasons: [String]
    /// tag id → outcome value (`consistent` | `better` | `worse`), flown only.
    let outcomes: [String: String]
    let note: String?
    let createdAt: String
    let updatedAt: String
}

/// Upsert body for `PUT /api/flights/{id}/debrief`.
///
/// Encoded with a plain `JSONEncoder` (NOT `JSONEncoder.weatherBrief`): the
/// shared `.convertToSnakeCase` strategy would lowercase the `outcomes` tag-id
/// keys (`IMC` → `imc`) and the server would reject them. The field names are
/// already the lowercase words the server expects, so a plain encoder is exact.
struct DebriefRequest: Encodable, Sendable, Equatable {
    let decision: String
    let reasons: [String]
    let outcomes: [String: String]
    let note: String?

    /// JSON body with tag-id dictionary keys preserved verbatim.
    func encoded() throws -> Data {
        try JSONEncoder().encode(self)
    }
}
