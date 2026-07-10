import CryptoKit
import FlyFunCommon
import Foundation
import OSLog
import UIKit
import UserNotifications

#if DEBUG
/// Server environment toggle, available only in simulator builds.
enum ServerEnvironment: String, CaseIterable {
    case production = "prod"
    case development = "dev"

    var baseURL: URL {
        switch self {
        case .production:
            URL(string: "https://weather.flyfun.aero")!
        case .development:
            URL(string: "https://localhost.ro-z.me:8443")!
        }
    }

    var label: String {
        switch self {
        case .production: "Production"
        case .development: "Local Dev"
        }
    }
}
#endif

/// Central app state: authentication, API client, repository.
@Observable
@MainActor
final class AppState {
    // MARK: - Auth (FlyFunCommon)

    @ObservationIgnored let tokenStore: KeychainBearerTokenStore
    @ObservationIgnored private(set) var rollingSession: RollingBearerSession!
    @ObservationIgnored private let callbackParser = AuthCallbackParser(
        customScheme: "flyfunweather",
        universalLinkHost: "weather.flyfun.aero"
    )

    /// Mirror of the keychain JWT — observable so SwiftUI re-renders on
    /// login/logout. Don't pass this around for API auth; use `apiClient`,
    /// which always reads through the live `RollingBearerSession`.
    private(set) var jwt: String?

    // MARK: - State

    private(set) var apiClient: APIClient?
    private(set) var repository: (any BriefingRepository)?
    /// Offline PIREP queue, scoped to the signed-in user (reassigned in
    /// `setupClient`). Same cross-user leak as the briefing cache: a single
    /// unscoped `pending_pireps.json` would sync user A's queued reports under
    /// user B. `private(set) var` so only sign-in reshapes it.
    private(set) var pirepOfflineStore = PirepOfflineStore()
    let userPreferences = UserPreferencesStore()
    /// Local device settings (e.g. offline auto-download mode). Server-side flags
    /// live in `userPreferences`; this is the on-device counterpart.
    let settings = AppSettingsStore()
    /// Live network reachability — gates Wi-Fi-only auto-download.
    let networkMonitor = NetworkMonitor()

    /// Navigation target requested by an App Intent (Siri / Shortcuts /
    /// Spotlight), consumed by the UI on the next `.active` scene phase. Reuses
    /// the same seam `onOpenURL` relies on. Observable so `FlightListView` routes
    /// when it changes.
    var pendingNavigation: PendingNavigation?
    /// (i)-popup help content (metrics + advisories). Seeded from disk cache or
    /// the bundled baseline at init; refreshed opportunistically when online.
    let helpCatalog = HelpCatalogStore()

    /// External "server data changed" nudge (e.g. a refresh push). The flight list
    /// and any open briefing observe this and re-sync to the newest online pack —
    /// push is just one more sync trigger, not a special path. `token` makes every
    /// signal distinct (so back-to-back nudges each fire); `flightId` carries the
    /// affected flight when known. Bump via `signalExternalSync`.
    private(set) var externalSync = ExternalSyncSignal(token: 0, flightId: nil)

    /// A distinct, observable data-sync nudge. `Equatable` so SwiftUI `onChange`
    /// fires once per signal.
    struct ExternalSyncSignal: Equatable {
        var token: Int
        var flightId: String?
    }

    /// Request a seamless re-sync of the visible flight list / open briefing to
    /// the latest online pack. Cheap by design — the observers no-op when nothing
    /// changed. `flightId` is the affected flight when the trigger names one
    /// (briefing push), else nil (e.g. a silent badge-sync).
    func signalExternalSync(flightId: String?) {
        externalSync = ExternalSyncSignal(token: externalSync.token &+ 1, flightId: flightId)
    }

    /// Weak handle to the live instance so a foregrounding App Intent can trigger
    /// `consumePendingNavigation()` immediately when the app process is already
    /// alive (warm foreground / already-active). Nil on a cold launch — the scene
    /// `.active` hook then consumes the target instead. Single source of truth is
    /// `PendingNavigationStore` (consumed exactly once via `take()`), so this only
    /// *prompts* a consume; it never double-routes.
    static weak var current: AppState?

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AppState")

    static let productionBaseURL = URL(string: "https://weather.flyfun.aero")!

