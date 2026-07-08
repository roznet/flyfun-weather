import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

/// One upcoming flight offered to the on-device model for grounded selection.
/// `line` is a human-readable, model-facing description (route + airport names +
/// date); `id` is the real flight id we map the choice back to.
struct FlightCandidate: Sendable {
    let id: String
    let line: String
}

/// Resolver **tier 2**: pick which of the user's upcoming flights a free phrase
/// refers to. Injectable (a protocol) so the integration is unit-testable with a
/// fake, independent of any on-device model.
protocol FlightPhraseResolving: Sendable {
    /// Return the chosen flight id, or nil for "none / model unavailable".
    /// Implementations must never return an id outside `candidates`.
    ///
    /// `nonisolated` (the app target defaults to `@MainActor`) — on-device model
    /// inference shouldn't be pinned to the main actor, and this keeps the
    /// requirement satisfiable from any context, including a test fake.
    nonisolated func pick(phrase: String, today: String, candidates: [FlightCandidate]) async -> String?
}

/// Grounded selection via the on-device Apple-Intelligence model.
///
/// Unlike blind `{place, when}` extraction, this hands the model the actual
/// candidate flights (ICAO + airport name + date) and asks *which one*. That lets
/// world-knowledge references resolve — "my beach trip" → the flight to *Nice
/// Côte d'Azur* — which token extraction never could. Safety is preserved by
/// construction: the model returns a **1-based index into the provided list**,
/// which we range-check and map back to a real id, so it can never surface a
/// flight that doesn't exist. Gated on model availability; nil → the resolver
/// falls straight to Siri disambiguation.
struct FoundationModelsPhraseResolver: FlightPhraseResolving {
    nonisolated func pick(phrase: String, today: String, candidates: [FlightCandidate]) async -> String? {
        #if canImport(FoundationModels)
        guard !candidates.isEmpty,
              case .available = SystemLanguageModel.default.availability else { return nil }
        let list = candidates.enumerated()
            .map { "\($0.offset + 1). \($0.element.line)" }
            .joined(separator: "\n")
        let prompt = """
        Today is \(today). The user said: "\(phrase)".
        Their upcoming flights:
        \(list)
        Reply with the number of the flight the user is referring to, or 0 if none match.
        """
        do {
            let session = LanguageModelSession()
            let response = try await session.respond(to: prompt, generating: FlightChoice.self)
            let choice = response.content.choice
            guard choice >= 1, choice <= candidates.count else { return nil }
            return candidates[choice - 1].id
        } catch {
            return nil
        }
        #else
        return nil
        #endif
    }
}

#if canImport(FoundationModels)
/// The 1-based index the model returns (0 = none of them). A small bounded
/// integer is easier and safer for the model to emit than a long opaque id, and
/// the range is validated before mapping back to the real flight.
@Generable
struct FlightChoice {
    @Guide(description: "The number of the matching flight from the list, or 0 if none match.")
    var choice: Int
}
#endif
