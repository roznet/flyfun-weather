import SwiftUI

/// Configuration for Skew-T plot appearance and axis ranges.
public struct SkewTConfiguration: Sendable {
    // Axis ranges
    public let pBottom: Double      // Bottom pressure (hPa), default 1050
    public let pTop: Double         // Top pressure (hPa), default 200
    public let tMin: Double         // Left temperature bound (°C), default -60
    public let tMax: Double         // Right temperature bound (°C), default 40
    public let skewAngle: Double    // Isotherm tilt (degrees), default 45

    // Margins
    public let margins: Margins

    // Appearance
    public let backgroundColor: Color
    public let isothermColor: Color
    public let dryAdiabatColor: Color
    public let moistAdiabatColor: Color
    public let mixingRatioColor: Color
    public let temperatureColor: Color
    public let dewpointColor: Color
    public let windBarbColor: Color
    public let gridLineWidth: CGFloat
    public let profileLineWidth: CGFloat

    public struct Margins: Sendable {
        public let left: CGFloat
        public let right: CGFloat
        public let top: CGFloat
        public let bottom: CGFloat

        public static let `default` = Margins(left: 50, right: 60, top: 20, bottom: 30)

        public init(left: CGFloat, right: CGFloat, top: CGFloat, bottom: CGFloat) {
            self.left = left
            self.right = right
            self.top = top
            self.bottom = bottom
        }
    }

    public static let `default` = SkewTConfiguration()

    public init(
        pBottom: Double = 1050,
        pTop: Double = 200,
        tMin: Double = -60,
        tMax: Double = 40,
        skewAngle: Double = 45,
        margins: Margins = .default,
        backgroundColor: Color = Color(.sRGB, red: 0.98, green: 0.98, blue: 1.0),
        isothermColor: Color = .gray.opacity(0.3),
        dryAdiabatColor: Color = .red.opacity(0.25),
        moistAdiabatColor: Color = .green.opacity(0.25),
        mixingRatioColor: Color = .blue.opacity(0.2),
        temperatureColor: Color = .red,
        dewpointColor: Color = .green,
        windBarbColor: Color = .primary,
        gridLineWidth: CGFloat = 0.5,
        profileLineWidth: CGFloat = 2.0
    ) {
        self.pBottom = pBottom
        self.pTop = pTop
        self.tMin = tMin
        self.tMax = tMax
        self.skewAngle = skewAngle
        self.margins = margins
        self.backgroundColor = backgroundColor
        self.isothermColor = isothermColor
        self.dryAdiabatColor = dryAdiabatColor
        self.moistAdiabatColor = moistAdiabatColor
        self.mixingRatioColor = mixingRatioColor
        self.temperatureColor = temperatureColor
        self.dewpointColor = dewpointColor
        self.windBarbColor = windBarbColor
        self.gridLineWidth = gridLineWidth
        self.profileLineWidth = profileLineWidth
    }
}
