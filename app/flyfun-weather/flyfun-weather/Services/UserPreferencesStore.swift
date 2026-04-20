import Foundation
import OSLog

/// Local read-only cache of the user's server-side preferences.
///
/// Persisted in UserDefaults so the app has last-known flags available
/// immediately at launch (including offline). Refreshed opportunistically
/// from `/user/preferences` when online; writes to prefs happen via the web.
@MainActor
@Observable
final class UserPreferencesStore {
    private static let defaultsKey = "cachedUserPreferences"
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "UserPreferences")

    private(set) var preferences: PreferencesResponse

    init() {
        if let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
           let cached = try? JSONDecoder().decode(PreferencesResponse.self, from: data) {
            self.preferences = cached
        } else {
            self.preferences = PreferencesResponse(pirepCanView: false, pirepCanPublish: false)
        }
    }

    /// Fetch latest preferences from the server and persist them.
    /// Silent no-op on transient network errors — cached value is preserved.
    func refresh(using client: APIClient) async {
        do {
            let fresh: PreferencesResponse = try await client.request("/user/preferences")
            preferences = fresh
            if let data = try? JSONEncoder().encode(fresh) {
                UserDefaults.standard.set(data, forKey: Self.defaultsKey)
            }
        } catch let error as APIError where error.isTransientNetwork {
            Self.logger.debug("Offline, keeping cached preferences")
        } catch {
            Self.logger.warning("Failed to refresh preferences: \(error.localizedDescription)")
        }
    }

    /// Clear cached preferences (on logout).
    func clear() {
        preferences = PreferencesResponse(pirepCanView: false, pirepCanPublish: false)
        UserDefaults.standard.removeObject(forKey: Self.defaultsKey)
    }
}
