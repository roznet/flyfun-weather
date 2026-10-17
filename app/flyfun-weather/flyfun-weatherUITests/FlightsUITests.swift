import XCTest
import UIKit

final class FlightsUITests: FlyfunUITestCase {

    /// #616: feedback sent from a briefing is the linked form, reached from the
    /// pack menu, and submits (the fixture repository accepts it).
    @MainActor
    func testBriefingFeedbackFromPackMenu() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        let packMenu = app.buttons["packToolbarMenu"].firstMatch
        XCTAssertTrue(packMenu.waitForExistence(timeout: Self.uiTimeout), "the pack menu should be in the toolbar")
        packMenu.tap()

        let feedback = menuItem(app, "briefingFeedbackButton", label: "Send Feedback on This Briefing")
        XCTAssertTrue(feedback.waitForExistence(timeout: Self.uiTimeout), "the pack menu should offer briefing feedback")
        feedback.tap()

        // A menu item tapped while the menu is still animating open can be
        // swallowed: on the 2026-10-05 CI run the menu was still showing this
        // item when the form wait expired, and the retry passed. If the form
        // hasn't come up and the item is still there, tap it again — as a
        // pilot would.
        let note = app.staticTexts["feedbackBriefingLinkNote"]
        if !note.waitForExistence(timeout: Self.probeTimeout) && feedback.exists { feedback.tap() }
        XCTAssertTrue(note.waitForExistence(timeout: Self.uiTimeout),
                      "the form should say the report links to this briefing")
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "briefing-feedback-form"
        shot.lifetime = .keepAlways
        add(shot)

