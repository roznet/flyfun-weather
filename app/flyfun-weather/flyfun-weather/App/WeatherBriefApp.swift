import SwiftUI
import TipKit

@main
struct WeatherBriefApp: App {
    @State private var appState = AppState()
    @Environment(\.scenePhase) private var scenePhase
    // Bridges APNs (device token, taps, silent badge-sync) into the app. Runs
    // in-process; the delegate reaches state via `AppState.current`.
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        // Under UI tests (`FLYFUN_UITEST=1`) leave TipKit unconfigured so no
        // coachmark ever displays. `.displayFrequency(.immediate)` on a fresh
        // simulator makes every tip eligible at once, and a `.popoverTip` scrim
        // intercepts taps — which silently blocks the journeys that tap controls
        // inside the briefing (#318). Not calling `Tips.configure` suppresses all
        // tips; product behaviour is unchanged for real launches.
        #if DEBUG
        if AppState.isUITesting { return }
        #endif
        // Contextual coachmarks for the briefing detail views (#312). Guarded
        // `try?` — a tip-store failure must never block app launch. Revisit
        // `.daily` later if the tips ever feel noisy.
        try? Tips.configure([
            .displayFrequency(.immediate),
            .datastoreLocation(.applicationDefault),
        ])
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if appState.isAuthenticated {
                    FlightListView()
                } else {
                    LoginView()
                }
            }
            .environment(appState)
            .onOpenURL { url in
                // Universal Links (Smart App Banner "Open", tapped briefing links)
                // route to a flight; everything else falls through to the
                // auth-callback path (auth-callback universal link + reviewer token).
                if appState.handleUniversalLink(url: url) { return }
                appState.handleAuthCallback(url: url)
            }
            .onChange(of: scenePhase) {
                if scenePhase == .active {
                    // Route any App Intent (Siri / Shortcuts / Spotlight) target
                    // first — same seam as `onOpenURL`. Synchronous so the flight
                    // list sees it before its own `.task` load runs.
                    appState.consumePendingNavigation()
                    Task { await appState.syncPendingPireps() }
                    Task { await appState.refreshUserPreferences() }
                    Task { await appState.refreshHelpCatalog() }
                    Task { await appState.pruneStaleCache() }
                    // Badge is server-authoritative: reconcile on every activation
                    // (the correctness backstop for coalesced/missed silent pushes),
                    // and re-upload a rotated APNs token when already authorized.
                    Task { await appState.reconcileBadge() }
                    Task { await appState.refreshPushRegistrationIfAuthorized() }
                }
            }
        }
    }
}
