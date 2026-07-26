//
//  RouteSigmetsTests.swift
//  flyfun-weatherTests
//
//  Area Hazards section (#493) — the iOS port of the web's route SIGMET table.
//  Covers the decode contract against the Python source of truth
//  (`models/observations.py::RouteSigmets`), the computed-field fallbacks, and
//  the row formatters that mirror the web's `sigmetLevel`/`sigmetBand`/
//  `sigmetEnroute` helpers.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Decode contract

@Suite struct RouteSigmetsDecodeTests {

    /// A real payload, copied in shape from a dev pack's `briefing.json`
    /// (`route_sigmets`) — including the server's computed `count` / `hazards` /
    /// `has_severe` and the `(lon, lat)` polygon tuples.
    private let json = """
    {
      "corridor_nm": 50.0,
      "fetch_time": "2026-07-17T05:46:41.551875Z",
      "altitude_low_ft": 0,
      "altitude_high_ft": 10000,
      "time_window_from": "2026-07-17T05:46:39.781164Z",
      "time_window_to": "2026-07-17T23:59:59Z",
      "route_firs": ["EGTT", "LFFF"],
      "count": 2,
      "hazards": ["TS", "TURB"],
      "has_severe": true,
      "sigmets": [
        {
          "fir_id": "LFFF", "fir_name": "LFFF PARIS", "hazard": "TS", "qualifier": "FRQ",
          "base_ft": null, "top_ft": 38000,
          "valid_from": "2026-07-17T04:50:00Z", "valid_to": "2026-07-17T06:00:00Z",
          "direction": "NE", "speed_kt": 30,
          "raw_text": "WSFR31 LFPW 170452\\nLFFF SIGMET T07 VALID 170450/170600 LFPW-\\nLFFF PARIS FIR/UIR FRQ TS OBS TOP FL380 MOV NE 30KT NC=",
          "matched_firs": ["LFFF"],
          "min_distance_nm": 34.04048689854602,
          "enroute_distance_from_nm": 206.2817732309028,
          "enroute_distance_to_nm": 222.1127708985998,
          "coords": [[2.75, 46.75], [1.75, 47.5], [2.75, 48.25], [2.75, 46.75]]
        },
        {
          "fir_id": "EGTT", "fir_name": "EGTT LONDON", "hazard": "TURB", "qualifier": "SEV",
          "base_ft": 5000, "top_ft": 18000,
          "valid_from": null, "valid_to": null,
          "direction": null, "speed_kt": null,
          "raw_text": "EGTT SIGMET 02 VALID 170600/171200 EGRR- SEV TURB FCST SFC/FL180 STNR NC=",
          "matched_firs": ["EGTT"],
          "min_distance_nm": 2.0,
          "enroute_distance_from_nm": null,
          "enroute_distance_to_nm": null,
          "coords": []
        }
      ]
    }
    """

    private func decoded() throws -> RouteSigmets {
        try JSONDecoder.weatherBrief.decode(RouteSigmets.self, from: Data(json.utf8))
    }

    @Test func decodesSummaryFields() throws {
        let block = try decoded()
        #expect(block.corridorNm == 50.0)
        #expect(block.altitudeLowFt == 0)
        #expect(block.altitudeHighFt == 10000)
        #expect(block.routeFirs == ["EGTT", "LFFF"])
        #expect(block.timeWindowTo == "2026-07-17T23:59:59Z")
        #expect(block.matched.count == 2)
    }

    /// The server's `computed_field`s ride along in the JSON and are what the
    /// summary bar reads.
    @Test func decodesServerComputedFields() throws {
        let block = try decoded()
        #expect(block.sigmetCount == 2)
        #expect(block.hazardTypes == ["TS", "TURB"])
        #expect(block.severe == true)
    }

