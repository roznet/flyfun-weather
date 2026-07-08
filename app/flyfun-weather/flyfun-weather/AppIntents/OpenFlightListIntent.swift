import AppIntents

/// "Show my FlyFun briefings" — foreground the app on the flight list.
///
/// No parameter, no resolver, no push: the simplest intent, proving the
/// `PendingNavigation` plumbing.
struct OpenFlightListIntent: AppIntent {
    static var title: LocalizedStringResource = "Show My Briefings"
    static var description = IntentDescription("Open FlyFun on your list of flight briefings.")
    static var openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        PendingNavigationStore.set(.flightList)
        // Prompt an immediate consume if the app process is already alive (warm
        // foreground); on a cold launch the scene `.active` hook consumes it.
        AppState.current?.consumePendingNavigation()
        return .result()
    }
}
