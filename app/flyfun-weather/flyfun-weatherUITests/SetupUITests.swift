//
//  SetupUITests.swift
//  flyfun-weatherUITests
//
//  First-run setup (#900). `FLYFUN_MOCK_SETUP=1` starts the fixture account
//  with setup not done (`FixtureSetupRepository`), so the wizard comes up
//  over the flight list; no other journey sets it, so none sees the wizard.
//

import XCTest

final class SetupUITests: FlyfunUITestCase {

    @MainActor
    private func launchWithSetupPending() -> XCUIApplication {
        launchMockApp(environment: ["FLYFUN_MOCK_SETUP": "1"])
    }

    /// Welcome → IFR FIKI → aircraft (FIKI box left alone) → done: the
    /// aircraft comes back FIKI, and Get Started lands on the flight list.
    @MainActor
    func testFinishingSetupSavesAFikiAircraft() throws {
        let app = launchWithSetupPending()
        let next = app.buttons["setupNextButton"].firstMatch
        XCTAssertTrue(next.waitForExistence(timeout: Self.uiTimeout), "the setup wizard should open on first sign-in")
        next.tap()

        let fikiCard = app.buttons["setupProfileCard-ifr_fiki"].firstMatch
        XCTAssertTrue(fikiCard.waitForExistence(timeout: Self.uiTimeout), "the profile step should list IFR FIKI")
        fikiCard.tap()
        next.tap()

        let icao = app.textFields["setupIcaoTypeField"].firstMatch
        XCTAssertTrue(icao.waitForExistence(timeout: Self.uiTimeout), "the aircraft step should ask for the ICAO type")
        icao.tap()
        icao.typeText("SR22")
        next.tap()

        let summary = app.staticTexts["setupSummary"].firstMatch
        XCTAssertTrue(summary.waitForExistence(timeout: Self.uiTimeout), "the done step should show what was saved")
        XCTAssertTrue(summary.label.contains("Default profile: IFR FIKI"), "summary was: \(summary.label)")
        XCTAssertTrue(summary.label.contains("FIKI equipped"), "IFR FIKI should pre-tick FIKI; summary was: \(summary.label)")

        app.buttons["setupFinishButton"].firstMatch.tap()
        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: Self.uiTimeout),
                      "Get Started should land on the flight list")
        XCTAssertFalse(app.buttons["setupNextButton"].exists, "the wizard should be gone")
    }

    /// Skip on the first screen closes the wizard onto the flight list.
    @MainActor
    func testSkippingSetupLandsOnTheFlightList() throws {
        let app = launchWithSetupPending()
        let skip = app.buttons["setupSkipButton"].firstMatch
        XCTAssertTrue(skip.waitForExistence(timeout: Self.uiTimeout), "the setup wizard should offer Skip")
        skip.tap()

        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: Self.uiTimeout),
                      "Skip should land on the flight list")
        XCTAssertFalse(app.buttons["setupSkipButton"].exists, "the wizard should be gone")
    }
}
