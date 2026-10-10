import CoreGraphics
import Foundation

// =============================================================================
// SYNC — paired with web/ts/visualization/runway-wind/runway-wind-core.ts (#758).
//
// The runway + wind widget's rules: geometry in a unit square and every
// string. Nothing here draws; `RunwayWindView.swift` owns the SwiftUI and
// `runway-wind-view.ts` the SVG. The cases in RunwayWindRulesTests.swift and
// web/tests/unit/runway-wind-core.test.ts are one for one.
//
// Symbol map (Swift → TypeScript), so `/sync-ios-web` can diff them:
//
//   center/rimR/barMaxHalf/…      → CENTER / RIM_R / BAR_MAX_HALF / …
//   bearingVector / point(at:r:)  → bearingVector / pointAt
//   designator                    → designator
//   axisDifference                → axisDifference
//   barHalfLengths                → barHalfLengths
//   parallelOffsets               → parallelOffsets
//   runwayBars                    → runwayBars
//   visibleLabels                 → visibleLabels
//   arrowLength / gustShown       → arrowLength / gustShown
//   windArrow / variableArc       → windArrow / variableArc
//   windLabel / windMark          → windLabel / windMark
//   endLine / componentsLine      → endLine / componentsLine
//   ghostLine / sampleTimeLabel   → ghostLine / sampleTimeLabel
//   crosswindText / crosswindTone → crosswindText / crosswindTone
//   wind(of:source:)              → windOf
//   pair(airports:…)              → runwayWindPair
//   missingWindLabel              → missingWindLabel
//
// The widget draws one airport: its runways north-up at their TRUE heading,
// through the centre (schematic: parallels side by side, length roughly
// proportional), and the wind as an arrow from the upwind rim toward the
// centre — it points the way the wind BLOWS, drawn on the side it comes FROM.
// Idents are the painted (magnetic) numbers; headings and winds are both true,
// so no magnetic variation is applied. Do not "fix" that.
//
// Geometry is a unit square (0…1, y down, north up), never points. Knots round
// with `.rounded()` (half away from zero) — the web's `roundHalfAway`.
//
// Deliberate divergence, so `/sync-ios-web` does not re-flag it:
//  - **All-ends list.** The web opens a `<details>` "All runways"; iOS shows the
//    same `endLine` rows when the dial is tapped. Same strings, own affordance.
//
// Observations only: the crosswind figure takes the colour of the advisory
// the server computed (the METAR/TAF table's); no new threshold here.
// =============================================================================

nonisolated enum RunwayWindSize: String, Sendable {
    case compact, regular, inline
}

