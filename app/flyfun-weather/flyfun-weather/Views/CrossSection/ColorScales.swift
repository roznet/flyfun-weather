import SwiftUI

/// Color functions for cross-section layers. Port of web's scales.ts.
enum ColorScales {
    // MARK: - Icing risk

    static func icingRiskColor(_ risk: String) -> Color {
        switch risk {
        case "light": Color(.sRGB, red: 0.4, green: 0.6, blue: 1.0, opacity: 0.35)
        case "moderate": Color(.sRGB, red: 1.0, green: 0.6, blue: 0.2, opacity: 0.45)
        case "severe": Color(.sRGB, red: 1.0, green: 0.2, blue: 0.2, opacity: 0.55)
        default: .clear
        }
    }

    // MARK: - CAT risk

    static func catRiskColor(_ risk: String) -> Color {
        switch risk {
        case "light": Color(.sRGB, red: 1.0, green: 0.75, blue: 0.0, opacity: 0.20)
        case "moderate": Color(.sRGB, red: 1.0, green: 0.65, blue: 0.0, opacity: 0.40)
        case "severe": Color(.sRGB, red: 1.0, green: 0.2, blue: 0.2, opacity: 0.55)
        default: .clear
        }
    }

    // MARK: - Cloud fill from dewpoint depression

    static func cloudFill(dewpointDepressionC: Double?, coverage: String) -> Color {
        let dd = dewpointDepressionC ?? 1.5
        let t = min(max(dd / 3.0, 0), 1.0)
        let r = (170 + 75 * t) / 255.0
        let g = (170 + 75 * t) / 255.0
        let b = (175 + 73 * t) / 255.0
        let alpha = coverageAlpha(coverage, t: t)
        return Color(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    }

    private static func coverageAlpha(_ coverage: String, t: Double) -> Double {
        switch coverage {
        case "sct": 0.40 + 0.15 * (1 - t)
        case "bkn": 0.50 + 0.30 * (1 - t)
        case "ovc": 0.60 + 0.32 * (1 - t)
        default: 0.30
        }
    }

    // MARK: - Inversion

    static func inversionOpacity(_ strengthC: Double) -> Double {
        let clamped = min(max(strengthC / 3.0, 0), 1.0)
        return 0.15 + 0.50 * clamped
    }

    static let inversionColor = Color(.sRGB, red: 233 / 255.0, green: 30 / 255.0, blue: 99 / 255.0)

    // MARK: - Terrain

    static let terrainFill = Color(.sRGB, red: 139 / 255.0, green: 115 / 255.0, blue: 85 / 255.0)
    static let terrainStroke = Color(.sRGB, red: 107 / 255.0, green: 91 / 255.0, blue: 69 / 255.0)

    // MARK: - Temperature lines

    static let freezingLevelColor = Color(.sRGB, red: 0, green: 188 / 255.0, blue: 212 / 255.0)
    static let minus10cColor = Color(.sRGB, red: 33 / 255.0, green: 150 / 255.0, blue: 243 / 255.0)
    static let minus20cColor = Color(.sRGB, red: 26 / 255.0, green: 35 / 255.0, blue: 126 / 255.0)

    // MARK: - Reference

    static let cruiseAltitudeColor = Color(.sRGB, red: 0.2, green: 0.2, blue: 0.2, opacity: 0.7)

    // MARK: - Sky background

    static let skyBlue = Color(.sRGB, red: 135 / 255.0, green: 206 / 255.0, blue: 235 / 255.0)
}
