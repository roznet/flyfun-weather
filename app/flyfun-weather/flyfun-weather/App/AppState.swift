import FlyFunCommon
import Foundation
import OSLog

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
    let pirepOfflineStore = PirepOfflineStore()
    let userPreferences = UserPreferencesStore()
    /// (i)-popup help content (metrics + advisories). Seeded from disk cache or
    /// the bundled baseline at init; refreshed opportunistically when online.
    let helpCatalog = HelpCatalogStore()

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

    // MARK: - Lifecycle

    init() {
        let store = KeychainBearerTokenStore(service: "aero.flyfun.weather")
        self.tokenStore = store
        self.jwt = store.token

        self.rollingSession = RollingBearerSession(
            store: store,
            onUnauthorized: { [weak self] in
                await self?.handleUnauthorized()
            }
        )

        if store.token?.isEmpty == false {
            setupClient()
        }
    }

    // MARK: - Auth

    /// Apply a JWT obtained from Apple-credential exchange or Google OAuth.
    func signIn(token: String) {
        Self.logger.info("Storing JWT after sign-in")
        applyToken(token)
        setupClient()
    }

    /// Handle a deep-link auth callback (`flyfunweather://auth?token=…` or
    /// `https://weather.flyfun.aero/auth/callback?token=…`).
    func handleAuthCallback(url: URL) {
        guard let token = callbackParser.token(from: url) else {
            Self.logger.warning("Invalid auth callback URL: \(url)")
            return
        }
        signIn(token: token)
    }

    func logout() {
        Self.logger.info("Logging out")
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
        let cache = BriefingCacheStore()
        repository = CachingBriefingRepository(client: client, online: online, cache: cache)
        Task { await userPreferences.refresh(using: client) }
        Task { await helpCatalog.refresh(using: client) }
    }
}
