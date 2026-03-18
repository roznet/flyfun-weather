import SwiftUI

/// SwiftUI view that renders a Skew-T log-P diagram for a sounding profile.
public struct SkewTView: View {
    private let profile: SoundingProfile
    private let config: SkewTConfiguration
    private let renderer: SkewTRenderer

    public init(profile: SoundingProfile, config: SkewTConfiguration = .default) {
        self.profile = profile
        self.config = config
        self.renderer = SkewTRenderer(profile: profile, config: config)
    }

    public var body: some View {
        Canvas { context, size in
            renderer.render(context: &context, size: size)
        }
        .accessibilityLabel(accessibilityDescription)
    }

    private var accessibilityDescription: String {
        var parts = ["Skew-T log-P diagram"]
        parts.append("\(profile.levels.count) sounding levels")
        if let indices = profile.indices {
            if let cape = indices.capeSurfaceJkg {
                parts.append("CAPE \(Int(cape)) J per kg")
            }
            if let cin = indices.cinSurfaceJkg {
                parts.append("CIN \(Int(cin)) J per kg")
            }
            if let fz = indices.freezingLevelFt {
                parts.append("Freezing level \(Int(fz)) feet")
            }
        }
        return parts.joined(separator: ", ")
    }
}

// MARK: - Preview

#Preview("Sample Sounding") {
    SkewTView(profile: .previewSample)
        .frame(width: 600, height: 800)
}

extension SoundingProfile {
    /// A sample sounding profile for SwiftUI previews and testing.
    public static let previewSample = SoundingProfile(
        levels: [
            SoundingLevel(pressureHPa: 1000, temperatureC: 28, dewpointC: 20,
                         windSpeedKt: 5, windDirectionDeg: 180),
            SoundingLevel(pressureHPa: 925, temperatureC: 22, dewpointC: 16,
                         windSpeedKt: 10, windDirectionDeg: 200),
            SoundingLevel(pressureHPa: 850, temperatureC: 16, dewpointC: 10,
                         windSpeedKt: 20, windDirectionDeg: 220),
            SoundingLevel(pressureHPa: 700, temperatureC: 2, dewpointC: -8,
                         windSpeedKt: 35, windDirectionDeg: 250),
            SoundingLevel(pressureHPa: 500, temperatureC: -15, dewpointC: -30,
                         windSpeedKt: 50, windDirectionDeg: 270),
            SoundingLevel(pressureHPa: 400, temperatureC: -28, dewpointC: -42,
                         windSpeedKt: 55, windDirectionDeg: 280),
            SoundingLevel(pressureHPa: 300, temperatureC: -42, dewpointC: -55,
                         windSpeedKt: 60, windDirectionDeg: 280),
            SoundingLevel(pressureHPa: 250, temperatureC: -52, dewpointC: -62,
                         windSpeedKt: 55, windDirectionDeg: 275),
            SoundingLevel(pressureHPa: 200, temperatureC: -58, dewpointC: -68,
                         windSpeedKt: 45, windDirectionDeg: 270),
        ],
        indices: SkewTIndices(
            lclPressureHPa: 880,
            capeSurfaceJkg: 1250,
            cinSurfaceJkg: -45,
            freezingLevelFt: 12500,
            liftedIndex: -4.2
        )
    )
}
