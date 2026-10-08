import SwiftUI

// =============================================================================
// SYNC — paired with web/ts/visualization/observed/ribbon-core.ts (#690).
//
// The Observed route ribbon's rules, extracted from `RouteRibbonView` so the
// two clients hold one comparable set: the cross-track mapping, the colour
// ladders, which bands get an arrow, and what every mark is called. Nothing
// here draws; `ObservedNutshellView.swift` owns the SwiftUI and
// `ribbon-view.ts` the SVG.
//
// Symbol map (Swift → TypeScript), so `/sync-ios-web` can diff them:
//
//   height/sigmetY/…/inset  → RIBBON_HEIGHT / SIGMET_Y / … / INSET
//   x(_:routeNm:width:)     → xForNm
//   y(cross:corridor:)      → yForCross
//   categoryColor           → categoryColour
//   dbzColor                → dbzColour
//   bandColor               → bandFill  (+ CORE_BAND_OPACITY)
//   radarColor              → radarFill
//   motionArrow             → motionArrowDir
//   sigmetText              → sigmetText
//   segmentLabel            → segmentLabel
//   stationLabel            → stationLabel
//   stormLabel              → stormLabel
//   weatherSummary          → weatherSummary
//   relativeMotionText      → relativeMotionText
//   stormPositionText       → stormPositionText
//   stormMotionText         → stormMotionText
//   stormMarkSize           → stormMarkSize
//   stationMarkSize         → stationMarkSize
//   isEndStation            → isEndStation
//   arrowMinGap/MinRainNm   → ARROW_MIN_GAP / ARROW_MIN_RAIN_NM
//   phaseLabel              → phaseLabel  (on LiveGlanceLine.phaseLabel here)
//
// Deliberate divergences, documented so `/sync-ios-web` does not re-flag them:
//  - **Category colours.** iOS MVFR is blue; the web's is amber, because the
//    web page already badges MVFR amber in every table and matching iOS there
//    would paint one airport two colours on one page. The thresholds and the
//    colour *roles* are shared; the palette is each platform's own.
//  - **dBZ palette.** The boundaries (35 / 41 / 50) are shared exactly — they
//    are the cell tiers. The web takes its hexes from its own VIP ramp
//    (`layer-legends.ts`) so the ribbon matches the radar legend beside it.
//
// Observations only: no verdict, no estimate. The colours describe category
// and strength — never "go".
// =============================================================================

private extension Array where Element == String? {
    /// The non-nil, **non-empty** members — the web side's `filter(Boolean)`.
    /// `compactMap { $0 }` alone keeps `""`, which left a stray separator in a
    /// label the two clients are supposed to produce identically.
    var present: [String] { compactMap { $0 }.filter { !$0.isEmpty } }
}

enum RouteRibbonRules {

    // MARK: Geometry (points from the top of the drawing)

    static let height: CGFloat = 214
    static let sigmetY: CGFloat = 7
    static let leftRowY: CGFloat = 26
    static let zoneTop: CGFloat = 40
    static let trackY: CGFloat = 100
    static let zoneBottom: CGFloat = 160
    static let rightRowY: CGFloat = 174
    static let axisY: CGFloat = 197
    /// Horizontal padding: the route line runs from `inset` to `width - inset`.
    static let inset: CGFloat = 20

    /// Along-route distance → x. Clamped, so a cell past the route's end sits
    /// on the end rather than off the drawing.
    static func x(_ nm: Double, routeNm: Double, width: CGFloat) -> CGFloat {
        inset + CGFloat(min(max(nm / max(routeNm, 1), 0), 1)) * (width - 2 * inset)
    }

    /// Off-track distance → y: left (−) above the line, right (+) below.
    static func y(cross: Double, corridor: Double) -> CGFloat {
        let f = CGFloat(max(-1, min(1, cross / max(corridor, 1))))
        return f < 0 ? trackY + f * (trackY - zoneTop) : trackY + f * (zoneBottom - trackY)
    }

    // MARK: Colour ladders

    static func categoryColor(_ category: String?) -> Color {
        switch category?.lowercased() {
        case "vfr": .green
        case "mvfr": .blue
        case "ifr": .red
        case "lifr": .purple
        default: .gray
        }
    }

    static func dbzColor(_ dbz: Double?) -> Color {
        guard let dbz else { return .green.opacity(0.4) }
        if dbz >= 50 { return .red }
        if dbz >= 41 { return .orange }
        if dbz >= 35 { return .yellow }
        return .green
    }

    /// Rain areas pale (their strength is in the cores drawn on top), cores
    /// by their peak.
    static func bandColor(_ band: RibbonWeather) -> Color {
        band.isCore ? dbzColor(band.peakDbz).opacity(0.85) : Color.green.opacity(0.28)
    }

    /// The radar strip's fill for one stretch, when there are no cell bands.
    /// "no_coverage" is grey — the radar could not see the stretch, which must
    /// never read as "no weather" (#574 invariant).
    static func radarColor(_ seg: LiveRibbonSegment) -> Color {
        switch seg.radarStatus {
        case "no_coverage": return Color.gray.opacity(0.35)
        case "measured":
            guard let dbz = seg.radarMaxDbz else { return Color.green.opacity(0.08) }
            if dbz >= 20 && dbz < 35 { return .green.opacity(0.6) }
            return dbz < 20 ? .green.opacity(0.25) : dbzColor(dbz)
        default: return .clear
        }
    }

    // MARK: Marks

