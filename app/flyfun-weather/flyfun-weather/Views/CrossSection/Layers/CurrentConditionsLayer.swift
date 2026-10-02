import SwiftUI

/// Current conditions overlay: METAR airport columns + route SIGMET zones —
/// group `conditions`, **default OFF** (as on the web). Shown only when the
/// pilot turns it on, and then always from the latest live data: it reads
/// `VizRouteData.currentConditions`, which `extractVizData` rebuilds from the
/// snapshot the live layer patches in place.
///
/// SYNC — port of web/ts/visualization/cross-section/layers/current-conditions.ts
/// (constants, span helpers, draw order). The readout lines mirror the web
/// tooltip's "Current conditions" section (cross-section/interaction.ts).
///
///  - METAR airports: a column ±2 nm around the airport's along-route position,
///    5000 ft tall from the terrain surface, filled with the flight-category
///    colour. Where columns overlap, the airport closest to the route draws on
///    top; labels ("ICAO CAT") are placed closest-to-route first and skipped
///    when they would overprint one already placed.
///  - SIGMETs: a red diagonally-hatched zone spanning the enroute extent on X
///    (widened to a 5 nm minimum) and the vertical band on Y (a nil bound spans
///    the full plot height), labelled with the hazard, deeper red for SEV/EMBD.
///
/// iOS has no time-axis (airport-profile) cross-section, so the web's
/// `timeAxisMode` early return has no counterpart here.
struct CurrentConditionsLayer: CrossSectionLayerProtocol {
    let id = "current-conditions"
    let name = "Current conditions"
    let group: LayerGroup = .conditions

    static let columnHalfWidthNm: Double = 2
    static let columnHeightFt: Double = 5000
    static let sigmetMinSpanNm: Double = 5

    // MARK: - Pure helpers (shared with the readout and tests)

    /// ±2 nm column span (nm) around an airport's along-route position.
    static func columnSpanNm(_ enrouteNm: Double) -> (from: Double, to: Double) {
        (enrouteNm - columnHalfWidthNm, enrouteNm + columnHalfWidthNm)
    }

    /// SIGMET enroute span (nm), widened to a 5 nm minimum centred on the midpoint.
    static func sigmetSpanNm(_ fromNm: Double, _ toNm: Double) -> (from: Double, to: Double) {
        let lo = min(fromNm, toNm)
        let hi = max(fromNm, toNm)
        if hi - lo >= sigmetMinSpanNm { return (lo, hi) }
        let mid = (lo + hi) / 2
        return (mid - sigmetMinSpanNm / 2, mid + sigmetMinSpanNm / 2)
    }

    /// Airports ordered for drawing: farthest-from-route first, so the one
    /// closest to the route draws last (on top) where columns overlap.
    static func sortColumnsForDraw(_ airports: [VizMetarColumn]) -> [VizMetarColumn] {
        airports.sorted { $0.distanceFromRouteNm > $1.distanceFromRouteNm }
    }

    /// SEV/EMBD qualifiers get the deeper-red severe styling. Note this is wider
    /// than `SigmetAlongRoute.isSevere` (SEV only, the summary-bar signal) — it
    /// matches the web layer's `isSevereSigmet`.
    static func isSevereSigmet(_ qualifier: String?) -> Bool {
        guard let q = qualifier?.uppercased() else { return false }
        return q.contains("SEV") || q.contains("EMBD")
    }

    /// Flight-category colour. Uses the app's canonical category palette
    /// (`MapColors.flightCategory`, the same as `FlightCategoryBadge` and the
    /// observations table) rather than the web's hexes — iOS deliberately has a
    /// single MVFR colour (blue, where the web uses amber).
    static func categoryColor(_ category: String) -> Color {
        MapColors.flightCategory(category)
    }

    /// "1 SIGMET zone, 8 METAR columns" — the canvas accessibility value while
    /// the layer is on (a Canvas has no introspectable children, so this is how
    /// VoiceOver and the XCUI live-scenario journey read what it carries).
    static func accessibilitySummary(_ cc: VizCurrentConditions?) -> String {
        let zones = cc?.sigmets.count ?? 0
        let columns = cc?.airports.count ?? 0
        return "\(zones) SIGMET zone\(zones == 1 ? "" : "s"), \(columns) METAR column\(columns == 1 ? "" : "s")"
    }

    // MARK: - Readout (web tooltip parity)

