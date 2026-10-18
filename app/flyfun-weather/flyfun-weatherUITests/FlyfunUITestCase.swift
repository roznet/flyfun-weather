//
//  FlyfunUITestCase.swift
//  flyfun-weatherUITests
//
//  XCUITest journeys (#314). Launched in mock mode (FLYFUN_UITEST + FLYFUN_MOCK)
//  so they skip the auth gate and serve fixtures — deterministic, offline, no
//  backend or OAuth. Selectors key off accessibilityIdentifiers, not visible
//  text, so they survive copy/localization changes.
//
//  This file is the shared base: setup, timeouts and the navigation helpers.
//  The journeys live in four subclasses (LiveScenario / Observed / Briefing /
//  Flights). Parallel testing hands out whole classes to simulator clones, so
//  one class of every journey ran them one after another on a single clone;
//  the four are balanced by measured duration (~190–200 s each on an iPad,
//  2026-10-17) so the clones finish together. Keep them balanced when adding a
//  long journey — the slowest class is the pass's wall clock.
//

import XCTest
import UIKit

class FlyfunUITestCase: XCTestCase {

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
    static let uiTimeout: TimeInterval = 20

    /// Wait for a "is it already on screen?" probe, where *not* finding it is a
    /// normal outcome that selects another path (iPad's sidebar toggle). Kept
    /// short on purpose: `uiTimeout` here would add its full duration to every
    /// run that legitimately takes the other branch.
    static let probeTimeout: TimeInterval = 8

    /// How long to let a navigation queued behind a sheet's dismissal land
    /// before believing what the probe just saw. See `returnToFlightList`.
    static let settleInterval: TimeInterval = 1.5

