import Foundation
import OSLog

/// State for the trip screen (#607).
///
/// Three things it deliberately does **not** do:
///
/// * **Re-derive anything.** The binding leg, the chain status and the headline
///   all come from the server. This type only decides what to *show*.
/// * **Fan out a refresh.** One `POST`, then poll. The server admits one leg at
///   a time and keeps going if the app is backgrounded; a client-side fan-out is
///   refused by the per-user cap and would block other users on the way.
/// * **Post for the AI paragraph on every open.** `GET /trips/{id}` already
///   returns the stored text plus `aiSummaryStale`, so the POST fires only when
///   the text is stale or missing — see `ensureAiSummary(for:)`, and note it is
///   additionally guarded to once per trip per instance.
@Observable
@MainActor
final class TripDetailViewModel {
    private(set) var state: LoadingState<TripResponse> = .idle
    /// True while reloading over an already-loaded trip — a subtle indicator
    /// rather than replacing the screen with a spinner, matching the flight list.
    private(set) var isRefreshing = false
    /// Legs whose briefing is queued/refreshing server-side right now. Fed by the
    /// shared active-refresh poll rather than by `trip.refresh`, because a leg can
    /// be refreshed from its own briefing, Siri, MCP or the scheduler — none of
    /// which opens a trip run, and all of which the web learned to report here.
    private(set) var refreshingLegIds: Set<String> = []
    /// Surfaced when an action fails. The screen keeps its content underneath.
    var actionError: String?
    /// Why there is no AI paragraph, when the server told us. `ai_disabled` is
    /// the inherited-consent case and is worth saying out loud — a pilot who
    /// turned AI off on one leg should see that the trip inherited it, not an
    /// unexplained missing section.
    private(set) var aiUnavailableReason: String?

    let tripId: String
    private let repository: any TripRepository
    private let briefingRepository: (any BriefingRepository)?
    private var pollTask: Task<Void, Never>?
    private var isLoading = false
    /// Trip ids this instance has already asked to regenerate, so a view that
    /// re-appears (an iPad detail re-present, a tab switch) cannot re-post.
    private var aiRequested: Set<String> = []

