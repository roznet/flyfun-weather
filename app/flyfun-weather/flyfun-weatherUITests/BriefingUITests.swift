import XCTest
import UIKit

final class BriefingUITests: FlyfunUITestCase {

    /// Journey 3 (#318) — briefing → advisory drill-down. Open fixture-1, tap the
    /// RED convective advisory's "Why it's RED", and confirm the detail sheet
    /// shows the per-model split (GFS + ECMWF rows). iPhone + iPad.
    @MainActor
    func testBriefingAdvisoryDrillDown() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        // Advisory is the default tab. The RED convective card offers the
        // "Why it's RED" drill-down (AMBER/RED only).
        let why = app.buttons["advisoryWhy-convective"].firstMatch
        XCTAssertTrue(why.waitForExistence(timeout: Self.uiTimeout),
                      "RED convective advisory should offer a 'Why it's RED' drill-down")
        // Coordinate tap: the button renders on-screen but XCUI reports it "not
        // hittable" (a thin control inside the scroll view); hitting its centre
        // point directly is reliable.
        why.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // The detail sheet shows the per-model reconciliation.
        XCTAssertTrue(app.descendants(matching: .any)["advisoryDetail"].waitForExistence(timeout: Self.uiTimeout),
                      "advisory detail sheet should open")
        XCTAssertTrue(app.descendants(matching: .any)["advisoryDetailModel-gfs"].waitForExistence(timeout: Self.uiTimeout),
                      "per-model GFS row (the RED driver) should be shown")
        XCTAssertTrue(app.descendants(matching: .any)["advisoryDetailModel-ecmwf"].exists,
                      "per-model ECMWF row should be shown")
    }

    /// Journey 4 (#318) — cross-section renders. Open fixture-1, switch to the
    /// Cross-Section tab, and confirm the canvas (not the loading/error
    /// placeholder) renders from the fixture's route analyses. iPhone + iPad.
    @MainActor
    func testCrossSectionRenders() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Cross-Section")

        // The canvas element only exists once the cross-section actually draws
        // (its absence is the "Loading…"/"No Data" placeholder), so finding it
        // confirms both the tab switch and a successful render from the fixture.
        XCTAssertTrue(app.descendants(matching: .any)["crossSectionCanvas"].waitForExistence(timeout: Self.uiTimeout),
                      "cross-section canvas should render for the fixture (ECMWF has data)")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "CrossSection-Mock"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey (#654) — the route map's observed imagery controls. The pack has
    /// no observed conditions, so a live layer supplies a radar sample (the
    /// same patch path a D-0 briefing takes); the map then offers the Observed
    /// menu. The fixture repository serves no frame listings, so the badge must
    /// say the imagery is unavailable rather than leaving a blank map that reads
    /// as "no echoes"; the same for lightning (#665), on by default. iPhone + iPad.
    @MainActor
    func testRouteMapObservedImageryControls() throws {
        let live = """
        {"flight_id": "fixture-1", "pack_timestamp": "2099-06-30T06:00:00+00:00",
         "live_updated_at": "2099-06-30T08:10:00Z",
         "observed_conditions": {"radii_nm": [5, 10, 20],
           "reflectivity": {"source": "opera_dbzh", "quantity": "DBZH", "units": "dBZ",
             "valid_time": "2099-06-30T08:05:00Z", "age_minutes": 5, "window_minutes": 10,
             "stations": []}}}
        """
        let app = launchMockApp(environment: ["FLYFUN_MOCK_LIVE_JSON": live])
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Map")

        let menu = app.buttons["map.observedMenu"].firstMatch
        XCTAssertTrue(menu.waitForExistence(timeout: Self.uiTimeout),
                      "the route map should offer the Observed menu for a briefing with observed data")
        let badge = app.descendants(matching: .any)["map.observedBadge"].firstMatch
        XCTAssertTrue(badge.waitForExistence(timeout: Self.uiTimeout),
                      "a failed frame listing should be named on the map, not left blank")
        XCTAssertTrue(badge.label.contains("imagery unavailable"), "badge was: \(badge.label)")
        // #665: lightning is on by default; with no server it says so rather
        // than drawing nothing silently.
        let lightningNamed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label CONTAINS %@", "Lightning unavailable"), object: badge)
        XCTAssertEqual(XCTWaiter.wait(for: [lightningNamed], timeout: Self.uiTimeout), .completed,
                       "the lightning layer should say it is unavailable, got: \(badge.label)")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "RouteMap-Observed-Mock"
        shot.lifetime = .keepAlways
        add(shot)

        menu.tap()
        XCTAssertTrue(app.buttons["Satellite infrared"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the menu should offer the satellite underlay toggle")
        XCTAssertTrue(menuItem(app, "map.observedLightningToggle", label: "Lightning").exists,
                      "the menu should offer the lightning toggle")
        let open = XCTAttachment(screenshot: app.screenshot())
        open.name = "RouteMap-Observed-Menu-Mock"
        open.lifetime = .keepAlways
        add(open)
    }

    /// Journey (#605) — the cross-section layer bar in each layout mode, and the
    /// scroll-trap fix. iPhone portrait: a compact chip switches its family in
    /// place and press-and-hold opens the methods row. iPad: a full chip opens
    /// its detail row. Both: a vertical swipe that starts ON the chart scrolls
    /// the page — it used to be swallowed as a scrub. Then landscape. The
    /// screenshots are the real check; an existence assertion passes on a
    /// layout that is visibly broken.
    @MainActor
    func testCrossSectionLayerBar() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)
        switchToBriefingTab(app, "Cross-Section")
        let canvas = app.descendants(matching: .any)["crossSectionCanvas"]
        XCTAssertTrue(canvas.waitForExistence(timeout: Self.uiTimeout), "cross-section canvas should render")
        let isPad = UIDevice.current.userInterfaceIdiom == .pad
        attachScreenshot(app, "LayerBar-1-initial")

        let icing = app.descendants(matching: .any)["layerFamily-icing"].firstMatch
        XCTAssertTrue(icing.waitForExistence(timeout: Self.uiTimeout), "the icing chip should be on the layer bar")
        if isPad {
            icing.tap()  // full chip: opens the family's detail row
            XCTAssertTrue(app.descendants(matching: .any)["layerFamilyDetail-icing"].waitForExistence(timeout: Self.uiTimeout),
                          "tapping a family chip should open its detail row")
        } else {
            icing.tap()  // compact chip: switches the family off in place
            let clouds = app.descendants(matching: .any)["layerFamily-clouds"].firstMatch
            clouds.press(forDuration: 0.8)
            XCTAssertTrue(app.descendants(matching: .any)["layerFamilyDetail-clouds"].waitForExistence(timeout: Self.uiTimeout),
                          "press-and-hold on a chip should open its methods row")
        }
        attachScreenshot(app, "LayerBar-2-detail")

        // Scroll trap: a vertical swipe starting on the chart must move the page.
        let before = canvas.frame.minY
        canvas.swipeUp()
        XCTAssertLessThan(canvas.frame.minY, before - 20,
                          "a vertical swipe on the chart should scroll the page, not scrub")
        attachScreenshot(app, "LayerBar-3-scrolled")

        // Back to the top before rotating, so iPad's bar is on screen to measure.
        app.swipeDown()
        app.swipeDown()

        XCUIDevice.shared.orientation = .landscapeLeft
        XCTAssertTrue(canvas.waitForExistence(timeout: Self.uiTimeout), "canvas should render in landscape")
        if !isPad {
            // iPhone landscape is the chart alone: the floating tab bar used to
            // sit over the terrain and the distance axis.
            let tabGone = XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "exists == false"),
                object: app.tabBars.buttons["Cross-Section"])
            XCTAssertEqual(XCTWaiter.wait(for: [tabGone], timeout: Self.uiTimeout), .completed,
                           "landscape should hide the tab bar")
        }
        // Let the rotation animation finish before measuring.
        Thread.sleep(forTimeInterval: 2)

        // Landscape is asserted on FRAMES: XCUI's landscape screenshots come
        // back rotated and cropped to the left half, so they cannot show a
        // pinned control drawn over another one — the bug this layout fixed.
        let sounding = app.buttons["Sounding"].firstMatch
        let options = app.buttons["crossSectionOptions"].firstMatch
        let cloudsChip = app.descendants(matching: .any)["layerFamily-clouds"].firstMatch
        let frames = [
            "window \(app.windows.firstMatch.frame)",
            // iPhone landscape hides the navigation bar entirely.
            "navBar \(app.navigationBars.firstMatch.exists ? "\(app.navigationBars.firstMatch.frame)" : "hidden")",
            "sounding \(sounding.frame) hittable=\(sounding.isHittable)",
            "options \(options.frame) hittable=\(options.isHittable)",
            "cloudsChip \(cloudsChip.frame) hittable=\(cloudsChip.isHittable)",
            "canvas \(canvas.frame)",
        ].joined(separator: "\n")
        let layout = XCTAttachment(string: frames)
        layout.name = "LayerBar-4-landscape-frames"
        layout.lifetime = .keepAlways
        add(layout)
        XCTAssertTrue(sounding.isHittable, "Sounding › should be reachable in landscape")
        XCTAssertTrue(options.isHittable, "the options button should be reachable in landscape")
        XCTAssertFalse(sounding.frame.intersects(options.frame), "Sounding › and the options button must not overlap")
        XCTAssertTrue(cloudsChip.isHittable, "the family chips should be reachable in landscape")
        XCTAssertLessThanOrEqual(cloudsChip.frame.maxY, canvas.frame.minY + 1, "the chips sit above the chart, not over it")
        XCTAssertLessThanOrEqual(canvas.frame.maxY, app.windows.firstMatch.frame.maxY + 1,
                                 "the whole chart, axis included, should be on screen")

        attachScreenshot(app, "LayerBar-4-landscape")
        let screen = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screen.name = "LayerBar-4-landscape-screen"
        screen.lifetime = .keepAlways
        add(screen)
        XCUIDevice.shared.orientation = .portrait
    }

    /// Journey 6 (#492) — Current Observations section. Open fixture-1, jump to
    /// the Observations scroll-spy section, and confirm the METAR/TAF/model
    /// comparison table renders. Also pins the responsive contract: the
    /// Condition/Wind axis picker exists only in compact width (iPhone), because
    /// regular width (iPad) shows both groups at once.
    @MainActor
    func testRouteObservationsSectionRenders() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        // The D-0 observations live on the Observed tab (#661); its spy pill
        // only exists when an airport actually reported, so finding it also
        // confirms the `hasObservations` gate and the spy wiring.
        switchToBriefingTab(app, "Observed")
        let pill = app.buttons["METAR/TAF"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout),
                      "the Observations spy pill should be present for the D-0 fixture")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.descendants(matching: .any)["observationsSection"].waitForExistence(timeout: Self.uiTimeout),
                      "observations section should render")

        // Fixture airports appear as rows (the ⓘ button carries the label).
        XCTAssertTrue(app.buttons["LFMD details"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "LFMD row should render")
        XCTAssertTrue(app.buttons["LFTH details"].firstMatch.exists,
                      "LFTH row (the CONFLICTING case) should render")

        // Responsive contract: an axis *picker* on iPhone, both groups at once on
        // iPad. Keyed off the segment buttons rather than the picker's
        // accessibilityIdentifier — SwiftUI doesn't surface that identifier on a
        // `.segmented` Picker. This is also the sharper assertion: in regular
        // width "Condition"/"Wind" appear as group *headers* (static text), so
        // their presence as buttons is exactly what distinguishes the two layouts.
        let conditionSegment = app.buttons["Condition"].firstMatch
        if UIDevice.current.userInterfaceIdiom == .pad {
            XCTAssertFalse(conditionSegment.exists,
                           "regular width shows both axes, so there should be no axis picker")
            XCTAssertTrue(app.staticTexts["Condition"].firstMatch.exists,
                          "regular width should render the Condition group header")
            XCTAssertTrue(app.staticTexts["Wind"].firstMatch.exists,
                          "regular width should render the Wind group header")
        } else {
            XCTAssertTrue(conditionSegment.exists,
                          "compact width should offer the Condition/Wind axis picker")
        }

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Observations-Condition"
        shot.lifetime = .keepAlways
        add(shot)

        // Switch to the Wind axis (compact only) and capture that too, so the
        // crosswind capsules are covered by a visual record.
        if UIDevice.current.userInterfaceIdiom != .pad {
            let wind = app.buttons["Wind"].firstMatch
            if wind.waitForExistence(timeout: Self.probeTimeout) {
                wind.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                let windShot = XCTAttachment(screenshot: app.screenshot())
                windShot.name = "Observations-Wind"
                windShot.lifetime = .keepAlways
                add(windShot)
            }
        }
    }

    /// Journey 6b (#492) — the per-airport ⓘ drill-down opens and shows the raw
    /// METAR/TAF plus the runway-wind breakdown (the web's obs popup).
    @MainActor
    func testRouteObservationsDetailSheet() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        switchToBriefingTab(app, "Observed")
        let pill = app.buttons["METAR/TAF"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout), "METAR/TAF spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        let info = app.buttons["LFTH details"].firstMatch
        XCTAssertTrue(info.waitForExistence(timeout: Self.uiTimeout), "LFTH row should render")
        info.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // The sheet is titled with the ICAO and shows the raw METAR text.
        XCTAssertTrue(app.staticTexts["METAR"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "detail sheet should show a METAR block")
        XCTAssertTrue(app.staticTexts["Runway wind"].firstMatch.exists,
                      "detail sheet should show the runway-wind breakdown")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Observations-DetailSheet"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey 7 (#493) — Area Hazards (route SIGMET) section. Open fixture-1,
    /// jump to the Hazards scroll-spy section, and confirm the table renders with
    /// the severe row present. Also pins the responsive contract: the movement
    /// column is inline in regular width (iPad) and folded into the detail sheet
    /// in compact width (iPhone).
    @MainActor
    func testRouteSigmetsSectionRenders() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        // On the Observed tab (#661). The spy pill only exists when a SIGMET
        // actually matched the corridor, so finding it also confirms the
        // `hasSigmets` gate and the spy wiring.
        switchToBriefingTab(app, "Observed")
        let pill = app.buttons["SIGMET"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout),
                      "the Hazards spy pill should be present for the D-0 fixture")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.descendants(matching: .any)["sigmetsSection"].waitForExistence(timeout: Self.uiTimeout),
                      "SIGMET section should render")

        // Both fixture bulletins appear as rows (the ⓘ button carries the label).
        XCTAssertTrue(app.buttons["LFMM EMBD TS details"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the embedded-TS row should render")
        XCTAssertTrue(app.buttons["LFMM SEV TURB details"].firstMatch.exists,
                      "the SEV row (which drives the severe banner) should render")

        // Responsive contract: "Move" is a column header on iPad only.
        let moveHeader = app.staticTexts["Move"].firstMatch
        if UIDevice.current.userInterfaceIdiom == .pad {
            XCTAssertTrue(moveHeader.exists, "regular width should render the Move column")
        } else {
            XCTAssertFalse(moveHeader.exists,
                           "compact width folds movement into the detail sheet")
        }

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Sigmets-Table"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey 7b (#493) — the per-SIGMET ⓘ drill-down opens and shows the raw
    /// bulletin (the web's SIGMET popup), which is the authoritative text.
    @MainActor
    func testRouteSigmetDetailSheet() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        switchToBriefingTab(app, "Observed")
        let pill = app.buttons["SIGMET"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout), "SIGMET spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        let info = app.buttons["LFMM SEV TURB details"].firstMatch
        XCTAssertTrue(info.waitForExistence(timeout: Self.uiTimeout), "SEV TURB row should render")
        info.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.staticTexts["Raw bulletin"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "detail sheet should show the raw bulletin block")
        XCTAssertTrue(app.staticTexts["Hazard"].firstMatch.exists,
                      "detail sheet should show the hazard meta block")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Sigmets-DetailSheet"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
