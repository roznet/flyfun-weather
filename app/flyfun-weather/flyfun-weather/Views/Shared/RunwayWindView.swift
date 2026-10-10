import SwiftUI

// Runway + wind widget (#758) — the drawing. All rules live in
// `RunwayWindRules.swift`.
//
// SYNC — paired with web/ts/visualization/runway-wind/runway-wind-view.ts.
// This file is the SwiftUI equivalent of that SVG and holds no rules of its
// own: a position, a label or a string changes in `RunwayWindRules.swift` and
// in `runway-wind-core.ts`, not here.
//
// `RunwayWindDialView` takes a dial and a size and never knows where it is
// placed; `RunwayWindPairCard` (departure + destination) is a layout
// container only.

private typealias R = RunwayWindRules

/// The dial alone: runways, the wind mark and (destination) the TAF ghost
/// arrow, drawn over the rules' unit square scaled to the view's size.
struct RunwayWindCanvas: View {
    let dial: RunwayWindRules.Dial
    let size: RunwayWindSize

    var body: some View {
        Canvas { context, canvasSize in
            let side = min(canvasSize.width, canvasSize.height)
            let ox = (canvasSize.width - side) / 2
            let oy = (canvasSize.height - side) / 2
            func p(_ u: CGPoint) -> CGPoint { CGPoint(x: ox + u.x * side, y: oy + u.y * side) }
            func d(_ u: Double) -> CGFloat { CGFloat(u) * side }
            let center = p(CGPoint(x: R.center, y: R.center))
            let scale = side / 100 // stroke widths are in the web's viewBox units

            // Rim.
            context.stroke(
                Path(ellipseIn: CGRect(x: center.x - d(R.rimR), y: center.y - d(R.rimR), width: d(R.rimR) * 2, height: d(R.rimR) * 2)),
                with: .color(Theme.border), lineWidth: max(1, 1.2 * scale)
            )
            if size != .inline {
                context.draw(
                    Text("N").font(.system(size: max(7, 6 * scale), weight: .semibold)).foregroundStyle(Theme.textMuted),
                    at: p(CGPoint(x: R.center, y: R.center - R.rimR + 0.07))
                )
            }

            // Runways.
            let bars = R.runwayBars(dial.picture?.runways ?? [])
            for bar in bars {
                var path = Path()
                path.move(to: p(bar.a))
                path.addLine(to: p(bar.b))
                context.stroke(
                    path, with: .color(Theme.text.opacity(0.55)),
                    style: StrokeStyle(lineWidth: 4.5 * scale, lineCap: .butt, dash: bar.hard == false ? [3 * scale, 2 * scale] : [])
                )
            }
            let best = dial.primary?.bestEnd
            for label in R.visibleLabels(bars, size: size, bestEnd: best) {
                let isBest = label.ident == best
                context.draw(
                    Text(label.ident)
                        .font(.system(size: max(8, 6.5 * scale), weight: isBest ? .bold : .regular))
                        .foregroundStyle(isBest ? Theme.text : Theme.textMuted),
                    at: p(label.at)
                )
            }

            // Ghost (TAF at ETA) under the primary mark.
            if size != .inline, let ghost = dial.ghost, let arrow = R.windArrow(ghost.wind) {
                drawArrow(context, arrow, p: p, scale: scale, ghost: true)
            }

            switch R.windMark(dial.primary, missing: R.missingWindLabel(dial)) {
            case .arrow(let arrow, let arc):
                if let arc {
                    var path = Path()
                    // SwiftUI angles: 0 = +x, clockwise on screen (y down);
                    // a true bearing b is (b - 90)°.
                    path.addArc(center: center, radius: d(arc.r),
                                startAngle: .degrees(arc.startDeg - 90),
                                endAngle: .degrees(arc.startDeg + arc.sweepDeg - 90),
                                clockwise: false)
                    context.stroke(path, with: .color(Theme.primary.opacity(0.55)), lineWidth: 3 * scale)
                }
                drawArrow(context, arrow, p: p, scale: scale, ghost: false)
            case .vrb(let r, let label):
                context.stroke(
                    Path(ellipseIn: CGRect(x: center.x - d(r), y: center.y - d(r), width: d(r) * 2, height: d(r) * 2)),
                    with: .color(Theme.primary), style: StrokeStyle(lineWidth: 1.5 * scale, dash: [3 * scale, 2.5 * scale])
                )
                if size != .inline { drawMarkLabel(context, label, at: p(CGPoint(x: R.center, y: 0.9)), scale: scale, muted: false) }
            case .calm(let label):
                let r = d(0.03)
                context.fill(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                             with: .color(Theme.primary))
                if size != .inline { drawMarkLabel(context, label, at: p(CGPoint(x: R.center, y: 0.9)), scale: scale, muted: false) }
            case .missing(let label):
                if size != .inline { drawMarkLabel(context, label, at: p(CGPoint(x: R.center, y: 0.9)), scale: scale, muted: true) }
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityElement()
        .accessibilityLabel("\(dial.icao): \(R.componentsLine(dial.primary, missing: R.missingWindLabel(dial)))")
    }

    private func drawMarkLabel(_ context: GraphicsContext, _ text: String, at point: CGPoint, scale: CGFloat, muted: Bool) {
        context.draw(
            Text(text).font(.system(size: max(8, 7 * scale))).italic(muted)
                .foregroundStyle(muted ? Theme.textMuted : Theme.text),
            at: point
        )
    }

    private func drawArrow(_ context: GraphicsContext, _ a: RunwayWindRules.WindArrow, p: (CGPoint) -> CGPoint, scale: CGFloat, ghost: Bool) {
        let from = p(a.from)
        let to = p(a.to)
        let dx = to.x - from.x
        let dy = to.y - from.y
        let len = max(hypot(dx, dy), 0.0001)
        let ux = dx / len
        let uy = dy / len
        let head = 4.5 * scale
        let wing = 2.8 * scale
        let base = CGPoint(x: to.x - ux * head, y: to.y - uy * head)
        let colour = ghost ? Theme.textMuted : Theme.primary
        let width = (ghost ? 1.2 : 2.4) * scale

        var shaft = Path()
        shaft.move(to: from)
        shaft.addLine(to: base)
        context.stroke(shaft, with: .color(colour), style: StrokeStyle(lineWidth: width, lineCap: .round))
        if let gustTo = a.gustTo {
            var gust = Path()
            gust.move(to: to)
            gust.addLine(to: p(gustTo))
            context.stroke(gust, with: .color(colour),
                           style: StrokeStyle(lineWidth: 1.2 * scale, lineCap: .round, dash: [2 * scale, 1.5 * scale]))
        }
        var tip = Path()
        tip.move(to: to)
        tip.addLine(to: CGPoint(x: base.x - uy * wing, y: base.y + ux * wing))
        tip.addLine(to: CGPoint(x: base.x + uy * wing, y: base.y - ux * wing))
        tip.closeSubpath()
        if ghost {
            context.stroke(tip, with: .color(colour), lineWidth: 1 * scale)
        } else {
            context.fill(tip, with: .color(colour))
        }
    }
}

/// One dial with its heading and text lines. Tap shows every runway end.
struct RunwayWindDialView: View {
    let dial: RunwayWindRules.Dial
    let size: RunwayWindSize
    @State private var showsAllEnds = false

    var body: some View {
        if size == .inline {
            RunwayWindCanvas(dial: dial, size: size)
        } else {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(dial.role)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.textMuted)
                    Text(dial.icao)
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(Theme.text)
                    Spacer(minLength: 0)
                    if let primary = dial.primary, size == .regular {
                        Text(R.windLabel(primary.wind))
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                    }
                }
                RunwayWindCanvas(dial: dial, size: size)
                    .frame(maxWidth: size == .regular ? 170 : 96)
                    .frame(maxWidth: .infinity)
                line(R.componentsLine(dial.primary, missing: R.missingWindLabel(dial)), wind: dial.primary, muted: false)
                    .accessibilityIdentifier("runwayWindLine\(dial.role)")
                if size == .regular, let ghost = dial.ghost {
                    line(R.ghostLine(ghost), wind: ghost, muted: true)
                }
                if showsAllEnds, let primary = dial.primary, primary.ends.count > 1 {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(primary.ends, id: \.ident) { end in
                            Text(R.endLine(end, primary.wind))
                                .font(.caption)
                                .foregroundStyle(Theme.text)
                        }
                        Text("Head/crosswind on each runway end from the latest METAR. “up to” is the worst case over a variable range or gust. True north up; runway numbers as painted.")
                            .font(.caption2)
                            .foregroundStyle(Theme.textMuted)
                    }
                    .transition(.opacity)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .onTapGesture {
                withAnimation(.snappy(duration: 0.2)) { showsAllEnds.toggle() }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("runwayWind\(dial.role)")
            .accessibilityHint(dial.primary.map { $0.ends.count > 1 } == true ? "Shows every runway end" : "")
        }
    }

    /// A text line with only its crosswind part toned (amber/red); green is quiet.
    private func line(_ text: String, wind: WindAtAirport?, muted: Bool) -> some View {
        var attributed = AttributedString(text)
        attributed.foregroundColor = muted ? Theme.textMuted : Theme.text
        if let tone = R.crosswindTone(wind?.advisory),
           let cross = R.crosswindText(wind),
           let range = attributed.range(of: cross, options: .backwards) {
            attributed[range].foregroundColor = tone == "red" ? Theme.red : Theme.amber
            attributed[range].font = (muted ? Font.caption : Font.subheadline).weight(.semibold)
        }
        return Text(attributed).font(muted ? .caption : .subheadline)
    }
}

/// Departure + destination dials (#758), side by side when they fit, stacked
/// otherwise. A layout container only.
struct RunwayWindPairCard: View {
    let dials: [RunwayWindRules.Dial]
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("Runway winds")
                .font(.headline)
                .foregroundStyle(Theme.text)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: Theme.spacingM) { dialViews(size) }
                VStack(alignment: .leading, spacing: Theme.spacingM) { dialViews(.regular) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("runwayWindPair")
    }

    /// Regular on iPad / landscape, compact side by side on an iPhone in portrait.
    private var size: RunwayWindSize { horizontalSizeClass == .regular ? .regular : .compact }

    @ViewBuilder
    private func dialViews(_ size: RunwayWindSize) -> some View {
        ForEach(dials) { dial in
            RunwayWindDialView(dial: dial, size: size)
                .frame(minWidth: size == .regular ? 200 : 130)
        }
    }
}
