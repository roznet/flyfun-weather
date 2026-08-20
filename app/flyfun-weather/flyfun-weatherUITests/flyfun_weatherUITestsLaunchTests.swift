//
//  flyfun_weatherUITestsLaunchTests.swift
//  flyfun-weatherUITests
//
//  Created by Brice Rosenzweig on 01/03/2026.
//

import XCTest

final class flyfun_weatherUITestsLaunchTests: XCTestCase {

    override class var runsForEachTargetApplicationUIConfiguration: Bool {
        true
    }

    @MainActor
    override func setUpWithError() throws {
        continueAfterFailure = false
        // Same reason as the journey suite: the simulator's persisted
        // orientation is an input, and CI's is landscape. This test exists to
        // file a launch screenshot, so an orientation that drifts with the
        // machine makes the screenshots incomparable run to run.
        XCUIDevice.shared.orientation = .portrait
    }

    /// File a launch screenshot of the app in the same mode the journeys run in.
    ///
    /// `FLYFUN_UITEST` + `FLYFUN_MOCK` are not cosmetic here. A bare
    /// `XCUIApplication().launch()` — the Xcode template's version of this
    /// test, which is what this was — starts the real app: the auth gate,
    /// TipKit, and the four network calls `AppState.setupClient()` fires,
    /// including the multi-megabyte airports-DB download. `launch()` waits for
    /// the app to go quiescent, so on a shared runner that is a wait on
    /// production, and it was routinely 70-284s against a launch timeout it
    /// could not always beat. That is the whole of the 2026-08-15 and
    /// 2026-08-20 nightly failures, and the retry-flapping in between: every
    /// mocked journey in the same runs launched the app fine in 16-60s.
    ///
    /// The screenshot is better for it too — signed-in with the fixture
    /// flights, rather than whatever the sign-in gate happened to render.
    @MainActor
    func testLaunch() throws {
        let app = XCUIApplication()
        app.launchEnvironment["FLYFUN_UITEST"] = "1"
        app.launchEnvironment["FLYFUN_MOCK"] = "1"
        app.launch()

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Launch Screen"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