    @Test func decodesPerSigmetFields() throws {
        let block = try decoded()
        let ts = try #require(block.matched.first { $0.hazard == "TS" })
        #expect(ts.firId == "LFFF")
        #expect(ts.firName == "LFFF PARIS")
        #expect(ts.qualifier == "FRQ")
        #expect(ts.baseFt == nil)
        #expect(ts.topFt == 38000)
        #expect(ts.direction == "NE")
        #expect(ts.speedKt == 30)
        #expect(ts.matchedFirs == ["LFFF"])
        #expect(ts.enrouteDistanceFromNm == 206.2817732309028)
        // Multi-line raw bulletin — what the detail sheet renders verbatim.
        #expect(ts.rawText?.components(separatedBy: .newlines).count == 3)
    }

    /// The polygon is retained for a future map/cross-section overlay; the table
    /// doesn't use it, but dropping it at decode would silently lose it.
    /// Python emits `(lon, lat)` tuples, which arrive as 2-element JSON arrays.
    @Test func decodesPolygonCoords() throws {
        let block = try decoded()
        let ts = try #require(block.matched.first { $0.hazard == "TS" })
        #expect(ts.coords?.count == 4)
        #expect(ts.coords?.first == [2.75, 46.75])
    }

    /// A stationary SIGMET has null movement fields, and an area the route only
    /// passes near has no enroute span — neither may throw.
    @Test func stationaryAndOffTrackSigmetDecodes() throws {
        let block = try decoded()
        let turb = try #require(block.matched.first { $0.hazard == "TURB" })
        #expect(turb.direction == nil)
        #expect(turb.speedKt == nil)
        #expect(turb.validFrom == nil)
        #expect(turb.enrouteDistanceFromNm == nil)
        #expect(turb.coords?.isEmpty == true)
    }

    /// A D-1+ pack has no SIGMET block at all; the section must simply not render
    /// rather than decoding into an empty shell. This is the bug #493 fixes from
    /// the other side — before it, the block was dropped even when present.
    @Test func absentBlockDecodesToNilOnSnapshot() throws {
        let snapshotJSON = """
        {
          "route": { "name": "EGTF EGLL", "waypoints": [], "cruise_altitude_ft": 5000,
                     "flight_ceiling_ft": 10000, "flight_duration_hours": 1.0 },
          "target_date": "2026-07-27", "days_out": 2
        }
        """
        let snap = try JSONDecoder.weatherBrief.decode(SnapshotResponse.self, from: Data(snapshotJSON.utf8))
        #expect(snap.routeSigmets == nil)
    }

    /// The common D-0 case: the fetch ran, nothing matched. The block is present
    /// but empty, and the section (plus its scroll-spy anchor) must stay hidden.
    @Test func emptySigmetListReadsAsNothingToShow() throws {
        let empty = """
        { "corridor_nm": 50.0, "fetch_time": "2026-07-17T05:46:41Z",
          "route_firs": ["EGTT"], "sigmets": [], "count": 0, "hazards": [], "has_severe": false }
        """
        let block = try JSONDecoder.weatherBrief.decode(RouteSigmets.self, from: Data(empty.utf8))
        #expect(block.matched.isEmpty)
        #expect(block.sigmetCount == 0)
        #expect(block.hazardTypes.isEmpty)
        #expect(block.severe == false)
    }

    /// The whole snapshot decodes with the SIGMET block attached — the field has
    /// to be wired onto `SnapshotResponse`, not just exist as a type.
    @Test func snapshotCarriesTheSigmetBlock() throws {
        let snapshotJSON = """
        {
          "route": { "name": "EGTF EGLL", "waypoints": [], "cruise_altitude_ft": 5000,
                     "flight_ceiling_ft": 10000, "flight_duration_hours": 1.0 },
          "target_date": "2026-07-17", "days_out": 0,
          "route_sigmets": \(json)
        }
        """
        let snap = try JSONDecoder.weatherBrief.decode(SnapshotResponse.self, from: Data(snapshotJSON.utf8))
        #expect(snap.routeSigmets?.matched.count == 2)
        #expect(snap.routeSigmets?.severe == true)
    }

