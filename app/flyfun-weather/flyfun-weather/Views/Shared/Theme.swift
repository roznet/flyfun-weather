import SwiftUI
import UIKit

/// App chrome design tokens (§1C). Borrows Flighty's structure/hierarchy but
/// keeps WeatherBrief's own colour identity — matched to the web app's
/// light + dark palette (`web/css/style.css` `:root` / `[data-theme="dark"]`)
/// so the two clients feel like one product.
///
/// Both modes ship: each token resolves per `userInterfaceStyle`, so following
/// the system appearance is automatic. This is the app-chrome palette only —
/// the cross-section / Skew-T visualisations have their own viz themes
/// (`ColorScales`); keep that distinction.
enum Theme {
    // MARK: Colour tokens (light / dark hex from the web palette)

    static let bg = dynamic(light: 0xf8f9fa, dark: 0x121218)          // page background
    static let surface = dynamic(light: 0xffffff, dark: 0x1e1e2a)     // cards / sheets
    static let text = dynamic(light: 0x1a1a2e, dark: 0xe4e4e8)        // primary text
    static let textMuted = dynamic(light: 0x6c757d, dark: 0x9ca3af)   // secondary
    static let border = dynamic(light: 0xdee2e6, dark: 0x2d2d3a)      // hairlines
    static let primary = dynamic(light: 0x2563eb, dark: 0x60a5fa)     // accent / interactive
    static let green = dynamic(light: 0x198754, dark: 0x34d399)       // assessment GREEN
    static let amber = dynamic(light: 0xcc8800, dark: 0xfbbf24)       // assessment AMBER
    static let red = dynamic(light: 0xdc3545, dark: 0xf87171)         // assessment RED / danger
    static let lifr = dynamic(light: 0x8e24aa, dark: 0xc084fc)        // LIFR category

    // MARK: Spacing (4 / 8 pt grid)

    static let spacingXS: CGFloat = 4
    static let spacingS: CGFloat = 8
    static let spacingM: CGFloat = 12
    static let cardPadding: CGFloat = 16
    static let sectionSpacing: CGFloat = 24
    static let cornerRadius: CGFloat = 14

    // MARK: Typography helpers

    /// Big, bold hero number — the signature element (assessment / what-changed).
    static let heroNumber = Font.system(size: 34, weight: .bold, design: .rounded)
    static let heroLabel = Font.system(.title3, design: .default).weight(.semibold)
}

extension Font {
    /// Tabular / monospaced digits for data readouts (scrub strip, route-graph
    /// values, Skew-T panel, freshness times) so columns align and don't jitter.
    static func tabularData(_ style: Font.TextStyle = .body) -> Font {
        .system(style).monospacedDigit()
    }
}

private extension Theme {
    static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(rgb: dark) : UIColor(rgb: light)
        })
    }
}

private extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(
            red: CGFloat((rgb >> 16) & 0xff) / 255.0,
            green: CGFloat((rgb >> 8) & 0xff) / 255.0,
            blue: CGFloat(rgb & 0xff) / 255.0,
            alpha: 1.0
        )
    }
}
