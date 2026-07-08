import Foundation

/// Drives a briefing refresh from an App Intent and reports the *initial* gate
/// outcome, without blocking on the full ~2-minute pipeline.
///
/// The server's refresh gate (see `refresh_briefing` / Decision 2) decides
/// quickly whether a full run is warranted; that decision arrives early on the
/// SSE stream. We surface it and — for a real run that started — return the
/// interim "started" outcome immediately (the design's accepted UX until the
/// briefing-ready push lands). A detached drain keeps the SSE channel open so a
/// started server run isn't torn down by our early return.
enum RefreshDriver {
    enum Outcome: Sendable {
        /// Gate decided no refresh was needed; carries the server's reason.
        case alreadyFresh(reason: String?)
        /// A refresh started (real run underway) — best interim UX without push.
        case started
        /// The refresh completed synchronously (fast realtime-only path).
        case completed
        /// A refresh is already running for this flight (409).
        case alreadyInProgress
        /// Rate-limited (429) — refreshed very recently.
        case rateLimited
        /// Any other failure; carries a message.
        case failed(String)
    }

    static func run(repository: any BriefingRepository, flightId: String) async -> Outcome {
        // If one is already running (started from web/another device), say so.
        if let status = try? await repository.refreshStatus(flightId: flightId), status.active {
            return .alreadyInProgress
        }
        return await withCheckedContinuation { continuation in
            // Unstructured Task: retained by the runtime until it finishes, so the
            // SSE stream stays alive (server run not disconnected) even after we
            // resume the continuation on the first progress event.
            Task {
                var reported = false
                func report(_ outcome: Outcome) {
                    guard !reported else { return }
                    reported = true
                    continuation.resume(returning: outcome)
                }
                do {
                    let stream = await repository.refreshStream(flightId: flightId)
                    for try await event in stream {
                        switch event.type {
                        case "complete":
                            if let decision = event.refreshDecision, decision.mode == "none" {
                                report(.alreadyFresh(reason: decision.reason))
                            } else {
                                report(.completed)
                            }
                            return
                        case "error":
                            report(.failed(event.message ?? "Refresh failed."))
                            return
                        case "progress", "briefing_ready":
                            // A real run is underway — report interim, keep draining.
                            report(.started)
                        default:
                            break
                        }
                    }
                    // Stream ended without any progress or terminal event: nothing
                    // actually started (the sibling `BriefingViewModel.refresh()`
                    // treats this same case as anomalous). Don't tell the pilot a
                    // refresh is in progress. If a progress event *had* arrived,
                    // `.started` was already reported and this is a no-op.
                    report(.failed("I couldn't start the refresh. Please try again from FlyFun."))
                } catch {
                    report(classify(error))
                }
            }
        }
    }

    private static func classify(_ error: Error) -> Outcome {
        if let apiError = error as? APIError {
            switch apiError {
            case .serverError(429, _): return .rateLimited
            case .serverError(409, _): return .alreadyInProgress
            case .unauthorized: return .failed("Please open FlyFun to sign in first.")
            default: break
            }
        }
        return .failed(error.localizedDescription)
    }
}