    /// The shipped fixture must render the section in mock mode — otherwise the
    /// XCUI journey and any manual mock-mode check silently exercise nothing.
    @Test func fixtureSnapshotCarriesSigmets() {
        let block = FixtureBriefingData.snapshot.routeSigmets
        #expect(block?.matched.isEmpty == false)
        #expect(block?.severe == true)
    }
}

// MARK: - Computed-field fallbacks

@Suite struct RouteSigmetsFallbackTests {

    /// `count` / `hazards` / `has_severe` are pydantic `computed_field`s. They are
    /// present today, but the table must not contradict itself if a producer ever
    /// omits them — a "0 SIGMETs" summary above two rows is worse than no summary.
    private let withoutComputed = """
    { "corridor_nm": 50.0, "fetch_time": "2026-07-17T05:46:41Z",
      "sigmets": [
        { "fir_id": "LFFF", "hazard": "TS", "qualifier": "EMBD", "raw_text": "a" },
        { "fir_id": "EGTT", "hazard": "TURB", "qualifier": "SEV", "raw_text": "b" },
        { "fir_id": "EGTT", "hazard": "TS", "qualifier": "ISOL", "raw_text": "c" }
      ] }
    """

    private func decoded() throws -> RouteSigmets {
        try JSONDecoder.weatherBrief.decode(RouteSigmets.self, from: Data(withoutComputed.utf8))
    }

    @Test func countFallsBackToTheRowCount() throws {
        #expect(try decoded().sigmetCount == 3)
    }

    /// Deduplicated and sorted, matching the server's `hazards` union.
    @Test func hazardsFallBackToTheSortedUnion() throws {
        #expect(try decoded().hazardTypes == ["TS", "TURB"])
    }

    @Test func severeFallsBackToScanningQualifiers() throws {
        #expect(try decoded().severe == true)
    }

    @Test func noSevereQualifierReadsAsNotSevere() throws {
        let json = """
        { "corridor_nm": 50.0, "sigmets": [
            { "fir_id": "LFFF", "hazard": "TS", "qualifier": "ISOL", "raw_text": "a" } ] }
        """
        let block = try JSONDecoder.weatherBrief.decode(RouteSigmets.self, from: Data(json.utf8))
        #expect(block.severe == false)
    }
}

// MARK: - Row formatters (web parity)

@Suite struct SigmetRowFormatterTests {

    private func sigmet(_ fields: String) throws -> SigmetAlongRoute {
        let json = "{ \"fir_id\": \"LFFF\", \(fields) }"
        return try JSONDecoder.weatherBrief.decode(SigmetAlongRoute.self, from: Data(json.utf8))
    }

    /// Mirrors the web's `sigmetLevel`, including the floor division — the same
    /// SIGMET must read as the same flight level on web, PDF and iOS.
    @Test func levelMatchesTheWebHelper() {
        #expect(SigmetAlongRoute.level(nil) == "?")
        #expect(SigmetAlongRoute.level(0) == "SFC")
        #expect(SigmetAlongRoute.level(-100) == "SFC")
        #expect(SigmetAlongRoute.level(38000) == "FL380")
        #expect(SigmetAlongRoute.level(9500) == "FL095")
        // Floors rather than rounds: FL097, not FL098.
        #expect(SigmetAlongRoute.level(9799) == "FL097")
    }

    @Test func levelBandRendersBothBounds() throws {
        #expect(try sigmet("\"base_ft\": 5000, \"top_ft\": 18000").levelBand == "FL050–FL180")
        #expect(try sigmet("\"base_ft\": 0, \"top_ft\": 18000").levelBand == "SFC–FL180")
    }

    /// An omitted base is genuinely unknown, not surface — so it renders "?" like
    /// the web, rather than overstating what the bulletin said.
    @Test func unknownBaseRendersAsQuestionMark() throws {
        #expect(try sigmet("\"top_ft\": 38000").levelBand == "?–FL380")
    }

    @Test func noVerticalBandRendersEmDash() throws {
        #expect(try sigmet("\"hazard\": \"TS\"").levelBand == "—")
    }

