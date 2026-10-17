import XCTest
import UIKit

final class ObservedUITests: FlyfunUITestCase {

    /// Journey (#661) — the flight-day Observed tab. It is appended to the
    /// right of the four briefing tabs only when the briefing carries
    /// observations; opens on its glance card (departure / destination, the
    /// chips) and its "Show radar & cells on map" lands on the Map with the
    /// observed controls and the cell overlay's own badge line (the mock has no
    /// cell server, so it must say "unavailable", never draw nothing silently).
    /// #690: the server's nutshell and route ribbon lead the Observed tab; a
    /// nutshell line opens the map on what it summarises, and a storm on the
    /// ribbon opens its detail — observed facts, then the estimate set apart
    /// under "Estimate at current motion" — whose "Show on map" lands on the map.
    @MainActor
    func testObservedNutshellRibbonAndTapToMap() throws {
        let focus = #"{"kind": "storm", "id": "core35-zz", "bbox": [5.3, 43.4, 5.7, 43.8], "layers": ["route", "radar", "cells", "lightning"]}"#
        let stationFocus = #"{"kind": "station", "id": "LFMD", "bbox": [6.8, 43.4, 7.1, 43.7], "layers": ["route", "metar"]}"#
        let live = """
        {"flight_id": "fixture-1", "pack_timestamp": "2099-06-30T06:00:00+00:00",
         "live_updated_at": "2099-06-30T08:10:00Z",
         "observed_conditions": {"radii_nm": [5, 10, 20],
           "summary_entries": [{"kind": "reflectivity", "text": "Heavy echo near ZZAA (observed 08:05Z)"}],
           "reflectivity": {"source": "opera_dbzh", "quantity": "DBZH", "units": "dBZ",
             "valid_time": "2099-06-30T08:05:00Z", "age_minutes": 5, "window_minutes": 10,
             "stations": []}},
         "glance": {"as_of": "2099-06-30T08:10:00Z", "headline": "Observed 08:10Z · as briefed",
           "comparison": "as_briefed", "lines": [
             {"phase": "departure", "icao": "LFMD", "text": "LFMD VFR · no cell within 20 NM · no lightning ≤20 NM"},
             {"phase": "enroute", "text": "1 cell 6 NM right of track; nearest 6 NM right at 40 NM ~08:40Z (48 dBZ), closing 7 kt · no SIGMET on route",
              "alert": true, "focus": \(focus)},
             {"phase": "arrival", "icao": "LFML", "text": "LFML VFR · no TAF for ETA · no cell within 20 NM now · lightning unavailable"}]},
         "ribbon": {"route_nm": 80.0, "flown_nm": 0.0, "segment_nm": 10.0, "radar_radius_nm": 10.0,
           "waypoints": [{"icao": "LFMD", "along_nm": 0.0}, {"icao": "LFML", "along_nm": 80.0}],
           "segments": [{"index": 0, "from_nm": 0.0, "to_nm": 40.0, "radar_status": "measured"},
                        {"index": 1, "from_nm": 40.0, "to_nm": 80.0, "radar_status": "measured", "radar_max_dbz": 48.0}],
           "stations": [{"icao": "LFMD", "role": "departure", "along_nm": 0.0, "metar_category": "VFR",
             "metar_time": "2099-06-30T08:00:00Z", "focus": \(stationFocus)}], "sigmets": []},
         "route_observations": {"airports": [{"icao": "LFMD", "name": "Cannes Mandelieu", "has_metar": true,
           "metar_raw": "LFMD 300800Z 09005KT CAVOK 22/14 Q1015", "metar_flight_category": "VFR"}]},
         "storms": {"status": "available", "corridor_nm": 30.0, "route_nm": 80.0, "storms": [
           {"id": "core35-zz", "lat": 43.6, "lon": 5.5, "peak_dbz": 48.0, "intensity": "heavy", "flashes": 0,
            "trend": "developing", "along_nm": 40.0, "offtrack_nm": 6.0, "cross_nm": 6.0, "side": "right",
            "ahead": true, "relative_motion": "closing", "closing_kt": 7.0, "motion_status": "available",
            "estimate": {"cpa_nm": 2.0, "cpa_time": "2099-06-30T08:45:00Z", "horizon_min": 40.0},
            "focus": \(focus)}]}}
        """
        let app = launchMockApp(environment: ["FLYFUN_MOCK_LIVE_JSON": live])
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Observed")

        // #697: the top is the highlight slot; with no highlight served the
        // nutshell headline stands in it. The other lines are under Details.
        XCTAssertTrue(app.descendants(matching: .any)["observedHighlight"].waitForExistence(timeout: Self.uiTimeout),
                      "the Observed tab should open on the highlight slot")
        XCTAssertTrue(app.staticTexts["Observed 08:10Z · as briefed"].firstMatch.exists,
                      "with no highlight, the headline should stand in its slot")
        XCTAssertFalse(app.descendants(matching: .any)["observedHighlightCaption"].firstMatch.exists,
                       "the experimental caption belongs to a highlight, not the headline")
        XCTAssertTrue(app.descendants(matching: .any)["observedRibbon"].firstMatch.exists, "the route ribbon should render")
        attachScreenshot(app, "Observed-Nutshell")

        // #747: a cell on the ribbon opens the inspector card under it (no
        // sheet); the rest of the old detail, estimate included, is under More.
        let storm = app.descendants(matching: .any)["ribbonStorm-core35-zz"].firstMatch
        XCTAssertTrue(storm.waitForExistence(timeout: Self.uiTimeout), "the storm should be on the ribbon")
        storm.tap()
        let detail = app.descendants(matching: .any)["stormDetail"].firstMatch
        XCTAssertTrue(detail.waitForExistence(timeout: Self.uiTimeout), "tapping a cell should open its card")
        XCTAssertTrue(app.staticTexts["Heavy cell"].firstMatch.exists, "the card should name the cell")
        XCTAssertFalse(app.buttons["map.observedMenu"].firstMatch.exists, "a mark must not navigate on its own")
        let more = app.descendants(matching: .any)["stormMore"].firstMatch
        let moreButton = more.exists ? more : app.buttons["More"].firstMatch
        tapAfterScrolling(app, moreButton)
        // The section header renders upper-cased: match either case in one
        // wait (waiting on the mixed-case label alone sat out its full timeout).
        let estimate = app.staticTexts.matching(
            NSPredicate(format: "label ==[c] %@", "Estimate at current motion")).firstMatch
        XCTAssertTrue(estimate.waitForExistence(timeout: Self.uiTimeout),
                      "the estimate should sit under its own label, under More")
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "A projection, not an observation")).firstMatch.exists,
                      "the estimate keeps its footnote")
        attachScreenshot(app, "Observed-StormCard")
        let showOnMap = app.buttons["stormShowOnMap"].firstMatch
        tapAfterScrolling(app, showOnMap)
        XCTAssertTrue(app.buttons["map.observedMenu"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "Show on map should land on the Map tab with the observed controls")
        attachScreenshot(app, "Observed-StormOnMap")

        // An airport: the card quotes its raw METAR, and Show on map opens the map.
        switchToBriefingTab(app, "Observed")
        let airport = app.descendants(matching: .any)["ribbonStation-LFMD-departure"].firstMatch
        XCTAssertTrue(airport.waitForExistence(timeout: Self.uiTimeout), "the departure should be on the ribbon")
        tapAfterScrolling(app, airport, up: false)
        let card = app.descendants(matching: .any)["ribbonInspector"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "tapping an airport should open its card")
        let raw = app.descendants(matching: .any)["ribbonInspectorRaw-METAR"].firstMatch
        XCTAssertTrue(raw.exists, "the airport card should quote the raw METAR")
        XCTAssertTrue(raw.label.contains("LFMD 300800Z"), "got \(raw.label)")
        XCTAssertTrue(app.buttons["ribbonInspectorIcao"].firstMatch.exists, "the ICAO should open the map too")
        attachScreenshot(app, "Observed-AirportCard")
        let airportMap = app.buttons["ribbonInspectorShowOnMap"].firstMatch
        tapAfterScrolling(app, airportMap)
        XCTAssertTrue(app.buttons["map.observedMenu"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the airport card's Show on map should open the map")

        // A nutshell line opens the map too.
        switchToBriefingTab(app, "Observed")
        let line = app.descendants(matching: .any)["observedNutshellLine-enroute"].firstMatch
        XCTAssertTrue(line.waitForExistence(timeout: Self.uiTimeout), "the en-route line should render")
        line.tap()
        XCTAssertTrue(app.buttons["map.observedMenu"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "a nutshell line should open the map")
    }

    @MainActor
    func testObservedTabGlanceAndShowOnMap() throws {
        let live = """
        {"flight_id": "fixture-1", "pack_timestamp": "2099-06-30T06:00:00+00:00",
         "live_updated_at": "2099-06-30T08:10:00Z",
         "observed_conditions": {"radii_nm": [5, 10, 20],
           "summary_entries": [{"kind": "reflectivity", "text": "Moderate rain near ZZAA (observed 08:05Z)"}],
           "reflectivity": {"source": "opera_dbzh", "quantity": "DBZH", "units": "dBZ",
             "valid_time": "2099-06-30T08:05:00Z", "age_minutes": 5, "window_minutes": 10,
             "stations": []}}}
        """
        let app = launchMockApp(environment: ["FLYFUN_MOCK_LIVE_JSON": live])
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Observed")

        XCTAssertTrue(app.descendants(matching: .any)["observedGlance"].waitForExistence(timeout: Self.uiTimeout),
                      "the Observed tab should open on its glance card")
        XCTAssertTrue(app.descendants(matching: .any)["observedNowSection"].firstMatch.exists,
                      "the radar & lightning summary should render")
        attachScreenshot(app, "Observed-Glance")

        let showOnMap = app.buttons["observedShowOnMap"].firstMatch
        XCTAssertTrue(showOnMap.waitForExistence(timeout: Self.uiTimeout), "Show on map should be offered")
        showOnMap.tap()
        XCTAssertTrue(app.buttons["map.observedMenu"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "Show on map should land on the Map tab's observed controls")
        let badge = app.descendants(matching: .any)["map.observedBadge"].firstMatch
        XCTAssertTrue(badge.waitForExistence(timeout: Self.uiTimeout), "the observed badge should render")
        let named = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", "Cell analysis unavailable"), object: badge)
        XCTAssertEqual(XCTWaiter.wait(for: [named], timeout: Self.uiTimeout), .completed,
                       "the cell overlay should say it is unavailable, got: \(badge.label)")
        attachScreenshot(app, "Observed-ShowOnMap")

        // The on-map line is a one-row summary; legends, sources and caveats
        // are a tap away so they don't cover the map.
        badge.tap()
        XCTAssertTrue(app.navigationBars["Observed"].waitForExistence(timeout: Self.uiTimeout),
                      "tapping the observed summary should open its details sheet")
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Cell analysis unavailable"))
                        .firstMatch.exists, "the sheet should carry the full layer lines")
        attachScreenshot(app, "Observed-MapDetails")
        app.navigationBars["Observed"].buttons["Done"].tap()
    }

    /// #697: the 08:30 tick with a fixed, grounded highlight (exported as
    /// `2026-10-02_lell_lemi_0830_highlight.json`). Alert-tier lines and
    /// change rows sit above the highlight, which carries its experimental
    /// caption with the written time and a 👍/👎; nothing alert is folded.
    @MainActor
    func testObservedHighlight() throws {
        let tick = try liveScenarioTick("2026-10-02_lell_lemi", "0830_highlight")
        let glance = try XCTUnwrap(tick.body["glance"] as? [String: Any])
        let highlight = try XCTUnwrap(glance["highlight"] as? [String: Any])
        let text = try XCTUnwrap(highlight["text"] as? String)
        let app = launchMockApp(environment: ["FLYFUN_MOCK_LIVE_JSON": tick.json])
        defer { app.terminate() }
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Observed")

        let card = app.descendants(matching: .any)["observedHighlight"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "the highlight card should render")
        let body = app.descendants(matching: .any)["observedHighlightText"].firstMatch
        XCTAssertTrue(body.exists && body.label == text, "the highlight is the server's text, word for word")
        let caption = app.descendants(matching: .any)["observedHighlightCaption"].firstMatch
        XCTAssertTrue(caption.label.contains("Experimental, still being calibrated"), "got \(caption.label)")
        XCTAssertTrue(caption.label.contains("written 08:20Z"), "the caption should carry the written time")
        XCTAssertTrue(app.buttons["highlightThumb-up"].firstMatch.exists, "the highlight should offer 👍")
        XCTAssertTrue(app.buttons["highlightThumb-down"].firstMatch.exists, "the highlight should offer 👎")

        // Alerts just under the highlight, never folded: every alert-tier line
        // and change row is on screen without opening Details.
        let alerts = app.descendants(matching: .any)["observedAlerts"].firstMatch
        XCTAssertTrue(alerts.exists, "the alert block should render under the highlight")
        XCTAssertGreaterThan(alerts.frame.minY, card.frame.minY, "the highlight comes first, alerts under it")
        for line in (glance["lines"] as? [[String: Any]]) ?? [] where line["alert"] as? Bool == true {
            let phase = line["phase"] as? String ?? ""
            XCTAssertTrue(app.descendants(matching: .any)["observedNutshellLine-\(phase)"].firstMatch.exists,
                          "the alert \(phase) line should not be folded")
        }
        let changes = (tick.body["changes"] as? [String: Any])?["changes"] as? [[String: Any]] ?? []
        for change in changes where change["tier"] as? String == "alert" {
            let key = change["key"] as? String ?? ""
            XCTAssertTrue(app.descendants(matching: .any)["liveChangeRow-\(key)"].firstMatch.exists,
                          "the alert change \(key) should not be folded")
        }
        // The headline is under Details when the highlight holds the slot.
        attachScreenshot(app, "Observed-Highlight")
        openObservedDetails(app)
        XCTAssertTrue(app.descendants(matching: .any)["observedNutshellHeadline"].waitForExistence(timeout: Self.uiTimeout),
                      "the headline should be under Details")
        attachScreenshot(app, "Observed-Highlight-Details")

        // A 👎 opens the comment sheet; sending it collapses the thumbs.
        let down = app.buttons["highlightThumb-down"].firstMatch
        for _ in 0..<4 where !down.isHittable { app.swipeDown() }
        down.tap()
        let send = app.buttons["Send"].firstMatch
        XCTAssertTrue(send.waitForExistence(timeout: Self.uiTimeout), "a thumb should open the comment sheet")
        send.tap()
        XCTAssertTrue(app.staticTexts["Thanks for the feedback!"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "after a rating the highlight shows a thank-you")
        XCTAssertFalse(app.buttons["highlightThumb-down"].firstMatch.exists)
    }

    /// LELL→LEMI 09:00 (see `LiveScenarioUITests`): two merged NEW changes at
    /// once — LECB 3 + LECM 3, and the LECB 2 → 4 reissue shown as a
    /// replacement — three badges, two trails. Here, not with its siblings,
    /// to balance the classes.
    @MainActor
    func testLiveScenarioLellLemi0900() throws {
        try runLellLemiTick("0900")
    }
}