    /// The readout lines at a cursor position: for each METAR column under the
    /// cursor "ICAO CAT · ceil FLxxx · vis …" then its raw METAR; for each SIGMET
    /// zone "SIGMET QUAL HAZARD FLbase–FLtop" (SFC / TOP for an open bound) then
    /// its raw text. With no cursor altitude only the X span is matched.
    static func readoutLines(
        _ cc: VizCurrentConditions?, distanceNm: Double, altitudeFt: Double?
    ) -> [String] {
        guard let cc else { return [] }
        var lines: [String] = []
        for a in cc.airports {
            let span = columnSpanNm(a.enrouteDistanceNm)
            guard distanceNm >= span.from, distanceNm <= span.to else { continue }
            if let alt = altitudeFt, alt < a.baseFt || alt > a.baseFt + columnHeightFt { continue }
            var parts = ["\(a.icao) \(a.flightCategory)"]
            if let ceil = a.ceilingFt { parts.append("ceil \(fmtFL(ceil))") }
            if let vis = a.visibilityM { parts.append("vis \(formatVisibility(vis))") }
            lines.append(parts.joined(separator: " · "))
            if let raw = a.metarRaw, !raw.isEmpty { lines.append(raw) }
        }
        for s in cc.sigmets {
            let span = sigmetSpanNm(s.enrouteFromNm, s.enrouteToNm)
            guard distanceNm >= span.from, distanceNm <= span.to else { continue }
            if let alt = altitudeFt {
                if let base = s.baseFt, alt < base { continue }
                if let top = s.topFt, alt > top { continue }
            }
            let baseLabel = s.baseFt.map { fmtFL($0) } ?? "SFC"
            let topLabel = s.topFt.map { fmtFL($0) } ?? "TOP"
            let band = "\(baseLabel)–\(topLabel)"
            let q = nonEmpty(s.qualifier).map { "\($0) " } ?? ""
            lines.append("SIGMET \(q)\(s.hazard) \(band)")
            if let raw = s.rawText, !raw.isEmpty { lines.append(raw) }
        }
        return lines
    }

    /// nil for a missing OR empty string — the web's truthiness test.
    static func nonEmpty(_ s: String?) -> String? {
        guard let s, !s.isEmpty else { return nil }
        return s
    }

    /// FL (≥ 5000 ft) or feet. Port of web `fmtFL` (interaction-utils.ts).
    static func fmtFL(_ ft: Double) -> String {
        if ft >= 5000 { return String(format: "FL%03d", Int((ft / 100).rounded())) }
        return "\(Int(ft.rounded()).formatted()) ft"
    }

    /// Metric visibility. Port of web `formatVisibility` (EU region), as used
    /// by `ForecastAirportCard`.
    static func formatVisibility(_ m: Double) -> String {
        if m >= 10000 { return ">10 km" }
        if m >= 5000 { return "\(Int((m / 1000).rounded())) km" }
        return "\(Int(m.rounded())) m"
    }

    // MARK: - Render

    func render(context: inout GraphicsContext, transform: CoordTransform, data: VizRouteData) {
        guard let cc = data.currentConditions else { return }
        // SIGMET zones first (broad hazard context), then METAR columns on top so
        // the airport flight category — the go/no-go readout — stays legible.
        for zone in cc.sigmets { drawSigmetZone(zone, context: &context, transform: transform) }
        drawMetarColumns(cc.airports, context: &context, transform: transform)
    }