    @Test func enrouteSpanPreferredOverOffTrackDistance() throws {
        let s = try sigmet("\"enroute_distance_from_nm\": 206.3, \"enroute_distance_to_nm\": 222.1, \"min_distance_nm\": 34.0")
        #expect(s.enrouteLabel == "206–222nm")
    }

    @Test func offTrackDistanceUsedWhenThereIsNoSpan() throws {
        #expect(try sigmet("\"min_distance_nm\": 34.04").enrouteLabel == "34nm off")
    }

    @Test func noIntersectionDataRendersEmDash() throws {
        #expect(try sigmet("\"hazard\": \"TS\"").enrouteLabel == "—")
    }

    @Test func movementNeedsBothDirectionAndSpeed() throws {
        #expect(try sigmet("\"direction\": \"NE\", \"speed_kt\": 30").movementLabel == "NE 30kt")
        // Stationary: the view renders "STNR" / "Stationary" for nil.
        #expect(try sigmet("\"hazard\": \"TS\"").movementLabel == nil)
        #expect(try sigmet("\"direction\": \"NE\"").movementLabel == nil)
        #expect(try sigmet("\"speed_kt\": 30").movementLabel == nil)
    }

    @Test func headlineJoinsQualifierAndHazard() throws {
        #expect(try sigmet("\"qualifier\": \"SEV\", \"hazard\": \"TURB\"").headline == "SEV TURB")
        #expect(try sigmet("\"hazard\": \"TS\"").headline == "TS")
        #expect(try sigmet("\"qualifier\": \"EMBD\"").headline == "EMBD")
        // Neither field present — still labelled, never blank.
        #expect(try sigmet("\"raw_text\": \"x\"").headline == "SIGMET")
    }

    @Test func severeIsQualifierSevOnly() throws {
        #expect(try sigmet("\"qualifier\": \"SEV\"").isSevere == true)
        #expect(try sigmet("\"qualifier\": \"sev\"").isSevere == true)
        #expect(try sigmet("\"qualifier\": \"EMBD\"").isSevere == false)
        #expect(try sigmet("\"hazard\": \"TS\"").isSevere == false)
    }

    /// Validity is aviation DDHHMMZ and always UTC, regardless of device locale.
    @Test func validityRendersDayHourMinuteZulu() throws {
        let s = try sigmet("\"valid_from\": \"2026-07-17T04:50:00Z\", \"valid_to\": \"2026-07-17T06:00:00Z\"")
        #expect(s.validityLabel == "170450Z → 170600Z")
    }

    @Test func validityConvertsAnOffsetToUtc() throws {
        let s = try sigmet("\"valid_from\": \"2026-07-17T06:50:00+02:00\"")
        #expect(s.validityLabel == "170450Z → ?")
    }

    /// The server writes `datetime.isoformat()` with microseconds — the plain
    /// ISO8601 parser rejects those, so the fractional-first fallback is required.
    @Test func validityParsesFractionalSeconds() throws {
        let s = try sigmet("\"valid_to\": \"2026-07-17T06:00:00.551875Z\"")
        #expect(s.validityLabel == "? → 170600Z")
    }

    /// Both bounds missing — the detail sheet drops the row rather than printing
    /// "? → ?".
    @Test func noValidityYieldsNil() throws {
        #expect(try sigmet("\"hazard\": \"TS\"").validityLabel == nil)
        #expect(SigmetAlongRoute.dayZulu("not a date") == nil)
    }

    /// Identity is FIR + raw bulletin: a FIR routinely has several concurrent
    /// SIGMETs, so keying on `fir_id` alone would collapse them into one row.
    @Test func idDistinguishesConcurrentSigmetsInOneFir() throws {
        let a = try sigmet("\"hazard\": \"TS\", \"raw_text\": \"first\"")
        let b = try sigmet("\"hazard\": \"TURB\", \"raw_text\": \"second\"")
        #expect(a.id != b.id)
    }
}
