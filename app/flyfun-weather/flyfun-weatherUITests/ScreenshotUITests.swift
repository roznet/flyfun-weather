import XCTest

/// App Store screenshots of one real briefing (`app/fixtures/showcase/showcase.json`, see
/// `FixtureShowcase`). Not a regression journey, but every capture is a hard
/// assertion — a store set missing a screen must fail, not ship short — and
/// it runs only when asked —
/// `scripts/ios_screenshots.py` sets `TEST_RUNNER_FLYFUN_SCREENSHOTS=1` and
/// collects the attachments, named `NN-screen`, from the result bundle.
final class ScreenshotUITests: FlyfunUITestCase {

    /// The fixture's absolute path on the host, which the simulator app can
    /// read: nothing is copied into the app bundle.
    static let showcasePath = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()  // → flyfun-weatherUITests/
        .deletingLastPathComponent()  // → app/flyfun-weather/
        .deletingLastPathComponent()  // → app/
        .appendingPathComponent("fixtures/showcase/showcase.json").path
    /// The flight id carries its date, which the date shift moves with the
    /// rest: match the card by the route part only.
    static let cardPrefix = "flightCard-egtf_big_rem_sapre_lsgs-"

    /// Let charts and maps finish drawing before a capture.
    static let renderSettle: UInt32 = 3

    @MainActor
    override func setUpWithError() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["FLYFUN_SCREENSHOTS"] == "1",
                          "screenshots run only from scripts/ios_screenshots.py")
        try super.setUpWithError()
    }

    @MainActor
    func testCaptureStoreScreenshots() throws {
        let app = launchMockApp(environment: ["FLYFUN_MOCK_SHOWCASE": Self.showcasePath])

        revealFlightList(app)
        let card = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", Self.cardPrefix)).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "the showcase flight should be listed")
        capture(app, "08-flights")

        card.tap()
        waitForBriefingLoaded(app)
        capture(app, "01-briefing")

        let advisories = app.buttons["Advisories"].firstMatch
        XCTAssertTrue(advisories.waitForExistence(timeout: Self.uiTimeout), "the Advisories pill should be shown")
        advisories.tap()
        // The iPad's summary already shows the whole advisory list, so the pill
        // doesn't scroll and this would be 01 again.
        if UIDevice.current.userInterfaceIdiom != .pad {
            capture(app, "02-advisories")
        }

        let why = app.buttons["advisoryWhy-vfr_feasibility"].firstMatch
        XCTAssertTrue(why.waitForExistence(timeout: Self.uiTimeout), "VFR Feasibility should offer 'Why it's RED'")
        why.tap()
        capture(app, "04-why-red")
        app.swipeDown(velocity: .fast)
        sleep(1)

        switchToBriefingTab(app, "Cross-Section")
        XCTAssertTrue(app.descendants(matching: .any)["crossSectionCanvas"].firstMatch
                        .waitForExistence(timeout: Self.uiTimeout), "the cross-section should render")
        capture(app, "03-cross-section")

        let sounding = app.buttons["Sounding"].firstMatch
        XCTAssertTrue(sounding.waitForExistence(timeout: Self.uiTimeout), "the cross-section should offer the Sounding")
        sounding.tap()
        capture(app, "05-skewt")

        switchToBriefingTab(app, "Map")
        capture(app, "06-map")

        switchToBriefingTab(app, "Discussion")
        capture(app, "07-discussion")
    }

    @MainActor
    private func capture(_ app: XCUIApplication, _ name: String) {
        sleep(Self.renderSettle)
        attachScreenshot(app, name)
    }
}
