import XCTest

/// Screenshots of any flight, for docs and talks: the **real** app against the
/// running `/devserver --https`, signed in as the dev user. Driven by
/// `scripts/capture/ios.py`, which mints the token, checks the flight is in the
/// dev DB and collects the attachments (`capture-<screen>`) from the result
/// bundle; it skips everywhere else (CI, the nightly, `ios_test.py`).
///
/// Unlike `ScreenshotUITests` (the store set, on a fixture) a missing screen
/// is not fatal: the journey records the failure and goes on to the next one,
/// so a flight without, say, a sounding still yields the rest. The script
/// reports what was skipped.
final class FlightCaptureUITests: FlyfunUITestCase {

    private let env = ProcessInfo.processInfo.environment

    /// Live data: charts draw after their fetch, map tiles after that.
    static let renderSettle: UInt32 = 3
    static let mapSettle: UInt32 = 6

    @MainActor
    override func setUpWithError() throws {
        try XCTSkipUnless(env["FLYFUN_CAPTURE"] == "1", "captures run only from scripts/capture/ios.py")
        try super.setUpWithError()
        continueAfterFailure = true
    }

    @MainActor
    func testCaptureFlight() throws {
        let flight = try XCTUnwrap(env["FLYFUN_CAPTURE_FLIGHT"], "FLYFUN_CAPTURE_FLIGHT is required")
        let token = try XCTUnwrap(env["FLYFUN_CAPTURE_TOKEN"], "FLYFUN_CAPTURE_TOKEN is required")
        let wanted = Set((env["FLYFUN_CAPTURE_SCREENS"] ?? "").split(separator: ",").map(String.init))
        func want(_ screen: String) -> Bool { wanted.isEmpty || wanted.contains(screen) }

        // Live-dev mode: the debug build's server picker set to the devserver
        // (`-key value` lands in UserDefaults' argument domain), tips off.
        let app = XCUIApplication()
        app.launchArguments += ["-serverEnvironment", "dev"]
        app.launchEnvironment["FLYFUN_UITEST"] = "1"
        app.launch()
        // A `scope: review` token signs in from a bare deep link; harmless
        // when the simulator is still signed in from an earlier run.
        app.open(URL(string: "flyfunweather://auth?token=\(token)")!)

        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightList"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the flight list should be shown once signed in")
        if want("flights") { capture(app, "flights") }

        guard openBriefing(app, flight) else {
            XCTFail("could not open \(flight)")
            return
        }
        // On flight day the briefing opens on the Observed tab; the summary and
        // its Advisories pill are on the Advisory tab. Both the pill and the
        // Observed tab wait on the snapshot, so poll them together — off flight
        // day the pill answers as soon as it loads instead of a full probe
        // spent waiting for an Observed tab that never comes.
        let observedTab = app.buttons["Observed"].firstMatch
        _ = firstToAppear([app.buttons["Advisories"].firstMatch, observedTab])
        if observedTab.exists {
            switchToBriefingTab(app, "Advisory")
        }
        if want("briefing") { capture(app, "briefing") }

        if want("advisories") {
            let pill = app.buttons["Advisories"].firstMatch
            if pill.waitForExistence(timeout: Self.probeTimeout) {
                pill.tap()
                capture(app, "advisories")
            } else {
                XCTFail("no Advisories pill on the briefing")
            }
        }

        // Observed is a tab only on flight day; its absence is a skip, not a failure.
        if want("observed"), app.buttons["Observed"].firstMatch.waitForExistence(timeout: Self.probeTimeout) {
            switchToBriefingTab(app, "Observed")
            capture(app, "observed")
        }

        if want("discussion") {
            switchToBriefingTab(app, "Discussion")
            capture(app, "discussion")
        }

        if want("cross-section") || want("skewt") {
            switchToBriefingTab(app, "Cross-Section")
            let canvas = app.descendants(matching: .any)["crossSectionCanvas"].firstMatch
            if canvas.waitForExistence(timeout: Self.uiTimeout) {
                if want("cross-section") { capture(app, "cross-section") }
            } else {
                XCTFail("the cross-section should render")
            }
            if want("skewt") {
                let sounding = app.buttons["Sounding"].firstMatch
                if sounding.waitForExistence(timeout: Self.uiTimeout) {
                    sounding.tap()
                    capture(app, "skewt")
                    app.swipeDown(velocity: .fast)
                    sleep(1)
                } else {
                    XCTFail("the cross-section should offer the Sounding")
                }
            }
        }

        if want("map") {
            switchToBriefingTab(app, "Map")
            capture(app, "map", settle: Self.mapSettle)
        }
    }

    /// Open the flight's briefing through its universal link (the route a
    /// tapped `briefing.html?flight=` link takes), falling back to finding
    /// its card in the list: the dev user's list is long and a past flight
    /// can sit far below the fold.
    @MainActor
    private func openBriefing(_ app: XCUIApplication, _ flight: String) -> Bool {
        let tab = app.buttons["Advisory"].firstMatch
        app.open(URL(string: "https://weather.flyfun.aero/briefing.html?flight=\(flight)")!)
        if tab.waitForExistence(timeout: Self.uiTimeout) { return true }

        revealFlightList(app)
        let card = app.descendants(matching: .any)["flightCard-\(flight)"].firstMatch
        for _ in 0..<30 where !card.isHittable {
            app.swipeUp()
        }
        guard card.waitForExistence(timeout: Self.probeTimeout) else { return false }
        card.tap()
        return tab.waitForExistence(timeout: Self.uiTimeout)
    }

    @MainActor
    private func capture(_ app: XCUIApplication, _ screen: String, settle: UInt32 = renderSettle) {
        sleep(settle)
        attachScreenshot(app, "capture-\(screen)")
    }
}
