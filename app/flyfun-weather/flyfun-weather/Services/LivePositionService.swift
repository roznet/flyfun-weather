import Foundation
import OSLog

nonisolated private let logger = Logger(subsystem: "aero.flyfun.weather", category: "LivePosition")

/// Pure rules for the GPS fixes the app keeps and sends (#761).
nonisolated enum LivePositionRules {
    /// The newest fix plus up to two recent ones go with each post.
    static let maxFixes = 3
    /// The recent fixes are at least this far apart. The buffer always ends on
    /// the latest fix: while it is under this after the one before it, the
    /// next fix replaces it instead of being added.
    static let minSpacing: TimeInterval = 60
    /// Unsent fixes kept for the next post: the server takes at most 5 a post.
    static let maxQueued = 5
    /// Older unsent fixes are kept this far apart, so a queue built up with no
    /// data in flight still spans the flight (the server measures the speed
    /// flown from fixes ≥ 15 min apart).
    static let queueSpacing: TimeInterval = 10 * 60

    /// `buffer` with `fix` added (oldest first), spaced and capped: the
    /// buffer always ends on the latest fix, which replaces the last one
    /// while that is under `spacing` after the one before it.
    static func appending(_ fix: LivePositionFix?, to buffer: [LivePositionFix],
                          spacing: TimeInterval = minSpacing, cap: Int = maxFixes) -> [LivePositionFix] {
        guard let fix else { return buffer }
        if let last = buffer.last, fix.at <= last.at { return buffer }
        var out = buffer
        if out.count >= 2, out[out.count - 1].at.timeIntervalSince(out[out.count - 2].at) < spacing {
            out[out.count - 1] = fix
        } else {
            out.append(fix)
        }
        return Array(out.suffix(cap))
    }

    /// What a failed post means for its fixes: `.retry` keeps them queued (no
    /// data, a server hiccup, 408 / 429); `.hold` keeps them but stops the
    /// per-poll retry until something new is submitted (409: the briefing
    /// cannot be read until it is refreshed); `.drop` discards them, since the
    /// same request would fail again (signed out, not the owner, no flight,
    /// any other 4xx); `.sent` when the reply came back but did not decode (a
    /// 2xx: the server recorded them).
    enum FailureAction: Equatable { case retry, hold, drop, sent }

    static func failureAction(for error: Error) -> FailureAction {
        guard let api = error as? APIError else { return .retry }
        switch api {
        case .unauthorized, .forbidden, .notFound:
            return .drop
        case .serverError(409, _):
            return .hold
        case .serverError(let code, _):
            return (400..<500).contains(code) && code != 408 && code != 429 ? .drop : .retry
        case .decodingError:
            return .sent
        case .networkError:
            return .retry
        }
    }

    /// The unsent queue with `fixes` merged in (oldest first): only fixes
    /// newer than `sent` (the newest the server has) and than the queue's
    /// last, spaced `queueSpacing` and capped at `maxQueued`.
    static func queued(_ fixes: [LivePositionFix], into queue: [LivePositionFix],
                       since sent: Date?) -> [LivePositionFix] {
        var out = queue
        for f in fixes.sorted(by: { $0.at < $1.at }) where sent.map({ f.at > $0 }) ?? true {
            out = appending(f, to: out, spacing: queueSpacing, cap: maxQueued)
        }
        return out
    }
}

/// Sends the app's GPS fixes to the server (#761), one queue per flight.
///
/// Every source feeds it: Start Flight tracking (with each live poll and on
/// return to the foreground), the landing auto-stop (with `landed`), and any
/// one-shot fix during the flight's live window (the PIREP form, the map's
/// locate button, the fix taken when the flight is opened). Owner only (the
/// server refuses anyone else). A post that fails for want of data (or a
/// server hiccup) keeps its fixes queued, and the next submit, live poll or
/// reconnect resends them: in flight there is often no data, and a late fix
/// still counts on the server. One the server refuses (4xx) is dropped, or
/// held until the next fix or landing on a 409 (the briefing needs a refresh)
/// (`LivePositionRules.failureAction`), never retried every poll. One post per
/// flight at a time; what arrives meanwhile goes with the next one. The queue
/// lives in memory: an app killed with fixes unsent loses them.
@MainActor
final class LivePositionService {
    private let repository: any BriefingRepository
    private var queues: [String: [LivePositionFix]] = [:]
    /// Flights with an app landing still to send.
    private var landings: Set<String> = []
    /// The newest fix the server has accepted, per flight, so an unchanged
    /// tracking buffer is not resent.
    private var lastSent: [String: Date] = [:]
    private var posting: Set<String> = []
    /// Flights whose last post got a 409: kept, not resent until something new.
    private var held: Set<String> = []
    private let defaults: UserDefaults
    private let consentKey: String

