//
//  WindFormatTests.swift
//  flyfun-weatherTests
//
//  Unit tests for the shared iOS wind formatter (WindFormat) — the standard
//  `dir@speedGgust` notation with a zero-padded 3-digit direction and the
//  gust-suppression rule. Mirrors the web `formatWind` tests in
//  web/tests/unit/units.test.ts so the two clients stay in sync.
//

import Foundation
import Testing
@testable import flyfun_weather

struct WindFormatTests {
    @Test func formatsDirSpeedGustWithPaddedDirection() {
        #expect(WindFormat.wind(speed: 15, dir: 273, gust: 22) == "273@15G22")
        #expect(WindFormat.wind(speed: 8, dir: 5, gust: nil) == "005@8")
    }

    @Test func dropsDirectionPrefixWhenMissingButKeepsSpeed() {
        #expect(WindFormat.wind(speed: 15, dir: nil, gust: nil) == "15")
    }

    @Test func returnsNilWhenSpeedMissing() {
        #expect(WindFormat.wind(speed: nil, dir: 270, gust: 30) == nil)
    }

    @Test func showsGustOnlyWhenItExceedsSpeedByThreshold() {
        let thr = Double(WindFormat.gustDisplayMinExcessKt)
        // +1 kt gust — suppressed
        #expect(WindFormat.wind(speed: 5, dir: 9, gust: 6) == "009@5")
        // exactly the threshold — shown
        #expect(WindFormat.wind(speed: 5, dir: 9, gust: 5 + thr) == "009@5G9")
        // well above — shown
        #expect(WindFormat.wind(speed: 5, dir: 9, gust: 10) == "009@5G10")
        // just below the threshold — suppressed
        #expect(WindFormat.wind(speed: 5, dir: 9, gust: 5 + thr - 1) == "009@5")
    }

    @Test func componentUsesTheSameGustRule() {
        #expect(WindFormat.component(12, gust: 18) == "12G18")
        #expect(WindFormat.component(6, gust: 6) == "6")     // no meaningful gust
        #expect(WindFormat.component(6, gust: nil) == "6")
        #expect(WindFormat.component(nil, gust: 10) == nil)
    }
}
