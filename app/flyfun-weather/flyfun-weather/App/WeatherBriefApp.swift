import SwiftUI
import TipKit

@main
struct WeatherBriefApp: App {
    @State private var appState = AppState()
    @Environment(\.scenePhase) private var scenePhase

    init() {
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
                appState.handleAuthCallback(url: url)
            }
            .onChange(of: scenePhase) {
                if scenePhase == .active {
                    Task { await appState.syncPendingPireps() }
                    Task { await appState.refreshUserPreferences() }
                    Task { await appState.refreshHelpCatalog() }
                }
            }
        }
    }
}
