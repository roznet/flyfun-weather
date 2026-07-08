import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

/// On-device Foundation Models parse of a messy flight phrase into `{place, when}`.
///
/// This is resolver **tier 2** — it only runs when the deterministic tier found
/// nothing *and* an Apple-Intelligence model is available on the device. The
/// model flattens language only; the deterministic matcher in `FlightResolver`
/// re-runs against real flights and stays the authority.
enum FlightQueryModel {
    struct Parsed: Sendable {
        let place: String?
        let when: String?
    }

    /// Returns nil when no on-device model is available (older/non-AI device,
    /// feature disabled, model not downloaded) or on any generation error — the
    /// caller then falls straight to Siri disambiguation with no loss of the
    /// deterministic path.
    static func parse(_ phrase: String) async -> Parsed? {
        #if canImport(FoundationModels)
        guard case .available = SystemLanguageModel.default.availability else { return nil }
        do {
            let session = LanguageModelSession()
            let prompt = "Extract the airport or city and the timeframe from this flight request: \"\(phrase)\""
            let response = try await session.respond(to: prompt, generating: FlightQuery.self)
            let query = response.content
            return Parsed(place: query.place, when: query.when)
        } catch {
            return nil
        }
        #else
        return nil
        #endif
    }
}

#if canImport(FoundationModels)
/// Guided-generation shape the on-device model fills in.
@Generable
struct FlightQuery {
    @Guide(description: "airport or city name mentioned, e.g. Fairoaks")
    var place: String?
    @Guide(description: "when: tomorrow, Saturday, next week")
    var when: String?
}
#endif
