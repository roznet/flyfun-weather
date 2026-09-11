import Foundation

/// Data access for flight trips (#602, iOS port #607).
///
/// **Deliberately its own protocol rather than nine more methods on
/// `BriefingRepository`.** That one is already ~40 methods and every addition
/// has to be implemented by all three conformers (Online, Caching, Fixture) —
/// the architecture doc calls this out explicitly. Trips are also online-only
/// apart from one cached document, so they share almost none of that protocol's
/// cache machinery.
///
/// Everything here is a thin pass-through: the interesting logic — the binding
/// leg, the chain status, the serial refresh driver — lives on the server, and
/// `designs/flight-trips.md` requires it stay there so web, iOS, MCP, the
/// notification coalescer and the AI guardrail can never disagree.
protocol TripRepository: Sendable {
    /// Every trip the viewer owns, each with its derived summary.
    ///
    /// Note for callers: the server builds a full summary per trip (a packs
    /// query, a debriefs query, and an uncached profile lookup per member leg),
    /// so this is more expensive than its payload size suggests. The flight list
    /// reloads far more often than a web page does — see
    /// `TripListCache.isCalendarStale(now:)` and the cadence note in
    /// `designs/plans/ios-trips.md` before wiring it into every reload.
    func trips() async throws -> [TripResponse]

    /// One trip. Carries `aiSummary` + `aiSummaryStale`, so the normal path
    /// renders the stored paragraph from here and never posts.
    func trip(id: String) async throws -> TripResponse

    /// Group flights into a new trip. A 1-leg trip is valid — "book the outbound
    /// now, add the return when it comes into range" is a normal flow.
    func createTrip(flightIds: [String], name: String?) async throws -> TripResponse

    func addTripLegs(tripId: String, flightIds: [String]) async throws -> TripResponse

    /// Unlink a leg. **Never a delete** — the flight and its (expensive) packs
    /// survive, and any confirmation shown to the user must say so.
    func removeTripLeg(tripId: String, flightId: String) async throws

    func updateTrip(tripId: String, request: UpdateTripRequest) async throws -> TripResponse

    /// Delete the container only. Its legs survive, unlinked.
    func deleteTrip(tripId: String) async throws

    /// Start a trip refresh. The server admits exactly **one leg at a time** and
    /// keeps going if the app is backgrounded or killed, so the client's job is
    /// only to poll ``tripRefreshStatus(tripId:)`` for progress.
    ///
    /// Never fan out over legs instead: `MAX_PER_USER` is 2 and the refresh
    /// executor has two workers for the whole process, so a 3-leg client-side
    /// fan-out is refused outright and would block other users on the way.
    func refreshTrip(tripId: String) async throws -> TripRefreshStatus

    func tripRefreshStatus(tripId: String) async throws -> TripRefreshStatus

    /// Generate the AI paragraph **only when `aiSummaryStale` is set**.
    ///
    /// This is a POST because it can spend money, but it is idempotent on
    /// unchanged inputs: the server keys the paragraph on each leg's
    /// `(fetch_timestamp, debrief_decision, derived state)` and a key hit returns
    /// the stored text with no model call. It regenerates when a leg refreshes,
    /// a debrief changes, or a departure passes and flips a leg's state — not
    /// when a view appears, and not as `days_out` ticks down.
    func tripAiSummary(tripId: String) async throws -> TripAiSummaryResponse
}

// MARK: - Offline cache

/// The trip list, cached so the flight list can still group offline.
///
/// Only the *list* is cached, and only the per-leg facts in it are safe to show
/// unqualified. The headline decays on a second clock that no push reports — see
/// ``isCalendarStale(now:)``.
struct TripListCache: Codable, Sendable {
    var trips: [TripResponse]
    /// When this document was fetched.
    var fetchedAt: Date

    /// Whether a cached summary's **headline** should still be shown.
    ///
    /// Re-downloading whenever a leg refreshes is the right trigger, and the app
    /// has that signal. But two things invalidate the headline with *no* refresh,
    /// no push and no new data at all:
    ///
    /// * **A departure passes.** A leg flips `remaining` → `flown` from the clock
    ///   alone, and only remaining legs can bind — so the binding leg changes
    ///   identity with nothing having happened.
    /// * **The UTC date rolls over.** Every `D-N` in the headline, plus
    ///   `decisionRipenessDays` and `decidableFrom`, moves with the calendar.
    ///
    /// So a headline cached on Monday saying "Sunday's LSGS → EGTF decides this
    /// trip. It is AMBER at D-5" is simply false by Wednesday. This returns true
    /// in both cases and the UI then suppresses the headline while still showing
    /// the legs.
    ///
    /// This is a *don't trust this* check on cached data, never a client-side
    /// re-derivation of the binding leg — which the design forbids.
    func isCalendarStale(now: Date = Date()) -> Bool {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        if !utc.isDate(fetchedAt, inSameDayAs: now) { return true }
        return trips.contains { trip in
            trip.summary.legs.contains { leg in
                guard let departure = leg.departureDate else { return false }
                return departure > fetchedAt && departure <= now
            }
        }
    }
}
