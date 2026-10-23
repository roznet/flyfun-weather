//
//  FixtureFilesTests.swift
//  flyfun-weatherTests
//
//  The mock API fixtures shared with Android (`app/fixtures/api/*.json`, #895)
//  are bundled into the Debug app and still decode through the production
//  DTOs. `FixtureFiles` falls back to the source tree on a simulator, so the
//  bundle check is what catches a resource phase that stopped copying them.
//

import Testing
import Foundation
@testable import flyfun_weather

@MainActor @Suite struct FixtureFilesTests {

    /// Every file in the shared folder, by name (no extension).
    static let names: [String] = {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: FixtureFiles.sourceDirectory, includingPropertiesForKeys: nil)) ?? []
        return files.filter { $0.pathExtension == "json" }
            .map { $0.deletingPathExtension().lastPathComponent }
            .sorted()
    }()

    @Test func everySharedFixtureIsInTheAppBundle() {
        #expect(!Self.names.isEmpty, "no fixtures found in \(FixtureFiles.sourceDirectory.path)")
        for name in Self.names {
            #expect(FixtureFiles.bundledURL(name) != nil, "\(name).json is not in the app bundle's api/ folder")
        }
    }

    @Test func theFixtureRepositoryDecodesThem() {
        #expect(FixtureBriefingData.pack.flightId == FixtureBriefingData.flightId)
        #expect(!FixtureBriefingData.routeAnalyses.analyses.isEmpty)
        #expect(FixtureTripData.legs.map(\.id) == [FixtureTripData.outboundId, FixtureTripData.returnId])
        #expect(FixtureTripData.trip.id == FixtureTripData.tripId)
    }
}
