import SwiftUI

// =============================================================================
// SYNC — paired with web/ts/visualization/observed/ribbon-tooltip.ts (#742,
// #747).
//
// What a ribbon mark says when it is picked: the web shows it as a hover
// tooltip, iOS as the inspector card under the ribbon (tap a mark). The
// wording is the same on both; `ObservedNutshellView.swift` owns the SwiftUI.
//
// Symbol map (Swift → TypeScript), so `/sync-ios-web` can diff them:
//
//   airport(for:in:)   → airportFor
//   stationContent     → stationTooltipHtml
//   stormContent       → stormTooltipHtml   (minus the web's "Click for detail")
//   bandContent        → bandTooltipHtml
//   sigmetContent      → sigmetTooltipHtml
//   segmentContent     → segmentTooltipHtml
//   bandAt             → bandAt
//   hhmmZ              → formatHhmmZ (helpers/live-layer.ts)
//
// iOS only, no web counterpart (the web hit-tests with the DOM's pointer
// events): `RibbonMarkKey`, `targets`, `hits`, `RibbonInspection`. The web
// stays hover-only; a tap there still opens the map / cell detail.
//
// Observations only: these rows describe what was measured and forecast,
// never a verdict.
//
// The value types are `nonisolated` (like the `/live` models) so their
// Equatable / Hashable conformances are usable off the main actor (tests).
// =============================================================================

/// One labelled line of the card ("METAR now" — "MVFR at 08:20Z").
nonisolated struct RibbonCardRow: Equatable, Sendable {
    let label: String
    let value: String
}

