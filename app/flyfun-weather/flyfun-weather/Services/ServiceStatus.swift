import Foundation
import Observation
import os

/// What the server says when it can't answer: down for maintenance, or
/// temporarily unavailable.
///
/// When the app behind weather.flyfun.aero is down or switched into
/// maintenance, Caddy answers every API call with a 502/503/504 and a small
/// JSON body written by `scripts/ops/maintenance.py`:
///
///     {"service_status": "maintenance" | "unavailable",
///      "detail": "FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.",
///      "until": "2026-11-07T14:30:00Z" | null, "updated_at": "..."}
///
/// `APIClient` still throws `.serverError(code, detail)` for it (so every
/// existing retry/classify decision on 5xx is unchanged), and additionally
/// reports it to `ServiceStatusStore`, which drives one app-wide banner.
nonisolated struct ServiceStatus: Equatable, Sendable {
    enum Kind: String, Sendable {
        case maintenance
        case unavailable
    }

    let kind: Kind
    let message: String
    let until: Date?

    static let genericMessage =
        "FlyFun Weather is temporarily unavailable. Please try again in a few minutes."

    /// Sentences the status page has carried this launch, so a later
    /// `serverError(503, sentence)` can be told apart from the app's own 503
    /// (see `APIError.isTransientNetwork`). Tiny: the text changes per window.
    private static let statusPageMessages = OSAllocatedUnfairLock<Set<String>>(initialState: [genericMessage])

    static func rememberStatusPageMessage(_ message: String) {
        statusPageMessages.withLock { seen in
            if seen.count > 32 { seen = [genericMessage] }
            seen.insert(message)
        }
    }

    static func isStatusPageMessage(_ message: String) -> Bool {
        statusPageMessages.withLock { $0.contains(message) }
    }

    /// The Caddy status body on a 5xx, or nil for anything else (an app error).
    static func parse(statusCode: Int, body: Data) -> ServiceStatus? {
        guard statusCode >= 500,
              let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let raw = object["service_status"] as? String,
              let kind = Kind(rawValue: raw) else { return nil }
        let detail = (object["detail"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let until = (object["until"] as? String).flatMap { ISO8601DateFormatter().date(from: $0) }
        return ServiceStatus(kind: kind,
                             message: (detail?.isEmpty == false ? detail : nil) ?? genericMessage,
                             until: until)
    }
}

/// App-wide "the server can't answer" state, set by `APIClient` when a call
/// comes back with the Caddy status body and cleared by the next 2xx.
@MainActor
@Observable
final class ServiceStatusStore {
    static let shared = ServiceStatusStore()

    private(set) var current: ServiceStatus?

    func report(_ status: ServiceStatus) {
        if current != status { current = status }
    }

    func clear() {
        if current != nil { current = nil }
    }

    /// The latest state reported from off the main actor. The hops below don't
    /// carry a value: each one applies whatever is latest when it runs, so two
    /// hops that run out of order (a 503 and a 2xx racing) still converge on the
    /// last report instead of leaving a stale banner up. Also lets the hot
    /// success path skip the hop when nothing is shown.
    nonisolated private static let latest = OSAllocatedUnfairLock<ServiceStatus?>(initialState: nil)

    /// From `APIClient` / URLSession delegates: a request hit the status page.
    nonisolated static func noteUnavailable(_ status: ServiceStatus) {
        latest.withLock { $0 = status }
        Task { @MainActor in shared.applyLatest() }
    }

    /// From `APIClient` / URLSession delegates: a request succeeded. Cheap
    /// when nothing is shown.
    nonisolated static func noteReachable() {
        let wasShown = latest.withLock { state -> Bool in
            defer { state = nil }
            return state != nil
        }
        guard wasShown else { return }
        Task { @MainActor in shared.applyLatest() }
    }

    private func applyLatest() {
        if let status = Self.latest.withLock({ $0 }) { report(status) } else { clear() }
    }
}
