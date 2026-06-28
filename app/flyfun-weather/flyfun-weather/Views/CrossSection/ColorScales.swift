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

/// Colour functions for cross-section layers. Port of web's scales.ts.
///
/// Every colour reads through `CrossSectionTheme.active` so a single theme
/// switch recolours the whole chart (mirrors web's `getActiveTheme()`). The
/// function/property names and signatures are unchanged from the pre-theme
/// version so all call sites stay put — only the bodies now defer to the theme.
nonisolated enum ColorScales {
    private static var theme: CrossSectionTheme { CrossSectionTheme.active }

    // MARK: - Icing risk

    static func icingRiskColor(_ risk: String) -> Color {
        theme.icing[risk] ?? .clear
    }

    // MARK: - CAT risk

    static func catRiskColor(_ risk: String) -> Color {
        theme.cat[risk] ?? .clear
    }

    // MARK: - Cloud fill from dewpoint depression

    /// RGBA components (0–1) for a DD-sourced cloud fill. Faithful port of the
    /// web `cloudFillFromDD`: dense (saturated, low DD) → thin (dry, high DD),
    /// interpolated by DD; alpha per coverage class.
    static func cloudRGBA(dewpointDepressionC: Double?, coverage: String) -> RGBA {
        let t = theme
        let (lo, hi) = t.cloudCoverageAlpha[coverage.lowercased()] ?? (0.30, 0.65)
        guard let dd = dewpointDepressionC else {
            // fallbackGray + the class's upper alpha (web behaviour for nil DD).
            let g = t.cloudFallbackGray
            return RGBA(r: g.r / 255.0, g: g.g / 255.0, b: g.b / 255.0, a: hi)
        }
        let f = min(max(dd / 3.0, 0), 1.0)
        let d = t.cloudDense, th = t.cloudThin
        return RGBA(
            r: (d.r + (th.r - d.r) * f) / 255.0,
            g: (d.g + (th.g - d.g) * f) / 255.0,
            b: (d.b + (th.b - d.b) * f) / 255.0,
            a: hi - (hi - lo) * f   // denser (hi) when saturated, lighter (lo) when dry
        )
    }

    static func cloudFill(dewpointDepressionC: Double?, coverage: String) -> Color {
        cloudRGBA(dewpointDepressionC: dewpointDepressionC, coverage: coverage).color
    }

    // MARK: - Inversion

    static func inversionOpacity(_ strengthC: Double) -> Double {
        let t = theme
        let clamped = min(max(strengthC / t.inversionMaxStrengthC, 0), 1.0)
        return min(t.inversionFloor + t.inversionScale * clamped, t.inversionCap)
    }

    static var inversionColor: Color { theme.inversionBase.color() }

    // MARK: - Terrain

    static var terrainFill: Color { theme.terrainFill.color() }
    static var terrainStroke: Color { theme.terrainStroke.color() }

    // MARK: - Temperature lines

    static var freezingLevelColor: Color { theme.freezingLevel }
    static var minus10cColor: Color { theme.minus10c }
    static var minus20cColor: Color { theme.minus20c }

    // MARK: - Reference

    static var cruiseAltitudeColor: Color { theme.cruise }
    static var ceilingColor: Color { theme.ceiling }

    // MARK: - Axes (grid + waypoint lines drawn over the sky)

    static var gridColor: Color { theme.axisGrid }
    static var waypointLineColor: Color { theme.axisWaypoint }

    // MARK: - Sky background

    static var skyBlue: Color { theme.skyBackground }

    // MARK: - NWP cloud (model-percentage-derived)

    /// Map METAR coverage class to a representative percentage. (Not themed —
    /// this is a data mapping, not a colour.)
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
    /// `nwpCloudFill`: bright (low cover) → bright−delta (high cover).
    static func nwpCloudRGBA(pct: Double) -> RGBA {
        let t = theme
        let f = min(1.0, max(0.0, pct / 100.0))
        return RGBA(
            r: (t.nwpBright.r - t.nwpDelta.r * f) / 255.0,
            g: (t.nwpBright.g - t.nwpDelta.g * f) / 255.0,
            b: (t.nwpBright.b - t.nwpDelta.b * f) / 255.0,
            a: t.nwpOpacityFloor + t.nwpOpacityScale * f
        )
    }

    /// Distinct hue from DD cloud layers so users can tell NWP and DD methods apart.
    static func nwpCloudFill(pct: Double) -> Color {
        nwpCloudRGBA(pct: pct).color
    }

    // MARK: - Stability lines (LCL / LFC / EL)

    static var lclColor: Color { theme.lcl }
    static var lfcColor: Color { theme.lfc }
    static var elColor: Color { theme.el }

    // MARK: - Soft cloud (GRAMET-style feathered fill)

    /// Coverage → fill alpha at the band's solid centre.
    static func softCloudCenterAlpha(_ coverage: String) -> Double {
        theme.softCloudCoverageAlpha[coverage.uppercased()] ?? 0.50
    }

    /// Fade fraction at top and bottom edges for soft-cloud bands.
    static var softCloudFeatherFraction: Double { theme.softCloudFeather }

    /// Base RGB (0–1) for soft cloud fills.
    static var softCloudFillRGB: (Double, Double, Double) {
        let f = theme.softCloudFill
        return (f.r / 255.0, f.g / 255.0, f.b / 255.0)
    }

    // MARK: - SFIP icing

    static func sfipRiskColor(_ risk: String) -> Color {
        theme.sfipIcing[risk] ?? .clear
    }

    // MARK: - Convective tower palette

    /// Subtle full-height column wash behind the tower for situational awareness.
    static func convectiveBgWash(_ risk: String) -> Color {
        theme.convBgWash[risk] ?? .clear
    }

    /// Tower body fill colour (LCL/base → EL/top).
    static func convectiveTowerFill(_ risk: String) -> Color {
        theme.convTowerFill[risk] ?? .clear
    }

    /// Diagonal hatching colour, drawn inside the tower.
    static func convectiveHatchColor(_ risk: String) -> Color {
        theme.convHatch[risk] ?? .clear
    }

    /// Rectangle outline around the tower body.
    static func convectiveEdgeColor(_ risk: String) -> Color {
        theme.convEdge[risk] ?? .clear
    }

    /// Anvil strip colour at tower top.
    static func convectiveStripColor(_ risk: String) -> Color {
        theme.convStrip[risk] ?? .clear
    }

    /// Pill-label text colour (TCU/CB/+TS).
    static func convectiveCBLabelColor(_ risk: String) -> Color {
        theme.convCBLabel[risk] ?? .gray
    }
}
