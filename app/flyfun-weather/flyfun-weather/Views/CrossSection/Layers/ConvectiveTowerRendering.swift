import SwiftUI

// MARK: - Convective tower rendering primitives
//
// Shared helpers used by the NWP and Thermo convective layers. Both render a
// vertical "tower" column (background wash + body fill + diagonal hatching +
// edge outline + anvil strip + CB/TCU/+TS pill). They differ only in how the
// tower base/top altitudes are derived.

/// Anvil strip thickness at tower top.
let CONVECTIVE_STRIP_HEIGHT: CGFloat = 5

/// Compute the half-open column x-range for a route point: midpoint to the
/// previous point on the left, midpoint to the next point on the right (with
/// plot edges as fallbacks at the route ends).
func columnBounds(
    pointIndex i: Int,
    in points: [VizPoint],
    transform: CoordTransform
) -> (left: CGFloat, right: CGFloat) {
    let plot = transform.plotArea
    let x = transform.distanceToX(points[i].distanceNm)
    let xLeft = i == 0
        ? plot.left
        : (transform.distanceToX(points[i - 1].distanceNm) + x) / 2
    let xRight = i == points.count - 1
        ? plot.right
        : (x + transform.distanceToX(points[i + 1].distanceNm)) / 2
    return (xLeft, xRight)
}

/// Draw a complete convective tower column at a given column extent and altitudes.
func drawConvectiveTower(
    _ context: inout GraphicsContext,
    transform: CoordTransform,
    risk: String,
    xLeft: CGFloat,
    xRight: CGFloat,
    yTop: CGFloat,
    yBase: CGFloat
) {
    let plot = transform.plotArea
    let colWidth = xRight - xLeft
    let towerHeight = yBase - yTop
    guard towerHeight > 0, colWidth > 0 else { return }

    // 1. Subtle full-height background wash.
    let bgWash = ColorScales.convectiveBgWash(risk)
    context.fill(
        Path(CGRect(x: xLeft, y: plot.top, width: colWidth, height: plot.height)),
        with: .color(bgWash)
    )

    // 2. Tower body fill.
    let towerRect = CGRect(x: xLeft, y: yTop, width: colWidth, height: towerHeight)
    context.fill(Path(towerRect), with: .color(ColorScales.convectiveTowerFill(risk)))

    // 3. Diagonal hatching, clipped to the tower rect.
    var hatched = context
    hatched.clip(to: Path(towerRect))
    drawDiagonalHatching(&hatched, rect: towerRect, color: ColorScales.convectiveHatchColor(risk))

    // 4. Edge outline.
    context.stroke(
        Path(towerRect.insetBy(dx: 0.5, dy: 0.5)),
        with: .color(ColorScales.convectiveEdgeColor(risk)),
        lineWidth: 1.5
    )

    // 5. Anvil strip at top — extends slightly beyond the column for shape.
    let anvilExtend = min(colWidth * 0.2, 8)
    let anvilRect = CGRect(
        x: xLeft - anvilExtend,
        y: yTop,
        width: colWidth + anvilExtend * 2,
        height: CONVECTIVE_STRIP_HEIGHT
    )
    context.fill(Path(anvilRect), with: .color(ColorScales.convectiveStripColor(risk)))

    // 6. Convection label (TCU / CB / +TS) for moderate+ when the column is wide enough.
    if colWidth > 18, risk != "low", risk != "marginal" {
        let cx = (xLeft + xRight) / 2
        let visTop = max(yTop, plot.top)
        let visBase = min(yBase, plot.bottom)
        let cy = visTop + (visBase - visTop) * 0.25
        drawCBLabel(&context, at: CGPoint(x: cx, y: cy), risk: risk)
    }
}

/// Diagonal hatching at a fixed pixel spacing within the given rect.
func drawDiagonalHatching(
    _ context: inout GraphicsContext,
    rect: CGRect,
    color: Color
) {
    let spacing: CGFloat = 8
    let totalSpan = rect.width + rect.height

    var path = Path()
    var offset: CGFloat = -rect.height
    while offset < totalSpan {
        path.move(to: CGPoint(x: rect.minX + offset, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + offset + rect.height, y: rect.minY))
        offset += spacing
    }
    context.stroke(path, with: .color(color), lineWidth: 1)
}

/// Map convective risk → METAR-style label.
private func riskToLabel(_ risk: String) -> String {
    switch risk {
    case "extreme": return "+TS"
    case "high": return "CB"
    default: return "TCU"
    }
}

/// Draw a TCU/CB/+TS pill label at the given centre point.
func drawCBLabel(
    _ context: inout GraphicsContext,
    at center: CGPoint,
    risk: String
) {
    let label = riskToLabel(risk)
    let resolved = context.resolve(
        Text(label)
            .font(.system(size: 10, weight: .bold))
            .foregroundColor(ColorScales.convectiveCBLabelColor(risk))
    )
    let size = resolved.measure(in: CGSize(width: 100, height: 30))
    let pillW = size.width + 6
    let pillH: CGFloat = 14
    let pillRect = CGRect(
        x: center.x - pillW / 2,
        y: center.y - pillH / 2,
        width: pillW,
        height: pillH
    )

    context.fill(
        Path(roundedRect: pillRect, cornerRadius: 3),
        with: .color(ColorScales.skyBlue.opacity(0.9))
    )
    context.draw(resolved, at: center, anchor: .center)
}

/// Estimate a sensible visual tower top when the thermodynamic EL is suspiciously
/// shallow on coarse pressure levels. Falls back to freezing/-10/-20 levels.
/// Mirrors web's `estimateTowerTop` in thermo-convective-bg.ts.
func estimateThermoTowerTop(
    point p: VizPoint,
    baseFt: Double,
    thermodynamicElFt: Double
) -> Double {
    let MIN_RELIABLE_TOWER_FT = 3000.0
    let depth = thermodynamicElFt - baseFt
    if depth >= MIN_RELIABLE_TOWER_FT { return thermodynamicElFt }

    let risk = p.convectiveRisk
    let alt = p.altitudeLines

    if risk == "low", let fl = alt.freezingLevelFt {
        // Shallow Cu typically tops near or just above the freezing level.
        return max(thermodynamicElFt, fl + 2000)
    }
    if risk == "moderate" || risk == "high" || risk == "extreme" {
        if let fl = alt.minus20cLevelFt { return max(thermodynamicElFt, fl) }
        if let fl = alt.minus10cLevelFt { return max(thermodynamicElFt, fl) }
    }
    return max(thermodynamicElFt, baseFt + 4000)
}
