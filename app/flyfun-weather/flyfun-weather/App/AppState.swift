import Foundation
import OSLog
import RZUtilsSwift

/// Keys for secure and user storage.
enum StorageKey: String {
    case jwt
    case selectedModel
}

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

    #if DEBUG
    static let defaultBaseURL = URL(string: "https://weather.flyfun.aero")!
    #else
    static let defaultBaseURL = URL(string: "https://weather.flyfun.aero")!
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
        guard url.scheme == "weatherbrief",
              url.host == "auth",
              url.path == "/callback" || url.pathComponents.contains("callback"),
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

    // MARK: - Private

    private func setupClient(jwt: String) {
        let client = APIClient(baseURL: Self.defaultBaseURL, jwt: jwt)
        apiClient = client
        repository = OnlineBriefingRepository(client: client)
    }
}
