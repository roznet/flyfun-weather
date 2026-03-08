import AuthenticationServices
import Foundation
import OSLog

/// Handles Google OAuth login via ASWebAuthenticationSession.
@MainActor
final class AuthService: NSObject, ASWebAuthenticationPresentationContextProviding {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Auth")

    // Keep a strong reference to the session so it isn't deallocated
    private var authSession: ASWebAuthenticationSession?

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            #if os(iOS)
            let scene = UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .first
            return scene?.keyWindow ?? ASPresentationAnchor()
            #else
            return NSApplication.shared.keyWindow ?? ASPresentationAnchor()
            #endif
        }
    }

    /// Opens the OAuth flow in an in-app browser and returns the JWT token.
    func signIn(baseURL: URL) async throws -> String {
        let loginURL = baseURL.appendingPathComponent("auth/login/google")
        var components = URLComponents(url: loginURL, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "platform", value: "ios"),
            URLQueryItem(name: "scheme", value: "flyfunweather"),
        ]

        guard let url = components.url else {
            throw URLError(.badURL)
        }

        Self.logger.info("Starting OAuth flow to \(url)")

        let callbackURL = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
            let session = ASWebAuthenticationSession(
                url: url,
                callback: .customScheme("flyfunweather")
            ) { url, error in
                if let error {
                    Self.logger.error("OAuth error: \(error)")
                    continuation.resume(throwing: error)
                } else if let url {
                    Self.logger.info("OAuth callback URL: \(url)")
                    continuation.resume(returning: url)
                } else {
                    continuation.resume(throwing: URLError(.cancelled))
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            self.authSession = session
            session.start()
        }

        Self.logger.info("OAuth callback received")

        guard let token = callbackURL.queryParam("token"), !token.isEmpty else {
            Self.logger.error("No token in callback URL: \(callbackURL)")
            throw URLError(.userAuthenticationRequired)
        }
        return token
    }
}