    /// `scope`: the signed-in user's cache scope (`AppState.cacheScope`), so
    /// one user's consents never apply to another on the same device.
    init(repository: any BriefingRepository, scope: String, defaults: UserDefaults = .standard) {
        self.repository = repository
        self.defaults = defaults
        self.consentKey = "livePositionConsentFlightIds.\(scope)"
    }

    /// Queue `fixes` (and the landing) for `flightId` and send them.
    func submit(_ fixes: [LivePositionFix], flightId: String, landed: Bool = false) {
        let before = queues[flightId]?.last?.at
        queues[flightId] = LivePositionRules.queued(fixes, into: queues[flightId] ?? [],
                                                    since: lastSent[flightId])
        if landed { landings.insert(flightId) }
        // Something new is worth one more try after a 409.
        if landed || queues[flightId]?.last?.at != before { held.remove(flightId) }
        flush(flightId: flightId)
    }

    /// Send what is queued for `flightId`, if anything (not while held).
    func flush(flightId: String) {
        let fixes = queues[flightId] ?? []
        let landed = landings.contains(flightId)
        guard !fixes.isEmpty || landed, !posting.contains(flightId), !held.contains(flightId) else { return }
        posting.insert(flightId)
        let request = LivePositionRequest(fixes: fixes, landed: landed)
        Task { [weak self, repository] in
            do {
                let result = try await repository.postLivePosition(flightId: flightId, request: request)
                self?.sent(fixes, landed: landed, flightId: flightId)
                logger.debug("Posted \(fixes.count) fixes: \(result.accepted) accepted, landed=\(result.landedRecorded)")
            } catch {
                switch LivePositionRules.failureAction(for: error) {
                case .retry:
                    self?.posting.remove(flightId)
                    logger.debug("Position post failed, kept for the next one: \(error)")
                case .hold:
                    self?.posting.remove(flightId)
                    self?.held.insert(flightId)
                    logger.warning("Position post refused for now, held for the next fix: \(error)")
                case .drop:
                    self?.drop(flightId: flightId)
                    logger.warning("Position post refused, \(fixes.count) fixes dropped: \(error)")
                case .sent:
                    self?.sent(fixes, landed: landed, flightId: flightId)
                    logger.warning("Position post reply unreadable, taken as sent: \(error)")
                }
            }
        }
    }

    /// Send every queue (back online).
    func flushAll() {
        for flightId in Set(queues.keys).union(landings) {
            flush(flightId: flightId)
        }
    }

    /// Refused for good: what is queued would be refused again.
    private func drop(flightId: String) {
        posting.remove(flightId)
        queues[flightId] = nil
        landings.remove(flightId)
    }

    private func sent(_ fixes: [LivePositionFix], landed: Bool, flightId: String) {
        posting.remove(flightId)
        if let newest = fixes.last?.at, lastSent[flightId].map({ newest > $0 }) ?? true {
            lastSent[flightId] = newest
        }
        let sentTimes = Set(fixes.map(\.at))
        queues[flightId] = (queues[flightId] ?? []).filter { !sentTimes.contains($0.at) }
        if landed { landings.remove(flightId) }
        // Queued while this post was out: send it now.
        if queues[flightId]?.isEmpty == false || landings.contains(flightId) {
            flush(flightId: flightId)
        }
    }

    // MARK: - Consent for the fix taken on opening

    /// Flights this user has pressed Start Flight for: opening one of them in
    /// its live window takes a fix without asking again.
    func grantConsent(flightId: String) {
        var ids = defaults.stringArray(forKey: consentKey) ?? []
        guard !ids.contains(flightId) else { return }
        ids.append(flightId)
        defaults.set(Array(ids.suffix(50)), forKey: consentKey)
    }

    func hasConsent(flightId: String) -> Bool {
        (defaults.stringArray(forKey: consentKey) ?? []).contains(flightId)
    }
}
