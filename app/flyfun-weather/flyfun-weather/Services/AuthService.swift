import AuthenticationServices
import Foundation
import OSLog

/// Handles OAuth login via ASWebAuthenticationSession (Google) and native Apple Sign-In.
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
            return scene?.keyWindow ?? {
                guard let windowScene = UIApplication.shared.connectedScenes.first as? UIWindowScene else {
                    return ASPresentationAnchor()
                }
                return ASPresentationAnchor(windowScene: windowScene)
            }()
            #else
            return NSApplication.shared.keyWindow ?? ASPresentationAnchor()
            #endif
        }
    }

    /// Opens the OAuth flow for the given provider and returns the JWT token.
    func signIn(baseURL: URL, provider: String = "google") async throws -> String {
        let loginURL = baseURL.appendingPathComponent("auth/login/\(provider)")
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

    // MARK: - Native Apple Sign-In

    /// Exchanges an Apple credential (from SignInWithAppleButton) with the server for a JWT.
    func exchangeAppleCredential(_ credential: ASAuthorizationAppleIDCredential, baseURL: URL) async throws -> String {
        guard let identityTokenData = credential.identityToken,
              let identityToken = String(data: identityTokenData, encoding: .utf8)
        else {
            Self.logger.error("No identity token in Apple credential")
            throw URLError(.userAuthenticationRequired)
        }

        // Apple only provides the name on the very first authorization.
        let firstName = credential.fullName?.givenName
        let lastName = credential.fullName?.familyName

        Self.logger.info("Exchanging Apple identity token (name: \(firstName ?? "nil") \(lastName ?? "nil"))")

        return try await exchangeAppleToken(
            baseURL: baseURL,
            identityToken: identityToken,
            firstName: firstName,
            lastName: lastName
        )
    }

    /// POST the Apple identity token to /auth/apple/token and return the JWT.
    private func exchangeAppleToken(
        baseURL: URL,
        identityToken: String,
        firstName: String?,
        lastName: String?
    ) async throws -> String {
        let url = baseURL.appendingPathComponent("auth/apple/token")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: String] = ["identity_token": identityToken]
        if let firstName { body["first_name"] = firstName }
        if let lastName { body["last_name"] = lastName }

        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard httpResponse.statusCode == 200 else {
            let detail = String(data: data, encoding: .utf8) ?? "Unknown error"
            Self.logger.error("Apple token exchange failed (\(httpResponse.statusCode)): \(detail)")
            throw URLError(.userAuthenticationRequired)
        }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = json["token"] as? String
        else {
            throw URLError(.cannotParseResponse)
        }
        return token
    }
}