    #if DEBUG
    @ObservationIgnored
    static var serverEnvironment: ServerEnvironment {
        get {
            guard let raw = UserDefaults.standard.string(forKey: "serverEnvironment"),
                  let env = ServerEnvironment(rawValue: raw) else { return .production }
            return env
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: "serverEnvironment") }
    }

    static var defaultBaseURL: URL { serverEnvironment.baseURL }
    #else
    static var defaultBaseURL: URL { productionBaseURL }
    #endif

    var isAuthenticated: Bool { jwt != nil }

    #if DEBUG
    /// Launched by a UI test (`FLYFUN_UITEST=1`) — skip the auth gate so the app
    /// renders past `LoginView` without a real sign-in.
    static var isUITesting: Bool { ProcessInfo.processInfo.environment["FLYFUN_UITEST"] == "1" }
    /// Serve bundled fixtures instead of the network (`FLYFUN_MOCK=1`) so UI tests
    /// are deterministic and offline. Stub at the app boundary — XCUITest can't
    /// intercept network the way Playwright can.
    static var isUITestMockMode: Bool { ProcessInfo.processInfo.environment["FLYFUN_MOCK"] == "1" }
    /// Present the fixtures as a cached/offline list (`FLYFUN_MOCK_OFFLINE=1`) so
    /// the offline journey (#318) sees the offline banner + read-only rows. Only
    /// meaningful together with `FLYFUN_MOCK`.
    static var isUITestOfflineMode: Bool { ProcessInfo.processInfo.environment["FLYFUN_MOCK_OFFLINE"] == "1" }
    #endif

    // MARK: - Lifecycle

    init() {
        let store = KeychainBearerTokenStore(service: "aero.flyfun.weather")
        self.tokenStore = store
        self.jwt = store.token
        Self.current = self

        self.rollingSession = RollingBearerSession(
            store: store,
            onUnauthorized: { [weak self] in
                await self?.handleUnauthorized()
            }
        )

        #if DEBUG
        if Self.isUITesting {
            setupUITestSession()
            return
        }
        #endif

        if store.token?.isEmpty == false {
            setupClient()
        }
    }

    #if DEBUG
    /// Wire up the session for UI tests. In mock mode the repository serves
    /// fixtures; otherwise it points at the selected server so tests can run
    /// against a live dev backend. Never writes to the keychain.
    private func setupUITestSession() {
        if Self.isUITestMockMode {
            // Fixtures need no real auth — a fake gate token is enough to render
            // past LoginView, and it lives on the observable mirror only.
            jwt = "uitest-token"
            repository = FixtureBriefingRepository(offline: Self.isUITestOfflineMode)
        } else {
            // Live-dev mode: mirror the real keychain token so the auth gate
            // matches what the API will actually use (nil → LoginView, as normal).
            jwt = tokenStore.token
            setupClient()
        }
    }
    #endif

    // MARK: - Auth

    /// Apply a JWT obtained from Apple-credential exchange or Google OAuth.
    func signIn(token: String) {
        Self.logger.info("Storing JWT after sign-in")
        applyToken(token)
        setupClient()
    }

    /// Handle a deep-link auth callback.
    ///
    /// The normal Google/Apple sign-in is captured *inside*
    /// `ASWebAuthenticationSession` and never reaches `onOpenURL`, so the only
    /// legitimate inbound deep link here is the **App Store reviewer** token
    /// (`flyfunweather://auth?token=<jwt>`), which carries a `scope:"review"`
    /// claim.
    ///
    /// Two guards, in order:
    /// 1. **Never overwrite an existing session.** The reviewer path is only for
    ///    a fresh (signed-out) device. Bailing when already authenticated closes
    ///    the residual forced-logout vector: the `scope` claim is read
    ///    *unverified* (we can't check the signature client-side), so a spoofed
    ///    `scope:"review"` token could otherwise be persisted over a logged-in
    ///    user's real token and 401 them out on the next request.
    /// 2. **Require `scope:"review"`.** Any other bare-token deep link is a
    ///    login-CSRF attempt and is ignored.
    ///
    /// Account-takeover / session-fixation is closed regardless: the claim is a
    /// routing hint only — the server verifies the signature on every API call,
    /// so a forged review token is inert (401s on first use) and only a
    /// genuinely server-signed reviewer token authenticates end-to-end.
    func handleAuthCallback(url: URL) {
        guard let token = callbackParser.token(from: url) else {
            Self.logger.warning("Invalid auth callback URL: \(url)")
            return
        }
        guard Self.shouldAcceptDeepLinkToken(token, alreadyAuthenticated: jwt != nil) else {
            Self.logger.warning("Ignoring deep-link token (already authenticated or non-review scope)")
            return
        }
        signIn(token: token)
    }

    /// The two-guard decision for an inbound bare-token deep link, extracted as a
    /// pure function so the ordering/logic is unit-testable off the MainActor
    /// (and can't silently regress). Accept only when the app is **signed out**
    /// AND the token carries `scope:"review"`. See `handleAuthCallback` for why.
    nonisolated static func shouldAcceptDeepLinkToken(
        _ token: String,
        alreadyAuthenticated: Bool
    ) -> Bool {
        guard !alreadyAuthenticated else { return false }
        return unverifiedScope(of: token) == "review"
    }

    /// Reads the (unverified) `scope` claim from a JWT payload for routing only.
    /// Never used for authorization — that stays with the server signature.
    nonisolated static func unverifiedScope(of jwt: String) -> String? {
        unverifiedPayload(of: jwt)?["scope"] as? String
    }

    /// The (unverified) `sub` claim as a String — the stable per-user identity the
    /// on-disk caches are scoped by. Coerces a numeric `sub` (some issuers encode
    /// the user id as an integer) to its decimal string. Scoping only, never
    /// authorization — the server verifies the signature on every API call.
    nonisolated static func unverifiedSubject(of jwt: String) -> String? {
        guard let sub = unverifiedPayload(of: jwt)?["sub"] else { return nil }
        if let s = sub as? String { return s.isEmpty ? nil : s }
        if let n = sub as? NSNumber { return n.stringValue }
        return nil
    }

    /// Decode a JWT payload segment without verifying the signature. Shared by the
    /// `scope` (deep-link routing) and `sub` (cache scoping) readers — both
    /// non-authoritative.
    nonisolated static func unverifiedPayload(of jwt: String) -> [String: Any]? {
        let parts = jwt.split(separator: ".")
        guard parts.count == 3 else { return nil }
        var b64 = String(parts[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return obj
    }

    /// Filesystem-safe, non-reversible cache-scope key for a token's subject.
    /// SHA-256 → 16 hex chars: keeps PII / odd characters off disk and is
    /// collision-safe for per-device user counts. Falls back to `"anon"` when
    /// there's no usable `sub` — which shouldn't happen for a server-issued token;
    /// the bucket just stays isolated from real users if it ever is used.
    nonisolated static func cacheScope(forToken token: String) -> String {
        guard let sub = unverifiedSubject(of: token) else { return "anon" }
        let digest = SHA256.hash(data: Data(sub.utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }

    func logout() {
        Self.logger.info("Logging out")
        // Unregister this device first — a signed-out device must stop receiving
        // this user's briefings. Uses the live client before it's cleared below.
        unregisterPushDevice()
        applyToken(nil)
        apiClient = nil
        repository = nil
        userPreferences.clear()
    }

    /// Sync the observable mirror after the rolling session cleared the
    /// store on 401. Triggers SwiftUI to swap views back to `LoginView`.
    func handleUnauthorized() {
        guard jwt != nil else { return }
        Self.logger.info("401 from server — clearing local auth state")
        jwt = nil
        apiClient = nil
        repository = nil
        userPreferences.clear()
    }

    /// Delete the user's account on the server, then log out locally.
    func deleteAccount() async throws {
        guard let apiClient else {
            throw URLError(.userAuthenticationRequired)
        }
        try await apiClient.requestVoid("/auth/account", method: "DELETE")
        Self.logger.info("Account deleted")
        // Account is gone — leave nothing on disk. Wipe this user's scoped cache
        // and offline PIREP queue (otherwise the 30-day janitor would be the only
        // thing to remove them).
        if let caching = cachingRepository {
            await caching.cache.clearAll()
        }
        await pirepOfflineStore.clear()
        logout()
    }

    #if DEBUG
    /// Switch server environment and reconnect if authenticated.
    func setServerEnvironment(_ env: ServerEnvironment) {
        Self.serverEnvironment = env
        Self.logger.info("Switched to \(env.label) (\(env.baseURL))")
        if tokenStore.token?.isEmpty == false {
            setupClient()
        }
    }
    #endif

    // MARK: - Intent navigation

    /// Pull any intent-requested navigation target from the cold-launch-safe
    /// store into the observable property so the UI can route to it. Called on
    /// every scene activation, which fires on both cold launch and warm
    /// foreground — one path covers both.
    func consumePendingNavigation() {
        if let nav = PendingNavigationStore.take() {
            pendingNavigation = nav
        }
    }

    /// Clear the pending target once the UI has routed to it.
    func clearPendingNavigation() {
        pendingNavigation = nil
    }

    // MARK: - Push notifications & badge

    /// UserDefaults key for the last-uploaded APNs token hex, so sign-out can
    /// unregister the exact device row even after the token is no longer live.
    private static let apnsTokenKey = "apnsDeviceToken"

    /// Ask for notification authorization and, if granted, register for remote
    /// notifications. Returns whether authorization was granted. Called when the
    /// user turns push on in Settings — the system prompt appears at most once.
    func requestPushAuthorizationAndRegister() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .badge, .sound])) ?? false
        if granted {
            UIApplication.shared.registerForRemoteNotifications()
        }
        return granted
    }

    /// Re-register for remote notifications on foreground when already authorized,
    /// so a rotated APNs token is re-uploaded. No-op (and no prompt) when the user
    /// hasn't authorized notifications. Safe to call on every `.active`.
    func refreshPushRegistrationIfAuthorized() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard settings.authorizationStatus == .authorized ||
              settings.authorizationStatus == .provisional else { return }
        UIApplication.shared.registerForRemoteNotifications()
    }

    /// Upload (upsert) this device's APNs token to the server. Called by the app
    /// delegate once APNs hands back a token.
    func uploadDeviceToken(_ hex: String) async {
        guard let apiClient else { return }
        UserDefaults.standard.set(hex, forKey: Self.apnsTokenKey)
        do {
            let body = try JSONEncoder.weatherBrief.encode(
                DeviceRegistrationRequest(token: hex, environment: PushSupport.environment)
            )
            _ = try await apiClient.requestData("/api/devices", method: "POST", body: body)
            Self.logger.info("Device token uploaded")
        } catch {
            Self.logger.warning("Device token upload failed: \(error.localizedDescription)")
        }
    }

    /// Unregister this device on the server (sign-out). Captures the live client
    /// synchronously so it still fires after `logout()` clears `apiClient`.
    func unregisterPushDevice() {
        guard let apiClient,
              let token = UserDefaults.standard.string(forKey: Self.apnsTokenKey) else { return }
        UserDefaults.standard.removeObject(forKey: Self.apnsTokenKey)
        Task { try? await apiClient.requestVoid("/api/devices/\(token)", method: "DELETE") }
    }

    /// Authoritative badge reconcile: read the server-derived unseen count and set
    /// the app icon badge. The correctness backstop for coalesced/missed pushes —
    /// call on every `.active` and after handling a silent push.
    func reconcileBadge() async {
        guard let apiClient else { return }
        do {
            let status: BadgeStatus = try await apiClient.request("/api/flights/badge")
            try? await UNUserNotificationCenter.current().setBadgeCount(status.count)
        } catch {
            Self.logger.debug("Badge reconcile skipped: \(error.localizedDescription)")
        }
    }

    /// Mark a flight's briefing seen (opened) and update the badge from the
    /// server's fresh count. The server also fires a silent badge-sync push to
    /// the user's other devices so they update too.
    func markBriefingSeen(flightId: String) async {
        guard let apiClient else { return }
        do {
            let status: BadgeStatus = try await apiClient.request(
                "/api/flights/\(flightId)/seen", method: "POST"
            )
            try? await UNUserNotificationCenter.current().setBadgeCount(status.count)
        } catch {
            Self.logger.debug("Mark-seen failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Offline sync

    func syncPendingPireps() async {
        guard let repository else { return }
        await pirepOfflineStore.load()
        let synced = await pirepOfflineStore.sync(using: repository)
        if synced > 0 {
            Self.logger.info("Synced \(synced) offline PIREPs")
        }
    }

    func refreshUserPreferences() async {
        guard let apiClient else { return }
        await userPreferences.refresh(using: apiClient)
    }

    /// Evict cached packs for flights that departed more than a week ago, so the
    /// offline cache doesn't grow unbounded as auto-download fills it. Safe to
    /// call on launch and foreground — a no-op when nothing is stale.
    func pruneStaleCache() async {
        guard let caching = cachingRepository else { return }
        await caching.pruneStalePacks(olderThanDays: Self.cacheRetentionDays)
        // Coarse cross-user janitor: per-user scoping means other users' dirs are
        // never touched above, so age out abandoned accounts' caches wholesale.
        // Run off the main actor — it's filesystem I/O.
        let scope = Self.cacheScope(forToken: tokenStore.token ?? "")
        let retention = Self.inactiveUserRetentionDays
        await Task.detached {
            BriefingCacheStore.evictInactiveUsers(
                base: BriefingCacheStore.defaultBase(),
                keeping: scope,
                olderThanDays: retention
            )
        }.value
    }

    /// Cached packs are kept until their flight is this many days in the past.
    static let cacheRetentionDays = 7

    /// A signed-out user's whole scoped cache dir is removed after this many days
    /// of inactivity (no `flights()` load rewriting its `flights.json` marker).
    static let inactiveUserRetentionDays = 30

    /// Pull the latest (i)-popup help content. Non-blocking; safe to call on
    /// launch, sign-in, and foreground — a `304` is a cheap no-op.
    func refreshHelpCatalog() async {
        guard let apiClient else { return }
        await helpCatalog.refresh(using: apiClient)
    }

    // MARK: - Private

    /// Typed accessor for cache operations (download/delete).
    var cachingRepository: CachingBriefingRepository? {
        repository as? CachingBriefingRepository
    }

    /// Per-user offline PIREP queue: `<Documents>/PendingPireps/<scope>/pending_pireps.json`.
    private static func scopedPirepURL(scope: String) -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
            .appendingPathComponent("PendingPireps", isDirectory: true)
            .appendingPathComponent(scope, isDirectory: true)
            .appendingPathComponent("pending_pireps.json")
    }

    /// Discard a pre-scoping `Documents/pending_pireps.json`. We deliberately do
    /// NOT migrate it into a scope: the legacy file carries no owner, so on a
    /// shared device (previous user logged out before the update, a different user
    /// signs in first) moving it would submit user A's queued reports — with their
    /// position and notes — under user B. A wrongly-attributed PIREP is worse than
    /// losing a rare unsynced draft (the queue is only non-empty if a report was
    /// submitted offline and never synced before the update), so drop it.
    private static func discardLegacyPireps() {
        let legacy = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
            .appendingPathComponent("pending_pireps.json")
        try? FileManager.default.removeItem(at: legacy)
    }

    private func applyToken(_ token: String?) {
        tokenStore.token = token
        jwt = token
    }

    private func setupClient() {
        let client = APIClient(
            baseURL: Self.defaultBaseURL,
            tokenStore: tokenStore,
            rollingSession: rollingSession
        )
        apiClient = client
        let online = OnlineBriefingRepository(client: client)
        // Scope the on-disk caches to the signed-in user so no user ever reads
        // another's flight list / briefings / queued PIREPs (see cacheScope).
        let scope = Self.cacheScope(forToken: tokenStore.token ?? "")
        let base = BriefingCacheStore.defaultBase()
        BriefingCacheStore.migrateLegacyLayout(base: base)
        let cache = BriefingCacheStore(cacheDir: BriefingCacheStore.scopedCacheDir(base: base, scope: scope))
        repository = CachingBriefingRepository(client: client, online: online, cache: cache)
        Self.discardLegacyPireps()
        pirepOfflineStore = PirepOfflineStore(fileURL: Self.scopedPirepURL(scope: scope))
        Task { await userPreferences.refresh(using: client) }
        Task { await helpCatalog.refresh(using: client) }
        // Open any cached airports DB immediately (offline-safe), then refresh it
        // from the server if the AIRAC copy changed (ETag → usually a 304 no-op).
        AirportDatabase.shared.loadCached()
        Task { await AirportDatabase.shared.refresh(using: client) }
    }
}
