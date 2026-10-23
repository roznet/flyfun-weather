#if DEBUG
import Foundation

/// Canned trip fixtures for the XCUI journeys (`FLYFUN_MOCK=1`), #607.
///
/// Two legs forming one trip, deliberately separate from `fixture-1` /
/// `fixture-2` so the existing journeys keep asserting against untouched rows.
/// The shape exercises the cases the trip UI must get right:
///
/// * a **flown** outbound and a **remaining** return, so the shrinking scope is
///   visible and the list shows only the remaining leg under the trip header;
/// * the return is the **binding leg** and it is AMBER, so the header chip names
///   a leg and its grade — never a colour for the trip;
/// * a two-night ground gap, well over `SORTIE_GAP_HOURS`, so the timeline draws
///   a labelled gap rather than a fuel-stop connector.
///
/// Departures are far-future so both legs land in "Future" whenever the suite
/// runs, while the summary marks the outbound `flown`. That mismatch is
/// deliberate: leg *state* is the server's call (clock time, refined by a
/// debrief) and the client must render what it is told rather than re-deriving
/// it from `departure_time` — a journey that passes here would fail against a
/// client that second-guessed the server.
@MainActor
enum FixtureTripData {
    static let tripId = "fixture-trip-1"
    static let outboundId = "fixture-trip-out"
    static let returnId = "fixture-trip-back"

    /// The two member flights, merged into the fixture flight list so the list
    /// can group them exactly as it groups real ones.
    static let legs: [FlightResponse] = FixtureFiles.decode([FlightResponse].self, "trip-legs")

    /// The trip, with the deterministic summary the server would compute. The
    /// headline is written the way `_build_headline` writes it — one sentence
    /// naming the deciding leg, never a verdict for the trip.
    static let trip: TripResponse = FixtureFiles.decode(TripResponse.self, "trip")
}
#endif
