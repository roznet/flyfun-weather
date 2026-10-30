import AppIntents

/// "Refresh my FlyFun briefing for the flight tomorrow to Fairoaks" — triggers a
/// refresh and speaks the freshness-gated outcome (Decision 2). Returns without
/// blocking on the full ~2-minute pipeline: the spoken line says to ask again,
/// and the briefing-ready push (#366) closes the loop.
///
/// With no flight given ("Refresh my FlyFun briefing") it refreshes the
/// **next** upcoming flight, like Check Briefing, so it never stops to ask
/// "which flight?" in the car.
struct RefreshBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Refresh Briefing"
    static var description = IntentDescription("Refresh the weather briefing for a flight, or your next flight.")

    @Parameter(title: "Flight")
    var flight: FlightEntity?

    init() {}

    /// For `Button(intent:)` on the Check Briefing card (#365).
    init(flight: FlightEntity?) {
        self.flight = flight
    }

    static var parameterSummary: some ParameterSummary {
        Summary("Refresh the briefing for \(\.$flight)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard IntentSupport.isSignedIn else {
            return .result(dialog: "\(IntentSupport.signedOutSpokenLine)")
        }
        let repo = IntentSupport.makeRepository()
        let target: FlightEntity
        if let flight {
            target = flight
        } else {
            guard let flights = try? await repo.flights(),
                  let next = FlightResolver.nextFlight(in: flights) else {
                return .result(dialog: "You have no upcoming flights.")
            }
            target = FlightEntity(next)
        }
        let outcome = await RefreshDriver.run(repository: repo, flightId: target.id)
        let line = Self.spokenLine(outcome, route: target.title)
        await IntentSupport.ensureAirportDatabase()
        let names = SpokenText.airportNames(in: [line])
        return .result(dialog: "\(SpokenText.speakable(line, names: names))")
    }

    /// The written line for an outcome ("EGTF → LFAT" route, server text
    /// verbatim); the caller speaks it through `SpokenText`.
    nonisolated static func spokenLine(_ outcome: RefreshDriver.Outcome, route: String) -> String {
        switch outcome {
        case .alreadyFresh(let reason):
            if let reason, !reason.isEmpty { return reason }
            return "Your \(route) briefing is already up to date."
        case .started:
            return "Refreshing your \(route) briefing. It takes about two minutes; ask me to read it again then."
        case .completed:
            return "Your \(route) briefing has been refreshed. Ask me to read it."
        case .alreadyInProgress:
            return "Your \(route) briefing is already being refreshed. Ask me to read it again in a couple of minutes."
        case .rateLimited:
            return "That briefing was refreshed very recently — try again in a few minutes."
        case .failed(let message):
            return message
        }
    }
}
