import AuthenticationServices
import Foundation
import OSLog
import UIKit

nonisolated private let logger = Logger(subsystem: "aero.flyfun.weather", category: "AutorouterLinker")

/// Links the pilot's autorouter.aero account from inside the app (#625).
///
/// The server's link flow is browser-based OAuth, and a browser can't send the
/// app's bearer token — so the app first asks for a short-lived signed URL
/// (`POST /autorouter/link-ticket`, via the repository), opens it in an
/// `ASWebAuthenticationSession`, and the server sends the pilot back to
/// `flyfunweather://autorouter/callback?status=authorized&code=…` (or
/// `status=error&reason=…`). The token is only stored when the app redeems
/// that code with its own bearer (`POST /autorouter/link-complete`) — the
/// server checks the code was issued for this same account, which is what
/// stops a link started by someone else from landing on theirs.
/// Before this the only path was signing in to the website and linking from
/// its Settings page.
protocol AutorouterLinking {
    /// Runs the in-app sign-in at `url`. Returns the link code to redeem, or
    /// `nil` when the pilot cancelled; throws `AutorouterLinkError` when the
    /// server reported a failure.
    @MainActor func link(at url: URL) async throws -> String?
}

extension AutorouterLinking {
    /// Fetch a link URL, run the in-app sign-in, and redeem the resulting code.
    /// `true` = linked, `false` = the pilot cancelled.
    @MainActor func connect(via repository: any BriefingRepository) async throws -> Bool {
        let url = try await repository.autorouterLinkURL(scheme: AutorouterLinker.callbackScheme)
        guard let code = try await link(at: url) else { return false }
        try await repository.completeAutorouterLink(code: code)
        return true
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

    func link(at url: URL) async throws -> String? {
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
                        // Neither URL nor error: nothing came back — a cancel.
                        continuation.resume(throwing: ASWebAuthenticationSessionError(.canceledLogin))
                    }
                }
                session.presentationContextProvider = self
                // Shared with Safari so an existing autorouter.aero login is
                // reused — linking is then one tap on Allow.
                session.prefersEphemeralWebBrowserSession = false
                self.session = session
                // start() returns false without ever calling the completion
                // (no anchor, or another session already showing); resume
                // here or the Connecting spinner never clears.
                if !session.start() {
                    continuation.resume(throwing: AutorouterLinkError(reason: "not_started"))
                }
            }
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            return nil
        }
        return try Self.outcome(from: callbackURL)
    }

    /// Interpret the server's callback URL: the link code to redeem.
    nonisolated static func outcome(from url: URL) throws -> String {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        guard components?.host == "autorouter" else {
            logger.error("Unexpected Autorouter callback: \(url)")
            throw AutorouterLinkError(reason: "unexpected_callback")
        }
        let items = components?.queryItems ?? []
        let status = items.first { $0.name == "status" }?.value
        if status == "authorized", let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty {
            return code
        }
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
            // Linking starts from a button on screen, so a window scene exists.
            guard let scene else { preconditionFailure("Autorouter link with no window scene") }
            return ASPresentationAnchor(windowScene: scene)
        }
    }
}
