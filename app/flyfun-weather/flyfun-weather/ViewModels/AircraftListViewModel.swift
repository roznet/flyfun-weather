import Foundation
import OSLog

/// Settings → Aircraft (#778): list, add, edit, delete and make-default for
/// the pilot's saved aircraft, the iOS counterpart of the web Settings
/// Aircraft tab.
///
/// The server owns the invariants (#777): every account has a default
/// aircraft, the last one can't be deleted (409), deleting the default
/// promotes another, and a `POST` over a sole placeholder replaces it. So
/// every change is followed by a re-read rather than patched locally, and a
/// delete removes the row only once the server has accepted it.
@Observable
@MainActor
final class AircraftListViewModel {
    /// Default first, then by title (`sortedForPicker`).
    private(set) var aircraft: [AircraftResponse] = []
    private(set) var isLoading: Bool = false
    /// True once a load has succeeded, so the view can tell "still loading"
    /// from "load failed" for an empty list.
    private(set) var hasLoaded: Bool = false
    /// True when the latest load failed (the list keeps what it had).
    private(set) var loadFailed: Bool = false
    /// Rows with a delete or make-default request in flight.
    private(set) var busyIds: Set<Int> = []
    /// Last failure to show the pilot (load, delete, make default). The
    /// last-aircraft 409 lands here as the server's own sentence.
    var errorMessage: String?
    /// The add / edit sheet, shared with the flight form.
    let form: AircraftFormModel

    /// Set when `load()` is called while a load is in flight.
    private var reloadRequested = false
    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "AircraftList")

    init(repository: any BriefingRepository) {
        self.repository = repository
        self.form = AircraftFormModel(repository: repository)
    }

    /// Single-flight, but never drops a request: a call while a load is in
    /// flight queues one more read, so the re-read after a delete or make
    /// default isn't lost to a concurrent pull-to-refresh (whose response may
    /// predate the change).
    func load() async {
        guard !isLoading else {
            reloadRequested = true
            return
        }
        isLoading = true
        defer { isLoading = false }
        repeat {
            reloadRequested = false
            do {
                aircraft = try await repository.aircraft().sortedForPicker()
                hasLoaded = true
                loadFailed = false
            } catch {
                if (error as? APIError)?.isCancellation == true { return }
                loadFailed = true
                // An empty list already says so inline; alert only when there
                // are (now possibly stale) rows on screen.
                if !aircraft.isEmpty {
                    errorMessage = "Couldn't refresh your aircraft: \(error.localizedDescription)"
                }
                Self.logger.error("Aircraft list load failed: \(error)")
            }
        } while reloadRequested
    }

    // MARK: - Add / edit

    func prepareNew() {
        form.prepareNew(existing: aircraft)
    }

    func prepareEdit(_ aircraft: AircraftResponse) {
        form.prepareEdit(aircraft)
    }

    /// Save the sheet, then re-read the list: a new default clears the old
    /// one's badge, and an add over the placeholder replaces that row.
    func save() async -> Bool {
        guard await form.save() != nil else { return false }
        errorMessage = nil
        await load()
        return true
    }

    // MARK: - Delete / make default

    /// Delete an aircraft. The row stays until the server accepts: the last
    /// aircraft is refused (409) and its message is shown instead. Deleting
    /// the default makes the server promote another, so the list is re-read.
    @discardableResult
    func delete(_ target: AircraftResponse) async -> Bool {
        guard !busyIds.contains(target.id) else { return false }
        busyIds.insert(target.id)
        defer { busyIds.remove(target.id) }
        errorMessage = nil
        do {
            try await repository.deleteAircraft(id: target.id)
        } catch {
            errorMessage = Self.deleteFailureMessage(for: error)
            Self.logger.error("Delete aircraft \(target.id) failed: \(error)")
            return false
        }
        // Drop it now so it can't flash back while the re-read is in flight.
        aircraft.removeAll { $0.id == target.id }
        await load()
        return true
    }

    /// Make an aircraft the account default (`PUT {is_default: true}`); the
    /// server clears the old default, so the list is re-read and re-sorted.
    func makeDefault(_ target: AircraftResponse) async {
        guard !target.isDefault, !busyIds.contains(target.id) else { return }
        busyIds.insert(target.id)
        defer { busyIds.remove(target.id) }
        errorMessage = nil
        do {
            _ = try await repository.updateAircraft(id: target.id, request: UpdateAircraftRequest(isDefault: true))
        } catch {
            errorMessage = "Couldn't make it the default: \(error.localizedDescription)"
            Self.logger.error("Make default \(target.id) failed: \(error)")
            return
        }
        await load()
    }

    /// The confirmation title before a delete, naming the aircraft like the
    /// web ("Delete N123?": tail, else nickname, else type).
    nonisolated static func deleteConfirmationTitle(for aircraft: AircraftResponse) -> String {
        "Delete \(aircraft.warningName)?"
    }

    /// What the pilot reads when a delete fails. The last-aircraft 409 carries
    /// the server's own sentence ("You can't delete your only aircraft…");
    /// the fallback matches the web's.
    static func deleteFailureMessage(for error: Error) -> String {
        if case APIError.serverError(409, let detail) = error {
            if let detail, !detail.isEmpty { return detail }
            return "You can't delete your only aircraft."
        }
        return "Failed to delete: \(error.localizedDescription)"
    }
}
