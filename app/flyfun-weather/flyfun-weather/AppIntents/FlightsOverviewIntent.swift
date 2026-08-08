import AppIntents

/// "How do my FlyFun flights look?" — speaks a one-line-per-flight traffic-light
/// summary of upcoming flights. No parameter. Reads from cache when offline.
struct FlightsOverviewIntent: AppIntent {
    static var title: LocalizedStringResource = "Flights Overview"
    static var description = IntentDescription("Hear a traffic-light summary of your upcoming flights.")

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        }
        let repo = IntentSupport.makeRepository()
        do {
            let flights = try await repo.flights()
            return .result(dialog: "\(IntentDialogs.overviewSummary(flights))")
        } catch APIError.unauthorized {
            // Token expired and the silent refresh failed (Decision 4).
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        } catch {
            return .result(dialog: "Sorry, I couldn't load your flights right now.")
        }
    }
}