/// What the card shows for one mark: a title, an optional subtitle, decoded
/// rows, then the raw reports (monospaced).
nonisolated struct RibbonCardContent: Equatable, Sendable {
    let title: String
    var subtitle: String? = nil
    var rows: [RibbonCardRow] = []
    var raws: [RibbonCardRow] = []

    /// What VoiceOver announces when the card opens: the decoded rows, not
    /// the raw METAR / TAF (those stay on the card, read on demand).
    var spoken: String {
        ([title, subtitle ?? ""] + rows.map { "\($0.label): \($0.value)" })
            .filter { !$0.isEmpty }
            .joined(separator: ". ")
    }

    /// Everything, for tests.
    var text: String {
        ([title, subtitle ?? ""] + rows.flatMap { [$0.label, $0.value] } + raws.flatMap { [$0.label, $0.value] })
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}

/// A mark's identity across `/live` refreshes: what the selection is kept by.
/// A station is its ICAO *and* role: on a round trip the departure and the
/// destination are the same airport at two ETAs, two discs, two cards.
nonisolated enum RibbonMarkKey: Hashable, Sendable {
    case station(String, role: String? = nil)
    case storm(String)
    case band(String)
    case sigmet(String)
    case segment(Int)

    /// The accessibility identifier of the mark on the ribbon.
    var identifier: String {
        switch self {
        case .station(let icao, let role): role.map { "ribbonStation-\(icao)-\($0)" } ?? "ribbonStation-\(icao)"
        case .storm(let id): "ribbonStorm-\(id)"
        case .band(let id): "ribbonBand-\(id)"
        case .sigmet(let id): "ribbonSigmet-\(id)"
        case .segment(let index): "ribbonSegment-\(index)"
        }
    }
}

/// A tappable area of the drawing. `z` is its paint order (higher = on top),
/// so a tap on a disc inside a rain area picks the disc.
nonisolated struct RibbonHitTarget: Equatable, Sendable {
    let key: RibbonMarkKey
    let rect: CGRect
    let z: Int
}

/// The open card: the marks found under the tap (chips when more than one)
/// and the one shown.
nonisolated struct RibbonInspection: Equatable, Sendable {
    var candidates: [RibbonMarkKey]
    var selected: RibbonMarkKey

    /// After a `/live` refresh: chips whose mark is gone drop out; the card
    /// closes (nil) when the selected mark itself is gone.
    func pruned(available: Set<RibbonMarkKey>) -> RibbonInspection? {
        guard available.contains(selected) else { return nil }
        return RibbonInspection(candidates: candidates.filter { available.contains($0) }, selected: selected)
    }
}

nonisolated extension RibbonStation {
    /// The station's selection key (ICAO + role).
    var markKey: RibbonMarkKey { .station(icao, role: role) }
}

/// A resolved mark: the model behind a key, for the card.
nonisolated enum RibbonMark {
    case station(RibbonStation, AirportObservation?)
    case storm(LiveStorm)
    /// A band has no focus of its own: it carries its radar stretch's
    /// (`RouteRibbonInspectorRules.bandFocus`).
    case band(RibbonWeather, LiveFocus?)
    case sigmet(RibbonSigmet)
    case segment(LiveRibbonSegment)

    var focus: LiveFocus? {
        switch self {
        case .station(let st, _): st.focus
        case .storm(let s): s.focus
        case .band(_, let focus): focus
        case .sigmet(let s): s.focus
        case .segment(let seg): seg.focus
        }
    }
}

enum RouteRibbonInspectorRules {

    private typealias R = RouteRibbonRules

    /// How far from a mark a tap still picks it: generous, for turbulence.
    static let hitRadius: CGFloat = 22

    // MARK: Content (same wording as ribbon-tooltip.ts)

    private static let roleLabels: [String: String] = [
        "departure": "Departure",
        "destination": "Destination",
        "alternate": "Alternate",
        "route": "En route",
    ]

    /// "08:20Z", or "" when the time is missing or unparseable (the web's
    /// `formatHhmmZ`).
    static func hhmmZ(_ iso: String?) -> String {
        guard let date = iso.flatMap(Date.parseISO8601) else { return "" }
        return LiveTime.zulu(date)
    }

    private static func nm(_ value: Double) -> Int { Int(value.rounded()) }

    /// The airport observation for a ribbon station, by ICAO.
    static func airport(for st: RibbonStation, in airports: [AirportObservation]?) -> AirportObservation? {
        (airports ?? []).first { $0.icao == st.icao }
    }

    /// An airport disc: who it is, its categories now and at ETA, the raw text.
    static func stationContent(_ st: RibbonStation, airport: AirportObservation?) -> RibbonCardContent {
        var c = RibbonCardContent(title: st.icao, subtitle: airport?.name)
        let role = st.role.map { roleLabels[$0] ?? $0 } ?? ""
        if !role.isEmpty { c.rows.append(.init(label: "Role", value: role)) }
        let metarAt = hhmmZ(st.metarTime ?? airport?.metarTime)
        if let cat = st.metarCategory {
            c.rows.append(.init(label: "METAR now", value: metarAt.isEmpty ? cat : "\(cat) at \(metarAt)"))
        } else {
            c.rows.append(.init(label: "METAR now", value: "unavailable"))
        }
        if let convective = st.convective, !convective.isEmpty {
            c.rows.append(.init(label: "Observed", value: convective.joined(separator: " ")))
        }
        let eta = hhmmZ(st.eta)
        if let tafCat = st.tafCategoryAtEta {
            var taf = tafCat
            if let type = st.tafTemporaryType, let cat = st.tafTemporaryCategory {
                taf += ", \(type) \(cat)"
            }
            c.rows.append(.init(label: eta.isEmpty ? "TAF at ETA" : "TAF at \(eta)", value: taf))
        } else if !eta.isEmpty {
            c.rows.append(.init(label: "ETA", value: eta))
        }
        if let weather = st.tafWeather, !weather.isEmpty {
            c.rows.append(.init(label: "TAF weather", value: weather.joined(separator: " ")))
        }
        if let cross = st.crossNm, st.role == "route" || st.role == "alternate" {
            let at = st.alongNm.map { " at \(nm($0)) NM" } ?? ""
            c.rows.append(.init(label: "Position",
                                value: "\(nm(abs(cross))) NM \(cross < 0 ? "left" : "right") of course\(at)"))
        }
        // Without a joined airport the raw text is unknown, not absent: say
        // nothing rather than "no report" beside the station's own category.
        if let airport {
            if let raw = airport.metarRaw, !raw.isEmpty {
                c.raws.append(.init(label: airport.metarReportType == "SPECI" ? "SPECI" : "METAR", value: raw))
            } else {
                c.rows.append(.init(label: "METAR", value: "no report"))
            }
            if let raw = airport.tafRaw, !raw.isEmpty {
                c.raws.append(.init(label: "TAF", value: raw))
            } else {
                c.rows.append(.init(label: "TAF", value: "none issued"))
            }
        }
        return c
    }

    /// A cell: strength, where it is, how it moves. The rest of what the old
    /// detail sheet held sits under the card's "More".
    static func stormContent(_ storm: LiveStorm) -> RibbonCardContent {
        let title = storm.intensity.map { "\($0.prefix(1).uppercased())\($0.dropFirst()) cell" } ?? "Cell"
        var c = RibbonCardContent(title: title, subtitle: storm.peakDbz.map { "\(nm($0)) dBZ" })
        c.rows.append(.init(label: "Position", value: R.stormPositionText(storm)))
        if storm.end == nil {
            let abeam = hhmmZ(storm.abeamEta)
            if !abeam.isEmpty { c.rows.append(.init(label: "Abeam at plan", value: abeam)) }
        }
        c.rows.append(.init(label: "Motion", value: R.stormMotionText(storm)))
        if let flashes = storm.flashes {
            c.rows.append(.init(label: "Lightning",
                                value: flashes == 0 ? "none" : flashes == 1 ? "1 flash" : "\(flashes) flashes"))
        } else if storm.flashesPending == true {
            c.rows.append(.init(label: "Lightning", value: "pending"))
        }
        if let top = storm.topFl { c.rows.append(.init(label: "Cloud top", value: "FL\(top)")) }
        if let trend = storm.trend, !trend.isEmpty { c.rows.append(.init(label: "Trend (30 min)", value: trend)) }
        return c
    }

    /// A rain area or convective core band.
    static func bandContent(_ band: RibbonWeather) -> RibbonCardContent {
        var c = RibbonCardContent(title: band.isCore ? "Convective core" : "Rain area",
                                  subtitle: band.peakDbz.map { "\(nm($0)) dBZ" })
        c.rows.append(.init(label: "Along route", value: "\(nm(band.fromNm ?? 0))–\(nm(band.toNm ?? 0)) NM"))
        if band.side == "both" {
            c.rows.append(.init(label: "Off track", value: "across the track"))
        } else {
            let near = nm(band.nearNm ?? 0), far = nm(band.farNm ?? 0)
            let span = near == far ? "\(near)" : "\(near)–\(far)"
            let side = band.side.map { " \($0)" } ?? ""
            c.rows.append(.init(label: "Off track", value: "\(span) NM\(side) of course"))
        }
        if let intensity = band.intensity, !intensity.isEmpty {
            c.rows.append(.init(label: "Intensity", value: intensity))
        }
        if let flashes = band.flashes, flashes > 0 {
            c.rows.append(.init(label: "Lightning", value: flashes == 1 ? "1 flash" : "\(flashes) flashes"))
        }
        if let deg = band.motionRelDeg {
            c.rows.append(.init(label: "Motion",
                                value: R.relativeMotionText(deg) + (band.speedKt.map { ", \(nm($0)) kt" } ?? "")))
        }
        return c
    }

    /// A SIGMET band across the top.
    static func sigmetContent(_ s: RibbonSigmet) -> RibbonCardContent {
        var c = RibbonCardContent(title: R.sigmetText(s), subtitle: s.new == true ? "new" : nil)
        if let label = s.label, !label.isEmpty { c.rows.append(.init(label: "SIGMET", value: label)) }
        if let lo = s.fromNm, let hi = s.toNm {
            c.rows.append(.init(label: "Along route", value: "\(nm(lo))–\(nm(hi)) NM"))
        }
        let from = hhmmZ(s.validFrom), to = hhmmZ(s.validTo)
        if !from.isEmpty || !to.isEmpty {
            c.rows.append(.init(label: "Valid", value: "\(from.isEmpty ? "?" : from)–\(to.isEmpty ? "?" : to)"))
        }
        if s.pending == true { c.rows.append(.init(label: "Status", value: "issued, not yet valid")) }
        return c
    }

    /// A radar-strip stretch (no cells feed).
    static func segmentContent(_ seg: LiveRibbonSegment) -> RibbonCardContent {
        RibbonCardContent(title: R.segmentLabel(seg))
    }

    static func content(_ mark: RibbonMark) -> RibbonCardContent {
        switch mark {
        case .station(let st, let airport): stationContent(st, airport: airport)
        case .storm(let s): stormContent(s)
        case .band(let b, _): bandContent(b)
        case .sigmet(let s): sigmetContent(s)
        case .segment(let seg): segmentContent(seg)
        }
    }

    /// The chip that switches the card to a mark: "EGLL", "Cell", "Rain",
    /// "Radar 0–40 NM" (two stretches side by side must read apart).
    static func chipLabel(_ mark: RibbonMark) -> String {
        switch mark {
        case .station(let st, _): st.icao
        case .storm: "Cell"
        case .band(let b, _): b.isCore ? "Core" : "Rain"
        case .sigmet(let s): R.sigmetText(s)
        case .segment(let seg): "Radar \(nm(seg.fromNm ?? 0))–\(nm(seg.toNm ?? 0)) NM"
        }
    }

    // MARK: Resolution

    /// The model behind a key in the current layer, nil when it is gone.
    static func resolve(
        _ key: RibbonMarkKey, ribbon: LiveRibbon, storms: [LiveStorm], airports: [AirportObservation]?
    ) -> RibbonMark? {
        switch key {
        case .station(let icao, let role):
            return (ribbon.stations ?? []).first { $0.icao == icao && $0.role == role }
                .map { .station($0, airport(for: $0, in: airports)) }
        case .storm(let id):
            return storms.first { $0.id == id }.map { .storm($0) }
        case .band(let id):
            return (ribbon.weather ?? []).first { $0.id == id }.map { .band($0, bandFocus($0, in: ribbon)) }
        case .sigmet(let id):
            return (ribbon.sigmets ?? []).first { $0.id == id }.map { .sigmet($0) }
        case .segment(let index):
            return (ribbon.segments ?? []).first { $0.index == index }.map { .segment($0) }
        }
    }

    /// "Show on map" for a rain area or core: the radar stretch under the
    /// band's middle (the last one past the end), as tapping the weather
    /// zones did before the card (`segmentFocus`).
    static func bandFocus(_ band: RibbonWeather, in ribbon: LiveRibbon) -> LiveFocus? {
        let segments = ribbon.segments ?? []
        let mid = ((band.fromNm ?? 0) + (band.toNm ?? band.fromNm ?? 0)) / 2
        return segments.first { ($0.fromNm ?? 0) <= mid && mid < ($0.toNm ?? 0) }?.focus
            ?? segments.last?.focus
    }

    // MARK: Geometry (shared with the drawing, so a tap matches what is seen)

    /// Where an airport disc sits: departure / destination on the line's
    /// ends, the others in the row on their side of the course.
    static func stationPoint(_ st: RibbonStation, routeNm: Double, width: CGFloat) -> CGPoint? {
        switch st.role {
        case "departure": return CGPoint(x: R.inset, y: R.trackY)
        case "destination": return CGPoint(x: width - R.inset, y: R.trackY)
        default:
            guard let along = st.alongNm else { return nil }
            return CGPoint(x: R.x(along, routeNm: routeNm, width: width),
                           y: (st.crossNm ?? 0) < 0 ? R.leftRowY : R.rightRowY)
        }
    }

    /// Each drawn bin of each band, cores first (they paint on top). The same
    /// rects the weather canvas fills.
    static func bandBinRects(
        _ ribbon: LiveRibbon, corridor: Double, width: CGFloat
    ) -> [(band: RibbonWeather, rect: CGRect)] {
        let routeNm = max(ribbon.routeNm ?? 1, 1)
        let half = (ribbon.weatherBinNm ?? 5) / 2
        let bands = (ribbon.weather ?? []).enumerated()
            .sorted { ($0.element.isCore ? 0 : 1, $0.offset) < ($1.element.isCore ? 0 : 1, $1.offset) }
            .map(\.element)
        var out: [(band: RibbonWeather, rect: CGRect)] = []
        for band in bands {
            for bin in band.profile ?? [] where bin.count == 3 {
                let x0 = R.x(bin[0] - half, routeNm: routeNm, width: width)
                let x1 = max(R.x(bin[0] + half, routeNm: routeNm, width: width), x0 + 1.5)
                let ya = R.y(cross: bin[1], corridor: corridor)
                let yb = R.y(cross: bin[2], corridor: corridor)
                let top = min(ya, yb)
                let bottom = max(max(ya, yb), top + 3)
                out.append((band, CGRect(x: x0, y: top, width: x1 - x0, height: bottom - top)))
            }
        }
        return out
    }

    /// The band drawn under a point of the drawing, cores before rain since
    /// they paint on top. `corridor` defaults as the web's `corridorOf(_, 30)`.
    static func bandAt(_ ribbon: LiveRibbon, point: CGPoint, width: CGFloat, corridor: Double? = nil) -> RibbonWeather? {
        let corridor = max(corridor ?? ribbon.weatherCorridorNm ?? 30, 1)
        return bandBinRects(ribbon, corridor: corridor, width: width).first { $0.rect.contains(point) }?.band
    }

    /// The point of a band its arrow and cell target sit on: its widest bin.
    static func anchor(_ band: RibbonWeather, routeNm: Double, corridor: Double, width: CGFloat) -> CGPoint? {
        guard let bin = (band.profile ?? []).filter({ $0.count == 3 })
            .max(by: { ($0[2] - $0[1]) < ($1[2] - $1[1]) }) else { return nil }
        let mid = (bin[1] + bin[2]) / 2
        return CGPoint(x: R.x(bin[0], routeNm: routeNm, width: width), y: R.y(cross: mid, corridor: corridor))
    }

    // MARK: Hit test

    /// The cell a band belongs to, when it is a core outlining a cell in
    /// `stormIds`: that band is drawn as the cell and picked as the cell.
    /// The one place this rule lives (targets, available keys, the view's
    /// accessibility elements).
    static func cellId(of band: RibbonWeather, in stormIds: Set<String>) -> String? {
        band.isCore ? anchoredCell(of: band, in: stormIds) : nil
    }

    /// The cell whose spot sits on this band's anchor: any band (rain or
    /// core) carrying a listed `storm_id`. A rain band keeps its own card and
    /// the cell's spot on top of it; a core *is* the cell (`cellId`).
    private static func anchoredCell(of band: RibbonWeather, in stormIds: Set<String>) -> String? {
        guard let sid = band.stormId, stormIds.contains(sid) else { return nil }
        return sid
    }

    private static func hasBins(_ band: RibbonWeather) -> Bool {
        (band.profile ?? []).contains { $0.count == 3 }
    }

    /// Paint order of the drawing, bottom to top.
    private enum Layer: Int { case segment = 0, rain, core, sigmet, storm, station }

    /// Every tappable mark of the drawing, as `RouteRibbonView` draws it.
    /// A core band that outlines a known cell belongs to that cell.
    static func targets(
        ribbon: LiveRibbon, storms: [LiveStorm], corridorNm: Double, width: CGFloat
    ) -> [RibbonHitTarget] {
        let routeNm = max(ribbon.routeNm ?? 1, 1)
        let corridor = max(ribbon.weatherCorridorNm ?? corridorNm, 1)
        let stormIds = Set(storms.map(\.id))
        var out: [RibbonHitTarget] = []

        if ribbon.weatherAvailable {
            for (band, rect) in bandBinRects(ribbon, corridor: corridor, width: width) {
                if let sid = cellId(of: band, in: stormIds) {
                    out.append(.init(key: .storm(sid), rect: rect, z: Layer.core.rawValue))
                } else {
                    out.append(.init(key: .band(band.id), rect: rect,
                                     z: (band.isCore ? Layer.core : Layer.rain).rawValue))
                }
            }
            // The cell's own spot: the anchor of its first band.
            var seen = Set<String>()
            for band in ribbon.weather ?? [] {
                guard let sid = anchoredCell(of: band, in: stormIds), !seen.contains(sid),
                      let at = anchor(band, routeNm: routeNm, corridor: corridor, width: width) else { continue }
                seen.insert(sid)
                out.append(.init(key: .storm(sid), rect: CGRect(x: at.x - 6, y: at.y - 6, width: 12, height: 12),
                                 z: Layer.storm.rawValue))
            }
        } else {
            for seg in ribbon.segments ?? [] {
                let x0 = R.x(seg.fromNm ?? 0, routeNm: routeNm, width: width)
                let x1 = R.x(seg.toNm ?? 0, routeNm: routeNm, width: width)
                out.append(.init(key: .segment(seg.index),
                                 rect: CGRect(x: x0, y: R.trackY - 8, width: max(x1 - x0, 1), height: 16),
                                 z: Layer.segment.rawValue))
            }
            let stormCorridor = max(corridorNm, 1)
            for storm in storms {
                guard let along = storm.alongNm else { continue }
                let at = CGPoint(x: R.x(along, routeNm: routeNm, width: width),
                                 y: R.y(cross: storm.crossNm ?? 0, corridor: stormCorridor))
                let size = R.stormMarkSize(storm.peakDbz)
                out.append(.init(key: .storm(storm.id),
                                 rect: CGRect(x: at.x - size / 2, y: at.y - size / 2, width: size, height: size),
                                 z: Layer.storm.rawValue))
            }
        }
        for s in ribbon.sigmets ?? [] {
            guard let lo = s.fromNm, let hi = s.toNm else { continue }
            let x0 = R.x(lo, routeNm: routeNm, width: width), x1 = R.x(hi, routeNm: routeNm, width: width)
            let w = max(x1 - x0, 4)
            out.append(.init(key: .sigmet(s.id),
                             rect: CGRect(x: (x0 + x1) / 2 - w / 2, y: R.sigmetY - 5.5, width: w, height: 11),
                             z: Layer.sigmet.rawValue))
        }
        for st in ribbon.stations ?? [] {
            guard let at = stationPoint(st, routeNm: routeNm, width: width) else { continue }
            // The disc and its TAF ring.
            let d = R.stationMarkSize(st) + 6
            out.append(.init(key: st.markKey, rect: CGRect(x: at.x - d / 2, y: at.y - d / 2, width: d, height: d),
                             z: Layer.station.rawValue))
        }
        return out
    }

    /// Every mark a refreshed layer still draws: what a selection survives
    /// on. The same marks as `targets`, from the models alone (no geometry);
    /// a test holds the two equal.
    static func availableKeys(ribbon: LiveRibbon, storms: [LiveStorm]) -> Set<RibbonMarkKey> {
        let stormIds = Set(storms.map(\.id))
        var keys = Set<RibbonMarkKey>()
        if ribbon.weatherAvailable {
            for band in ribbon.weather ?? [] where hasBins(band) {
                keys.insert(cellId(of: band, in: stormIds).map { .storm($0) } ?? .band(band.id))
                if let sid = anchoredCell(of: band, in: stormIds) { keys.insert(.storm(sid)) }
            }
        } else {
            for seg in ribbon.segments ?? [] { keys.insert(.segment(seg.index)) }
            for storm in storms where storm.alongNm != nil { keys.insert(.storm(storm.id)) }
        }
        for s in ribbon.sigmets ?? [] where s.fromNm != nil && s.toNm != nil { keys.insert(.sigmet(s.id)) }
        for st in ribbon.stations ?? [] where isEnd(st) || st.alongNm != nil { keys.insert(st.markKey) }
        return keys
    }

    private static func isEnd(_ st: RibbonStation) -> Bool {
        st.role == "departure" || st.role == "destination"
    }

    /// The marks a tap picks, best first: those drawn under the finger (top
    /// paint layer first), then the others within `radius`, nearest first.
    /// Empty when the tap is on bare ribbon.
    static func hits(at point: CGPoint, in targets: [RibbonHitTarget], radius: CGFloat = hitRadius) -> [RibbonMarkKey] {
        var best: [RibbonMarkKey: Hit] = [:]
        for (order, t) in targets.enumerated() {
            let dx = max(t.rect.minX - point.x, 0, point.x - t.rect.maxX)
            let dy = max(t.rect.minY - point.y, 0, point.y - t.rect.maxY)
            let distance = (dx * dx + dy * dy).squareRoot()
            guard distance <= radius else { continue }
            let hit = Hit(key: t.key, inside: distance == 0, z: t.z, distance: distance, order: order)
            if let old = best[t.key], !hit.isBetter(than: old) { continue }
            best[t.key] = hit
        }
        return best.values.sorted { $0.isBetter(than: $1) }.map(\.key)
    }

    /// One target's distance from a tap. Under the finger beats near; under
    /// the finger, the top paint layer wins; near, the nearest; then the
    /// order the targets were listed in.
    private struct Hit {
        let key: RibbonMarkKey
        let inside: Bool
        let z: Int
        let distance: CGFloat
        let order: Int

        func isBetter(than b: Hit) -> Bool {
            if inside != b.inside { return inside }
            if inside {
                if z != b.z { return z > b.z }
            } else {
                if distance != b.distance { return distance < b.distance }
                if z != b.z { return z > b.z }
            }
            return order < b.order
        }
    }

    /// The card after a tap: the marks under it, or nil (close) on bare
    /// ribbon or when the tap's best mark is the one already shown.
    static func inspection(after hits: [RibbonMarkKey], current: RibbonInspection?) -> RibbonInspection? {
        guard let first = hits.first else { return nil }
        if current?.selected == first { return nil }
        return RibbonInspection(candidates: hits, selected: first)
    }
}
