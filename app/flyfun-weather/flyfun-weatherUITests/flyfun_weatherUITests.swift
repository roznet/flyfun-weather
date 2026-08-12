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
import UIKit

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

    /// Open the edit form for a listed flight via its trailing swipe action.
    @MainActor
    private func openEditForm(_ app: XCUIApplication, flightId: String) {
        revealFlightList(app)
        let card = app.descendants(matching: .any)["flightCard-\(flightId)"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10), "\(flightId) should be listed")
        card.swipeLeft()
        let edit = app.buttons["editFlightSwipeButton"].firstMatch
        XCTAssertTrue(edit.waitForExistence(timeout: 5), "the Edit swipe action should appear")
        edit.tap()
        XCTAssertTrue(app.textFields["waypointsField"].waitForExistence(timeout: 5),
                      "the edit form should appear")
    }

    /// Pick a value from a `.menu`-style SwiftUI `Picker`, addressed by the
    /// accessibility identifier set on it (the rendered *label* folds in the
    /// current value, so it is not a stable selector).
    @MainActor
    private func selectFromMenuPicker(_ app: XCUIApplication, identifier: String, value: String) {
        let picker = app.buttons[identifier].firstMatch
        XCTAssertTrue(picker.waitForExistence(timeout: 5), "the \(identifier) picker should be present")
        picker.tap()
        let option = app.buttons[value].firstMatch
        XCTAssertTrue(option.waitForExistence(timeout: 5), "\(value) should be offered by \(identifier)")
        option.tap()
    }

    /// Replace a text field's contents. Deleting the existing value key-by-key is
    /// the idiom that works on both idioms; the long-press "Select All" menu is
    /// timing-sensitive.
    @MainActor
    private func replaceText(_ field: XCUIElement, with text: String) {
        field.tap()
        let existing = (field.value as? String) ?? ""
        if !existing.isEmpty {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: existing.count))
        }
        field.typeText(text)
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
        XCTAssertTrue(app.staticTexts["routeChangedNote"].waitForExistence(timeout: 5),
                      "an origin/destination change should explain Move vs Duplicate inline")

        app.buttons["submitFlightButton"].tap()

        let move = app.buttons["moveFlightButton"].firstMatch
        XCTAssertTrue(move.waitForExistence(timeout: 10),
                      "a structural change should offer Move / Duplicate, not a plain Save")
        move.tap()

        // The regression: the sheet must actually go away.
        XCTAssertTrue(waypoints.waitForNonExistence(timeout: 10),
                      "the edit form should dismiss once the move succeeds")

        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-moved-fixture-2"]
                        .firstMatch.waitForExistence(timeout: 10),
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

        XCTAssertTrue(app.staticTexts["dateChangedNote"].waitForExistence(timeout: 5),
                      "00:xx Paris is the previous UTC day, so the date note should show")

        app.buttons["submitFlightButton"].tap()

        let duplicate = app.buttons["duplicateFlightButton"].firstMatch
        XCTAssertTrue(duplicate.waitForExistence(timeout: 10),
                      "a UTC-day change should offer Move / Duplicate")
        duplicate.tap()

        XCTAssertTrue(app.textFields["waypointsField"].waitForNonExistence(timeout: 10),
                      "the edit form should dismiss once the duplicate is created")

        revealFlightList(app)
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-created-1"]
                        .firstMatch.waitForExistence(timeout: 10),
                      "the duplicate should be listed")
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-2"].firstMatch.exists,
                      "Duplicate keeps the original, so both flights should be listed")
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

    /// Journey 6 (#492) — Current Observations section. Open fixture-1, jump to
    /// the Observations scroll-spy section, and confirm the METAR/TAF/model
    /// comparison table renders. Also pins the responsive contract: the
    /// Condition/Wind axis picker exists only in compact width (iPhone), because
    /// regular width (iPad) shows both groups at once.
    @MainActor
    func testRouteObservationsSectionRenders() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        // Advisory is the default tab; the spy pill for the D-0 observations
        // section only exists when an airport actually reported, so finding it
        // also confirms the `hasObservations` gate and the spy wiring.
        let pill = app.buttons["Observations"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 15),
                      "the Observations spy pill should be present for the D-0 fixture")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.descendants(matching: .any)["observationsSection"].waitForExistence(timeout: 10),
                      "observations section should render")

        // Fixture airports appear as rows (the ⓘ button carries the label).
        XCTAssertTrue(app.buttons["LFMD details"].firstMatch.waitForExistence(timeout: 5),
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
            if wind.waitForExistence(timeout: 5) {
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

        let pill = app.buttons["Observations"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 15), "Observations spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        let info = app.buttons["LFTH details"].firstMatch
        XCTAssertTrue(info.waitForExistence(timeout: 10), "LFTH row should render")
        info.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // The sheet is titled with the ICAO and shows the raw METAR text.
        XCTAssertTrue(app.staticTexts["METAR"].firstMatch.waitForExistence(timeout: 10),
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

        // The spy pill only exists when a SIGMET actually matched the corridor,
        // so finding it also confirms the `hasSigmets` gate and the spy wiring.
        let pill = app.buttons["Hazards"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 15),
                      "the Hazards spy pill should be present for the D-0 fixture")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.descendants(matching: .any)["sigmetsSection"].waitForExistence(timeout: 10),
                      "SIGMET section should render")

        // Both fixture bulletins appear as rows (the ⓘ button carries the label).
        XCTAssertTrue(app.buttons["LFMM EMBD TS details"].firstMatch.waitForExistence(timeout: 5),
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

        let pill = app.buttons["Hazards"].firstMatch
        XCTAssertTrue(pill.waitForExistence(timeout: 15), "Hazards spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        let info = app.buttons["LFMM SEV TURB details"].firstMatch
        XCTAssertTrue(info.waitForExistence(timeout: 10), "SEV TURB row should render")
        info.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        XCTAssertTrue(app.staticTexts["Raw bulletin"].firstMatch.waitForExistence(timeout: 10),
                      "detail sheet should show the raw bulletin block")
        XCTAssertTrue(app.staticTexts["Hazard"].firstMatch.exists,
                      "detail sheet should show the hazard meta block")

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "Sigmets-DetailSheet"
        shot.lifetime = .keepAlways
        add(shot)
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
        XCTAssertTrue(pill.waitForExistence(timeout: 15), "Conditions spy pill should be present")
        pill.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

        // Both fixture cards render.
        XCTAssertTrue(app.staticTexts["LFMD"].firstMatch.waitForExistence(timeout: 10),
                      "departure card should render")
        XCTAssertTrue(app.staticTexts["LFML"].firstMatch.exists, "arrival card should render")

        // A caption line is ~15pt tall; two lines ~30. Per-character wrapping of an
        // 11-character wind blew this out past 150. 34 leaves room for larger
        // default type on iPad without admitting a second line.
        let maxSingleLine: CGFloat = 34

        // The arrival wind is the worst case — gusting, so the longest string.
        let wind = firstElement(in: app, labelled: "300@12G18kt")
        XCTAssertTrue(wind.waitForExistence(timeout: 10), "arrival wind cell should render")
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
        XCTAssertTrue(toggle.waitForExistence(timeout: 15),
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
        XCTAssertTrue(toggle.waitForExistence(timeout: 5),
                      "the toggle must survive collapsing — it is the only way back")
        toggle.tap()
        XCTAssertTrue(app.descendants(matching: .any)["flightList"].waitForExistence(timeout: 10),
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
        XCTAssertTrue(app.descendants(matching: .any)["flightCard-fixture-1"].waitForExistence(timeout: 10),
                      "fixture-1 should be listed before the delete")

        app.buttons["More"].firstMatch.tap()
        let menuItem = app.buttons["bulkSelectMenuItem"].firstMatch
        XCTAssertTrue(menuItem.waitForExistence(timeout: 5),
                      "the More menu should offer Select & Delete Flights")
        menuItem.tap()

        // The sheet opens already in select mode, so a row tap ticks it.
        let row1 = app.descendants(matching: .any)["selectFlightRow-fixture-1"].firstMatch
        XCTAssertTrue(row1.waitForExistence(timeout: 10), "the selection sheet should list fixture-1")
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
        if !confirm.waitForExistence(timeout: 5) {
            confirm = app.alerts.buttons["Delete"].firstMatch
        }
        XCTAssertTrue(confirm.waitForExistence(timeout: 5), "a delete confirmation should appear")
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

    /// First element with an exact accessibility label, regardless of element type.
    /// SwiftUI renders a `Label(_:systemImage:)` as different element types across
    /// idioms, so matching on the label text is more durable than on `.staticTexts`.
    @MainActor
    private func firstElement(in app: XCUIApplication, labelled label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    @MainActor
    func testLaunchPerformance() throws {
        measure(metrics: [XCTApplicationLaunchMetric()]) {
            XCUIApplication().launch()
        }
    }
}
