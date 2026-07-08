import AppIntents

/// "Refresh my FlyFun briefing for the flight tomorrow to Fairoaks" — triggers a
/// refresh and speaks the freshness-gated outcome (Decision 2). Returns without
/// blocking on the full ~2-minute pipeline; the interim "started" line is the
/// accepted UX until the briefing-ready push lands.
struct RefreshBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Refresh Briefing"
    static var description = IntentDescription("Refresh the weather briefing for a flight.")

    @Parameter(title: "Flight")
    var flight: FlightEntity

    static var parameterSummary: some ParameterSummary {
        Summary("Refresh the briefing for \(\.$flight)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        }
        let repo = IntentSupport.makeRepository()
        let name = flight.destinationIcao ?? flight.title
        let outcome = await RefreshDriver.run(repository: repo, flightId: flight.id)
        let line: String
        switch outcome {
        case .alreadyFresh(let reason):
            if let reason, !reason.isEmpty {
                line = reason
            } else {
                line = "Your \(name) briefing is already up to date."
            }
        case .started:
            line = "Refreshing your \(name) briefing — open FlyFun shortly."
        case .completed:
            line = "Your \(name) briefing has been refreshed."
        case .alreadyInProgress:
            line = "Your \(name) briefing is already being refreshed."
        case .rateLimited:
            line = "That briefing was refreshed very recently — try again in a few minutes."
        case .failed(let message):
            line = message
        }
        return .result(dialog: "\(line)")
    }
}
