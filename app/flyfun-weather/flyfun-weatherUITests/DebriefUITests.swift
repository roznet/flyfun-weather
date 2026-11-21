import XCTest

final class DebriefUITests: FlyfunUITestCase {

    /// Journey (#1024): after the window the briefing opens its Observed tab on
    /// the Debrief section, drawn from the server's report (the shared
    /// `app/fixtures/api/debrief-report.json`, served for `fixture-1` under
    /// `FLYFUN_MOCK_DEBRIEF=1`). "I flew it" shows the report's questions; one
    /// tap answers one and the answer stays selected.
    @MainActor
    func testDebriefSectionAnswersOneQuestion() throws {
        let app = launchMockApp(environment: ["FLYFUN_MOCK_DEBRIEF": "1"])
        openFixture1Briefing(app)

        let section = app.descendants(matching: .any)["debriefSection"].firstMatch
        if !section.waitForExistence(timeout: Self.uiTimeout) {
            // Open questions land on Observed; switch by hand if a slow load
            // left the briefing on Advisory.
            switchToBriefingTab(app, "Observed")
        }
        XCTAssertTrue(section.waitForExistence(timeout: Self.uiTimeout), "the Observed tab should open on the Debrief section")
        XCTAssertTrue(app.staticTexts["No radar along the route."].firstMatch.exists, "the server's headline should lead it")

        let flown = app.buttons["debriefFlown"].firstMatch
        XCTAssertTrue(flown.waitForExistence(timeout: Self.uiTimeout))
        flown.tap()

        let light = app.buttons["debriefAnswer-icing-110-192-light"].firstMatch
        XCTAssertTrue(light.waitForExistence(timeout: Self.uiTimeout), "flown should show the report's icing question")
        light.tap()
        let selected = NSPredicate(format: "isSelected == true")
        expectation(for: selected, evaluatedWith: light)
        waitForExpectations(timeout: Self.uiTimeout)
        attachScreenshot(app, "Debrief-Section")
    }
}
