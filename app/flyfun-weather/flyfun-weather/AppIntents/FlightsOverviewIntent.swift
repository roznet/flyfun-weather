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
        let flights = try await repo.flights()
        return .result(dialog: "\(IntentDialogs.overviewSummary(flights))")
    }
}
