import AppIntents

/// "What's my FlyFun assessment for the flight to Cannes?" — speaks a one- or
/// two-line summary (GREEN/AMBER/RED + top advisories) without opening the app.
/// Reads from cache when offline.
struct CheckBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Check Briefing"
    static var description = IntentDescription("Hear the assessment and top concerns for a flight.")

    @Parameter(title: "Flight")
    var flight: FlightEntity

    static var parameterSummary: some ParameterSummary {
        Summary("Check the briefing for \(\.$flight)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        }
        let repo = IntentSupport.makeRepository()
        do {
            let flights = try await repo.flights()
            guard let match = flights.first(where: { $0.id == flight.id }) else {
                return .result(dialog: "I couldn't find that flight.")
            }
            return .result(dialog: "\(IntentDialogs.checkSummary(match))")
        } catch APIError.unauthorized {
            // Token expired and the silent refresh failed (Decision 4).
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        } catch {
            return .result(dialog: "Sorry, I couldn't load your briefing right now.")
        }
    }
}
