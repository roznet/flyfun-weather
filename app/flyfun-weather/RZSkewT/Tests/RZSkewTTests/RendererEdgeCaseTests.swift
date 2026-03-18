import CoreGraphics
import Foundation
import Testing
@testable import RZSkewT

@Suite("Renderer edge cases")
struct RendererEdgeCaseTests {

    // These tests verify the renderer doesn't crash on edge-case inputs.
    // We can't inspect pixels, but we can verify no fatal errors occur.

    private func renderToContext(profile: SoundingProfile, size: CGSize = CGSize(width: 600, height: 800)) {
        let renderer = SkewTRenderer(profile: profile)
        // SkewTTransform requires positive dimensions — verified by precondition
        // The renderer itself should handle all data edge cases gracefully
        _ = renderer.parcelPath
        _ = renderer.backgroundLines
    }

    @Test("Empty levels array does not crash")
    func emptyLevels() {
        let profile = SoundingProfile(levels: [])
        renderToContext(profile: profile)
    }

    @Test("Single level does not crash")
    func singleLevel() {
        let profile = SoundingProfile(levels: [
            SoundingLevel(pressureHPa: 1000, temperatureC: 20, dewpointC: 10)
        ])
        renderToContext(profile: profile)
    }

    @Test("All levels at same pressure does not crash")
    func samePressure() {
        let levels = (0..<5).map { _ in
            SoundingLevel(pressureHPa: 850, temperatureC: 15, dewpointC: 10)
        }
        let profile = SoundingProfile(levels: levels)
        renderToContext(profile: profile)
    }

    @Test("Levels with no dewpoint or wind data does not crash")
    func missingOptionalData() {
        let levels = [
            SoundingLevel(pressureHPa: 1000, temperatureC: 25),
            SoundingLevel(pressureHPa: 850, temperatureC: 15),
            SoundingLevel(pressureHPa: 700, temperatureC: 0),
            SoundingLevel(pressureHPa: 500, temperatureC: -20),
        ]
        let profile = SoundingProfile(levels: levels)
        renderToContext(profile: profile)
        // parcelPath should be empty since no dewpoint at surface
        let renderer = SkewTRenderer(profile: profile)
        #expect(renderer.parcelPath.isEmpty)
    }

    @Test("Profile with only wind data, no dewpoint")
    func windOnlyData() {
        let levels = [
            SoundingLevel(pressureHPa: 1000, temperatureC: 20,
                         windSpeedKt: 10, windDirectionDeg: 270),
            SoundingLevel(pressureHPa: 500, temperatureC: -15,
                         windSpeedKt: 50, windDirectionDeg: 280),
        ]
        let profile = SoundingProfile(levels: levels)
        renderToContext(profile: profile)
    }

    @Test("Background lines compute without crash for custom config")
    func customConfigBackgroundLines() {
        let config = SkewTConfiguration(
            pBottom: 950, pTop: 300,
            tMin: -30, tMax: 30
        )
        let lines = BackgroundLines.compute(config: config)
        #expect(lines.isotherms.count > 0)
        #expect(lines.dryAdiabats.count > 0)
        #expect(lines.moistAdiabats.count > 0)
    }

    @Test("Preview sample profile creates valid renderer")
    func previewSample() {
        let renderer = SkewTRenderer(profile: .previewSample)
        #expect(!renderer.parcelPath.isEmpty)
        #expect(renderer.backgroundLines.isotherms.count > 0)
    }

    // MARK: - Model conformances

    @Test("SoundingLevel conforms to Codable roundtrip")
    func soundingLevelCodable() throws {
        let level = SoundingLevel(pressureHPa: 850, altitudeFt: 5000,
                                  temperatureC: 15, dewpointC: 8,
                                  windSpeedKt: 20, windDirectionDeg: 270)
        let data = try JSONEncoder().encode(level)
        let decoded = try JSONDecoder().decode(SoundingLevel.self, from: data)
        #expect(level == decoded)
    }

    @Test("SoundingProfile conforms to Codable roundtrip")
    func soundingProfileCodable() throws {
        let profile = SoundingProfile(
            levels: [
                SoundingLevel(pressureHPa: 1000, temperatureC: 25, dewpointC: 18),
                SoundingLevel(pressureHPa: 500, temperatureC: -15),
            ],
            indices: SkewTIndices(capeSurfaceJkg: 1200, freezingLevelFt: 12000)
        )
        let data = try JSONEncoder().encode(profile)
        let decoded = try JSONDecoder().decode(SoundingProfile.self, from: data)
        #expect(profile == decoded)
    }

    @Test("AtmosphericPoint conforms to Equatable and Hashable")
    func atmosphericPointProtocols() {
        let a = AtmosphericPoint(tempC: 20, pressureHPa: 1000)
        let b = AtmosphericPoint(tempC: 20, pressureHPa: 1000)
        let c = AtmosphericPoint(tempC: 15, pressureHPa: 850)
        #expect(a == b)
        #expect(a != c)
        #expect(a.hashValue == b.hashValue)

        // Can be used in a Set
        let set: Set<AtmosphericPoint> = [a, b, c]
        #expect(set.count == 2)
    }
}
