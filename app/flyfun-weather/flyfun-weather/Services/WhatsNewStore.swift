import Foundation
import OSLog

/// Local cache of the release stream ("What's New") plus the unseen badge count.
///
/// Modeled on `HelpCatalogStore`: the server is the single source of truth, the
/// last-fetched stream is persisted to disk, and the view reads that cache — so
/// What's New opens with content in the cockpit like every other screen, rather
/// than being the one blank view when the app is offline.
///
/// The stream itself is global (not per-user), so unlike the briefing cache it
/// needs no per-user scoping — nothing user-specific is written to disk. The
/// unseen count *is* per-user and is deliberately NOT persisted: it comes from
/// `/messages/status` on each refresh, so a signed-out or offline app simply
/// shows no dot rather than a stale one.
///
/// The seen-pointer (`messages_last_seen_id`) is one value per user, shared with
/// the web app — marking seen here also clears the web's nav dot, matching the
/// cross-surface briefing badge.
@MainActor
@Observable
final class WhatsNewStore {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "WhatsNew")

    /// Cached stream, newest first. Seeded from disk at init.
    private(set) var messages: [SystemMessage]

    /// Highlighted entries the user hasn't seen. Drives the More-menu dot.
    private(set) var unseenCount = 0

    /// True once a refresh has completed (or failed) at least once this launch —
    /// lets the view tell "nothing downloaded yet" from "the stream is empty".
    private(set) var hasLoaded = false

    private let fileURL: URL

    init(fileURL: URL? = nil) {
        self.fileURL = fileURL ?? FileManager.default
            .urls(for: .documentDirectory, in: .userDomainMask).first!
            .appendingPathComponent("whats-new.json")
        messages = Self.loadFromDisk(self.fileURL) ?? []
    }

    // MARK: - Sync

    /// Fetch the stream and the unseen count, then persist the stream.
    ///
    /// Non-blocking by design (call from a `Task`). The two calls are
    /// independent, so a failing `/status` (e.g. an expired session) still
    /// leaves the stream itself refreshed, and vice versa.
    ///
    /// This is the authoritative read — the What's New view calls it on open, so
    /// what the reader sees is always current. Background callers should prefer
    /// `syncIfNeeded`.
    func refresh(using client: APIClient) async {
        await fetchStream(using: client)
        await readStatus(using: client)
        hasLoaded = true
    }

    /// Background sync (launch / sign-in / foreground): always re-read the cheap
    /// status, and download the stream only when the cache can't be current.
    ///
    /// `/status` already reports `latest_message_date`, so a newer date than
    /// anything cached means there is something to fetch. The blind spot — a
    /// second entry published on a date we already hold — costs nothing that
    /// matters: the dot still updates from the status count, and opening the view
    /// runs a full `refresh` anyway.
    func syncIfNeeded(using client: APIClient) async {
        let status = await readStatus(using: client)
        if Self.needsStreamDownload(latestMessageDate: status?.latestMessageDate,
                                    cachedDates: messages.map(\.date)) {
            await fetchStream(using: client)
        }
        hasLoaded = true
    }

    /// Whether the cached stream can't be current. Nil `latestMessageDate` means
    /// the status read failed or the server has no entries — either way there is
    /// nothing to fetch. `YYYY-MM-DD` sorts lexicographically the same way it
    /// sorts in time, so a plain string compare is the date compare.
    /// `nonisolated` — pure, touches no store state, so tests (and any
    /// off-main caller) can reach it without hopping to the main actor.
    nonisolated static func needsStreamDownload(latestMessageDate: String?, cachedDates: [String]) -> Bool {
        guard let latest = latestMessageDate else { return false }
        guard let newestCached = cachedDates.max() else { return true }
        return latest > newestCached
    }

    /// Download the stream and persist it. A transient network error is a silent
    /// no-op so the cached copy stays in place offline.
    private func fetchStream(using client: APIClient) async {
        do {
            let fresh: [SystemMessage] = try await client.request("/api/messages")
            messages = fresh
            persist(fresh)
        } catch let error as APIError where error.isTransientNetwork {
            Self.logger.debug("Offline, keeping cached release stream")
        } catch {
            Self.logger.warning("Failed to refresh release stream: \(error.localizedDescription)")
        }
    }

    /// Re-read the badge count (cheap; no stream download). Returns the status so
    /// callers can also use its `latestMessageDate`.
    @discardableResult
    private func readStatus(using client: APIClient) async -> MessagesStatus? {
        do {
            let status: MessagesStatus = try await client.request("/api/messages/status")
            unseenCount = status.unseenCount
            return status
        } catch {
            Self.logger.debug("Unseen-count check skipped: \(error.localizedDescription)")
            return nil
        }
    }

    /// Mark the whole stream seen (server stores `messages_last_seen_id`) and
    /// clear the dot locally. Optimistic: the dot clears even if the POST fails,
    /// since the next `/status` read is authoritative anyway.
    func markSeen(using client: APIClient) async {
        unseenCount = 0
        do {
            _ = try await client.requestData("/api/messages/seen", method: "POST")
        } catch {
            Self.logger.warning("Failed to mark messages seen: \(error.localizedDescription)")
        }
    }

    // MARK: - Persistence

    private func persist(_ messages: [SystemMessage]) {
        do {
            try JSONEncoder.weatherBrief.encode(messages).write(to: fileURL, options: .atomic)
        } catch {
            Self.logger.warning("Failed to write release-stream cache: \(error.localizedDescription)")
        }
    }

    private static func loadFromDisk(_ url: URL) -> [SystemMessage]? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            return try JSONDecoder.weatherBrief.decode([SystemMessage].self, from: Data(contentsOf: url))
        } catch {
            logger.error("Failed to load cached release stream: \(error.localizedDescription)")
            return nil
        }
    }
}
