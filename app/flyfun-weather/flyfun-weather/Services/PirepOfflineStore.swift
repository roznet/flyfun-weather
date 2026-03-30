import Foundation
import OSLog

/// Simple JSON-file-based offline queue for unsent PIREPs.
actor PirepOfflineStore {
    private let fileURL: URL
    private var pending: [SubmitPirepRequest] = []
    private var loaded = false

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "PirepOffline")

    init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        fileURL = docs.appendingPathComponent("pending_pireps.json")
    }

    /// Load pending PIREPs from disk.
    func load() {
        guard !loaded else { return }
        loaded = true
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        do {
            let data = try Data(contentsOf: fileURL)
            pending = try JSONDecoder.weatherBrief.decode([SubmitPirepRequest].self, from: data)
            Self.logger.info("Loaded \(self.pending.count) pending PIREPs")
        } catch {
            Self.logger.error("Failed to load pending PIREPs: \(error)")
        }
    }

    /// Add a PIREP to the offline queue.
    func enqueue(_ request: SubmitPirepRequest) {
        pending.append(request)
        save()
    }

    /// Get all pending PIREPs.
    var pendingCount: Int { pending.count }

    /// Sync all pending PIREPs to the server.
    /// Returns the number successfully synced.
    func sync(using repository: any BriefingRepository) async -> Int {
        guard !pending.isEmpty else { return 0 }

        let toSync = pending
        do {
            let _ = try await repository.submitPirepsBatch(toSync)
            // All synced (server handles dedup via client_uuid)
            pending.removeAll()
            save()
            Self.logger.info("Synced \(toSync.count) PIREPs")
            return toSync.count
        } catch {
            Self.logger.error("Failed to sync PIREPs: \(error)")
            return 0
        }
    }

    private func save() {
        do {
            let data = try JSONEncoder.weatherBrief.encode(pending)
            try data.write(to: fileURL, options: .atomic)
        } catch {
            Self.logger.error("Failed to save pending PIREPs: \(error)")
        }
    }
}
