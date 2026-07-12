//
//  BuildInfoTests.swift
//  flyfun-weatherTests
//
//  Guards the build-stamp pipeline end-to-end: the "Stamp Build Info" build
//  phase writes BuildStamp.plist into the app bundle, and BuildInfo reads it
//  back. These tests are hosted in the app, so Bundle.main here is the same
//  bundle Settings ▸ Developer reads from — if the phase stops running or the
//  keys are renamed, this fails instead of the Developer section quietly
//  showing "not stamped".
//

import Foundation
import Testing
@testable import flyfun_weather

@Suite("BuildInfo")
struct BuildInfoTests {

    @Test func stampIsPresentInTheAppBundle() {
        #expect(BuildInfo.buildDate != nil, "BuildStamp.plist missing — did the Stamp Build Info phase run?")
        #expect(BuildInfo.revision != nil)
    }

    @Test func buildDateIsUTCMinuteStamp() throws {
        let date = try #require(BuildInfo.buildDate)
        // Format written by scripts/stamp-build-info.sh: "2026-07-12 19:23 UTC".
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm 'UTC'"
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.locale = Locale(identifier: "en_US_POSIX")
        #expect(formatter.date(from: date) != nil, "unexpected build-date format: \(date)")
    }

    @Test func versionCombinesMarketingAndBuildNumbers() {
        // e.g. "1.2 (8)" — neither half may fall back to "?".
        #expect(!BuildInfo.version.contains("?"))
    }

    @Test func summaryIsOneLineAndCarriesTheCommit() throws {
        let commit = try #require(BuildInfo.revision)
        #expect(BuildInfo.summary.contains(commit))
        #expect(!BuildInfo.summary.contains("\n"))
    }
}
