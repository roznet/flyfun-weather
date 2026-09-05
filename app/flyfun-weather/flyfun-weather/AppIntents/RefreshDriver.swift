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
    enum Outcome: Sendable, Equatable {
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
            // resume the continuation once the full-run `briefing_ready` event
            // proves this was not a realtime path awaiting a durable cache save.
            Task {
                var reported = false
                func report(_ outcome: Outcome) {
                    guard !reported else { return }
                    reported = true
                    continuation.resume(returning: outcome)
                }
                do {
                    // Siri/App-Intent refresh is non-user-present, so it still
                    // emails on completion (closing the refresh-intent loop).
                    let stream = await repository.refreshStream(flightId: flightId, source: .siri)
                    for try await event in stream {
                        switch event.type {
                        case "complete":
                            // Siri has no BriefingViewModel to persist a same-ID
                            // realtime refresh. Use the same repository/cache
                            // seam before reporting completion, targeting only
                            // the pack identified by the server event.
                            if event.refreshDecision?.mode == "realtime",
                               let caching = repository as? CachingBriefingRepository {
                                guard let timestamp = event.pack?.fetchTimestamp else {
                                    report(.failed("The refreshed briefing could not be matched to its offline pack."))
                                    return
                                }
                                do {
                                    try await caching.persistRealtimeRefresh(
                                        event, flightId: flightId, timestamp: timestamp)
                                } catch {
                                    report(.failed("The briefing refreshed online, but its offline copy could not be saved."))
                                    return
                                }
                            }
                            if let decision = event.refreshDecision, decision.mode == "none" {
                                report(.alreadyFresh(reason: decision.reason))
                            } else {
                                report(.completed)
                            }
                            return
                        case "error":
                            report(.failed(event.message ?? "Refresh failed."))
                            return
                        case "briefing_ready":
                            // A full run has crossed the gate. Realtime-only paths
                            // reach `complete` instead, so their cache-save result
                            // remains reportable to Siri before the outcome closes.
                            report(.started)
                        case "progress":
                            break
                        default:
                            break
                        }
                    }
                    // Stream ended without any progress or terminal event: nothing
                    // actually started (the sibling `BriefingViewModel.refresh()`
                    // treats this same case as anomalous). Don't tell the pilot a
                    // refresh is in progress. If `briefing_ready` had arrived,
                    // `.started` was already reported and this is a no-op.
                    report(.failed("I couldn't start the refresh. Please try again from FlyFun."))
                } catch {
                    report(classify(error))
                }
            }
        }
    }

    /// Map a thrown API error to a spoken outcome. `internal` (not `private`) so
    /// the pure mapping is unit-testable.
    static func classify(_ error: Error) -> Outcome {
        if let apiError = error as? APIError {
            switch apiError {
            case .serverError(429, _): return .rateLimited
            case .serverError(409, _): return .alreadyInProgress
            case .unauthorized: return .failed(IntentSupport.signedOutSpokenLine)
            default: break
            }
        }
        return .failed(error.localizedDescription)
    }
}
