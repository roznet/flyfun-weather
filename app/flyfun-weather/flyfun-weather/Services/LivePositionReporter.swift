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

    /// `buffer` with `fix` added (oldest first), spaced and capped.
    static func appending(_ fix: LivePositionFix?, to buffer: [LivePositionFix]) -> [LivePositionFix] {
        guard let fix else { return buffer }
        if let last = buffer.last, fix.at <= last.at { return buffer }
        var out = buffer
        if out.count >= 2, out[out.count - 1].at.timeIntervalSince(out[out.count - 2].at) < minSpacing {
            out[out.count - 1] = fix
        } else {
            out.append(fix)
        }
        return Array(out.suffix(maxFixes))
    }

    /// Whether `fixes` hold anything newer than the newest fix the server
    /// already accepted (`sent`): nothing new means no post.
    static func hasNew(_ fixes: [LivePositionFix], since sent: Date?) -> Bool {
        guard let newest = fixes.last?.at else { return false }
        guard let sent else { return true }
        return newest > sent
    }
}

/// Posts the tracking service's recent fixes to the server (#761): on Start
/// Flight and on return to the foreground (each with the next fix), with
/// every live poll, and at the landing auto-stop (with `landed`). Owner only
/// (the server refuses anyone else). Fire-and-forget: no offline queue,
/// since only the newest fix matters and the next post carries it; a failed
/// post is logged and dropped. Never blocks the live poll's GET.
@MainActor
final class LivePositionReporter {
    private let repository: any BriefingRepository
    private let flightId: String
    /// The newest fix the server has accepted, so an unchanged buffer is not resent.
    private var lastSent: Date?
    private var posting = false

    init(repository: any BriefingRepository, flightId: String) {
        self.repository = repository
        self.flightId = flightId
    }

    func report(_ fixes: [LivePositionFix], landed: Bool = false) {
        guard landed || LivePositionRules.hasNew(fixes, since: lastSent) else { return }
        // One post at a time; the landing always goes.
        guard landed || !posting else { return }
        posting = true
        let request = LivePositionRequest(fixes: fixes, landed: landed)
        let newest = fixes.last?.at
        Task { [weak self, repository, flightId] in
            defer { self?.posting = false }
            do {
                let result = try await repository.postLivePosition(flightId: flightId, request: request)
                if let newest, let self, self.lastSent.map({ newest > $0 }) ?? true {
                    self.lastSent = newest
                }
                logger.debug("Posted \(fixes.count) fixes: \(result.accepted) accepted, landed=\(result.landedRecorded)")
            } catch {
                logger.debug("Position post failed, dropped: \(error)")
            }
        }
    }
}
