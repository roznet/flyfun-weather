//
//  flyfun_weatherUITests.swift
//  flyfun-weatherUITests
//
//  XCUITest journeys (#314). Launched in mock mode (FLYFUN_UITEST + FLYFUN_MOCK)
//  so they skip the auth gate and serve fixtures — deterministic, offline, no
//  backend or OAuth. Selectors key off accessibilityIdentifiers, not visible
//  text, so they survive copy/localization changes.
//

import XCTest

final class flyfun_weatherUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// Launch the app as a UI test would: fake-authenticated + fixture-backed.
    /// `offline: true` also sets `FLYFUN_MOCK_OFFLINE` so the fixtures present as
    /// a cached list (offline banner + read-only rows) for the offline journey.
    @MainActor
    private func launchMockApp(offline: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["FLYFUN_UITEST"] = "1"
        app.launchEnvironment["FLYFUN_MOCK"] = "1"
        if offline { app.launchEnvironment["FLYFUN_MOCK_OFFLINE"] = "1" }
        app.launch()
        return app
    }

    /// A briefing is open once its internal tab bar is present. The tab titles
    /// (Advisory / Cross-Section / …) render as buttons on both idioms — an
    /// iPhone bottom `Tab`, an iPad pill band — so this is the idiom-agnostic
    /// "briefing loaded" signal (replaces keying off localized header text).
    @MainActor
    private func waitForBriefingLoaded(_ app: XCUIApplication) {
        XCTAssertTrue(app.buttons["Advisory"].firstMatch.waitForExistence(timeout: 15),
                      "the briefing (with its Advisory tab) should be shown")
    }

    /// Open `fixture-1`'s briefing: reveal the list, tap the seeded flight, and
    /// wait for the briefing tabs. Works on both idioms — iPhone pushes the
    /// detail, iPad selects it in the split view.
    @MainActor
    private func openFixture1Briefing(_ app: XCUIApplication) {
        revealFlightList(app)
        // `.firstMatch`: SwiftUI propagates the card's accessibilityIdentifier to
        // its child nodes, so the id resolves to several elements — fine for
        // `waitForExistence`, but `.tap()` needs a single element.
        let card = app.descendants(matching: .any)["flightCard-fixture-1"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10), "fixture-1 should be listed")
        card.tap()
        waitForBriefingLoaded(app)
    }

    /// Switch the open briefing to a named internal tab. On iPhone the tabs are a
    /// native bottom `TabBar` (selection registers only via the tab-bar button);
    /// on iPad they're a custom pill band of plain buttons. Try the tab bar
    /// first, then fall back to a plain button.
    @MainActor
    private func switchToBriefingTab(_ app: XCUIApplication, _ title: String) {
        // Prefer the bottom tab bar (iPhone); fall back to the pill band (iPad).
        var tab = app.tabBars.buttons[title]
        if !tab.waitForExistence(timeout: 3) { tab = app.buttons[title].firstMatch }
        XCTAssertTrue(tab.waitForExistence(timeout: 10), "\(title) tab should be present")
        // Coordinate tap: in this SwiftUI setup a plain `.tap()` on the tab-bar
        // button passes XCUI's hittability gate without registering the TabView
        // selection; hitting the element's centre point directly does.
        tab.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
    }

    /// iPhone collapses the split view to show the flight list directly; iPad in
    /// portrait collapses to the detail pane with the list behind a "Show Sidebar"
    /// toggle. Reveal it so the list is reachable on both idioms.
    @MainActor
    private func revealFlightList(_ app: XCUIApplication) {
        // Already on screen (iPhone, or iPad landscape) — keyed off the list's
        // accessibility identifier, not fixture content, so renaming a fixture
        // route can't silently break iPad handling.
        if app.descendants(matching: .any)["flightList"].waitForExistence(timeout: 5) { return }
        // iPad portrait: the list sits behind the split-view toggle — a system
        // control with no stable identifier we can set, matched by its (English)
        // label. CI runs English sims.
        let showSidebar = app.buttons["Show Sidebar"]
        if showSidebar.exists { showSidebar.tap(); return }
        XCTFail("Could not reveal flight list: neither the list nor the sidebar toggle was found")
    }

    /// Journey 1 — launch in mock mode lands past the login gate on the flight
    /// list, and the seeded fixtures render (iPhone + iPad).
    @MainActor
    func testFlightListRendersSeededFlights() throws {
        let app = launchMockApp()
        revealFlightList(app)

        // Both fixture flights render (keyed off the card identifiers, not the
        // route text, so a route-formatter change can't break the assertion).
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: 10),
                      "first fixture flight should be listed")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-2"].waitForExistence(timeout: 5),
                      "second fixture flight should be listed")

        // The primary action is reachable.
        XCTAssertTrue(app.buttons["addFlightButton"].exists, "Add Flight button should be present")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "FlightList-Mock"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey 2 — add-flight form: submit is gated until ≥2 waypoints, then a
    /// create round-trips and the new flight appears in the list.
    @MainActor
    func testAddFlightValidationAndCreate() throws {
        let app = launchMockApp()
        revealFlightList(app)

        app.buttons["addFlightButton"].tap()

        let waypoints = app.textFields["waypointsField"]
        XCTAssertTrue(waypoints.waitForExistence(timeout: 5), "add-flight form should appear")
        let submit = app.buttons["submitFlightButton"]

        // One waypoint → submit disabled.
        waypoints.tap()
        waypoints.typeText("EGLL")
        XCTAssertFalse(submit.isEnabled, "one waypoint is not enough to submit")

        // Second waypoint → submit enabled.
        waypoints.typeText(" EGKK")
        XCTAssertTrue(submit.isEnabled, "two waypoints should enable submit")

        submit.tap()

        // Creating the flight selects it, so the app navigates to the new flight's
        // briefing. Confirm we landed there via the briefing tab bar (#318 — no
        // longer keying off localized header text).
        waitForBriefingLoaded(app)
    }

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
        XCTAssertTrue(why.waitForExistence(timeout: 10),
                      "RED convective advisory should offer a 'Why it's RED' drill-down")
        // Coordinate tap: the button renders on-screen but XCUI reports it "not
        // hittable" (a thin control inside the scroll view); hitting its centre
        // point directly is reliable.
        why.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // The detail sheet shows the per-model reconciliation.
        XCTAssertTrue(app.descendants(matching: .any)["advisoryDetail"].waitForExistence(timeout: 10),
                      "advisory detail sheet should open")
        XCTAssertTrue(app.descendants(matching: .any)["advisoryDetailModel-gfs"].waitForExistence(timeout: 5),
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
        XCTAssertTrue(app.descendants(matching: .any)["crossSectionCanvas"].waitForExistence(timeout: 15),
                      "cross-section canvas should render for the fixture (ECMWF has data)")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "CrossSection-Mock"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey 5 (#318) — offline path. Launch in mock-offline mode and confirm
    /// the flight list shows the offline banner (read-only state) and that the
    /// one offline-ready flight (fixture-1) still opens from cache. iPhone + iPad.
    @MainActor
    func testOfflinePathBannerAndCachedOpen() throws {
        let app = launchMockApp(offline: true)
        revealFlightList(app)

        XCTAssertTrue(app.descendants(matching: .any)["offlineBanner"].waitForExistence(timeout: 10),
                      "offline banner should show when the list is served from cache")

        // The offline-ready flight is still listed and openable from cache.
        let card = app.descendants(matching: .any)["flightCard-fixture-1"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10), "cached flight should be listed offline")
        card.tap()
        waitForBriefingLoaded(app)   // the cached flight's briefing opens offline
    }

    @MainActor
    func testLaunchPerformance() throws {
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }
}
