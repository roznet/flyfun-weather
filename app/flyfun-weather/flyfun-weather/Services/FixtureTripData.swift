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

    private static func decode<T: Decodable>(_ type: T.Type, _ json: String) -> T {
        // try! on purpose, matching `FixtureBriefingData`: a fixture that stops
        // decoding must fail loudly here rather than silently emptying the trip
        // and making a journey fail with a misleading "no trips" message.
        try! JSONDecoder.weatherBrief.decode(T.self, from: Data(json.utf8))
    }

    /// The two member flights, merged into the fixture flight list so the list
    /// can group them exactly as it groups real ones.
    static let legs: [FlightResponse] = decode([FlightResponse].self, """
    [
      {
        "id": "fixture-trip-out", "user_id": "uitest", "route_name": "EGTF LSGS",
        "waypoints": ["EGTF", "LSGS"], "departure_time": "2099-08-07T08:00:00Z",
        "target_date": "2099-08-07", "target_time_utc": 800,
        "cruise_altitude_ft": 9000, "flight_ceiling_ft": 14000, "flight_duration_hours": 2.1,
        "private": false, "auto_refresh": false, "created_at": "2099-07-20T09:00:00Z",
        "trip": {
          "id": "fixture-trip-1", "name": "Alps weekend",
          "position": 1, "total": 2, "auto_refresh": true
        },
        "latest_briefing": {
          "fetch_timestamp": "2099-08-06T06:00:00Z",
          "assessment": "green", "unseen": false
        }
      },
      {
        "id": "fixture-trip-back", "user_id": "uitest", "route_name": "LSGS EGTF",
        "waypoints": ["LSGS", "EGTF"], "departure_time": "2099-08-09T14:00:00Z",
        "target_date": "2099-08-09", "target_time_utc": 1400,
        "cruise_altitude_ft": 9000, "flight_ceiling_ft": 14000, "flight_duration_hours": 2.3,
        "private": false, "auto_refresh": false, "created_at": "2099-07-20T09:00:00Z",
        "trip": {
          "id": "fixture-trip-1", "name": "Alps weekend",
          "position": 2, "total": 2, "auto_refresh": true
        },
        "latest_briefing": {
          "fetch_timestamp": "2099-08-06T06:00:00Z",
          "assessment": "amber", "unseen": false
        }
      }
    ]
    """)

    /// The trip, with the deterministic summary the server would compute. The
    /// headline is written the way `_build_headline` writes it — one sentence
    /// naming the deciding leg, never a verdict for the trip.
    static let trip: TripResponse = decode(TripResponse.self, """
    {
      "id": "fixture-trip-1", "user_id": "uitest", "name": "Alps weekend",
      "notes": null, "auto_refresh": true, "auto_refresh_hour": 7,
      "notify_override": "default", "created_at": "2099-07-20T09:00:00Z",
      "flight_ids": ["fixture-trip-out", "fixture-trip-back"],
      "ai_summary": "The Friday run out to Sion looks settled. Sunday's return is the one to keep an eye on — an amber at five days out, with icing and cloud base both flagged, and it firms up midweek.",
      "ai_summary_at": "2099-08-06T06:05:00Z",
      "ai_summary_stale": false,
      "refresh": null,
      "summary": {
        "trip_id": "fixture-trip-1", "name": "Alps weekend",
        "total_legs": 2, "remaining_legs": 1,
        "chain_status": "AMBER",
        "binding_leg_id": "fixture-trip-back",
        "binding_basis": "assessment",
        "beyond_horizon_leg_ids": [], "pending_coverage_leg_ids": [],
        "needs_briefing_leg_ids": [], "unavailable_leg_ids": [],
        "decision_ripeness_days": 5, "decidable_from": "2099-08-08",
        "is_round_trip": true,
        "chain_label": "EGTF → LSGS → EGTF",
        "continuity_warnings": [],
        "headline": "Sunday's LSGS → EGTF decides this trip. It is AMBER at D-5. Not decidable on high-resolution guidance until Sat 08 Aug.",
        "legs": [
          {
            "flight_id": "fixture-trip-out", "label": "EGTF → LSGS",
            "origin": "EGTF", "destination": "LSGS",
            "departure_time": "2099-08-07T08:00:00Z", "duration_hours": 2.1,
            "state": "flown", "grade_kind": "assessment",
            "assessment": "GREEN", "assessment_reason": "Settled high pressure over the Alps.",
            "days_out": 1, "fetch_timestamp": "2099-08-06T06:00:00Z",
            "advisory_summary": { "red": 0, "amber": 0, "top": [] },
            "gap_hours_before": null, "same_sortie_as_previous": false
          },
          {
            "flight_id": "fixture-trip-back", "label": "LSGS → EGTF",
            "origin": "LSGS", "destination": "EGTF",
            "departure_time": "2099-08-09T14:00:00Z", "duration_hours": 2.3,
            "state": "remaining", "grade_kind": "assessment",
            "assessment": "AMBER", "assessment_reason": "Marginal cruise icing and a lowering cloud base on the return.",
            "days_out": 5, "fetch_timestamp": "2099-08-06T06:00:00Z",
            "advisory_summary": {
              "red": 0, "amber": 2,
              "top": [
                { "status": "AMBER", "name": "Icing" },
                { "status": "AMBER", "name": "Cloud base" }
              ]
            },
            "gap_hours_before": 51.9, "same_sortie_as_previous": false
          }
        ]
      }
    }
    """)
}
#endif
