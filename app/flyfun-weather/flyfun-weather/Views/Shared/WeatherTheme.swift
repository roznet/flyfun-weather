import SwiftUI

enum WeatherTheme {
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let section: CGFloat = 24
    }

    enum Radius {
        static let card: CGFloat = 8
        static let control: CGFloat = 8
    }

    static func background(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 18 / 255, green: 18 / 255, blue: 24 / 255, opacity: 1)
            : Color(.sRGB, red: 248 / 255, green: 249 / 255, blue: 250 / 255, opacity: 1)
    }

    static func surface(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 30 / 255, green: 30 / 255, blue: 42 / 255, opacity: 1)
            : .white
    }

    static func border(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 45 / 255, green: 45 / 255, blue: 58 / 255, opacity: 1)
            : Color(.sRGB, red: 222 / 255, green: 226 / 255, blue: 230 / 255, opacity: 1)
    }

    static func mutedText(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 156 / 255, green: 163 / 255, blue: 175 / 255, opacity: 1)
            : Color(.sRGB, red: 108 / 255, green: 117 / 255, blue: 125 / 255, opacity: 1)
    }

    static func text(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 228 / 255, green: 228 / 255, blue: 232 / 255, opacity: 1)
            : Color(.sRGB, red: 26 / 255, green: 26 / 255, blue: 46 / 255, opacity: 1)
    }

    static func primary(_ scheme: ColorScheme) -> Color {
        scheme == .dark
            ? Color(.sRGB, red: 96 / 255, green: 165 / 255, blue: 250 / 255, opacity: 1)
            : Color(.sRGB, red: 37 / 255, green: 99 / 255, blue: 235 / 255, opacity: 1)
    }

    static func shadow(_ scheme: ColorScheme) -> Color {
        .black.opacity(scheme == .dark ? 0.28 : 0.08)
    }
}
