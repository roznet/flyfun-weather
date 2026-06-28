import SwiftUI

// =============================================================================
// SYNC — mirrors web/ts/visualization/cross-section/theme.ts
//   • Keep theme IDs identical ("standard" / "high-contrast" / "gramet" /
//     "light") so a route renders with visually matching colours on both
//     platforms and the two files can be diffed.
//   • Port colour VALUES verbatim from the web themes. Widths/dashes for the
//     temperature/stability/reference lines are NOT themed on iOS (the line
//     layers hardcode them) — only colours are, per #320.
//   • Web-only theme fields with no iOS layer yet are intentionally not ported:
//     nightShading, obscuration, compareModelColors, coverageOpacity, sld.
//     Add them here (and the matching layer) when iOS grows those layers.
// =============================================================================

/// 0–255 RGB triple, the unit the web theme stores cloud / terrain / inversion
/// colours in (cloud fills interpolate between two RGBs, so the raw channels are
/// kept rather than pre-baked `Color`s).
struct RGB: Sendable {
    let r: Double
    let g: Double
    let b: Double
    func color(_ alpha: Double = 1) -> Color {
        Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: alpha)
    }
}

/// Built-in cross-section colour themes. Raw values match the web `ThemeId`
/// union so persisted prefs and the preset `themeId` map cross platforms.
enum CrossSectionThemeID: String, CaseIterable, Identifiable, Sendable {
    case standard
    case highContrast = "high-contrast"
    case gramet
    case light
    var id: String { rawValue }

    /// The theme for this ID. Always present — `CrossSectionTheme.all` is keyed
    /// by every case — so call sites don't need to unwrap. Falls back to
    /// `.standard` only to stay total.
    var theme: CrossSectionTheme { CrossSectionTheme.all[self] ?? .standard }
}

/// A complete cross-section colour theme. Mirrors the web `CrossSectionTheme`
/// interface, trimmed to the fields iOS layers actually render. Fields are `var`
/// so derived themes can be built by copy-and-override (the web `{...STANDARD}`
/// spread pattern).
struct CrossSectionTheme: Sendable {
    var id: CrossSectionThemeID
    var label: String

    // Sky + axes
    var skyBackground: Color
    var axisGrid: Color
    var axisWaypoint: Color

    // Terrain
    var terrainFill: RGB
    var terrainStroke: RGB

    // Temperature isotherm line colours
    var freezingLevel: Color
    var minus10c: Color
    var minus20c: Color

    // Stability parcel-level line colours
    var lcl: Color
    var lfc: Color
    var el: Color

    // Reference lines
    var cruise: Color
    var ceiling: Color

    // DD-sourced cloud fill (dense → thin interpolation, per-coverage alpha)
    var cloudDense: RGB
    var cloudThin: RGB
    var cloudCoverageAlpha: [String: (lo: Double, hi: Double)]  // keyed "few/sct/bkn/ovc"
    var cloudFallbackGray: RGB

    // NWP cover%-sourced cloud fill
    var nwpBright: RGB
    var nwpDelta: RGB
    var nwpOpacityFloor: Double
    var nwpOpacityScale: Double

    // Soft (GRAMET-style feathered) cloud fill
    var softCloudFill: RGB
    var softCloudCoverageAlpha: [String: Double]  // keyed "OVC/BKN/SCT/FEW"
    var softCloudFeather: Double

    // Hazard band palettes (keyed by risk class)
    var icing: [String: Color]
    var sfipIcing: [String: Color]
    var cat: [String: Color]
    var convBgWash: [String: Color]
    var convTowerFill: [String: Color]
    var convHatch: [String: Color]
    var convEdge: [String: Color]
    var convStrip: [String: Color]
    var convCBLabel: [String: Color]

    // Inversion band
    var inversionBase: RGB
    var inversionFloor: Double
    var inversionScale: Double
    var inversionMaxStrengthC: Double
    var inversionCap: Double
}

// MARK: - Theme registry (ported from web theme.ts)

