import SwiftUI

@main
struct WeatherBriefApp: App {
    @State private var appState = AppState()

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
        }
    }
}
