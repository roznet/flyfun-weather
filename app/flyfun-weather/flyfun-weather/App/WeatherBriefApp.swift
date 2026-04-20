import SwiftUI

@main
struct WeatherBriefApp: App {
    @State private var appState = AppState()
    @Environment(\.scenePhase) private var scenePhase

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
                }
            }
        }
    }
}
