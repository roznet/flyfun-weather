import FlyFunCommon
import Foundation
import OSLog

/// Shared plumbing for App Intents (Siri / Shortcuts / Spotlight).
///
/// Intents run **in-process** and reuse the same Keychain JWT + on-disk briefing
/// cache the app itself uses, so they build a repository that reads through the
/// exact same `RollingBearerSession` / `BriefingCacheStore` scope as `AppState`
/// (see `AppState.setupClient`). This keeps intents as thin as the MCP tools —
/// no new networking, cache-first offline behaviour for free.
///
/// `@MainActor` because it touches `AppState`'s static config (`defaultBaseURL`)
/// and the `AirportDatabase` singleton; intent `perform()` bodies `await` into it.
@MainActor
enum IntentSupport {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Intents")

    /// The Keychain-backed bearer store, identical to `AppState`'s (`service:`
    /// must match so the same JWT is read).
    static func makeTokenStore() -> KeychainBearerTokenStore {
        KeychainBearerTokenStore(service: "aero.flyfun.weather")
    }

    /// Whether a signed-in JWT is present. Cheap Keychain read; the token's
    /// *validity* is still enforced server-side on the first request.
    static var isSignedIn: Bool {
        makeTokenStore().token?.isEmpty == false
    }

    /// Spoken line for a signed-out / expired-token background intent (Decision 4).
    /// `nonisolated` so non-MainActor helpers (e.g. `RefreshDriver.classify`) can
    /// reference it instead of duplicating the literal.
    nonisolated static let signedOutSpokenLine = "Please open FlyFun to sign in first."

    /// Build a cache-first repository scoped to the signed-in user, mirroring
    /// `AppState.setupClient` (reusing its static helpers so the scoping/layout
    /// logic isn't duplicated). Safe to call cold — it never assumes `AppState`
    /// is already alive.
    static func makeRepository() -> CachingBriefingRepository {
        let store = makeTokenStore()
        // Intents surface auth failures as spoken/foreground prompts (see
        // Decision 4), so the rolling session's unauthorized hook is a no-op here
        // — it still clears the token on a 401, and the intent reads the thrown
        // `APIError.unauthorized` to decide what to say.
        let rolling = RollingBearerSession(store: store, onUnauthorized: {})
        let client = APIClient(
            baseURL: AppState.defaultBaseURL,
            tokenStore: store,
            rollingSession: rolling
        )
        let online = OnlineBriefingRepository(client: client)
        let base = BriefingCacheStore.defaultBase()
        BriefingCacheStore.migrateLegacyLayout(base: base)
        let scope = AppState.cacheScope(forToken: store.token ?? "")
        let cache = BriefingCacheStore(cacheDir: BriefingCacheStore.scopedCacheDir(base: base, scope: scope))
        return CachingBriefingRepository(client: client, online: online, cache: cache)
    }

    /// Ensure the local airports DB is opened **and loaded** (offline-safe) so the
    /// deterministic resolver can expand place names ↔ ICAO. Awaits the load so a
    /// freshly-spawned Siri process doesn't search before the DB opens. No-op once
    /// loaded; the network refresh is the app's job, not the intent's.
    static func ensureAirportDatabase() async {
        await AirportDatabase.shared.ensureLoaded()
    }

    /// Medium-style, time-less date string ("9 Jul 2026") shared by the resolver's
    /// candidate lines and the spoken dialogs. `nonisolated` so both the
    /// MainActor resolver and any dialog builder can use the one formatter.
    nonisolated static func mediumDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        return formatter.string(from: date)
    }
}