    /// Progress-poll cadence while a chain runs. The refresh is server-driven and
    /// survives the app being killed, so this is purely a readout — a leg takes
    /// minutes, and 5 s matches the flight list's active-refresh poll and the
    /// web trip page.
    private static let pollInterval: Duration = .seconds(5)
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "TripDetail")

    init(
        tripId: String,
        repository: any TripRepository,
        briefingRepository: (any BriefingRepository)? = nil
    ) {
        self.tripId = tripId
        self.repository = repository
        self.briefingRepository = briefingRepository
    }

    var trip: TripResponse? {
        if case .loaded(let trip) = state { return trip }
        return nil
    }

    /// Whether a trip refresh is in flight right now.
    var isRefreshRunning: Bool { trip?.refresh?.active == true }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        if case .loaded = state {
            isRefreshing = true
        } else {
            state = .loading
        }
        defer { isRefreshing = false }
        do {
            let trip = try await repository.trip(id: tripId)
            state = .loaded(trip)
            // A fresh GET re-establishes consent: the server re-checks the gate on
            // read, so a stale "AI is off" note must not outlive it.
            if trip.aiSummary?.isEmpty == false { aiUnavailableReason = nil }
            if trip.refresh?.active == true { startPolling() }
            await ensureAiSummary(for: trip)
        } catch {
            Self.logger.warning("Trip \(self.tripId) load failed: \(error)")
            if case .loaded = state {
                // Keep what's on screen; a reload failure is not a reason to blank
                // a trip the pilot is reading.
                actionError = Self.message(for: error, fallback: "Couldn’t refresh this trip.")
            } else {
                state = .error(error)
            }
        }
    }

    /// Fetch the AI paragraph, at most once per trip per instance.
    ///
    /// Posted in exactly two cases, and skipped otherwise:
    ///
    /// * **`aiSummaryStale`** — the stored paragraph no longer matches the member
    ///   packs, so it needs regenerating.
    /// * **No paragraph at all** — which the GET cannot explain. `ai_summary` is
    ///   nil both for a trip that never had one *and* for a trip where a member
    ///   leg has the AI digest switched off (the server re-checks that consent
    ///   gate on read and blanks the text). Only this endpoint returns the
    ///   reason, and on the consent path it returns before generating anything,
    ///   so asking costs nothing.
    ///
    /// When the GET already carried text and did not call it stale, posting could
    /// only return what we already have — so we don't. The call is idempotent on
    /// unchanged inputs anyway (keyed on each leg's fetch timestamp, debrief
    /// decision and derived state; a key hit makes no model call), which is why
    /// no debounce beyond `aiRequested` is needed.
    private func ensureAiSummary(for trip: TripResponse) async {
        let needsAsking = trip.aiSummaryStale || (trip.aiSummary ?? "").isEmpty
        guard needsAsking, aiRequested.insert(trip.id).inserted else { return }
        do {
            let result = try await repository.tripAiSummary(tripId: trip.id)
            aiUnavailableReason = result.unavailableReason
            guard case .loaded(var current) = state, current.id == trip.id else { return }
            current.aiSummary = result.text
            current.aiSummaryAt = result.generatedAt
            current.aiSummaryStale = false
            state = .loaded(current)
        } catch {
            // Non-fatal and deliberately silent: the deterministic headline is
            // always there and is the thing the pilot is meant to read. A banner
            // saying the optional paragraph failed would be noise.
            Self.logger.info("Trip \(trip.id) AI summary refresh failed: \(error)")
        }
    }

    // MARK: - Refresh

    /// Start a trip refresh, then poll for progress.
    func refresh() async {
        guard !isRefreshRunning else { return }
        do {
            let status = try await repository.refreshTrip(tripId: tripId)
            apply(status)
            startPolling()
        } catch {
            actionError = Self.message(for: error, fallback: "Couldn’t start the trip refresh.")
        }
    }

    /// Poll refresh status until the chain finishes, then reload once so the legs
    /// show their new packs. Cancelled on disappear — the chain itself keeps
    /// running server-side either way.
    func startPolling() {
        guard pollTask == nil else { return }
        // `Task {}` created from this `@MainActor` type inherits the actor, so the
        // body runs on the main actor and can touch state directly.
        pollTask = Task { [weak self] in
            // Cleared on every exit path so a finished chain can be polled again.
            // `stopPolling()` also clears it; a double-clear is harmless because
            // both write nil.
            defer { self?.pollTask = nil }
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.pollInterval)
                guard !Task.isCancelled, let self else { return }
                do {
                    let status = try await self.repository.tripRefreshStatus(tripId: self.tripId)
                    self.apply(status)
                    await self.pollActiveLegRefreshes()
                    if !status.active {
                        // Reload once so the leg rows pick up their new packs: the
                        // status document carries progress, not assessments.
                        await self.load()
                        return
                    }
                } catch {
                    // A dropped poll is not a failed refresh — the server drives
                    // the chain regardless, and it survives the app being killed.
                    // Stop polling rather than nagging about a readout.
                    Self.logger.info("Trip refresh poll stopped: \(error)")
                    return
                }
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// Legs refreshing right now, including refreshes started outside any trip
    /// run. Best-effort: this is an indicator, so a failure leaves it as-is.
    func pollActiveLegRefreshes() async {
        guard let briefingRepository else { return }
        guard let entries = try? await briefingRepository.activeRefreshes() else { return }
        let memberIds = Set(trip?.summary.legs.map(\.flightId) ?? [])
        refreshingLegIds = Set(entries.map(\.flightId)).intersection(memberIds)
    }

    private func apply(_ status: TripRefreshStatus) {
        guard case .loaded(var trip) = state else { return }
        trip.refresh = status
        state = .loaded(trip)
    }

    // MARK: - Mutations

    func setAutoRefresh(_ enabled: Bool) async {
        await mutate(UpdateTripRequest(autoRefresh: enabled),
                     fallback: "Couldn’t change auto-refresh for this trip.")
    }

    func rename(to name: String) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        await mutate(UpdateTripRequest(name: trimmed),
                     fallback: "Couldn’t rename this trip.")
    }

    private func mutate(_ request: UpdateTripRequest, fallback: String) async {
        do {
            let updated = try await repository.updateTrip(tripId: tripId, request: request)
            state = .loaded(updated)
        } catch {
            actionError = Self.message(for: error, fallback: fallback)
        }
    }

    /// Unlink a leg. The flight and its briefings survive — the confirmation that
    /// precedes this must have said so.
    func removeLeg(flightId: String) async {
        do {
            try await repository.removeTripLeg(tripId: tripId, flightId: flightId)
            await load()
        } catch {
            actionError = Self.message(for: error, fallback: "Couldn’t remove this leg from the trip.")
        }
    }

    /// Delete the trip container. Returns true when the caller should dismiss.
    func deleteTrip() async -> Bool {
        do {
            try await repository.deleteTrip(tripId: tripId)
            return true
        } catch {
            actionError = Self.message(for: error, fallback: "Couldn’t delete this trip.")
            return false
        }
    }

    private static func message(for error: Error, fallback: String) -> String {
        if let apiError = error as? APIError {
            return apiError.errorDescription ?? fallback
        }
        return error.localizedDescription
    }
}
