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
/// server refuses anyone else). A post that fails keeps its fixes queued, and
/// the next submit, live poll or reconnect resends them: in flight there is
/// often no data, and a late fix still counts on the server. One post per
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

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    /// Queue `fixes` (and the landing) for `flightId` and send them.
    func submit(_ fixes: [LivePositionFix], flightId: String, landed: Bool = false) {
        queues[flightId] = LivePositionRules.queued(fixes, into: queues[flightId] ?? [],
                                                    since: lastSent[flightId])
        if landed { landings.insert(flightId) }
        flush(flightId: flightId)
    }

    /// Send what is queued for `flightId`, if anything.
    func flush(flightId: String) {
        let fixes = queues[flightId] ?? []
        let landed = landings.contains(flightId)
        guard !fixes.isEmpty || landed, !posting.contains(flightId) else { return }
        posting.insert(flightId)
        let request = LivePositionRequest(fixes: fixes, landed: landed)
        Task { [weak self, repository] in
            do {
                let result = try await repository.postLivePosition(flightId: flightId, request: request)
                self?.sent(fixes, landed: landed, flightId: flightId)
                logger.debug("Posted \(fixes.count) fixes: \(result.accepted) accepted, landed=\(result.landedRecorded)")
            } catch {
                self?.posting.remove(flightId)
                logger.debug("Position post failed, kept for the next one: \(error)")
            }
        }
    }

    /// Send every queue (back online).
    func flushAll() {
        for flightId in Set(queues.keys).union(landings) {
            flush(flightId: flightId)
        }
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

    private static let consentKey = "livePositionConsentFlightIds"
    /// Flights the pilot has pressed Start Flight for: opening one of them in
    /// its live window takes a fix without asking again.
    static func grantConsent(flightId: String, defaults: UserDefaults = .standard) {
        var ids = defaults.stringArray(forKey: consentKey) ?? []
        guard !ids.contains(flightId) else { return }
        ids.append(flightId)
        defaults.set(Array(ids.suffix(50)), forKey: consentKey)
    }

    static func hasConsent(flightId: String, defaults: UserDefaults = .standard) -> Bool {
        (defaults.stringArray(forKey: consentKey) ?? []).contains(flightId)
    }
}