    private func drawMetarColumns(
        _ airports: [VizMetarColumn], context: inout GraphicsContext, transform: CoordTransform
    ) {
        guard !airports.isEmpty else { return }
        let plotArea = transform.plotArea

        // Fills + outlines: farthest-from-route first → closest on top.
        for a in Self.sortColumnsForDraw(airports) {
            let span = Self.columnSpanNm(a.enrouteDistanceNm)
            let x0 = transform.distanceToX(span.from)
            let x1 = transform.distanceToX(span.to)
            let yBase = transform.altitudeToY(a.baseFt)
            let yTop = transform.altitudeToY(a.baseFt + Self.columnHeightFt)
            let rect = CGRect(x: x0, y: yTop, width: x1 - x0, height: yBase - yTop)
            let color = Self.categoryColor(a.flightCategory)
            context.fill(Path(rect), with: .color(color.opacity(0.32)))
            context.stroke(Path(rect), with: .color(color), lineWidth: 1.5)
        }

        // Labels: closest-to-route first (label priority), with X-collision
        // avoidance so clustered corridor airports (<4 nm apart) don't overprint.
        var placed: [(CGFloat, CGFloat)] = []
        let byProximity = airports.sorted { $0.distanceFromRouteNm < $1.distanceFromRouteNm }
        for a in byProximity {
            let cx = transform.distanceToX(a.enrouteDistanceNm)
            let resolved = context.resolve(
                Text("\(a.icao) \(a.flightCategory)")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Color(.sRGB, red: 17 / 255, green: 24 / 255, blue: 39 / 255))
            )
            let size = resolved.measure(in: CGSize(width: 200, height: 30))
            let lx0 = cx - size.width / 2
            let lx1 = cx + size.width / 2
            if placed.contains(where: { lx0 < $0.1 + 2 && lx1 > $0.0 - 2 }) { continue }
            placed.append((lx0, lx1))

            let yTop = transform.altitudeToY(a.baseFt + Self.columnHeightFt)
            let ty = max(plotArea.top + 2, yTop + 3)
            // A light plate stands in for the web's white text halo.
            context.fill(
                Path(roundedRect: CGRect(x: lx0 - 2, y: ty - 1, width: size.width + 4, height: size.height + 2),
                     cornerRadius: 2),
                with: .color(.white.opacity(0.85)))
            context.draw(resolved, at: CGPoint(x: cx, y: ty), anchor: .top)
        }
    }

    private func drawSigmetZone(
        _ s: VizSigmetZone, context: inout GraphicsContext, transform: CoordTransform
    ) {
        let plotArea = transform.plotArea
        let top = plotArea.top
        let bottom = plotArea.bottom

        let span = Self.sigmetSpanNm(s.enrouteFromNm, s.enrouteToNm)
        // Clamp to the plot area so a span past the route ends doesn't place its
        // label in the axis margin (the renderer already clips the fills).
        let x0 = max(plotArea.left, transform.distanceToX(span.from))
        let x1 = min(plotArea.right, transform.distanceToX(span.to))
        guard x1 > x0 else { return }

        // Vertical band: a nil bound spans the full plot height (unknown extent).
        var yTop = s.topFt.map { transform.altitudeToY($0) } ?? top
        var yBase = s.baseFt.map { transform.altitudeToY($0) } ?? bottom
        yTop = max(top, min(yTop, bottom))
        yBase = max(top, min(yBase, bottom))
        if yBase < yTop { swap(&yTop, &yBase) }

        let severe = Self.isSevereSigmet(s.qualifier)
        let (r, g, b): (Double, Double, Double) = severe ? (160.0, 0.0, 0.0) : (200.0, 45.0, 45.0)
        func rgb(_ alpha: Double) -> Color {
            Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: alpha)
        }

        let rect = CGRect(x: x0, y: yTop, width: x1 - x0, height: yBase - yTop)
        context.fill(Path(rect), with: .color(rgb(severe ? 0.26 : 0.16)))
        Self.drawDiagonalHatch(
            &context, rect: rect, color: rgb(severe ? 0.7 : 0.5), lineWidth: severe ? 2 : 1.5)
        context.stroke(Path(rect), with: .color(rgb(0.9)), lineWidth: severe ? 2 : 1.5)

        // Hazard label inside the zone (top-left).
        let label = Self.nonEmpty(s.qualifier).map { "\($0) \(s.hazard)" } ?? s.hazard
        let resolved = context.resolve(
            Text(label).font(.system(size: 11, weight: .bold)).foregroundColor(rgb(1))
        )
        let size = resolved.measure(in: CGSize(width: 300, height: 30))
        let origin = CGPoint(x: x0 + 4, y: yTop + 4)
        context.fill(
            Path(roundedRect: CGRect(x: origin.x - 2, y: origin.y - 1,
                                     width: size.width + 4, height: size.height + 2), cornerRadius: 2),
            with: .color(.white.opacity(0.85)))
        context.draw(resolved, at: origin, anchor: .topLeading)
    }

    /// 45° diagonal hatching clipped to a rectangle, snapped to a global grid so
    /// adjacent zones align (mirrors the web's `drawRectDiagonalHatch`).
    private static func drawDiagonalHatch(
        _ context: inout GraphicsContext, rect: CGRect, color: Color, lineWidth: CGFloat
    ) {
        guard rect.width > 0, rect.height > 0 else { return }
        let spacing: CGFloat = 8
        var hatchContext = context
        hatchContext.clip(to: Path(rect))
        let x0 = rect.minX, y0 = rect.minY, x1 = rect.maxX, y1 = rect.maxY
        // Lines y = x + c on a fixed c-grid.
        let cMin = y0 - x1
        let cMax = y1 - x0
        var c = (cMin / spacing).rounded(.up) * spacing
        var path = Path()
        while c <= cMax {
            let ax = max(x0, y0 - c)
            let bx = min(x1, y1 - c)
            if bx > ax {
                path.move(to: CGPoint(x: ax, y: ax + c))
                path.addLine(to: CGPoint(x: bx, y: bx + c))
            }
            c += spacing
        }
        hatchContext.stroke(path, with: .color(color), lineWidth: lineWidth)
    }
}