nonisolated enum RunwayWindRules {

    // MARK: Geometry constants (unit square)

    static let center: Double = 0.5
    /// Dial rim: the wind arrow starts here, on the upwind side.
    static let rimR: Double = 0.46
    /// Half-length of the longest runway.
    static let barMaxHalf: Double = 0.3
    /// Shortest bar as a fraction of the longest, so short strips stay visible.
    static let barMinRatio: Double = 0.4
    /// Distance between adjacent parallel runway centre lines.
    static let parallelGap: Double = 0.08
    /// Two runways within this many degrees (mod 180) are drawn as parallels.
    static let parallelToleranceDeg: Double = 10
    /// Ident labels sit this far beyond the bar end, on the approach side.
    static let labelGap: Double = 0.07
    /// Parallel runways' labels are pushed this many times further apart than
    /// their bars, so "05L" and "05R" don't overprint.
    static let labelSpread: Double = 2.5
    /// Arrow length at and above `arrowFullScaleKt`.
    static let arrowMaxLen: Double = 0.3
    /// Arrow length floor, so a 2 kt wind still has a shaft behind its head.
    static let arrowMinLen: Double = 0.12
    static let arrowFullScaleKt: Double = 35
    /// Dashed circle drawn for a VRB wind.
    static let vrbCircleR: Double = 0.38

    /// Unit vector for a true bearing, north up, y down.
    static func bearingVector(_ deg: Double) -> CGPoint {
        let r = deg * .pi / 180
        return CGPoint(x: sin(r), y: -cos(r))
    }

    /// The point at `bearing` and distance `r` from `origin` (default the centre).
    static func point(at bearing: Double, r: Double, origin: CGPoint = CGPoint(x: 0.5, y: 0.5)) -> CGPoint {
        let v = bearingVector(bearing)
        return CGPoint(x: origin.x + v.x * r, y: origin.y + v.y * r)
    }

    // MARK: Runways

    struct RunwayLabel: Equatable {
        let ident: String
        let at: CGPoint
    }

    struct RunwayBar: Equatable {
        let id: String
        /// Threshold of the first listed end (bearing heading + 180 from the centre).
        let a: CGPoint
        /// The opposite end.
        let b: CGPoint
        let hard: Bool?
        let labels: [RunwayLabel]
    }

    /// L / C / R from an ident ("05L" → "L"), or nil.
    static func designator(_ ident: String) -> Character? {
        guard let c = ident.trimmingCharacters(in: .whitespaces).uppercased().last else { return nil }
        return c == "L" || c == "C" || c == "R" ? c : nil
    }

    private static func axis(_ r: RunwayInfo) -> Double { r.ends[0].headingTrue }

    /// Angle between two runway axes, 0…90 (a runway is the same both ways).
    static func axisDifference(_ a: Double, _ b: Double) -> Double {
        let d = ((a - b).truncatingRemainder(dividingBy: 180) + 180).truncatingRemainder(dividingBy: 180)
        return min(d, 180 - d)
    }

    /// Bar half-length for each runway: proportional to length, floored.
    /// Unknown lengths draw full length — nothing says the strip is short.
    static func barHalfLengths(_ runways: [RunwayInfo]) -> [Double] {
        let known = runways.compactMap(\.lengthFt).filter { $0 > 0 }
        let longest = Double(known.max() ?? 0)
        return runways.map { r in
            guard let length = r.lengthFt, length > 0, longest > 0 else { return barMaxHalf }
            return barMaxHalf * max(barMinRatio, Double(length) / longest)
        }
    }

    /// Perpendicular offset of each runway's centre line from the dial centre.
    /// Near-parallel runways are spread side by side: by their L/C/R
    /// designator when every one has one (L to the left of its own end's
    /// heading), else in listed order.
    static func parallelOffsets(_ runways: [RunwayInfo]) -> [CGPoint] {
        var offsets = Array(repeating: CGPoint.zero, count: runways.count)
        var assigned = Array(repeating: false, count: runways.count)
        for i in runways.indices where !assigned[i] {
            var group = [i]
            for j in runways.indices where j > i && !assigned[j]
                && axisDifference(axis(runways[i]), axis(runways[j])) <= parallelToleranceDeg {
                group.append(j)
            }
            group.forEach { assigned[$0] = true }
            guard group.count > 1 else { continue }
            let designators = group.map { designator(runways[$0].ends[0].ident) }
            if designators.allSatisfy({ $0 != nil }) {
                let hasCentre = designators.contains("C")
                for (n, k) in group.enumerated() {
                    let d = designators[n]
                    let slot: Double = d == "C" ? 0 : (d == "L" ? -1 : 1) * (hasCentre ? 1 : 0.5)
                    // Right of the end's own heading is bearing + 90; L is the negative slot.
                    let v = bearingVector(axis(runways[k]) + 90)
                    offsets[k] = CGPoint(x: v.x * slot * parallelGap, y: v.y * slot * parallelGap)
                }
            } else {
                let v = bearingVector(axis(runways[i]) + 90)
                for (n, k) in group.enumerated() {
                    let slot = Double(n) - Double(group.count - 1) / 2
                    offsets[k] = CGPoint(x: v.x * slot * parallelGap, y: v.y * slot * parallelGap)
                }
            }
        }
        return offsets
    }

    /// Every runway as a bar through the centre at its true heading, with an
    /// ident label beyond each end on its approach side.
    static func runwayBars(_ runways: [RunwayInfo]) -> [RunwayBar] {
        let drawable = runways.filter { !$0.ends.isEmpty }
        let halves = barHalfLengths(drawable)
        let offsets = parallelOffsets(drawable)
        return drawable.enumerated().map { i, r in
            let origin = CGPoint(x: center + offsets[i].x, y: center + offsets[i].y)
            let labelOrigin = CGPoint(x: center + offsets[i].x * labelSpread, y: center + offsets[i].y * labelSpread)
            let ax = axis(r)
            return RunwayBar(
                id: r.id,
                a: point(at: ax + 180, r: halves[i], origin: origin),
                b: point(at: ax, r: halves[i], origin: origin),
                hard: r.hard,
                // Landing on an end you fly its heading, so you arrive from heading + 180.
                labels: r.ends.map {
                    RunwayLabel(ident: $0.ident, at: point(at: $0.headingTrue + 180, r: halves[i] + labelGap, origin: labelOrigin))
                }
            )
        }
    }

    /// Which labels a size shows: all (regular), the best end only (compact,
    /// all when there is no best end), none (inline).
    static func visibleLabels(_ bars: [RunwayBar], size: RunwayWindSize, bestEnd: String?) -> [RunwayLabel] {
        let all = bars.flatMap(\.labels)
        switch size {
        case .inline: return []
        case .compact where bestEnd != nil: return all.filter { $0.ident == bestEnd }
        default: return all
        }
    }

    // MARK: Wind

    struct WindArrow: Equatable {
        /// On the rim, upwind.
        let from: CGPoint
        /// Arrow head, toward the centre; length on a fixed kt scale.
        let to: CGPoint
        /// Where a shown gust extends the arrow to, or nil.
        let gustTo: CGPoint?
    }

    struct VariableArc: Equatable {
        let from: CGPoint
        let to: CGPoint
        /// Clockwise sweep from `variableFrom` to `variableTo`, degrees.
        let sweepDeg: Double
        /// Start bearing (true), for drawing the arc.
        let startDeg: Double
        let r: Double
    }

    enum WindMark: Equatable {
        case arrow(WindArrow, arc: VariableArc?)
        case vrb(r: Double, label: String)
        case calm(label: String)
        case missing(label: String)
    }

    static func arrowLength(_ kt: Double) -> Double {
        min(arrowMaxLen, max(arrowMinLen, kt / arrowFullScaleKt * arrowMaxLen))
    }

    /// Whether a gust is shown beside `base` (the shared suppression rule).
    static func gustShown(base: Double, gust: Double?) -> Bool {
        guard let gust else { return false }
        return Int(gust.rounded()) - Int(base.rounded()) >= WindFormat.gustDisplayMinExcessKt
    }

    /// The arrow for a directional wind; nil for calm, VRB or no wind.
    static func windArrow(_ w: WindSample) -> WindArrow? {
        guard !w.isCalm, !w.isVariable, let dir = w.directionTrue, let speed = w.speedKt else { return nil }
        let d = Double(dir)
        let gust = w.gustKt.map(Double.init)
        return WindArrow(
            from: point(at: d, r: rimR),
            to: point(at: d, r: rimR - arrowLength(Double(speed))),
            gustTo: gustShown(base: Double(speed), gust: gust) ? point(at: d, r: rimR - arrowLength(gust!)) : nil
        )
    }

    /// The rim arc of a `dddVddd` range, or nil.
    static func variableArc(_ w: WindSample) -> VariableArc? {
        guard let from = w.variableFrom, let to = w.variableTo else { return nil }
        let sweep = ((to - from) % 360 + 360) % 360
        return VariableArc(
            from: point(at: Double(from), r: rimR), to: point(at: Double(to), r: rimR),
            sweepDeg: Double(sweep), startDeg: Double(from), r: rimR
        )
    }

    private static func pad2(_ n: Int) -> String { String(format: "%02d", n) }

    /// The wind as reported: "270@12G22", "270@12 240V300", "VRB 04", "VRB 04G15", "Calm".
    static func windLabel(_ w: WindSample) -> String {
        if w.isCalm { return "Calm" }
        guard let speed = w.speedKt else { return "" }
        let s = Double(speed)
        if w.isVariable || w.directionTrue == nil {
            let gust = gustShown(base: s, gust: w.gustKt.map(Double.init)) ? "G\(Int(Double(w.gustKt!).rounded()))" : ""
            return "VRB \(pad2(Int(s.rounded())))\(gust)"
        }
        let base = WindFormat.wind(speed: s, dir: w.directionTrue.map(Double.init), gust: w.gustKt.map(Double.init)) ?? ""
        if let from = w.variableFrom, let to = w.variableTo {
            return "\(base) \(from.paddedHeading)V\(to.paddedHeading)"
        }
        return base
    }

    /// What the dial draws for the primary wind; `missing` says why there is none.
    static func windMark(_ w: WindAtAirport?, missing: String) -> WindMark {
        guard let w else { return .missing(label: missing) }
        let s = w.wind
        if s.isCalm { return .calm(label: "Calm") }
        if s.isVariable || s.directionTrue == nil { return .vrb(r: vrbCircleR, label: windLabel(s)) }
        guard let arrow = windArrow(s) else { return .missing(label: missing) }
        return .arrow(arrow, arc: variableArc(s))
    }

    // MARK: Words

    private static func headPart(_ e: EndComponents) -> String {
        let hw = Int(e.headwindKt.rounded())
        return hw < 0 ? "\(-hw) kt tail" : "\(hw) kt head"
    }

    private static func crossPart(_ e: EndComponents, _ s: WindSample) -> String {
        let xw = Int(abs(e.crosswindKt).rounded())
        let sideName = e.side ?? ""
        let side = xw != 0 && !sideName.isEmpty ? " from \(sideName)" : ""
        var out = "\(xw) kt X-wind\(side)"
        let gx = e.gustCrosswindKt.map { Int(abs($0).rounded()) }
        let gustOn = gx.map { $0 - xw >= WindFormat.gustDisplayMinExcessKt } ?? false
        if gustOn, let gx { out += " (G \(gx))" }
        // The variable range's worst case, when it says more than the figures above.
        let mx = Int(e.maxCrosswindKt.rounded())
        if s.variableFrom != nil && mx > max(xw, gustOn ? gx! : 0) { out += " · up to \(mx) kt" }
        return out
    }

    /// One end's components: "27 · 6 kt head · 11 kt X-wind from left (G 17)".
    static func endLine(_ e: EndComponents, _ s: WindSample) -> String {
        "\(e.ident) · \(headPart(e)) · \(crossPart(e, s))"
    }

    private static func bestEnd(_ w: WindAtAirport) -> EndComponents? {
        guard let best = w.bestEnd else { return nil }
        return w.ends.first { $0.ident == best }
    }

    /// The dial's one line for a wind: its best end, or what stands in for it.
    static func componentsLine(_ w: WindAtAirport?, missing: String) -> String {
        guard let w else { return missing }
        let s = w.wind
        if s.isCalm { return "Calm" }
        if s.isVariable || s.directionTrue == nil {
            guard let speed = s.speedKt else { return missing }
            let worst = w.ends.isEmpty
                ? Int(Double(max(speed, s.gustKt ?? 0)).rounded())
                : w.ends.map { Int($0.maxCrosswindKt.rounded()) }.max()!
            return "\(windLabel(s)) · up to \(worst) kt X-wind"
        }
        guard let best = bestEnd(w) else { return "No runway data" }
        return endLine(best, s)
    }

    /// "14Z" for a whole hour, else "1437Z"; "" without a time. A time without
    /// a zone is UTC (the server's convention).
    static func sampleTimeLabel(_ iso: String?) -> String {
        guard let iso, !iso.isEmpty else { return "" }
        guard let date = Date.parseISO8601(iso) ?? Date.parseISO8601(iso + "Z") else { return "" }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.hour, .minute], from: date)
        let hh = pad2(c.hour ?? 0)
        let mm = c.minute ?? 0
        return mm == 0 ? "\(hh)Z" : "\(hh)\(pad2(mm))Z"
    }

    /// The ghost (TAF at ETA) line: "TAF 14Z 250@18G28 · 27 · 14 kt X-wind from left".
    static func ghostLine(_ w: WindAtAirport) -> String {
        let s = w.wind
        let head = [s.source.uppercased(), sampleTimeLabel(s.time), windLabel(s)]
            .filter { !$0.isEmpty }.joined(separator: " ")
        if s.isCalm || s.isVariable || s.directionTrue == nil { return head }
        guard let best = bestEnd(w) else { return head }
        return "\(head) · \(best.ident) · \(crossPart(best, s))"
    }

    /// The crosswind part of `componentsLine` / `ghostLine`, the only part the
    /// advisory tone colours; nil when the line has none.
    static func crosswindText(_ w: WindAtAirport?) -> String? {
        guard let w else { return nil }
        let s = w.wind
        if s.isCalm || s.isVariable || s.directionTrue == nil { return nil }
        return bestEnd(w).map { crossPart($0, s) }
    }

    /// The crosswind figure's tone: the server's advisory tier; green is quiet.
    static func crosswindTone(_ advisory: String?) -> String? {
        advisory == "amber" || advisory == "red" ? advisory : nil
    }

    // MARK: Placement helpers

    static func wind(of p: RunwayWindPicture?, source: String) -> WindAtAirport? {
        p?.winds.first { $0.wind.source == source }
    }

    struct Dial: Equatable, Identifiable {
        /// "DEP" or "ARR".
        let role: String
        let icao: String
        /// Nil when the airport has no picture (no runway data, or none fetched).
        let picture: RunwayWindPicture?
        let primary: WindAtAirport?
        /// The TAF at ETA for the destination; never for the departure ("now").
        let ghost: WindAtAirport?

        var id: String { role }
    }

    /// The departure + destination dials, or [] when neither airport has a
    /// picture (an older server: the tab renders as before).
    static func pair(airports: [AirportObservation]?, departure: String?, destination: String?) -> [Dial] {
        func find(_ icao: String) -> AirportObservation? {
            (airports ?? []).first { $0.icao.uppercased() == icao.uppercased() }
        }
        var dials: [Dial] = []
        func add(_ role: String, _ icao: String?) {
            guard let icao, !icao.isEmpty else { return }
            let picture = find(icao)?.runwayWind
            dials.append(Dial(
                role: role, icao: icao.uppercased(), picture: picture,
                primary: wind(of: picture, source: "metar"),
                ghost: role == "ARR" ? wind(of: picture, source: "taf") : nil
            ))
        }
        add("DEP", departure)
        if let destination, destination.uppercased() != (departure ?? "").uppercased() { add("ARR", destination) }
        return dials.contains { $0.picture != nil } ? dials : []
    }

    /// What stands in for a missing primary wind.
    static func missingWindLabel(_ d: Dial) -> String {
        d.picture == nil ? "No runway data" : "No METAR"
    }
}
