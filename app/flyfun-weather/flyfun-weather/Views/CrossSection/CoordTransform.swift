import Foundation

/// Maps between data coordinates (distance_nm, altitude_ft) and pixel coordinates.
struct CoordTransform {
    let plotArea: PlotArea
    let maxDistanceNm: Double
    let maxAltitudeFt: Double

    struct PlotArea {
        let left: CGFloat
        let top: CGFloat
        let width: CGFloat
        let height: CGFloat

        var right: CGFloat { left + width }
        var bottom: CGFloat { top + height }
    }

    static let margins = (left: CGFloat(50), right: CGFloat(40), top: CGFloat(20), bottom: CGFloat(30))

    /// Gap between a left-axis value label and the plot edge. The route graph
    /// reserves `margins.left - axisLabelPadding` for its fixed-width Y labels so
    /// its plot area lines up with the cross-section's (#6).
    static let axisLabelPadding: CGFloat = 6

    init(size: CGSize, maxDistanceNm: Double, maxAltitudeFt: Double) {
        self.maxDistanceNm = maxDistanceNm
        self.maxAltitudeFt = maxAltitudeFt
        self.plotArea = PlotArea(
            left: Self.margins.left,
            top: Self.margins.top,
            width: size.width - Self.margins.left - Self.margins.right,
            height: size.height - Self.margins.top - Self.margins.bottom
        )
    }

    func distanceToX(_ distanceNm: Double) -> CGFloat {
        plotArea.left + CGFloat(distanceNm / maxDistanceNm) * plotArea.width
    }

    func altitudeToY(_ altitudeFt: Double) -> CGFloat {
        plotArea.top + (1.0 - CGFloat(altitudeFt / maxAltitudeFt)) * plotArea.height
    }

    func xToDistance(_ x: CGFloat) -> Double {
        Double((x - plotArea.left) / plotArea.width) * maxDistanceNm
    }

    func yToAltitude(_ y: CGFloat) -> Double {
        (1.0 - Double((y - plotArea.top) / plotArea.height)) * maxAltitudeFt
    }
}
