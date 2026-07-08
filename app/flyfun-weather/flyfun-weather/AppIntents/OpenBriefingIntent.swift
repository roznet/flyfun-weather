import AppIntents

/// "Open my next FlyFun briefing" — foreground the app on a flight's briefing.
///
/// With no flight given, opens the **next** upcoming flight's briefing. Works
/// offline from cache (the flight list is cache-first). Sign-out needs no special
/// handling here: the app foregrounds and its own auth gate shows the login
/// screen; we just fall back to the list target.
struct OpenBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Open Briefing"
    static var description = IntentDescription("Open the briefing for a flight, or your next flight.")
    static var openAppWhenRun = true

    @Parameter(title: "Flight")
    var flight: FlightEntity?

    static var parameterSummary: some ParameterSummary {
        Summary("Open briefing for \(\.$flight)")
    }

    @MainActor
    func perform() async throws -> some IntentResult {
        if let flight {
            PendingNavigationStore.set(.briefing(flightId: flight.id))
        } else if IntentSupport.isSignedIn,
                  let flights = try? await IntentSupport.makeRepository().flights(),
                  let next = FlightResolver.nextFlight(in: flights) {
            PendingNavigationStore.set(.briefing(flightId: next.id))
        } else {
            // Signed out, or no upcoming flight — just open the list.
            PendingNavigationStore.set(.flightList)
        }
        // Prompt an immediate consume if the app is already alive; else the scene
        // `.active` hook consumes on launch.
        AppState.current?.consumePendingNavigation()
        return .result()
    }
}
