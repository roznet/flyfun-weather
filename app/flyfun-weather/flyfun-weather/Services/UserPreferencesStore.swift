import Foundation
import OSLog

/// Local read-only cache of the user's server-side preferences.
///
/// Persisted in UserDefaults so the app has last-known flags available
/// immediately at launch (including offline). Refreshed opportunistically
/// from `/api/user/preferences` when online; writes to prefs happen via the web.
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
            self.preferences = .empty
        }
    }

    /// Fetch latest preferences from the server and persist them.
    /// Silent no-op on transient network errors — cached value is preserved.
    func refresh(using client: APIClient) async {
        do {
            let fresh: PreferencesResponse = try await client.request("/api/user/preferences")
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

    /// Enable/disable the push channel server-side, then refresh the cache.
    /// Alert delivery is gated on this flag; the device stays registered either
    /// way so the badge keeps syncing (badge follows "a device is registered").
    func updateNotifyPush(_ enabled: Bool, using client: APIClient) async {
        await put(NotifyPushUpdateRequest(notifyPush: enabled), field: "notify_push", client: client)
    }

    /// Enable/disable the email channel server-side, then refresh the cache.
    func updateNotifyEmail(_ enabled: Bool, using client: APIClient) async {
        await put(NotifyEmailUpdateRequest(notifyEmail: enabled), field: "notify_email", client: client)
    }

    /// Change the "Briefing updates" 3-stop (folds to scope + change-only).
    func updateBriefingUpdates(_ updates: BriefingUpdates, using client: APIClient) async {
        await put(
            BriefingUpdatesUpdateRequest(notifyScope: updates.scope, notifyChangeOnly: updates.changeOnly),
            field: "briefing_updates", client: client
        )
    }

    /// Dismiss the one-time channel-invariant decay notice.
    func dismissDecayNotice(using client: APIClient) async {
        await put(NotifyDecayDismissRequest(notifyDecayNotice: false), field: "notify_decay_notice", client: client)
    }

    /// Shared PUT + cache-adopt for a partial preferences update. The server
    /// merges the single changed key into `app_prefs_json` and returns the fresh
    /// full preferences, which we adopt as the new cache.
    private func put(_ request: some Encodable, field: String, client: APIClient) async {
        do {
            let body = try JSONEncoder.weatherBrief.encode(request)
            let fresh: PreferencesResponse = try await client.request(
                "/api/user/preferences", method: "PUT", body: body
            )
            preferences = fresh
            if let data = try? JSONEncoder().encode(fresh) {
                UserDefaults.standard.set(data, forKey: Self.defaultsKey)
            }
        } catch {
            Self.logger.warning("Failed to update \(field): \(error.localizedDescription)")
        }
    }

    /// Clear cached preferences (on logout).
    func clear() {
        preferences = .empty
        UserDefaults.standard.removeObject(forKey: Self.defaultsKey)
    }
}