    /// An arrow toward the track when closing, away from it when moving away.
    /// nil when the motion was not measured — never a guess.
    static func motionArrow(_ storm: LiveStorm, cross: Double) -> String? {
        let below = cross >= 0
        switch storm.relativeMotion {
        case "closing": return below ? "arrow.up" : "arrow.down"
        case "moving_away": return below ? "arrow.down" : "arrow.up"
        default: return nil
        }
    }

    /// Storm marker diameter by peak reflectivity (no cell bands: storms as
    /// points).
    static func stormMarkSize(_ peakDbz: Double?) -> CGFloat {
        let dbz = peakDbz ?? 0
        if dbz >= 50 { return 18 }
        if dbz >= 41 { return 14 }
        return 10
    }

    /// An airport's disc: the route's ends sit on the line and are drawn
    /// larger than the en-route airports in their rows.
    static func stationMarkSize(_ st: RibbonStation) -> CGFloat {
        isEndStation(st) ? 16 : 10
    }

    static func isEndStation(_ st: RibbonStation) -> Bool {
        st.role == "departure" || st.role == "destination"
    }

    /// Minimum gap between two band arrows; a closer one is dropped rather
    /// than drawn overlapping.
    static let arrowMinGap: CGFloat = 16
    /// A rain area gets an arrow only once it is this long along the route — a
    /// short one is a shower, and its arrow would be noise.
    static let arrowMinRainNm: Double = 15

    // MARK: Words (labels + the accessible name of each mark)

    static func sigmetText(_ s: RibbonSigmet) -> String {
        let hazard = [s.qualifier, s.hazard].present.joined(separator: " ")
        let fir = s.label?.split(separator: ":").first.map(String.init)
        return [fir, hazard.isEmpty ? "SIGMET" : hazard].present.joined(separator: " ")
    }

    static func segmentLabel(_ seg: LiveRibbonSegment) -> String {
        let span = "\(Int((seg.fromNm ?? 0).rounded()))–\(Int((seg.toNm ?? 0).rounded())) NM"
        switch seg.radarStatus {
        case "measured":
            if let dbz = seg.radarMaxDbz { return "\(span): radar peak \(Int(dbz.rounded())) dBZ" }
            return "\(span): no radar echo"
        case "no_coverage": return "\(span): radar coverage insufficient"
        default: return "\(span): no radar sample"
        }
    }

    static func stationLabel(_ st: RibbonStation) -> String {
        var parts = [st.icao, st.metarCategory ?? "METAR unavailable"]
        parts += st.convective ?? []
        if let taf = st.tafCategoryAtEta { parts.append("TAF at ETA \(taf)") }
        if let type = st.tafTemporaryType, let cat = st.tafTemporaryCategory { parts.append("\(type) \(cat)") }
        if let cross = st.crossNm, st.role == "route" || st.role == "alternate" {
            parts.append("\(Int(abs(cross).rounded())) NM \(cross < 0 ? "left" : "right") of course")
        }
        return parts.joined(separator: " ")
    }

    static func stormLabel(_ storm: LiveStorm) -> String {
        var parts = ["Cell \(Int((storm.peakDbz ?? 0).rounded())) dBZ"]
        parts.append(stormPositionText(storm))
        parts.append(stormMotionText(storm))
        if let flashes = storm.flashes, flashes > 0 { parts.append(flashes == 1 ? "1 flash" : "\(flashes) flashes") }
        return parts.joined(separator: ", ")
    }

    /// VoiceOver: the weather zones in a sentence.
    static func weatherSummary(_ bands: [RibbonWeather]) -> String {
        let cores = bands.filter(\.isCore)
        if bands.isEmpty { return "No rain or cells within the corridor" }
        let strongest = cores.compactMap(\.peakDbz).max()
        var s = "\(bands.count - cores.count) rain areas, \(cores.count) cells along the route"
        if let strongest { s += ", strongest \(Int(strongest.rounded())) dBZ" }
        return s
    }

    /// Motion relative to the course in words, for labels.
    static func relativeMotionText(_ deg: Double) -> String {
        let a = abs(deg)
        if a <= 30 { return "moving along the course" }
        if a >= 150 { return "moving against the course" }
        return deg > 0 ? "drifting toward the right of course" : "drifting toward the left of course"
    }

    /// "8 NM right of track at 85 NM", or from the airport past the route's
    /// ends — the server's `storm_position_text`.
    static func stormPositionText(_ storm: LiveStorm) -> String {
        let off = "\(Int((storm.offtrackNm ?? 0).rounded())) NM"
        if storm.end != nil {
            let place = storm.endIcao ?? storm.end ?? ""
            return [off, storm.endBearing, "of", place].present.joined(separator: " ")
        }
        guard let side = storm.side else { return "on track at \(Int((storm.alongNm ?? 0).rounded())) NM" }
        return "\(off) \(side) of track at \(Int((storm.alongNm ?? 0).rounded())) NM"
    }

    /// Observed motion against the track — the server's wording.
    static func stormMotionText(_ storm: LiveStorm) -> String {
        switch storm.relativeMotion {
        case "closing": return storm.closingKt.map { "closing \(Int($0.rounded())) kt" } ?? "closing"
        case "moving_away": return storm.closingKt.map { "moving away \(Int((-$0).rounded())) kt" } ?? "moving away"
        case "parallel": return "moving along the track"
        case "stationary": return "nearly stationary"
        default: return "motion not yet measured"
        }
    }
}
