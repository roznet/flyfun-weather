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

    @MainActor
    func testLaunch() throws {
        let app = XCUIApplication()
        app.launch()

        // Insert steps here to perform after app launch but before taking a screenshot,
        // such as logging into a test account or navigating somewhere in the app

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Launch Screen"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