extension CrossSectionTheme {
    /// Convenience for an rgba() colour given 0–255 channels.
    private static func c(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 1) -> Color {
        Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: a)
    }

    // --- Standard (current production values) ---
    static let standard = CrossSectionTheme(
        id: .standard,
        label: "Standard",
        skyBackground: c(115, 149, 219),                 // #7395DB
        axisGrid: c(255, 255, 255, 0.35),
        axisWaypoint: c(255, 255, 255, 0.45),
        terrainFill: RGB(r: 139, g: 115, b: 85),         // #8B7355
        terrainStroke: RGB(r: 107, g: 91, b: 69),        // #6B5B45
        freezingLevel: c(0, 188, 212),                   // #00bcd4
        minus10c: c(33, 150, 243),                       // #2196f3
        minus20c: c(26, 35, 126),                        // #1a237e
        lcl: c(76, 175, 80),                             // #4caf50
        lfc: c(255, 152, 0),                             // #ff9800
        el: c(244, 67, 54),                              // #f44336
        cruise: c(55, 65, 81),                           // #374151
        ceiling: c(148, 103, 189),                       // #9467bd
        cloudDense: RGB(r: 140, g: 140, b: 150),
        cloudThin: RGB(r: 250, g: 250, b: 255),
        cloudCoverageAlpha: [
            "few": (0.20, 0.35), "sct": (0.50, 0.65),
            "bkn": (0.60, 0.88), "ovc": (0.70, 0.95),
        ],
        cloudFallbackGray: RGB(r: 180, g: 180, b: 185),
        nwpBright: RGB(r: 245, g: 245, b: 255),
        nwpDelta: RGB(r: 105, g: 105, b: 100),
        nwpOpacityFloor: 0.30,
        nwpOpacityScale: 0.55,
        softCloudFill: RGB(r: 255, g: 255, b: 255),
        softCloudCoverageAlpha: ["OVC": 0.85, "BKN": 0.65, "SCT": 0.45, "FEW": 0.15],
        softCloudFeather: 0.15,
        icing: [
            "none": .clear,
            "light": c(185, 170, 230, 0.70),
            "moderate": c(120, 100, 215, 0.85),
            "severe": c(65, 35, 155, 0.93),
        ],
        sfipIcing: [
            "none": .clear,
            "light": c(185, 170, 230, 0.78),
            "moderate": c(120, 100, 215, 0.92),
            "severe": c(65, 35, 155, 1.00),
        ],
        cat: [
            "none": .clear,
            "light": c(255, 193, 7, 0.20),
            "moderate": c(255, 152, 0, 0.40),
            "severe": c(220, 53, 69, 0.55),
        ],
        convBgWash: [
            "marginal": c(200, 200, 200, 0.04),
            "low": c(255, 235, 59, 0.06),
            "moderate": c(255, 152, 0, 0.08),
            "high": c(220, 53, 69, 0.10),
            "extreme": c(183, 28, 28, 0.14),
        ],
        convTowerFill: [
            "marginal": c(180, 180, 180, 0.15),
            "low": c(255, 235, 59, 0.18),
            "moderate": c(255, 152, 0, 0.25),
            "high": c(220, 53, 69, 0.30),
            "extreme": c(183, 28, 28, 0.35),
        ],
        convHatch: [
            "marginal": c(140, 140, 140, 0.15),
            "low": c(180, 160, 0, 0.20),
            "moderate": c(200, 100, 0, 0.35),
            "high": c(200, 40, 40, 0.40),
            "extreme": c(150, 20, 20, 0.50),
        ],
        convEdge: [
            "marginal": c(140, 140, 140, 0.25),
            "low": c(180, 160, 0, 0.30),
            "moderate": c(200, 100, 0, 0.50),
            "high": c(200, 40, 40, 0.60),
            "extreme": c(150, 20, 20, 0.70),
        ],
        convStrip: [
            "marginal": c(160, 160, 160, 0.40),
            "low": c(255, 235, 59, 0.50),
            "moderate": c(255, 152, 0, 0.75),
            "high": c(220, 53, 69, 0.85),
            "extreme": c(183, 28, 28, 0.90),
        ],
        convCBLabel: [
            "moderate": c(200, 100, 0, 0.80),
            "high": c(200, 40, 40, 0.90),
            "extreme": c(150, 20, 20, 0.95),
        ],
        inversionBase: RGB(r: 233, g: 30, b: 99),
        inversionFloor: 0.15,
        inversionScale: 0.50,
        inversionMaxStrengthC: 3,
        inversionCap: 0.65
    )

    // --- High contrast (deep-navy sky) ---
    static let highContrast: CrossSectionTheme = {
        var t = standard
        t.id = .highContrast
        t.label = "High Contrast"
        t.skyBackground = c(27, 48, 96)                  // #1B3060
        t.axisGrid = c(255, 255, 255, 0.30)
        t.axisWaypoint = c(255, 255, 255, 0.40)
        t.terrainFill = RGB(r: 74, g: 58, b: 40)         // #4A3A28
        t.terrainStroke = RGB(r: 107, g: 91, b: 69)      // #6B5B45
        t.freezingLevel = c(0, 229, 255)                 // #00e5ff
        t.minus10c = c(66, 165, 245)                     // #42a5f5
        t.minus20c = c(124, 77, 255)                     // #7c4dff
        t.lcl = c(105, 240, 174)                         // #69f0ae
        t.lfc = c(255, 171, 64)                          // #ffab40
        t.el = c(255, 82, 82)                            // #ff5252
        t.cruise = c(224, 224, 224)                      // #e0e0e0
        t.ceiling = c(206, 147, 216)                     // #ce93d8
        t.cloudDense = RGB(r: 70, g: 70, b: 70)
        t.cloudThin = RGB(r: 230, g: 230, b: 230)
        t.cloudCoverageAlpha = [
            "few": (0.15, 0.30), "sct": (0.45, 0.60),
            "bkn": (0.55, 0.85), "ovc": (0.65, 0.95),
        ]
        t.cloudFallbackGray = RGB(r: 120, g: 120, b: 120)
        t.nwpBright = RGB(r: 230, g: 230, b: 230)
        t.nwpDelta = RGB(r: 150, g: 150, b: 150)
        // softClouds inherited (web omits → factory default white fill)
        t.icing = [
            "none": .clear,
            "light": c(200, 220, 240, 0.70),
            "moderate": c(154, 176, 224, 0.80),
            "severe": c(132, 112, 216, 0.90),
        ]
        t.sfipIcing = [
            "none": .clear,
            "light": c(200, 220, 240, 0.80),
            "moderate": c(154, 176, 224, 0.90),
            "severe": c(132, 112, 216, 1.00),
        ]
        t.cat = [
            "none": .clear,
            "light": c(24, 136, 72, 0.40),
            "moderate": c(152, 184, 48, 0.55),
            "severe": c(200, 208, 16, 0.70),
        ]
        t.convBgWash = [
            "marginal": c(248, 160, 32, 0.06),
            "low": c(248, 160, 32, 0.10),
            "moderate": c(240, 120, 32, 0.14),
            "high": c(216, 80, 32, 0.18),
            "extreme": c(232, 24, 24, 0.22),
        ]
        t.convTowerFill = [
            "marginal": c(248, 160, 32, 0.20),
            "low": c(248, 160, 32, 0.28),
            "moderate": c(240, 120, 32, 0.38),
            "high": c(216, 80, 32, 0.48),
            "extreme": c(232, 24, 24, 0.55),
        ]
        t.convHatch = [
            "marginal": c(248, 160, 32, 0.25),
            "low": c(248, 160, 32, 0.35),
            "moderate": c(240, 120, 32, 0.50),
            "high": c(216, 80, 32, 0.60),
            "extreme": c(232, 24, 24, 0.70),
        ]
        t.convEdge = [
            "marginal": c(248, 160, 32, 0.30),
            "low": c(248, 160, 32, 0.40),
            "moderate": c(240, 120, 32, 0.55),
            "high": c(216, 80, 32, 0.65),
            "extreme": c(232, 24, 24, 0.75),
        ]
        t.convStrip = [
            "marginal": c(248, 160, 32, 0.45),
            "low": c(248, 160, 32, 0.55),
            "moderate": c(240, 120, 32, 0.75),
            "high": c(216, 80, 32, 0.85),
            "extreme": c(232, 24, 24, 0.92),
        ]
        t.convCBLabel = [
            "moderate": c(240, 120, 32, 0.85),
            "high": c(216, 80, 32, 0.92),
            "extreme": c(232, 24, 24, 0.95),
        ]
        t.inversionBase = RGB(r: 255, g: 82, b: 82)
        t.inversionFloor = 0.25
        t.inversionScale = 0.55
        t.inversionCap = 0.80
        return t
    }()

    // --- GRAMET (CloudPath-inspired deep blue) ---
    static let gramet: CrossSectionTheme = {
        var t = standard
        t.id = .gramet
        t.label = "GRAMET"
        t.skyBackground = c(43, 93, 168)                 // #2B5DA8
        t.axisGrid = c(255, 255, 255, 0.25)
        t.axisWaypoint = c(255, 255, 255, 0.35)
        t.terrainFill = RGB(r: 139, g: 105, b: 20)       // #8B6914
        t.terrainStroke = RGB(r: 107, g: 80, b: 16)      // #6B5010
        t.freezingLevel = c(255, 68, 68)                 // #FF4444
        t.minus10c = c(34, 204, 68)                      // #22CC44
        t.minus20c = c(34, 204, 68)                      // #22CC44
        t.cruise = c(224, 224, 224)                      // #e0e0e0
        t.ceiling = c(206, 147, 216)                     // #ce93d8
        // clouds / nwpClouds / cat / inversion inherited from standard
        t.icing = [
            "none": .clear,
            "light": c(170, 230, 205, 0.45),
            "moderate": c(110, 200, 165, 0.60),
            "severe": c(45, 130, 100, 0.75),
        ]
        t.sfipIcing = [
            "none": .clear,
            "light": c(170, 230, 205, 0.55),
            "moderate": c(110, 200, 165, 0.70),
            "severe": c(45, 130, 100, 0.85),
        ]
        // Convective inherits standard except the CB pill label.
        t.convCBLabel = [
            "moderate": c(255, 180, 40, 1.0),
            "high": c(255, 80, 60, 1.0),
            "extreme": c(255, 50, 30, 1.0),
        ]
        // Soft clouds inherit Standard's white fill / alphas unchanged — the web
        // gramet `softClouds` block is identical to the factory default, so we
        // don't re-state it here.
        return t
    }()

    // --- Light (Windy-inspired white sky, gray clouds) ---
    static let light: CrossSectionTheme = {
        var t = standard
        t.id = .light
        t.label = "Light"
        t.skyBackground = c(248, 249, 251)               // #F8F9FB
        t.axisGrid = c(20, 30, 50, 0.18)
        t.axisWaypoint = c(20, 30, 50, 0.32)
        t.terrainFill = RGB(r: 164, g: 130, b: 86)       // #A48256
        t.terrainStroke = RGB(r: 122, g: 94, b: 61)      // #7A5E3D
        t.freezingLevel = c(2, 119, 189)                 // #0277BD
        t.minus10c = c(21, 101, 192)                     // #1565C0
        t.minus20c = c(13, 71, 161)                      // #0D47A1
        t.lcl = c(46, 125, 50)                           // #2E7D32
        t.lfc = c(230, 81, 0)                            // #E65100
        t.el = c(198, 40, 40)                            // #C62828
        t.cruise = c(33, 33, 33)                         // #212121
        t.ceiling = c(106, 27, 154)                      // #6A1B9A
        t.cloudDense = RGB(r: 70, g: 80, b: 95)
        t.cloudThin = RGB(r: 195, g: 200, b: 210)
        t.cloudCoverageAlpha = [
            "few": (0.15, 0.30), "sct": (0.40, 0.55),
            "bkn": (0.55, 0.80), "ovc": (0.70, 0.90),
        ]
        t.cloudFallbackGray = RGB(r: 150, g: 155, b: 165)
        t.nwpBright = RGB(r: 225, g: 228, b: 232)
        t.nwpDelta = RGB(r: 155, g: 160, b: 165)
        t.nwpOpacityFloor = 0.35
        t.nwpOpacityScale = 0.70
        t.icing = [
            "none": .clear,
            "light": c(170, 140, 220, 0.55),
            "moderate": c(110, 80, 200, 0.72),
            "severe": c(60, 30, 145, 0.88),
        ]
        t.sfipIcing = [
            "none": .clear,
            "light": c(170, 140, 220, 0.65),
            "moderate": c(110, 80, 200, 0.82),
            "severe": c(60, 30, 145, 0.95),
        ]
        t.cat = [
            "none": .clear,
            "light": c(255, 152, 0, 0.30),
            "moderate": c(245, 124, 0, 0.50),
            "severe": c(198, 40, 40, 0.65),
        ]
        t.convBgWash = [
            "marginal": c(120, 120, 120, 0.05),
            "low": c(255, 193, 7, 0.10),
            "moderate": c(245, 124, 0, 0.14),
            "high": c(220, 53, 69, 0.18),
            "extreme": c(136, 14, 79, 0.22),
        ]
        t.convTowerFill = [
            "marginal": c(120, 120, 120, 0.20),
            "low": c(255, 193, 7, 0.28),
            "moderate": c(245, 124, 0, 0.40),
            "high": c(220, 53, 69, 0.50),
            "extreme": c(136, 14, 79, 0.55),
        ]
        t.convHatch = [
            "marginal": c(100, 100, 100, 0.25),
            "low": c(180, 130, 0, 0.35),
            "moderate": c(200, 80, 0, 0.50),
            "high": c(180, 30, 30, 0.60),
            "extreme": c(100, 10, 50, 0.70),
        ]
        t.convEdge = [
            "marginal": c(100, 100, 100, 0.35),
            "low": c(180, 130, 0, 0.45),
            "moderate": c(200, 80, 0, 0.60),
            "high": c(180, 30, 30, 0.70),
            "extreme": c(100, 10, 50, 0.80),
        ]
        t.convStrip = [
            "marginal": c(120, 120, 120, 0.50),
            "low": c(255, 193, 7, 0.65),
            "moderate": c(245, 124, 0, 0.80),
            "high": c(220, 53, 69, 0.88),
            "extreme": c(136, 14, 79, 0.92),
        ]
        t.convCBLabel = [
            "moderate": c(200, 80, 0, 0.92),
            "high": c(180, 30, 30, 0.95),
            "extreme": c(100, 10, 50, 1.00),
        ]
        t.inversionBase = RGB(r: 194, g: 24, b: 91)
        t.inversionFloor = 0.20
        t.inversionScale = 0.55
        t.inversionCap = 0.75
        t.softCloudFill = RGB(r: 70, g: 80, b: 95)
        t.softCloudCoverageAlpha = ["OVC": 0.55, "BKN": 0.40, "SCT": 0.25, "FEW": 0.10]
        t.softCloudFeather = 0.15
        return t
    }()

    /// Registry keyed by ID. Mirrors web `THEMES`.
    static let all: [CrossSectionThemeID: CrossSectionTheme] = [
        .standard: standard,
        .highContrast: highContrast,
        .gramet: gramet,
        .light: light,
    ]

    // MARK: - Active theme (module-level indirection, mirrors web get/setActiveTheme)
    //
    // The cross-section renders synchronously on the main actor (SwiftUI Canvas),
    // and so do the only writers (the view model / renderer). Isolating the shared
    // value (and `ColorScales`, which reads it) to `@MainActor` makes that a
    // compiler-enforced guarantee rather than a documented convention — an
    // off-main caller won't compile. Defaults to `.standard`; the view model sets
    // it to the booted theme (GRAMET) before the first frame.

    @MainActor private static var _active: CrossSectionTheme = standard

    @MainActor static var active: CrossSectionTheme { _active }

    @MainActor static func setActive(_ id: CrossSectionThemeID) {
        _active = all[id] ?? standard
    }
}
