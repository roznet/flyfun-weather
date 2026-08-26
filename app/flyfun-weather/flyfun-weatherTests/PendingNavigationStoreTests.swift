//
//  PendingNavigationStoreTests.swift
//  flyfun-weatherTests
//
//  `PendingNavigationStore` is process-global — static methods over one key in
//  `UserDefaults.standard` — and `take()` is destructive: it reads *and*
//  removes. swift-testing runs suites in parallel, so two tests driving the
//  shared store interleave as `set → set → take → take`, handing one test the
//  other's value and the second a `nil`. That is exactly what
//  ForecastMapTests.pendingNavigationRoundTripsForecastMap and
//  UniversalLinkRoutingTests.pendingNavigationRoundTripsShare did — observed
//  failing once, passing on the next run (#578).
//
//  The tests were wrong, not the store: overwriting an unconsumed target is
//  documented, correct behaviour for the cold-launch/warm-foreground path it
//  serves. So the store takes an injectable `UserDefaults` and each test uses
//  its own suite — genuinely independent rather than merely ordered, and the
//  shape this needs anyway if Widgets/Live Activities move it to an App Group.
//

import Foundation
import Testing
@testable import flyfun_weather

extension PendingNavigationStore {
    /// A store over a `UserDefaults` suite private to one test.
    ///
    /// Pass `#function` (the default) and the suite is named after the calling
    /// test, so two tests can never collide however they are scheduled.
    static func testStore(_ name: String = #function) -> PendingNavigationStore {
        guard let defaults = UserDefaults(suiteName: testSuiteName(name)) else {
            fatalError("could not open a defaults suite for \(name)")
        }
        return PendingNavigationStore(defaults: defaults)
    }

    /// Drop a test suite's contents so a rerun starts clean.
    static func removeTestStore(_ name: String = #function) {
        UserDefaults.standard.removePersistentDomain(forName: testSuiteName(name))
    }

    private static func testSuiteName(_ name: String) -> String {
        let slug = name.filter { $0.isLetter || $0.isNumber }
        return "aero.flyfun.weather.tests.pendingNavigation.\(slug)"
    }
}

@Suite("PendingNavigationStore")
struct PendingNavigationStoreTests {

    @Test func roundTripsEveryTarget() {
        let store = PendingNavigationStore.testStore()
        defer { PendingNavigationStore.removeTestStore() }

        let targets: [PendingNavigation] = [
            .flightList,
            .briefing(flightId: "abc"),
            .share(code: "aB3xy7Q9"),
            .forecastMap(MapDeepLink(day: 2, hour: 6, model: "gfs",
                                     metric: "ceiling_ft", airport: "EGLL")),
        ]
        for target in targets {
            store.set(target)
            #expect(store.take() == target)
        }
    }

    @Test func takeConsumesTheTarget() {
        let store = PendingNavigationStore.testStore()
        defer { PendingNavigationStore.removeTestStore() }

        store.set(.flightList)
        #expect(store.take() == .flightList)
        #expect(store.take() == nil, "take() reads and removes")
    }

    @Test func theMostRecentRequestWins() {
        // The documented behaviour the racing tests were mistaken for a bug:
        // an unconsumed target is overwritten, because the pilot's latest
        // request is the one the app should honour on next activation.
        let store = PendingNavigationStore.testStore()
        defer { PendingNavigationStore.removeTestStore() }

        store.set(.flightList)
        store.set(.briefing(flightId: "second"))
        #expect(store.take() == .briefing(flightId: "second"))
    }

    @Test func twoStoresDoNotSeeEachOthersTargets() {
        // The property that makes the two round-trip tests independent rather
        // than merely ordered — the fix for the race, asserted directly.
        let a = PendingNavigationStore.testStore("suiteA")
        let b = PendingNavigationStore.testStore("suiteB")
        defer {
            PendingNavigationStore.removeTestStore("suiteA")
            PendingNavigationStore.removeTestStore("suiteB")
        }

        a.set(.share(code: "aB3xy7Q9"))
        b.set(.flightList)

        #expect(a.take() == .share(code: "aB3xy7Q9"))
        #expect(b.take() == .flightList)
    }

    @Test func garbageInDefaultsDecodesToNil() {
        // The store parses a compact string, so a stale or hand-edited value
        // must not crash the launch path.
        let suite = "aero.flyfun.weather.tests.pendingNavigation.garbage"
        let defaults = UserDefaults(suiteName: suite)!
        defer { UserDefaults.standard.removePersistentDomain(forName: suite) }

        defaults.set("briefing:", forKey: "pendingNavigation")
        #expect(PendingNavigationStore(defaults: defaults).take() == nil)
        defaults.set("not-a-target", forKey: "pendingNavigation")
        #expect(PendingNavigationStore(defaults: defaults).take() == nil)
    }
}