    /// Launch the app as a UI test would: fake-authenticated + fixture-backed.
    /// `offline: true` also sets `FLYFUN_MOCK_OFFLINE` so the fixtures present as
    /// a cached list (offline banner + read-only rows) for the offline journey.
    /// `environment` adds launch variables (e.g. `FLYFUN_MOCK_LIVE_JSON`).
    @MainActor
    func launchMockApp(offline: Bool = false, environment: [String: String] = [:]) -> XCUIApplication {
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
    func waitForBriefingLoaded(_ app: XCUIApplication) {
        XCTAssertTrue(app.buttons["Advisory"].firstMatch.waitForExistence(timeout: Self.uiTimeout),
                      "the briefing (with its Advisory tab) should be shown")
    }

    /// Open `fixture-1`'s briefing: reveal the list, tap the seeded flight, and
    /// wait for the briefing tabs. Works on both idioms — iPhone pushes the
    /// detail, iPad selects it in the split view.
    @MainActor
    func openFixture1Briefing(_ app: XCUIApplication) {
        revealFlightList(app)
        // `.firstMatch`: SwiftUI propagates the card's accessibilityIdentifier to
        // its child nodes, so the id resolves to several elements — fine for
        // `waitForExistence`, but `.tap()` needs a single element.
        let card = app.descendants(matching: .any)["flightCard-fixture-1"].firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: Self.uiTimeout), "fixture-1 should be listed")
        card.tap()
        waitForBriefingLoaded(app)
    }

    /// Switch the open briefing to a named internal tab. Both idioms render a
    /// native `TabView` (#437): a bottom tab bar on iPhone, a top tab bar on
    /// iPad. XCUI surfaces the iPad one as plain buttons rather than a
    /// `tabBars` element, so try the tab bar first and fall back to a button.
    @MainActor
    func switchToBriefingTab(_ app: XCUIApplication, _ title: String) {
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
    func revealFlightList(_ app: XCUIApplication) {
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
    func firstToAppear(_ elements: [XCUIElement]) -> Int? {
        let deadline = Date().addingTimeInterval(Self.probeTimeout)
        repeat {
            if let i = elements.firstIndex(where: \.exists) { return i }
            Thread.sleep(forTimeInterval: 0.25)
        } while Date() < deadline
        return nil
    }

    /// Get back to the flight list after a Move / Duplicate. Both select the new
    /// flight, so iPhone pushes its briefing on top of the list; iPad keeps the
    /// list in the split view and this is a no-op. The back button carries the
    /// sidebar's title ("Flights") — same English-label assumption as the
    /// "Show Sidebar" fallback above.
    ///
    /// Seeing the list is not enough to conclude we have arrived. Move and
    /// Duplicate dismiss the form first and select the new flight only once the
    /// reload behind `onCreated` lands, so the list is on screen for a few
    /// hundred milliseconds *before* the briefing is pushed over it. A probe
    /// that catches that window returns "already there", the push follows, and
    /// the caller is left on a briefing — which is how the Duplicate journey
    /// failed in the 2026-10-07 nightly, 470 ms after `waypointsField` went
    /// away. So re-probe after a settle, and take the back button on any round
    /// it is offered.
    @MainActor
    func returnToFlightList(_ app: XCUIApplication) {
        // Either the list is already showing (iPad) or the pushed briefing's
        // back button is (iPhone) — whichever comes first, not the list's full
        // probe first (8 s on every iPhone run).
        let list = app.descendants(matching: .any)["flightList"]
        let back = app.navigationBars.buttons["Flights"].firstMatch
        // Three rounds covers the worst order: transient list, then the pushed
        // briefing, then the list again for good after tapping back.
        for _ in 0..<3 {
            switch firstToAppear([list, back]) {
            case 0:
                Thread.sleep(forTimeInterval: Self.settleInterval)
                if list.exists { revealFlightList(app); return }
            case 1:
                back.tap()
            default:
                break
            }
        }
        revealFlightList(app)
    }

    /// Open the edit form for a listed flight via its trailing swipe action.
    @MainActor
    func openEditForm(_ app: XCUIApplication, flightId: String) {
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
    /// Swipe `element` into view, then tap it once the scroll has stopped:
    /// a tap that lands while a swipe is still decelerating only stops the
    /// scroll, so the control never sees it (#748: "More" stayed collapsed).
    /// An element behind the iPhone's bottom tab bar still reports hittable,
    /// but its tap lands on the bar. A fling overshoots it under the sticky
    /// section chips at the top, so drag it a third of the screen instead.
    @MainActor
    func tapAfterScrolling(_ app: XCUIApplication, _ element: XCUIElement, up: Bool = true) {
        let tabBar = app.tabBars.firstMatch
        let underTabBar = tabBar.exists && tabBar.frame.minY > app.frame.midY
            && element.frame.midY > tabBar.frame.minY
        if underTabBar {
            let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
            from.press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)))
            Thread.sleep(forTimeInterval: 1.0)
        } else if !element.isHittable {
            if up { app.swipeUp() } else { app.swipeDown() }
            Thread.sleep(forTimeInterval: 1.0)
        }
        element.tap()
    }

    @MainActor
    func scrollToFormRow(_ app: XCUIApplication, _ element: XCUIElement, maxSwipes: Int = 8) {
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
    func selectFromMenuPicker(_ app: XCUIApplication, identifier: String, value: String) {
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
    func replaceText(_ field: XCUIElement, with text: String) {
        field.tap()
        let existing = (field.value as? String) ?? ""
        if !existing.isEmpty {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: existing.count))
        }
        field.typeText(text)
    }

    /// Open the Observed tab's "Details" fold (#697) unless it already is:
    /// the expanded state is remembered across launches, so read the toggle's
    /// value before tapping rather than toggling blind.
    @MainActor
    func openObservedDetails(_ app: XCUIApplication) {
        let toggle = app.buttons["observedDetailsToggle"].firstMatch
        guard toggle.waitForExistence(timeout: Self.uiTimeout) else {
            XCTFail("the Observed tab should have a Details fold")
            return
        }
        guard (toggle.value as? String) != "expanded" else { return }
        // Below the ribbon: bring it on screen before tapping.
        for _ in 0..<4 where !toggle.isHittable { app.swipeUp() }
        // The value flips inside an animation, and a tap landing while the
        // swipe's scroll is still settling can be dropped: wait for it, and
        // tap once more only if it never opened.
        let expanded = { (timeout: TimeInterval) in XCTWaiter.wait(for: [XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == %@", "expanded"), object: toggle)], timeout: timeout) }
        toggle.tap()
        if expanded(5) != .completed {
            toggle.tap()
            XCTAssertEqual(expanded(Self.uiTimeout), .completed, "tapping Details should open it")
        }
    }

    @MainActor
    func attachScreenshot(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// First element with an exact accessibility label, regardless of element type.
    /// SwiftUI renders a `Label(_:systemImage:)` as different element types across
    /// idioms, so matching on the label text is more durable than on `.staticTexts`.
    @MainActor
    func firstElement(in app: XCUIApplication, labelled label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    // MARK: - Live layer: a real flight morning, tick by tick (#637, §36)

    /// One tick of a frozen flight morning, as the `/live` body the server
    /// produced for it (tests/fixtures/live_scenarios, exported by
    /// scripts/export_live_scenario_ios.py; the Python suite fails if these
    /// drift from the server's rules).
    func liveScenarioTick(_ scenario: String, _ hhmm: String) throws -> (json: String, body: [String: Any]) {
        let bundle = Bundle(for: FlyfunUITestCase.self)
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
    func runLiveScenario(
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

                // #697: the nutshell lines and the non-alert change rows live
                // under the Details fold; alert ones stay just under the highlight.
                openObservedDetails(app)
                let section = app.descendants(matching: .any)["liveChangesSection"]
                XCTAssertTrue(section.waitForExistence(timeout: Self.uiTimeout),
                              "\(hhmm): the live changes panel should render")

                // #690: the nutshell is the server's text, word for word.
                if let glance = tick.body["glance"] as? [String: Any] {
                    XCTAssertTrue(app.descendants(matching: .any)["observedNutshell"].firstMatch.exists,
                                  "\(hhmm): the nutshell should replace the glance card")
                    // #816: the flight strip leads, and "Destination at your
                    // ETA" stands in for a non-alert arrival line.
                    if let flight = glance["flight"] as? [String: Any], let text = flight["text"] as? String {
                        let strip = app.descendants(matching: .any)["observedFlightStrip"].firstMatch
                        XCTAssertTrue(strip.exists, "\(hhmm): the flight strip should render")
                        XCTAssertTrue(strip.label.contains(text), "\(hhmm): the strip should read \"\(text)\", got \(strip.label)")
                    }
                    let hasDestination = glance["destination"] is [String: Any]
                    if hasDestination {
                        XCTAssertTrue(app.descendants(matching: .any)["observedDestination"].firstMatch.exists,
                                      "\(hhmm): the destination-at-ETA card should render unfolded")
                    }
                    for line in (glance["lines"] as? [[String: Any]]) ?? [] {
                        let phase = line["phase"] as? String ?? ""
                        let text = line["text"] as? String ?? ""
                        if hasDestination, phase == "arrival", line["alert"] as? Bool != true { continue }
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
    func currentConditionsSummary(_ app: XCUIApplication, label: String) -> String {
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

    /// A menu item by its identifier, else by its label. On iOS 27 some SwiftUI
    /// menu items (a Button with a Label or a Text) come through without their
    /// accessibilityIdentifier — the item is there, the lookup by id misses it —
    /// while iOS 26 keeps it.
    @MainActor
    func menuItem(_ app: XCUIApplication, _ identifier: String, label: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "identifier == %@ OR label == %@", identifier, label)).firstMatch
    }

    /// One tick of the 2026-10-02 LELL→LEMI scenario (dep 08:00Z): LEVC
    /// thunderstorms under the route, a one-report MVFR at the destination,
    /// LECB 3 / LECM 3 EMBD TS at LEMI. Each tick is its own test — every tick
    /// is a full relaunch (~45–75 s), and as one test the four were the longest
    /// journey by far, so no class split could balance around it.
    ///
    /// Current conditions on the cross-section (#641): counts are what the
    /// ticks' `route_sigmets` / `route_observations` carry with an enroute span
    /// / a category + enroute distance — at 05:10 one LECB EMBD TS; by 08:30 a
    /// second LECB and a LECM EMBD TS at LEMI, and LECH (LIFR) has started
    /// reporting. The live layer must reach the chart, not just the tables.
    @MainActor
    func runLellLemiTick(_ hhmm: String) throws {
        let conditions = [
            "0510": "1 SIGMET zone, 8 METAR columns",
            "0830": "3 SIGMET zones, 9 METAR columns",
        ]
        try runLiveScenario(
            "2026-10-02_lell_lemi", ticks: [hhmm],
            conditions: conditions.filter { $0.key == hhmm },
            screenshotTick: "0710")
    }
}
