import Foundation

/// Maps between (temperature_C, pressure_hPa) and pixel coordinates on a Skew-T log-P diagram.
public struct SkewTTransform: Sendable {
    public let plotArea: PlotArea
    public let config: SkewTConfiguration

    // Precomputed values
    private let logPBottom: Double
    private let logPTop: Double
    private let logRange: Double
    private let skewTan: Double
    private let tRange: Double

    public struct PlotArea: Sendable {
        public let left: CGFloat
        public let top: CGFloat
        public let width: CGFloat
        public let height: CGFloat

        public var right: CGFloat { left + width }
        public var bottom: CGFloat { top + height }
    }

    public init(size: CGSize, config: SkewTConfiguration = .default) {
        precondition(size.width > 0 && size.height > 0, "SkewTTransform requires positive dimensions")
        self.config = config
        self.plotArea = PlotArea(
            left: config.margins.left,
            top: config.margins.top,
            width: size.width - config.margins.left - config.margins.right,
            height: size.height - config.margins.top - config.margins.bottom
        )
        self.logPBottom = log(config.pBottom)
        self.logPTop = log(config.pTop)
        self.logRange = logPBottom - logPTop
        self.skewTan = tan(config.skewAngle * .pi / 180.0)
        self.tRange = config.tMax - config.tMin
    }

    // MARK: - Forward transforms

    /// Convert pressure (hPa) to Y pixel coordinate.
    /// Higher pressure (bottom of atmosphere) maps to bottom of plot; lower pressure maps to top.
    public func pressureToY(_ p: Double) -> CGFloat {
        let fraction = (logPBottom - log(p)) / logRange
        return plotArea.bottom - CGFloat(fraction) * plotArea.height
    }

    /// Convert temperature (°C) at a given pressure to X pixel coordinate.
    /// The skew makes isotherms tilt — warmer temperatures shift right at lower pressures.
    public func temperatureToX(_ tempC: Double, atPressure p: Double) -> CGFloat {
        let logFraction = (logPBottom - log(p)) / logRange
        let skewOffset = logFraction * skewTan
        let normalizedT = (tempC - config.tMin) / tRange
        return plotArea.left + CGFloat(normalizedT + skewOffset) * plotArea.width
    }

    /// Convert (temperature, pressure) to pixel point.
    public func point(tempC: Double, pressureHPa: Double) -> CGPoint {
        CGPoint(
            x: temperatureToX(tempC, atPressure: pressureHPa),
            y: pressureToY(pressureHPa)
        )
    }

    // MARK: - Inverse transforms

    /// Convert Y pixel coordinate to pressure (hPa).
    public func yToPressure(_ y: CGFloat) -> Double {
        let fraction = Double((plotArea.bottom - y) / plotArea.height)
        return exp(logPBottom - fraction * logRange)
    }

    /// Convert X pixel coordinate at a given pressure to temperature (°C).
    public func xToTemperature(_ x: CGFloat, atPressure p: Double) -> Double {
        let logFraction = (logPBottom - log(p)) / logRange
        let skewOffset = logFraction * skewTan
        let normalizedT = Double((x - plotArea.left) / plotArea.width) - skewOffset
        return normalizedT * tRange + config.tMin
    }

    // MARK: - Pressure axis ticks

    /// Standard pressure levels for axis labels.
    public static let standardPressureLevels: [Double] = [
        1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100
    ]

    /// Pressure levels that fall within the configured range.
    public var visiblePressureLevels: [Double] {
        Self.standardPressureLevels.filter { $0 <= config.pBottom && $0 >= config.pTop }
    }
}
