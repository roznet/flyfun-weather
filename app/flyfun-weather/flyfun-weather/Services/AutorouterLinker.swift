import AuthenticationServices
import Foundation
import OSLog
import UIKit

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "AutorouterLinker")

/// Links the pilot's autorouter.aero account from inside the app (#625).
///
/// The server's link flow is browser-based OAuth, and a browser can't send the
/// app's bearer token — so the app first asks for a short-lived signed URL
/// (`POST /autorouter/link-ticket`, via the repository), opens it in an
/// `ASWebAuthenticationSession`, and the server sends the pilot back to
/// `flyfunweather://autorouter/callback?status=linked|error&reason=…`.
/// Before this the only path was signing in to the website and linking from
/// its Settings page.
protocol AutorouterLinking {
    /// Runs the in-app sign-in at `url`. Returns `false` when the pilot
    /// cancelled; throws `AutorouterLinkError` when the server reported a
    /// failure.
    @MainActor func link(at url: URL) async throws -> Bool
}

extension AutorouterLinking {
    /// Fetch a link URL from the server and run the in-app sign-in. `true` =
    /// linked, `false` = the pilot cancelled.
    @MainActor func connect(via repository: any BriefingRepository) async throws -> Bool {
        let url = try await repository.autorouterLinkURL(scheme: AutorouterLinker.callbackScheme)
        return try await link(at: url)
    }

    /// Pilot-facing text for a failed `connect`.
    static func message(for error: Error) -> String {
        (error as? AutorouterLinkError)?.errorDescription
            ?? "Couldn't connect Autorouter: \(error.localizedDescription)"
    }
}

/// A failure the server reported on the callback (`status=error&reason=…`).
struct AutorouterLinkError: LocalizedError, Equatable {
    let reason: String

    var errorDescription: String? {
        switch reason {
        case "denied":
            return "Autorouter access wasn't granted. Try again and choose Allow on autorouter.aero."
        case "expired":
            return "The connection request timed out. Please try again."
        default:
            return "Couldn't connect Autorouter. Please try again."
        }
    }
}

@MainActor
final class AutorouterLinker: NSObject, AutorouterLinking, ASWebAuthenticationPresentationContextProviding {
    /// Registered in Info.plist; also on the server's `OAUTH_ALLOWED_SCHEMES`.
    nonisolated static let callbackScheme = "flyfunweather"

    /// Strong reference so the session isn't deallocated mid-flow.
    private var session: ASWebAuthenticationSession?

    func link(at url: URL) async throws -> Bool {
        defer { session = nil }
        let callbackURL: URL
        do {
            callbackURL = try await withCheckedThrowingContinuation { continuation in
                // Completion runs on Apple's XPC queue: @Sendable keeps it from
                // inheriting MainActor isolation (same pattern as
                // FlyFunAuthService.signIn).
                let session = ASWebAuthenticationSession(
                    url: url,
                    callback: .customScheme(Self.callbackScheme)
                ) { @Sendable url, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else if let url {
                        continuation.resume(returning: url)
                    } else {
                        continuation.resume(throwing: URLError(.cancelled))
                    }
                }
                session.presentationContextProvider = self
                // Shared with Safari so an existing autorouter.aero login is
                // reused — linking is then one tap on Allow.
                session.prefersEphemeralWebBrowserSession = false
                self.session = session
                session.start()
            }
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            return false
        }
        return try Self.outcome(from: callbackURL)
    }

    /// Interpret the server's callback URL. `true` = linked.
    nonisolated static func outcome(from url: URL) throws -> Bool {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        guard components?.host == "autorouter" else {
            logger.error("Unexpected Autorouter callback: \(url)")
            throw AutorouterLinkError(reason: "unexpected_callback")
        }
        let items = components?.queryItems ?? []
        let status = items.first { $0.name == "status" }?.value
        if status == "linked" { return true }
        let reason = items.first { $0.name == "reason" }?.value ?? "unknown"
        logger.info("Autorouter link failed: \(reason)")
        throw AutorouterLinkError(reason: reason)
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let scene = UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .first
            if let key = scene?.keyWindow { return key }
            if let scene { return ASPresentationAnchor(windowScene: scene) }
            return ASPresentationAnchor()
        }
    }
}
