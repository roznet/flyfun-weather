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

    @MainActor
    override func setUpWithError() throws {
        continueAfterFailure = false
        // Pin the orientation instead of inheriting whatever the simulator was
        // last left in. Device orientation persists in the simulator's own
        // state, so it is an input to the test that nothing here was setting:
        // locally the devices happen to sit in portrait, while GitHub's runner
        // image hands out an iPhone 17 already in landscape (874x402).
        //
        // That is what made the first four nightlies fail the same four
        // journeys — deterministically, and not for the reason it looked like.
        // `Form` is backed by a lazy `List` (see `scrollToFormRow`), so halving
        // the height does not merely push the Route section off-screen: it
        // leaves `waypointsField` out of the accessibility tree altogether, and
        // "the form should appear" fails against a form that is plainly open.
        // Raising the timeout could never have fixed it, and didn't.
        //
        // Portrait is also the orientation these journeys are written for — the
        // iPad split-view handling in `revealFlightList` / `switchToBriefingTab`
        // assumes it, and #494's one-line assertions only bite in the narrower
        // layout. Landscape is worth covering, but as its own deliberate test.
        XCUIDevice.shared.orientation = .portrait
    }

    /// How long to wait for something that *should* appear — a presented sheet,
    /// a pushed detail, a rendered section.
    ///
    /// Sized for the slowest machine that runs these, not the fastest: GitHub's
    /// shared macOS runners are several times slower than local Apple silicon.
    /// Waiting longer is close to free, because `waitForExistence` returns the
    /// moment the element exists; the cost is paid only by a test that was
    /// going to fail anyway.
    ///
    /// This value was raised from 5s to 20s to fix four nightly failures it
    /// turned out not to cause — those were the landscape simulator described
    /// in `setUpWithError`, and stayed red at 20s. Kept anyway, on its own
    /// merits, but the episode is the argument for reading the result bundle's
    /// element tree before assuming a red CI-only test is just slow.
    private static let uiTimeout: TimeInterval = 20

    /// Wait for a "is it already on screen?" probe, where *not* finding it is a
    /// normal outcome that selects another path (iPad's sidebar toggle). Kept
    /// short on purpose: `uiTimeout` here would add its full duration to every
    /// run that legitimately takes the other branch.
    private static let probeTimeout: TimeInterval = 8

    /// Launch the app as a UI test would: fake-authenticated + fixture-backed.
    /// `offline: true` also sets `FLYFUN_MOCK_OFFLINE` so the fixtures present as
    /// a cached list (offline banner + read-only rows) for the offline journey.
    /// `environment` adds launch variables (e.g. `FLYFUN_MOCK_LIVE_JSON`).
    @MainActor
    private func launchMockApp(offline: Bool = false, environment: [String: String] = [:]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["FLYFUN_UITEST"] = "1"
        app.launchEnvironment["FLYFUN_MOCK"] = "1"
        if offline { app.launchEnvironment["FLYFUN_MOCK_OFFLINE"] = "1" }
        for (key, value) in environment { app.launchEnvironment[key] = value }
        app.launch()
        return app
    }

    /// A briefing is open once its internal tab bar is present. The tab titles
    /// (Advisory / Cross-Section / …) render as buttons on both idioms — an
    /// iPhone bottom `Tab`, an iPad top tab bar — so this is the idiom-agnostic
    /// "briefing loaded" signal (replaces keying off localized header text).
    /// Keyed on Advisory, which is the first tab and so never paginated away.
    @MainActor
    private func waitForBriefingLoaded(_ app: XCUIApplication) {
        XCTAssertTrue(app.buttons["Advisory"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
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
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "fixture-1 should be listed")
        card.tap()
        waitForBriefingLoaded(app)
    }

    /// #616: feedback sent from a briefing is the linked form, reached from the
    /// pack menu, and submits (the fixture repository accepts it).
    @MainActor
    func testBriefingFeedbackFromPackMenu() throws {
        let app = launchMockApp()
        openFixture1Briefing(app)

        let packMenu = app.buttons["packToolbarMenu"].firstMatch
        XCTAssertTrue(packMenu.waitForExistence(timeout: Self.uiTimeout), "the pack menu should be in the toolbar")
        packMenu.tap()

        let feedback = app.buttons["briefingFeedbackButton"].firstMatch
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

    /// Switch the open briefing to a named internal tab. Both idioms render a
    /// native `TabView` (#437): a bottom tab bar on iPhone, a top tab bar on
    /// iPad. XCUI surfaces the iPad one as plain buttons rather than a
    /// `tabBars` element, so try the tab bar first and fall back to a button.
    @MainActor
    private func switchToBriefingTab(_ app: XCUIApplication, _ title: String) {
        // The bottom tab bar on iPhone; a plain button in the iPad top bar —
        // which has no TabBar element, so waiting for one there only burns 3 s
        // per switch, and the live scenario switches on every tick.
        var tab = app.tabBars.buttons[title]
        if UIDevice.current.userInterfaceIdiom == .pad || !tab.waitForExistence(timeout: 3) {
            tab = app.buttons[title].firstMatch
        }
        // iPad: when the split view's detail column is narrow — portrait with
        // the sidebar showing, which is how these journeys run — the top tab
        // bar paginates, leaving the later tabs (Cross-Section, Map) behind a
        // "Next Page" chevron. Page forward until the wanted tab surfaces,
        // exactly as a pilot on an 11-inch would. Hiding the sidebar would also
        // widen the bar, but paging is what the app actually asks of the user.
        // A tab can also *exist* yet sit half under the chevron (the last one
        // on a page — Observed on an 11-inch); its centre tap then misses, so
        // keep paging until it clears the chevron. The bar keeps its page, so
        // a tab left behind by an earlier switch (Advisory after Observed) is
        // reached by paging back once forward runs out.
        func hiddenBehind(_ chevron: String) -> Bool {
            let c = app.buttons[chevron]
            return c.exists && tab.frame.maxX > c.frame.minX && tab.frame.minX < c.frame.maxX
        }
        // Only the first look waits — the bar may still be appearing after a
        // navigation. After a page tap XCUI has already waited for the app to
        // idle, so a plain `exists` answers; a 2 s wait per page on a tab that
        // is on another page was most of the ~30 s each live-scenario tick
        // spent paging on iPad.
        var firstLook = true
        func visible() -> Bool {
            let present = firstLook ? tab.waitForExistence(timeout: 2) : tab.exists
            firstLook = false
            return present && !hiddenBehind("Next Page") && !hiddenBehind("Previous Page")
        }
        for chevron in ["Next Page", "Previous Page"] {
            var pages = 0
            while !visible() && pages < 4 {
                let button = app.buttons[chevron]
                guard button.exists else { break }
                button.tap()
                pages += 1
                tab = app.buttons[title].firstMatch
            }
        }
        XCTAssertTrue(tab.waitForExistence(timeout: Self.uiTimeout), "\(title) tab should be present")
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
        let list = app.descendants(matching: .any)["flightList"]
        // iPad portrait: the list sits behind the split-view toggle — a system
        // control with no stable identifier we can set, matched by its (English)
        // label. CI runs English sims.
        let showSidebar = app.buttons["Show Sidebar"]
        // Whichever surfaces first decides the path. Waiting on the list alone
        // spent the whole probe (8 s) on every iPad-portrait launch, where the
        // list is never there — ~32 s of the live scenario's four relaunches.
        switch firstToAppear([list, showSidebar]) {
        case 0: return
        case 1: showSidebar.tap()
        default: XCTFail("Could not reveal flight list: neither the list nor the sidebar toggle was found")
        }
    }

    /// Index of the first of `elements` to exist, polled together for up to
    /// `probeTimeout`; nil if none does. For "which path is this?" branches,
    /// where waiting on one candidate first burns the whole probe whenever the
    /// other is the answer — a wait on an element that will never come is
    /// also what runs into XCUI's snapshot timeout on CI's slower simulators.
    @MainActor
    private func firstToAppear(_ elements: [XCUIElement]) -> Int? {
        let deadline = Date().addingTimeInterval(Self.probeTimeout)
        repeat {
            if let i = elements.firstIndex(where: \.exists) { return i }
            Thread.sleep(forTimeInterval: 0.25)
        } while Date() < deadline
        return nil
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

    /// Get back to the flight list after a Move / Duplicate. Both select the new
    /// flight, so iPhone pushes its briefing on top of the list; iPad keeps the
    /// list in the split view and this is a no-op. The back button carries the
    /// sidebar's title ("Flights") — same English-label assumption as the
    /// "Show Sidebar" fallback above.
    @MainActor
    private func returnToFlightList(_ app: XCUIApplication) {
        // Either the list is already showing (iPad) or the pushed briefing's
        // back button is (iPhone) — whichever comes first, not the list's full
        // probe first (8 s on every iPhone run).
        let list = app.descendants(matching: .any)["flightList"]
        let back = app.navigationBars.buttons["Flights"].firstMatch
        if firstToAppear([list, back]) == 1 { back.tap() }
        revealFlightList(app)
    }

    /// Open the edit form for a listed flight via its trailing swipe action.
    @MainActor
    private func openEditForm(_ app: XCUIApplication, flightId: String) {
        revealFlightList(app)
        let card = app.descendants(matching: .any)["flightCard-\(flightId)"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "\(flightId) should be listed")
        card.swipeLeft()
        let edit = app.buttons["editFlightSwipeButton"].firstMatch
        XCTAssertTrue(edit.waitForExistence(timeout: Self.uiTimeout), "the Edit swipe action should appear")
        edit.tap()
        XCTAssertTrue(app.textFields["waypointsField"].waitForExistence(timeout: Self.uiTimeout),
                      "the edit form should appear")
    }

    /// Scroll a `Form` row into view. `Form` is backed by a lazy `List`, so a
    /// section below the fold is not merely off-screen — it is absent from the
    /// accessibility tree entirely, and `waitForExistence` on it fails no matter
    /// how long it waits. Swipe until it materializes.
    @MainActor
    private func scrollToFormRow(_ app: XCUIApplication, _ element: XCUIElement, maxSwipes: Int = 8) {
        var swipes = 0
        while !element.exists && swipes < maxSwipes {
            app.swipeUp()
            swipes += 1
        }
    }

    /// Pick a value from a `.menu`-style SwiftUI `Picker`, addressed by the
    /// accessibility identifier set on it (the rendered *label* folds in the
    /// current value, so it is not a stable selector).
    @MainActor
    private func selectFromMenuPicker(_ app: XCUIApplication, identifier: String, value: String) {
        let picker = app.buttons[identifier].firstMatch
        scrollToFormRow(app, picker)
        XCTAssertTrue(picker.waitForExistence(timeout: Self.uiTimeout), "the \(identifier) picker should be present")
        picker.tap()
        let option = app.buttons[value].firstMatch
        XCTAssertTrue(option.waitForExistence(timeout: Self.uiTimeout), "\(value) should be offered by \(identifier)")
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
    /// as "no echoes". iPhone + iPad.
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

        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = "RouteMap-Observed-Mock"
        shot.lifetime = .keepAlways
        add(shot)

        menu.tap()
        XCTAssertTrue(app.buttons["Satellite infrared"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the menu should offer the satellite underlay toggle")
        let open = XCTAttachment(screenshot: app.screenshot())
        open.name = "RouteMap-Observed-Menu-Mock"
        open.lifetime = .keepAlways
        add(open)
    }

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
           "stations": [], "sigmets": []},
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

        XCTAssertTrue(app.descendants(matching: .any)["observedNutshell"].waitForExistence(timeout: Self.uiTimeout),
                      "the Observed tab should open on the server's nutshell")
        XCTAssertTrue(app.staticTexts["Observed 08:10Z · as briefed"].firstMatch.exists, "the headline should render")
        XCTAssertTrue(app.descendants(matching: .any)["observedRibbon"].firstMatch.exists, "the route ribbon should render")
        attachScreenshot(app, "Observed-Nutshell")

        // A storm on the ribbon opens its detail; the estimate is labelled.
        let storm = app.descendants(matching: .any)["ribbonStorm-core35-zz"].firstMatch
        XCTAssertTrue(storm.waitForExistence(timeout: Self.uiTimeout), "the storm should be on the ribbon")
        storm.tap()
        let detail = app.descendants(matching: .any)["stormDetail"].firstMatch
        XCTAssertTrue(detail.waitForExistence(timeout: Self.uiTimeout), "tapping a storm should open its detail")
        // The sheet opens at the medium detent and is a lazy List: the estimate
        // section below the fold is not built (absent from the a11y tree) until
        // the sheet is expanded.
        let estimate = app.staticTexts["Estimate at current motion"].firstMatch
        let estimateCaps = app.staticTexts["ESTIMATE AT CURRENT MOTION"].firstMatch
        if !estimate.exists && !estimateCaps.exists { detail.swipeUp() }
        XCTAssertTrue(estimate.waitForExistence(timeout: Self.uiTimeout) || estimateCaps.exists,
                      "the estimate should sit under its own label")
        attachScreenshot(app, "Observed-StormDetail")
        let showOnMap = app.buttons["stormShowOnMap"].firstMatch
        if !showOnMap.isHittable { app.swipeUp() }
        showOnMap.tap()
        XCTAssertTrue(app.buttons["map.observedMenu"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "Show on map should land on the Map tab with the observed controls")
        attachScreenshot(app, "Observed-StormOnMap")

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

    @MainActor
    private func attachScreenshot(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
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

        XCTAssertTrue(app.descendants(matching: .any)["offlineBanner"].waitForExistence(timeout: Self.uiTimeout),
                      "offline banner should show when the list is served from cache")

        // The offline-ready flight is still listed and openable from cache.
        let card = app.descendants(matching: .any)["flightCard-fixture-1"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "cached flight should be listed offline")
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

    /// First element with an exact accessibility label, regardless of element type.
    /// SwiftUI renders a `Label(_:systemImage:)` as different element types across
    /// idioms, so matching on the label text is more durable than on `.staticTexts`.
    @MainActor
    private func firstElement(in app: XCUIApplication, labelled label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
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

    // MARK: - Live layer: a real flight morning, tick by tick (#637, §36)

    /// One tick of a frozen flight morning, as the `/live` body the server
    /// produced for it (tests/fixtures/live_scenarios, exported by
    /// scripts/export_live_scenario_ios.py; the Python suite fails if these
    /// drift from the server's rules).
    private func liveScenarioTick(_ scenario: String, _ hhmm: String) throws -> (json: String, body: [String: Any]) {
        let bundle = Bundle(for: flyfun_weatherUITests.self)
        let name = "\(scenario)_\(hhmm)"
        let url = try XCTUnwrap(
            bundle.url(forResource: name, withExtension: "json")
                ?? bundle.url(forResource: name, withExtension: "json", subdirectory: "LiveScenarios"),
            "missing UI-test fixture \(name).json — run scripts/export_live_scenario_ios.py"
        )
        let data = try Data(contentsOf: url)
        let body = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        return (String(decoding: data, as: UTF8.self), body)
    }

    /// Every change the server reported renders as a row, at its tier: an alert
    /// must look like one, a highlight must not. Screenshots the panel and the
    /// Observations / Hazards tables at `screenshotTick` only: six near-identical
    /// sets added ~25 s a tick for a reader who looks at one. The other ticks
    /// keep every assertion and scroll to the Hazards table only when they
    /// issued a SIGMET, for its NEW badges.
    ///
    /// The mock flight is fixture-1 (LFMD→LFML) — its header says so — but the
    /// live layer replaces the observation and SIGMET tables wholesale, so those
    /// show the scenario's real corridor airports and SIGMETs.
    ///
    /// `conditions` names the ticks at which to open the Cross-Section tab, turn
    /// the current-conditions layer on (it is off by default) and check the
    /// canvas reports the expected SIGMET zones / METAR columns — the summary
    /// the canvas carries as its accessibility value while the layer is drawn.
    /// Returns the summary read at each of those ticks.
    @MainActor
    @discardableResult
    private func runLiveScenario(
        _ scenario: String, ticks: [String], conditions: [String: String] = [:],
        screenshotTick: String? = nil
    ) throws -> [String: String] {
        var readSummaries: [String: String] = [:]
        for hhmm in ticks {
            try XCTContext.runActivity(named: "\(scenario) \(hhmm)Z") { _ in
                let tick = try liveScenarioTick(scenario, hhmm)
                let app = launchMockApp(environment: ["FLYFUN_MOCK_LIVE_JSON": tick.json])
                defer { app.terminate() }
                let shoot = hhmm == screenshotTick
                openFixture1Briefing(app)
                switchToBriefingTab(app, "Observed")

                let section = app.descendants(matching: .any)["liveChangesSection"]
                XCTAssertTrue(section.waitForExistence(timeout: Self.uiTimeout),
                              "\(hhmm): the live changes panel should render")

                // #690: the nutshell is the server's text, word for word.
                if let glance = tick.body["glance"] as? [String: Any] {
                    XCTAssertTrue(app.descendants(matching: .any)["observedNutshell"].firstMatch.exists,
                                  "\(hhmm): the nutshell should replace the glance card")
                    for line in (glance["lines"] as? [[String: Any]]) ?? [] {
                        let phase = line["phase"] as? String ?? ""
                        let text = line["text"] as? String ?? ""
                        let row = app.descendants(matching: .any)["observedNutshellLine-\(phase)"].firstMatch
                        XCTAssertTrue(row.waitForExistence(timeout: Self.uiTimeout),
                                      "\(hhmm): the \(phase) nutshell line should render")
                        XCTAssertTrue(row.label.contains(text),
                                      "\(hhmm): the \(phase) line should read \"\(text)\", got \(row.label)")
                    }
                    if shoot { attachScreenshot(app, "Live-\(scenario)-\(hhmm)-0-nutshell") }
                }

                let changes = (tick.body["changes"] as? [String: Any]) ?? [:]
                let title = (changes["baseline_source"] as? String) == "live_start"
                    ? "Since live tracking began" : "Since this briefing"
                XCTAssertTrue(app.staticTexts[title].firstMatch.exists, "\(hhmm): panel title should read \"\(title)\"")
                for change in (changes["changes"] as? [[String: Any]]) ?? [] {
                    let key = change["key"] as? String ?? ""
                    let expected = "\(change["tier"] as? String ?? ""), \(change["direction"] as? String ?? "")"
                    let rows = app.descendants(matching: .any).matching(identifier: "liveChangeRow-\(key)")
                    XCTAssertTrue(rows.firstMatch.waitForExistence(timeout: Self.uiTimeout),
                                  "\(hhmm): \(change["message"] ?? key) should be listed")
                    let values = rows.allElementsBoundByIndex.compactMap { $0.value as? String }
                    XCTAssertTrue(values.contains(expected),
                                  "\(hhmm): \(change["message"] ?? key) should render as \(expected), got \(values)")
                    // #669: a change on screen for the Nth time says so.
                    if let trail = change["trail"] as? [String: Any], (trail["times_today"] as? Int ?? 0) >= 2 {
                        let labels = rows.allElementsBoundByIndex.map(\.label)
                        XCTAssertTrue(labels.contains { $0.contains("time today") },
                                      "\(hhmm): \(change["message"] ?? key) should carry its trail line, got \(labels)")
                    }
                }
                // #669: every recently cleared change is listed, plainly, with
                // when it cleared.
                for row in (changes["recently_cleared"] as? [[String: Any]]) ?? [] {
                    let key = row["key"] as? String ?? ""
                    let element = app.descendants(matching: .any).matching(identifier: "liveClearedRow-\(key)").firstMatch
                    XCTAssertTrue(element.waitForExistence(timeout: Self.uiTimeout),
                                  "\(hhmm): cleared \(row["message"] ?? key) should be listed")
                    XCTAssertTrue(element.label.contains("cleared"),
                                  "\(hhmm): cleared \(row["message"] ?? key) should say when it cleared, got \(element.label)")
                    XCTAssertEqual(element.value as? String, "cleared, \(row["direction"] as? String ?? "")",
                                   "\(hhmm): a cleared row is never alert-styled")
                }
                if shoot { attachScreenshot(app, "Live-\(scenario)-\(hhmm)-1-changes") }

                // Every SIGMET new to the flight carries the NEW badge: the
                // server's `new_sigmets` (#689: its chain did not start in the
                // briefing, so a reissue of a briefed SIGMET is not NEW).
                let issued = (changes["new_sigmets"] as? [String]) ?? []
                for (pill, sectionId, label) in [("METAR/TAF", "observationsSection", "2-observations"),
                                                 ("SIGMET", "sigmetsSection", "3-hazards")] {
                    guard shoot || (sectionId == "sigmetsSection" && !issued.isEmpty) else { continue }
                    let button = app.buttons[pill].firstMatch
                    guard button.waitForExistence(timeout: Self.probeTimeout) else { continue }
                    button.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                    let target = app.descendants(matching: .any)[sectionId].firstMatch
                    if target.waitForExistence(timeout: Self.uiTimeout) && shoot {
                        // The pill scrolls with an animation: wait for the
                        // section to settle on screen before the screenshot.
                        _ = XCTWaiter.wait(for: [XCTNSPredicateExpectation(
                            predicate: NSPredicate(format: "isHittable == true"), object: target)], timeout: 5)
                        Thread.sleep(forTimeInterval: 0.8)
                        attachScreenshot(app, "Live-\(scenario)-\(hhmm)-\(label)")
                    }
                    if sectionId == "sigmetsSection" {
                        let badges = app.descendants(matching: .any)
                            .matching(NSPredicate(format: "label CONTAINS[c] %@", "Issued since the briefing"))
                        XCTAssertEqual(badges.count, issued.count,
                                       "\(hhmm): each newly issued SIGMET should be badged NEW in the hazards table")
                    }
                }

                // The digest caveat and the teaser stay on the Advisory tab (#661).
                switchToBriefingTab(app, "Advisory")
                XCTAssertTrue(app.descendants(matching: .any)["observedTeaser"].waitForExistence(timeout: Self.uiTimeout),
                              "\(hhmm): the Advisory tab should carry the Observed teaser row")
                // "Written at" is when the digest was written: the briefing the
                // changes are measured from, or — when they run from the live
                // layer's own starting point — the pack (fixture-1: 06:00Z),
                // never the live start. Matched on its text: the caveat's id
                // also lands on its icon.
                let fromLiveStart = (changes["baseline_source"] as? String) == "live_start"
                let baselineHHMM = (changes["baseline_at"] as? String).map { String($0.dropFirst(11).prefix(5)) + "Z" }
                let written = fromLiveStart ? "06:00Z" : (baselineHHMM ?? "06:00Z")
                let caveat = app.staticTexts
                    .matching(NSPredicate(format: "label BEGINSWITH %@", "Written at")).firstMatch
                if caveat.exists {
                    XCTAssertTrue(caveat.label.contains("Written at \(written)"),
                                  "\(hhmm): digest caveat should read \"Written at \(written)\", got \(caveat.label)")
                }

                if let expected = conditions[hhmm] {
                    let summary = currentConditionsSummary(app, label: "Live-\(scenario)-\(hhmm)-4-cross-section")
                    XCTAssertEqual(summary, expected,
                                   "\(hhmm): the current-conditions layer should draw the tick's SIGMETs and METARs")
                    readSummaries[hhmm] = summary
                }
            }
        }
        return readSummaries
    }

    /// Open the Cross-Section tab, make sure the current-conditions layer is on
    /// (its pill sits in the Observed family's detail row — the family chip
    /// alone would only bring back the default-on observed lines), and return
    /// the canvas's accessibility value: "N SIGMET zones, M METAR columns"
    /// while the layer is drawn, empty while it is off. Idempotent: the layer
    /// state persists across launches, so a later tick may find it already on.
    @MainActor
    private func currentConditionsSummary(_ app: XCUIApplication, label: String) -> String {
        switchToBriefingTab(app, "Cross-Section")
        let canvas = app.descendants(matching: .any)["crossSectionCanvas"].firstMatch
        XCTAssertTrue(canvas.waitForExistence(timeout: Self.uiTimeout), "cross-section canvas should render")

        if ((canvas.value as? String) ?? "").isEmpty {
            let chip = app.descendants(matching: .any)["layerFamily-observed"].firstMatch
            XCTAssertTrue(chip.waitForExistence(timeout: Self.uiTimeout),
                          "the Observed family should be on the bar when the live layer carries METARs/SIGMETs")
            if !chip.isHittable { canvas.swipeUp() }  // iPhone: the chips sit under the chart
            if UIDevice.current.userInterfaceIdiom == .pad {
                chip.tap()  // full chip: opens the family's detail row
            } else {
                chip.press(forDuration: 0.8)  // compact chip: hold opens the methods row
            }
            let pill = app.descendants(matching: .any)["layerPill-current-conditions"].firstMatch
            XCTAssertTrue(pill.waitForExistence(timeout: Self.uiTimeout),
                          "the Observed detail row should offer the current-conditions pill")
            pill.tap()
            let drawn = XCTNSPredicateExpectation(
                predicate: NSPredicate(format: "value CONTAINS %@", "SIGMET zone"), object: canvas)
            XCTAssertEqual(XCTWaiter.wait(for: [drawn], timeout: Self.uiTimeout), .completed,
                           "turning the pill on should draw the current-conditions layer")
        }
        Thread.sleep(forTimeInterval: 0.8)
        attachScreenshot(app, label)
        return (canvas.value as? String) ?? ""
    }

    /// 2026-10-02 LELL→LEMI (dep 08:00Z): LEVC thunderstorms under the route,
    /// a one-report MVFR at the destination, LECB 3 / LECM 3 EMBD TS at LEMI.
    @MainActor
    func testLiveScenarioLellLemi() throws {
        // Current conditions on the cross-section (#641). Counts are what the
        // ticks' `route_sigmets` / `route_observations` carry with an enroute
        // span / a category + enroute distance: at 05:10 one LECB EMBD TS; by
        // 08:30 a second LECB and a LECM EMBD TS at LEMI, and LECH (LIFR) has
        // started reporting. The live layer must reach the chart, not just the
        // tables.
        let summaries = try runLiveScenario(
            // Four ticks, each carrying something the others don't: 05:10 the
            // live-start baseline, 07:10 trails + cleared rows after the switch
            // to the briefing baseline, 08:30 the second cross-section reading,
            // 09:00 two merged NEW changes at once — LECB 3 + LECM 3, and the
            // LECB 2 → 4 reissue shown as a replacement — three badges, two
            // trails. 06:00 (a wind change — rows render kind-agnostically) and
            // 10:20 (09:00's kinds again) were dropped; each tick is a full
            // relaunch, ~45-75 s.
            "2026-10-02_lell_lemi", ticks: ["0510", "0710", "0830", "0900"],
            conditions: [
                "0510": "1 SIGMET zone, 8 METAR columns",
                "0830": "3 SIGMET zones, 9 METAR columns",
            ],
            screenshotTick: "0710")
        XCTAssertNotEqual(summaries["0510"], summaries["0830"],
                          "the SIGMET zones drawn at 08:30 should differ from 05:10")
        // #669: the 07:10 tick exercises both trail cases checked per tick
        // above — the 06:00 LEMI MVFR blip as a cleared row, and LEVC TS/CB
        // on screen for the 2nd time — so those checks are not vacuous.
        let at0710 = (try liveScenarioTick("2026-10-02_lell_lemi", "0710").body["changes"] as? [String: Any]) ?? [:]
        let cleared = (at0710["recently_cleared"] as? [[String: Any]]) ?? []
        XCTAssertTrue(cleared.contains { $0["key"] as? String == "metar:LEMI" })
        let current = (at0710["changes"] as? [[String: Any]]) ?? []
        XCTAssertTrue(current.contains { (($0["trail"] as? [String: Any])?["times_today"] as? Int ?? 0) >= 2 })
    }
}
