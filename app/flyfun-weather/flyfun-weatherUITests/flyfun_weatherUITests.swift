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
    @MainActor
    private func launchMockApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["FLYFUN_UITEST"] = "1"
        app.launchEnvironment["FLYFUN_MOCK"] = "1"
        app.launch()
        return app
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
        // briefing. Confirm we landed there (the briefing header shows the route).
        // The briefing header has no stable identifier yet — tracked in #318 — so
        // this is the one assertion that keys off visible text by necessity.
        XCTAssertTrue(app.staticTexts["EGLL → EGKK"].waitForExistence(timeout: 10),
                      "the created flight's briefing should be shown")
    }

    @MainActor
    func testLaunchPerformance() throws {
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }
}
