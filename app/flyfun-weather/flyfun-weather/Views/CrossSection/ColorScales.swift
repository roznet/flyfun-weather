import SwiftUI

/// RGBA components in 0–1, used where a cloud style needs both the solid fill
/// and a matching fully-transparent stop (e.g. natural-style radial gradients).
struct RGBA: Sendable {
    let r: Double
    let g: Double
    let b: Double
    let a: Double

    var color: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: a) }
    /// Same hue, fully transparent — the outer stop of a soft blob gradient.
    var clear: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: 0) }
    func withAlpha(_ alpha: Double) -> Color { Color(.sRGB, red: r, green: g, blue: b, opacity: alpha) }
}

/// Color functions for cross-section layers. Port of web's scales.ts.
nonisolated enum ColorScales {
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

    /// RGBA components (0–1) for a DD-sourced cloud fill. Mirrors web
    /// `cloudFillFromDD`. Returned as a tuple so the natural style can build
    /// both a solid and a fully-transparent gradient stop from the same colour.
    static func cloudRGBA(dewpointDepressionC: Double?, coverage: String) -> RGBA {
        let dd = dewpointDepressionC ?? 1.5
        let t = min(max(dd / 3.0, 0), 1.0)
        return RGBA(
            r: (170 + 75 * t) / 255.0,
            g: (170 + 75 * t) / 255.0,
            b: (175 + 73 * t) / 255.0,
            a: coverageAlpha(coverage, t: t)
        )
    }

    static func cloudFill(dewpointDepressionC: Double?, coverage: String) -> Color {
        cloudRGBA(dewpointDepressionC: dewpointDepressionC, coverage: coverage).color
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

    // MARK: - NWP cloud (model-percentage-derived, blue-tinted)

    /// Map METAR coverage class to a representative percentage.
    static func coverageToPct(_ coverage: String) -> Double {
        switch coverage.uppercased() {
        case "OVC": return 90
        case "BKN": return 65
        case "SCT": return 35
        case "FEW": return 15
        default: return 35
        }
    }

    /// RGBA components (0–1) for an NWP cover%-derived cloud fill. Mirrors web
    /// `nwpCloudFill`.
    static func nwpCloudRGBA(pct: Double) -> RGBA {
        let t = min(1.0, max(0.0, pct / 100.0))
        // Brighter blue at low cover, darker at high cover.
        return RGBA(
            r: (210 - 60 * t) / 255.0,
            g: (220 - 50 * t) / 255.0,
            b: (235 - 20 * t) / 255.0,
            a: 0.30 + 0.50 * t
        )
    }

    /// Blue-tinted fill from coverage percentage. Distinct hue from DD cloud layers
    /// so users can tell NWP and DD methods apart at a glance.
    static func nwpCloudFill(pct: Double) -> Color {
        nwpCloudRGBA(pct: pct).color
    }

    // MARK: - Stability lines (LCL / LFC / EL)
    // Colours mirror the web cross-section theme `stability` block so parcel
    // levels read identically across clients.

    static let lclColor = Color(.sRGB, red: 76 / 255.0, green: 175 / 255.0, blue: 80 / 255.0)   // #4caf50
    static let lfcColor = Color(.sRGB, red: 255 / 255.0, green: 152 / 255.0, blue: 0 / 255.0)   // #ff9800
    static let elColor = Color(.sRGB, red: 244 / 255.0, green: 67 / 255.0, blue: 54 / 255.0)    // #f44336

    // MARK: - Soft cloud (GRAMET-style feathered fill)

    /// Coverage → fill alpha at the band's solid centre.
    static func softCloudCenterAlpha(_ coverage: String) -> Double {
        switch coverage.uppercased() {
        case "OVC": return 0.85
        case "BKN": return 0.65
        case "SCT": return 0.45
        case "FEW": return 0.15
        default: return 0.50
        }
    }

    /// Fade fraction at top and bottom edges for soft-cloud bands.
    static let softCloudFeatherFraction: Double = 0.15

    /// Base RGB for soft cloud fills (white-ish).
    static let softCloudFillRGB: (Double, Double, Double) = (255 / 255.0, 255 / 255.0, 255 / 255.0)

    // MARK: - SFIP icing

    static func sfipRiskColor(_ risk: String) -> Color {
        switch risk {
        case "light": Color(.sRGB, red: 0.45, green: 0.7, blue: 1.0, opacity: 0.40)
        case "moderate": Color(.sRGB, red: 1.0, green: 0.55, blue: 0.0, opacity: 0.50)
        case "severe": Color(.sRGB, red: 0.85, green: 0.1, blue: 0.55, opacity: 0.55)
        default: .clear
        }
    }

    // MARK: - Convective tower palette

    /// Subtle full-height column wash behind the tower for situational awareness.
    static func convectiveBgWash(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 1.0, green: 0.95, blue: 0.6, opacity: 0.08)
        case "moderate": Color(.sRGB, red: 1.0, green: 0.7, blue: 0.0, opacity: 0.10)
        case "high": Color(.sRGB, red: 1.0, green: 0.2, blue: 0.2, opacity: 0.12)
        case "extreme": Color(.sRGB, red: 0.6, green: 0.0, blue: 0.5, opacity: 0.15)
        default: .clear
        }
    }

    /// Tower body fill colour (LCL/base → EL/top).
    static func convectiveTowerFill(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 1.0, green: 0.9, blue: 0.4, opacity: 0.30)
        case "moderate": Color(.sRGB, red: 1.0, green: 0.6, blue: 0.0, opacity: 0.40)
        case "high": Color(.sRGB, red: 1.0, green: 0.2, blue: 0.2, opacity: 0.50)
        case "extreme": Color(.sRGB, red: 0.55, green: 0.0, blue: 0.45, opacity: 0.55)
        default: .clear
        }
    }

    /// Diagonal hatching colour, drawn inside the tower.
    static func convectiveHatchColor(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 0.85, green: 0.65, blue: 0.0, opacity: 0.35)
        case "moderate": Color(.sRGB, red: 0.85, green: 0.40, blue: 0.0, opacity: 0.50)
        case "high": Color(.sRGB, red: 0.7, green: 0.05, blue: 0.05, opacity: 0.60)
        case "extreme": Color(.sRGB, red: 0.35, green: 0.0, blue: 0.35, opacity: 0.65)
        default: .clear
        }
    }

    /// Rectangle outline around the tower body.
    static func convectiveEdgeColor(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 0.7, green: 0.55, blue: 0.0, opacity: 0.6)
        case "moderate": Color(.sRGB, red: 0.85, green: 0.4, blue: 0.0, opacity: 0.7)
        case "high": Color(.sRGB, red: 0.7, green: 0.1, blue: 0.1, opacity: 0.8)
        case "extreme": Color(.sRGB, red: 0.4, green: 0.0, blue: 0.4, opacity: 0.85)
        default: .clear
        }
    }

    /// Anvil strip colour at tower top.
    static func convectiveStripColor(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 0.85, green: 0.7, blue: 0.0, opacity: 0.6)
        case "moderate": Color(.sRGB, red: 0.85, green: 0.45, blue: 0.0, opacity: 0.75)
        case "high": Color(.sRGB, red: 0.7, green: 0.1, blue: 0.1, opacity: 0.8)
        case "extreme": Color(.sRGB, red: 0.4, green: 0.0, blue: 0.4, opacity: 0.85)
        default: .clear
        }
    }

    /// Pill-label text colour (TCU/CB/+TS).
    static func convectiveCBLabelColor(_ risk: String) -> Color {
        switch risk {
        case "low": Color(.sRGB, red: 0.6, green: 0.45, blue: 0.0, opacity: 0.9)
        case "moderate": Color(.sRGB, red: 0.7, green: 0.35, blue: 0.0, opacity: 0.95)
        case "high": Color(.sRGB, red: 0.65, green: 0.1, blue: 0.1, opacity: 1.0)
        case "extreme": Color(.sRGB, red: 0.4, green: 0.0, blue: 0.4, opacity: 1.0)
        default: .gray
        }
    }
}