        // A vertical-axis TextField, exposed as a text *field* on both idioms.
        // There used to be a text-view probe first: it missed on every run
        // (8 s locally), and on CI's slower simulators its repeated snapshots
        // ran to XCUI's "Timed out while evaluating UI query".
        let field = app.textFields["feedbackCommentField"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: Self.uiTimeout), "the comment field should be shown")
        field.tap()
        field.typeText("No TAF at EGSC today")
        app.buttons["feedbackSubmitButton"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Thanks for your feedback!"].waitForExistence(timeout: Self.uiTimeout),
                      "submitting should show the thanks state")
    }

    /// Journey 1 — launch in mock mode lands past the login gate on the flight
    /// list, and the seeded fixtures render (iPhone + iPad).
    @MainActor
    func testFlightListRendersSeededFlights() throws {
        let app = launchMockApp()
        revealFlightList(app)

        // Both fixture flights render (keyed off the card identifiers, not the
        // route text, so a route-formatter change can't break the assertion).
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: Self.uiTimeout),
                      "first fixture flight should be listed")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-2"].waitForExistence(timeout: Self.uiTimeout),
                      "second fixture flight should be listed")

        // The primary action is reachable.
        XCTAssertTrue(app.buttons["addFlightButton"].exists, "Add Flight button should be present")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "FlightList-Mock"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey — a trip in the flight list (#607): one header row for the chain,
    /// its remaining leg beneath it, the flown leg as its own row; tapping the
    /// header opens the trip screen with the binding-leg callout and timeline.
    /// Backed by `FixtureTripData` (a flown outbound, an AMBER binding return).
    @MainActor
    func testTripHeaderOpensTripScreen() throws {
        let app = launchMockApp()
        revealFlightList(app)

        let header = app.descendants(matching: .any)["tripHeaderRow-fixture-trip-1"].firstMatch
        XCTAssertTrue(header.waitForExistence(timeout: Self.uiTimeout),
                      "the fixture trip should render a header row")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-trip-back"].exists,
                      "the remaining return leg should be listed")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-trip-out"].exists,
                      "the flown outbound should still be listed, as its own row")

        let list = XCTAttachment(screenshot: app.screenshot())
        list.name = "Trip-FlightList"
        list.lifetime = .keepAlways
        add(list)

        header.tap()
        XCTAssertTrue(app.descendants(matching: .any)["tripBindingCallout"].firstMatch
                        .waitForExistence(timeout: Self.uiTimeout),
                      "the trip screen should lead with the binding-leg callout")
        XCTAssertTrue(app.descendants(matching: .any)["tripTimeline"].firstMatch.exists,
                      "the trip screen should draw the leg timeline")

        let detail = XCTAttachment(screenshot: app.screenshot())
        detail.name = "Trip-Detail"
        detail.lifetime = .keepAlways
        add(detail)
    }

    /// Journey 2 — add-flight form: submit is gated until ≥2 waypoints, then a
    /// create round-trips and the new flight appears in the list.
    @MainActor
    func testAddFlightValidationAndCreate() throws {
        let app = launchMockApp()
        revealFlightList(app)

        app.buttons["addFlightButton"].tap()

        let waypoints = app.textFields["waypointsField"]
        XCTAssertTrue(waypoints.waitForExistence(timeout: Self.uiTimeout), "add-flight form should appear")
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

    /// Journey 8 (#552) — the reported regression: an edit that changes a field the
    /// flight ID is built from must offer Move / Duplicate and then actually
    /// **dismiss**. Here the destination changes, so Move replaces the flight: the
    /// form closes and the list shows the new flight in place of the old one.
    @MainActor
    func testStructuralEditOffersMoveAndDismisses() throws {
        let app = launchMockApp()
        openEditForm(app, flightId: "fixture-2")

        // fixture-2 is EGTF → LFAT; retype it with a new destination.
        let waypoints = app.textFields["waypointsField"]
        replaceText(waypoints, with: "EGTF LFMD")

        // The inline note explains what Move will discard, before the pilot commits.
        XCTAssertTrue(app.staticTexts["routeChangedNote"].waitForExistence(timeout: Self.uiTimeout),
                      "an origin/destination change should explain Move vs Duplicate inline")

        app.buttons["submitFlightButton"].tap()

        let move = app.buttons["moveFlightButton"].firstMatch
        XCTAssertTrue(move.waitForExistence(timeout: Self.uiTimeout),
                      "a structural change should offer Move / Duplicate, not a plain Save")
        move.tap()

        // The regression: the sheet must actually go away.
        XCTAssertTrue(waypoints.waitForNonExistence(timeout: Self.uiTimeout),
                      "the edit form should dismiss once the move succeeds")

        returnToFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-moved-fixture-2"]
                        .firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the moved flight should be listed")
        XCTAssertFalse(app.descendants(matching: .any)["flightCard-fixture-2"].firstMatch.exists,
                       "Move replaces the flight, so the original should be gone")
    }

    /// Journey 9 (#552) — the date half of the same choice, driven through the
    /// timezone picker so it exercises the local-day-vs-UTC-day trap: fixture-2
    /// departs 13:00Z; shown in Europe/Paris that is 15:00 on the same local day,
    /// and moving it to 00:xx local lands on the *previous* UTC day. Duplicate
    /// keeps both flights.
    @MainActor
    func testDateEditCrossingUtcMidnightOffersDuplicate() throws {
        let app = launchMockApp()
        openEditForm(app, flightId: "fixture-2")

        // The route must resolve before its airports' zones are offered.
        selectFromMenuPicker(app, identifier: "departureTimezonePicker", value: "Paris (GMT+2)")
        selectFromMenuPicker(app, identifier: "departureHourPicker", value: "00")

        XCTAssertTrue(app.staticTexts["dateChangedNote"].waitForExistence(timeout: Self.uiTimeout),
                      "00:xx Paris is the previous UTC day, so the date note should show")

        app.buttons["submitFlightButton"].tap()

        let duplicate = app.buttons["duplicateFlightButton"].firstMatch
        XCTAssertTrue(duplicate.waitForExistence(timeout: Self.uiTimeout),
                      "a UTC-day change should offer Move / Duplicate")
        duplicate.tap()

        XCTAssertTrue(app.textFields["waypointsField"].waitForNonExistence(timeout: Self.uiTimeout),
                      "the edit form should dismiss once the duplicate is created")

        returnToFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-created-1"]
                        .firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the duplicate should be listed")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-2"].firstMatch.exists,
                      "Duplicate keeps the original, so both flights should be listed")
    }

    /// Journey 5 (#318) — offline path. Launch in mock-offline mode and confirm
    /// the flight list shows the offline banner (read-only state) and that the
    /// one offline-ready flight (fixture-1) still opens from cache. iPhone + iPad.
    @MainActor
    func testOfflinePathBannerAndCachedOpen() throws {
        let app = launchMockApp(offline: true)
        revealFlightList(app)

        XCTAssertTrue(app.descendants(matching: .any)["offlineBanner"].waitForExistence(timeout: Self.uiTimeout),
                      "offline banner should show when the list is served from cache")

        // The offline-ready flight is still listed and openable from cache.
        let card = app.descendants(matching: .any)["flightCard-fixture-1"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "cached flight should be listed offline")
        card.tap()
        waitForBriefingLoaded(app)   // the cached flight's briefing opens offline
    }

    /// Journey 8 (#494) — the departure/arrival condition cards keep every cell on
    /// one line. On iPad the two cards used to sit in a size-class `HStack` that
    /// squeezed them until SwiftUI compressed the `Text` views to one character per
    /// line ("200@8kt" drawn vertically, "VFR" as "V"/"FR", "Departure" hyphenated).
    ///
    /// Asserting on geometry rather than existence is the point: the elements were
    /// always *present* while the bug was live — only their frames were wrong.
    @MainActor
    func testAirportConditionsCardsStayOnOneLine() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        let pill = app.buttons["Conditions"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout), "Conditions spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // Both fixture cards render.
        XCTAssertTrue(app.staticTexts["LFMD"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "departure card should render")
        XCTAssertTrue(app.staticTexts["LFML"].firstMatch.exists, "arrival card should render")

        // A caption line is ~15pt tall; two lines ~30. Per-character wrapping of an
        // 11-character wind blew this out past 150. 34 leaves room for larger
        // default type on iPad without admitting a second line.
        let maxSingleLine: CGFloat = 34

        // The arrival wind is the worst case — gusting, so the longest string.
        let wind = firstElement(in: app, labelled: "300@12G18kt")
        XCTAssertTrue(wind.waitForExistence(timeout: Self.uiTimeout), "arrival wind cell should render")
        XCTAssertLessThan(wind.frame.height, maxSingleLine,
                          "wind cell wrapped to multiple lines — the row is being compressed (#494)")

        // The category badge and the section label were the other two victims.
        let badge = app.staticTexts["VFR"].firstMatch
        XCTAssertTrue(badge.exists, "category badge should render")
        XCTAssertLessThan(badge.frame.height, maxSingleLine,
                          "category badge wrapped — it should never break mid-word (#494)")

        let label = app.staticTexts["Departure"].firstMatch
        XCTAssertTrue(label.exists, "section label should render")
        XCTAssertLessThan(label.frame.height, maxSingleLine,
                          "section label wrapped/hyphenated — it should keep its intrinsic width (#494)")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "AirportConditions"
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// Journey 9 — the iPad forecast map carries its own sidebar toggle. The map
    /// draws no navigation bar, so the split view's system toggle has nowhere to
    /// live; without this control, collapsing the sidebar over the map stranded the
    /// user there with no route back to the flight list.
    @MainActor
    func testForecastMapOffersSidebarToggleOnPad() throws {
        try XCTSkipUnless(UIDevice.current.userInterfaceIdiom == .pad,
                          "compact width opens the map as a cover with a close button instead")

        let app = launchMockApp()
        revealFlightList(app)

        app.buttons["forecastMapButton"].tap()

        let toggle = app.buttons["mapSidebarToggle"]
        XCTAssertTrue(toggle.waitForExistence(timeout: Self.uiTimeout),
                      "the iPad map should offer a sidebar toggle")

        // Collapse the sidebar — the state the user got stranded in.
        toggle.tap()
        XCTAssertFalse(app.descendants(matching: .any)["flightList"].waitForExistence(timeout: 3),
                       "the flight list should be hidden after collapsing the sidebar")

        let collapsed = XCTAttachment(screenshot: app.screenshot())
        collapsed.name = "ForecastMap-SidebarCollapsed"
        collapsed.lifetime = .keepAlways
        add(collapsed)

        // …and the same control brings it back. This is the whole bug.
        XCTAssertTrue(toggle.waitForExistence(timeout: Self.uiTimeout),
                      "the toggle must survive collapsing — it is the only way back")
        toggle.tap()
        XCTAssertTrue(app.descendants(matching: .any)["flightList"].waitForExistence(timeout: Self.uiTimeout),
                      "tapping the toggle again should restore the flight list")
    }

    /// Journey 10 (#553) — multi-select + bulk delete. Open the sheet from the
    /// More menu, tick both fixture flights, delete, confirm — and check the rows
    /// are gone from the flight list itself, not merely from the sheet.
    ///
    /// The whole point of the dedicated sheet is that it can't disturb the main
    /// list, so the assertion has to land back on the main list.
    @MainActor
    func testBulkSelectAndDeleteFlights() throws {
        let app = launchMockApp()
        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: Self.uiTimeout),
                      "fixture-1 should be listed before the delete")

        app.buttons["More"].firstMatch.tap()
        let menuItem = app.buttons["bulkSelectMenuItem"].firstMatch
        XCTAssertTrue(menuItem.waitForExistence(timeout: Self.uiTimeout),
                      "the More menu should offer Select & Delete Flights")
        menuItem.tap()

        // The sheet opens already in select mode, so a row tap ticks it.
        let row1 = app.descendants(matching: .any)["selectFlightRow-fixture-1"].firstMatch
        XCTAssertTrue(row1.waitForExistence(timeout: Self.uiTimeout), "the selection sheet should list fixture-1")
        row1.tap()
        app.descendants(matching: .any)["selectFlightRow-fixture-2"].firstMatch.tap()

        let deleteButton = app.buttons["bulkDeleteButton"].firstMatch
        XCTAssertTrue(deleteButton.isEnabled, "Delete should be enabled once flights are selected")
        deleteButton.tap()

        // Destructive confirmation — an alert (never a popover, which would drop
        // the Cancel button on iPad). SwiftUI doesn't always surface an
        // accessibilityIdentifier set on an alert button, so fall back to the
        // alert's own Delete button.
        var confirm = app.buttons["confirmBulkDeleteButton"].firstMatch
        if !confirm.waitForExistence(timeout: Self.probeTimeout) {
            confirm = app.alerts.buttons["Delete"].firstMatch
        }
        XCTAssertTrue(confirm.waitForExistence(timeout: Self.uiTimeout), "a delete confirmation should appear")
        XCTAssertTrue(app.alerts.buttons["Cancel"].firstMatch.exists,
                      "the confirmation must keep a Cancel button on every idiom")
        confirm.tap()

        // Back on the flight list, both rows are gone. Deliberately no
        // `revealFlightList` here: with every fixture deleted the list is replaced
        // by its empty state, so the helper's `flightList` identifier is gone and
        // it would fail the test on a *correct* outcome.
        let gone = NSPredicate(format: "exists == false")
        expectation(for: gone, evaluatedWith: app.descendants(matching: .any)["flightCard-fixture-1"])
        expectation(for: gone, evaluatedWith: app.descendants(matching: .any)["flightCard-fixture-2"])
        waitForExpectations(timeout: 15)
    }

    /// Journey 2b — "Paste Flight Plan": the paste sheet must open over the
    /// add-flight form, and a successful parse must fill the route field and
    /// leave the form standing, ready for the pilot to tap Create. Regression:
    /// the paste sheet was anchored to a lazy `Form` row, so tapping Paste Flight
    /// Plan dismissed the whole form and left the app on whatever briefing was
    /// last selected, with no flight created.
    @MainActor
    func testPasteFlightPlanFillsFormAndKeepsItOpen() throws {
        let app = launchMockApp()
        // Open a briefing first: on iPad that leaves it in the detail pane behind
        // the add-flight sheet, which is the state the bug was reported from —
        // the form vanishing reads as "the app went back to an old briefing".
        openFixture1Briefing(app)
        // iPhone pushes the briefing over the list, so getting back needs the
        // back-tap `returnToFlightList` knows about; on iPad it is a no-op.
        returnToFlightList(app)

        app.buttons["addFlightButton"].firstMatch.tap()
        XCTAssertTrue(app.textFields["waypointsField"].waitForExistence(timeout: Self.uiTimeout),
                      "add-flight form should appear")

        let paste = app.buttons["pasteFplButton"].firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: Self.uiTimeout),
                      "the Import section should offer Paste Flight Plan")
        paste.tap()

        let editor = app.textViews["fplTextEditor"].firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: Self.uiTimeout),
                      "the FPL paste sheet should show its text editor")
        editor.tap()
        // One line: XCUI's typeText sends a newline as Return, which the sheet's
        // TextEditor accepts but which makes the typed text diverge from the
        // pasted original. The parse fixture only keys off the (FPL- prefix.
        editor.typeText("(FPL-N122DR-ZG-S22T/L-SBDGORVY/LB2-LSGS0800-N0178A110 SAPRE1D SAPRE/N0189F180 IFR L615 DJL A6 SOMDA T11 VATRI B3 BILGO H20 XORBI H40 ABB N20 ELDAX M8 WAFFU Y8 GWC-EGTF0257-PBN/A1B2C2D2L1O2 DOF/260927)")

        app.buttons["parseFplButton"].firstMatch.tap()

        // The form must still be up — this is the regression the journey guards.
        let waypoints = app.textFields["waypointsField"]
        XCTAssertTrue(waypoints.waitForExistence(timeout: Self.uiTimeout),
                      "the add-flight form should still be open after Parse & Fill")
        let value = (waypoints.value as? String) ?? ""
        XCTAssertTrue(value.contains("LSGS") && value.contains("EGTF"),
                      "the parsed route should be filled into the route field, got: \(value)")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "after-parse-fill"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
