import Foundation
import OSLog
import RZUtilsSwift

/// Keys for secure and user storage.
enum StorageKey: String {
    case jwt
    case selectedModel
}

#if targetEnvironment(simulator)
/// Server environment toggle, available only in simulator builds.
enum ServerEnvironment: String, CaseIterable {
    case production = "prod"
    case development = "dev"

    var baseURL: URL {
        switch self {
        case .production:
            URL(string: "https://weather.flyfun.aero")!
        case .development:
            URL(string: "https://localhost.ro-z.me:8000")!
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
    // MARK: - Storage

    @ObservationIgnored
    private var secureStorage = CodableSecureStorage<StorageKey, String>(key: .jwt, service: "aero.flyfun.weather")

    // MARK: - State

    private(set) var apiClient: APIClient?
    private(set) var repository: (any BriefingRepository)?

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AppState")

    static let productionBaseURL = URL(string: "https://weather.flyfun.aero")!

    #if targetEnvironment(simulator)
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

    var isAuthenticated: Bool {
        apiClient != nil
    }

    // MARK: - Lifecycle

    init() {
        // Restore JWT from keychain
        if let jwt = secureStorage.wrappedValue, !jwt.isEmpty {
            setupClient(jwt: jwt)
        }
    }

    // MARK: - Auth

    func handleAuthCallback(url: URL) {
        let isCustomScheme = url.scheme == "flyfunweather" && url.host == "auth"
        let isUniversalLink = url.scheme == "https" && url.host == "weather.flyfun.aero"
        guard (isCustomScheme || isUniversalLink),
              url.path == "/callback" || url.path == "/auth/callback",
              let token = url.queryParam("token"), !token.isEmpty
        else {
            Self.logger.warning("Invalid auth callback URL: \(url)")
            return
        }
        Self.logger.info("Auth callback received, storing JWT")
        secureStorage.wrappedValue = token
        setupClient(jwt: token)
    }

    func logout() {
        Self.logger.info("Logging out")
        secureStorage.wrappedValue = nil
        apiClient = nil
        repository = nil
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

    #if targetEnvironment(simulator)
    /// Switch server environment and reconnect if authenticated.
    func setServerEnvironment(_ env: ServerEnvironment) {
        Self.serverEnvironment = env
        Self.logger.info("Switched to \(env.label) (\(env.baseURL))")
        // Re-setup client with new base URL if we have a JWT
        if let jwt = secureStorage.wrappedValue, !jwt.isEmpty {
            setupClient(jwt: jwt)
        }
    }
    #endif

    // MARK: - Private

    /// Typed accessor for cache operations (download/delete).
    var cachingRepository: CachingBriefingRepository? {
        repository as? CachingBriefingRepository
    }

    private func setupClient(jwt: String) {
        let client = APIClient(baseURL: Self.defaultBaseURL, jwt: jwt)
        apiClient = client
        let online = OnlineBriefingRepository(client: client)
        let cache = BriefingCacheStore()
        repository = CachingBriefingRepository(client: client, online: online, cache: cache)
    }
}
