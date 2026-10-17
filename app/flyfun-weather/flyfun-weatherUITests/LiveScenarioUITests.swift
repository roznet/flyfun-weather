import XCTest
import UIKit

/// The LELL→LEMI live scenario's ticks (see `runLellLemiTick`). Four ticks,
/// each carrying something the others don't: 05:10 the live-start baseline,
/// 07:10 trails + cleared rows after the switch to the briefing baseline,
/// 08:30 the second cross-section reading, 09:00 two merged NEW changes at
/// once. 09:00 sits in `ObservedUITests` to balance the classes. 06:00 (a wind
/// change — rows render kind-agnostically) and 10:20 (09:00's kinds again)
/// were dropped.
final class LiveScenarioUITests: FlyfunUITestCase {

    @MainActor
    func testLiveScenarioLellLemi0510() throws {
        try runLellLemiTick("0510")
    }

    /// #669: 07:10 exercises both trail cases checked per tick — the 06:00
    /// LEMI MVFR blip as a cleared row, and LEVC TS/CB on screen for the 2nd
    /// time — so those checks are not vacuous.
    @MainActor
    func testLiveScenarioLellLemi0710() throws {
        let at0710 = (try liveScenarioTick("2026-10-02_lell_lemi", "0710").body["changes"] as? [String: Any]) ?? [:]
        let cleared = (at0710["recently_cleared"] as? [[String: Any]]) ?? []
        XCTAssertTrue(cleared.contains { $0["key"] as? String == "metar:LEMI" })
        let current = (at0710["changes"] as? [[String: Any]]) ?? []
        XCTAssertTrue(current.contains { (($0["trail"] as? [String: Any])?["times_today"] as? Int ?? 0) >= 2 })
        try runLellLemiTick("0710")
    }

    @MainActor
    func testLiveScenarioLellLemi0830() throws {
        try runLellLemiTick("0830")
    }
}
