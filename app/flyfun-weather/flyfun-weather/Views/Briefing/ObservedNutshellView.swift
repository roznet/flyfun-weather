import SwiftUI

// The Observed tab's top: "what you need to know → details" (#690,
// designs/future/observed-tab-presentation.md §3–5).
//
// Everything here is rendered from the server's `glance` / `ribbon` / `storms`
// blocks (`tasks/live_glance.py`): the text is the server's, word for word, so
// iOS, web and the agent `live` block agree. Nothing is derived here beyond
// placing marks. Observations only: the storm estimate appears in the cell's
// inspector card alone, under "More", labelled "Estimate at current motion".
//
// SYNC — the drawing's web counterparts are
// web/ts/visualization/observed/nutshell-view.ts (the nutshell card) and
// .../ribbon-view.ts (the ribbon as SVG, the legend, the cell detail). The
// rules both sides share live in RouteRibbonRules.swift ↔ ribbon-core.ts, and
// what a picked mark says in RouteRibbonInspectorRules.swift ↔
// ribbon-tooltip.ts (#747: a tap card here, a hover tooltip on the web);
// nothing in this file decides a colour, a position or a label.

// MARK: - Nutshell

/// The headline comparing with the briefing, then one line per phase. Each
/// line opens the map on what it summarises (its `focus`).
///
/// Since #697 this is the body of the Observed tab's "Details" fold: the
/// alert-tier lines sit above the highlight (`ObservedAlertsCard`) and are
/// passed here only by a caller that wants every line. `showsHeadline` is
/// false when the headline already stands in the highlight's slot.
struct ObservedNutshellCard: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance
    var lines: [LiveGlanceLine]? = nil
    var showsHeadline: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            if showsHeadline, let headline = glance.headline {
                Text(headline)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("observedNutshellHeadline")
            }
            ForEach(lines ?? glance.items) { line in
                ObservedNutshellLineRow(viewModel: viewModel, line: line)
            }
            if showsMapButton {
                Button {
                    viewModel.setFocusIntent(FocusIntent(target: .map, showObservedCells: true))
                } label: {
                    Label("Show radar & cells on map", systemImage: "map")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("observedShowOnMap")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedNutshell")
    }

    private var showsMapButton: Bool {
        if case .loaded(let snapshot) = viewModel.snapshotState {
            return snapshot.observedConditions?.hasAnyField ?? false
        }
        return false
    }
}

/// One nutshell line: alert bar, phase label, the server's text, and the tap
/// that opens the map on its `focus`. Shared by the nutshell (Details) and
/// the alert block above the highlight (#697), so an alert line reads the
/// same in both places.
struct ObservedNutshellLineRow: View {
    let viewModel: BriefingViewModel
    let line: LiveGlanceLine

    var body: some View {
        let content = HStack(alignment: .top, spacing: Theme.spacingS) {
            // An alert-tier change in this phase: a red bar, nothing more —
            // the words are the server's.
            RoundedRectangle(cornerRadius: 2)
                .fill(line.alert == true ? Theme.red : Theme.border)
                .frame(width: 4)
            VStack(alignment: .leading, spacing: 2) {
                Text(line.phaseLabel.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.textMuted)
                Text(line.text ?? "")
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if line.focus != nil {
                Image(systemName: "map")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .padding(.top, 2)
            }
        }
        .opacity(line.passed == true ? 0.55 : 1)
        .frame(minHeight: 44, alignment: .leading)
        .contentShape(Rectangle())

        if let focus = line.focus {
            Button {
                viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
            } label: { content }
            .buttonStyle(.plain)
            .accessibilityIdentifier("observedNutshellLine-\(line.phase ?? "")")
            .accessibilityValue(line.alert == true ? "alert" : "")
        } else {
            content
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("observedNutshellLine-\(line.phase ?? "")")
        }
    }
}

// MARK: - Ribbon

/// Layer 1: the route ribbon. x = distance along the route (ETAs on the axis),
/// y = left/right of track. Lanes, top to bottom: stations (METAR now, TAF at
/// ETA beneath, hatched when a PROB/TEMPO sets it), the SIGMET band, radar per
/// segment, storms either side of the track line.
///
/// Tapping a mark opens the inspector card under the ribbon (#747): what the
/// web's hover tooltip says, with the map one button away. No mark navigates
/// on its own. Tapping another mark swaps the card; ✕, the same mark again or
/// bare ribbon closes it. The selection is kept by the mark's identity across
/// `/live` refreshes and closes when its mark is gone.
struct RouteRibbonCard: View {
    let viewModel: BriefingViewModel
    let ribbon: LiveRibbon
    let storms: LiveStorms?
    @State private var inspection: RibbonInspection?

    private typealias I = RouteRibbonInspectorRules

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack(alignment: .firstTextBaseline) {
                Text("Along the route")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Spacer(minLength: 0)
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
            RouteRibbonView(
                ribbon: ribbon, storms: shownStorms, corridorNm: corridorNm,
                selected: inspection?.selected,
                onTap: { hits in
                    withAnimation(.snappy(duration: 0.2)) {
                        inspection = I.inspection(after: hits, current: inspection)
                    }
                }
            )
            .frame(height: RouteRibbonRules.height)
            if !ribbon.weatherAvailable {
                Text("Rain and cells unavailable: radar strip within 10 NM of the route")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
            if let inspection, let mark = resolve(inspection.selected) {
                RibbonInspectorCard(
                    mark: mark,
                    selected: inspection.selected,
                    chips: chips(inspection),
                    onSelect: { key in self.inspection?.selected = key },
                    onClose: { withAnimation(.snappy(duration: 0.2)) { self.inspection = nil } },
                    onShowOnMap: { focus in viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus)) }
                )
                .transition(.opacity)
            }
            RouteRibbonLegend(weather: ribbon.weatherAvailable)
            Label("Tap a mark for details", systemImage: "hand.tap")
                .font(.system(size: 10))
                .foregroundStyle(Theme.textMuted)
                .accessibilityHidden(true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedRibbon")
        .onChange(of: availableKeys) { _, keys in
            inspection = inspection?.pruned(available: keys)
        }
        .onChange(of: inspection?.selected) { _, key in
            // VoiceOver: say what the card now shows.
            guard let key, let mark = resolve(key) else { return }
            AccessibilityNotification.Announcement(I.content(mark).spoken).post()
        }
    }

    /// Storms are drawn only while the cells feed is up.
    private var shownStorms: [LiveStorm] { storms?.isAvailable == true ? storms?.items ?? [] : [] }
    private var corridorNm: Double { storms?.corridorNm ?? 30 }

    /// The airports' raw METAR / TAF: the live layer's, else the snapshot's
    /// (which the live layer patches when newer). Joined by ICAO.
    private var airports: [AirportObservation]? {
        if let airports = viewModel.liveLayerForPack?.routeObservations?.airports { return airports }
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot.routeObservations?.airports }
        return nil
    }

    private var availableKeys: Set<RibbonMarkKey> {
        I.availableKeys(ribbon: ribbon, storms: shownStorms)
    }

    private func resolve(_ key: RibbonMarkKey) -> RibbonMark? {
        I.resolve(key, ribbon: ribbon, storms: shownStorms, airports: airports)
    }

    private func chips(_ inspection: RibbonInspection) -> [RibbonInspectorCard.Chip] {
        inspection.candidates.compactMap { key in
            resolve(key).map { RibbonInspectorCard.Chip(key: key, label: I.chipLabel($0)) }
        }
    }

    private var caption: String {
        var parts: [String] = []
        if ribbon.weatherAvailable, let corridor = ribbon.weatherCorridorNm ?? storms?.corridorNm {
            parts.append("±\(Int(corridor.rounded())) NM of course")
        } else if let r = ribbon.radarRadiusNm {
            parts.append("radar ≤\(Int(r.rounded())) NM")
        }
        if let t = ribbon.radarTime.flatMap(Date.parseISO8601) { parts.append(LiveTime.zulu(t)) }
        return parts.joined(separator: " · ")
    }
}

/// The ribbon drawing: a symbolic map of the route. The middle line is the
/// route, straight and to scale in distance, with the departure and
/// destination as circles at its ends and the planned position on it.
/// Facing the direction of flight, left of course is above the line and right
/// of course below: an airport row on each side (circle = METAR now, ring =
/// TAF at its ETA), and between the rows and the line the rain areas and
/// convective cores the cells feed outlines within the corridor, at their
/// distance off track, coloured by strength, with an arrow for their motion
/// relative to the course. SIGMETs run as a thin band across the top.
/// Without the cells feed, the radar strip hugs the line instead.
///
/// One tap gesture covers the whole drawing: the tap is hit-tested against
/// every mark (`RouteRibbonInspectorRules.hits`: the marks under the finger
/// first, top paint layer winning, then the others within 22 pt, nearest
/// first) and the result handed to `onTap`. Each mark
/// is also an accessibility element whose activation selects it.
struct RouteRibbonView: View {
    let ribbon: LiveRibbon
    let storms: [LiveStorm]
    let corridorNm: Double
    let selected: RibbonMarkKey?
    /// The marks under a tap, best first; empty on bare ribbon.
    let onTap: ([RibbonMarkKey]) -> Void

    /// All geometry, colours and wording live in `RouteRibbonRules` — the
    /// file the web's `ribbon-core.ts` is diffed against — and the hit test
    /// in `RouteRibbonInspectorRules`. Nothing in this view decides what a
    /// mark looks like or is called.
    private typealias R = RouteRibbonRules
    private typealias I = RouteRibbonInspectorRules

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .topLeading) {
                if ribbon.weatherAvailable {
                    weatherCanvas(width)
                    ForEach(bandAnchors(width), id: \.band.id) { a in bandMark(a, width) }
                } else {
                    ForEach(ribbon.segments ?? []) { seg in radarStrip(seg, width) }
                }
                trackLine(width)
                ForEach(ribbon.sigmets ?? []) { s in sigmetMark(s, width) }
                if ribbon.weatherAvailable {
                    ForEach(arrows(width), id: \.id) { a in arrowMark(a) }
                    ForEach(stormTargets(width), id: \.storm.id) { t in stormTarget(t, width) }
                } else {
                    ForEach(storms) { storm in stormMark(storm, width) }
                }
                ForEach(ribbon.stations ?? [], id: \.markKey) { st in stationMark(st, width) }
                if let flown = ribbon.flownNm, flown > 0 {
                    aircraftMark(x: x(flown, width))
                }
                axis(width)
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            .contentShape(Rectangle())
            .onTapGesture(coordinateSpace: .local) { location in
                onTap(I.hits(at: location, in: targets(width)))
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var routeNm: Double { max(ribbon.routeNm ?? 1, 1) }
    private var corridor: Double { max(ribbon.weatherCorridorNm ?? corridorNm, 1) }

    private func x(_ nm: Double, _ width: CGFloat) -> CGFloat {
        R.x(nm, routeNm: routeNm, width: width)
    }

    private func targets(_ width: CGFloat) -> [RibbonHitTarget] {
        I.targets(ribbon: ribbon, storms: storms, corridorNm: corridorNm, width: width)
    }

    private func isSelected(_ key: RibbonMarkKey) -> Bool { selected == key }

    /// VoiceOver / XCUI: the mark as one element named by `label`; activating
    /// it selects the mark (the other marks around it become chips). Applied
    /// before `.position` so the element's frame is the mark's own.
    private func accessibleMark(
        _ view: some View, key: RibbonMarkKey, label: String, at point: CGPoint, width: CGFloat
    ) -> some View {
        view
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(label)
            .accessibilityAddTraits(isSelected(key) ? [.isButton, .isSelected] : .isButton)
            .accessibilityIdentifier(key.identifier)
            .accessibilityAction {
                let near = I.hits(at: point, in: targets(width))
                onTap([key] + near.filter { $0 != key })
            }
    }

    // MARK: Route line, ends, aircraft

    private func trackLine(_ width: CGFloat) -> some View {
        Path { p in
            p.move(to: CGPoint(x: R.inset, y: R.trackY))
            p.addLine(to: CGPoint(x: width - R.inset, y: R.trackY))
        }
        .stroke(Theme.text.opacity(0.75), style: StrokeStyle(lineWidth: 2))
    }

    private func aircraftMark(x: CGFloat) -> some View {
        Image(systemName: "airplane")
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(Theme.primary)
            .background(Circle().fill(Theme.surface).frame(width: 18, height: 18))
            .position(x: x, y: R.trackY)
            .accessibilityLabel("Planned position now")
    }

    // MARK: Weather bands

    private func weatherCanvas(_ width: CGFloat) -> some View {
        let bins = I.bandBinRects(ribbon, corridor: corridor, width: width)
        let selected = self.selected
        return Canvas { ctx, _ in
            // Rain first, cores on top (the rects come cores first).
            for (band, rect) in bins.reversed() {
                ctx.fill(Path(rect), with: .color(R.bandColor(band)))
            }
            // The selected band, or the cores of the selected cell: outlined.
            for (band, rect) in bins where Self.outlines(band, selected) {
                ctx.stroke(Path(rect.insetBy(dx: -1, dy: -1)), with: .color(Theme.primary), lineWidth: 1.5)
            }
        }
        .accessibilityLabel(R.weatherSummary(ribbon.weather ?? []))
    }

    private static func outlines(_ band: RibbonWeather, _ selected: RibbonMarkKey?) -> Bool {
        switch selected {
        case .band(let id): band.id == id
        case .storm(let sid): band.isCore && band.stormId == sid
        default: false
        }
    }

    private func anchor(_ band: RibbonWeather, _ width: CGFloat) -> CGPoint? {
        I.anchor(band, routeNm: routeNm, corridor: corridor, width: width)
    }

    private struct BandAnchor { let band: RibbonWeather; let at: CGPoint }

    /// The bands that are not a known cell's core, each at its widest bin:
    /// their accessibility elements (the Canvas draws them).
    private func bandAnchors(_ width: CGFloat) -> [BandAnchor] {
        let stormIds = Set(storms.map(\.id))
        return (ribbon.weather ?? []).compactMap { band in
            if I.cellId(of: band, in: stormIds) != nil { return nil }
            return anchor(band, width).map { BandAnchor(band: band, at: $0) }
        }
    }

    private func bandMark(_ a: BandAnchor, _ width: CGFloat) -> some View {
        let content = I.bandContent(a.band)
        let label = ([content.title] + content.rows.map(\.value)).joined(separator: ", ")
        return accessibleMark(Color.clear.frame(width: 22, height: 22),
                              key: .band(a.band.id), label: label, at: a.at, width: width)
            .position(a.at)
    }

    private struct Arrow { let id: String; let at: CGPoint; let deg: Double; let color: Color }

    /// One arrow per moving band, strongest first, skipping one that would
    /// sit on top of another; rain only when it is a sizeable area.
    private func arrows(_ width: CGFloat) -> [Arrow] {
        let moving = (ribbon.weather ?? [])
            .filter { $0.motionRelDeg != nil && ($0.isCore || (($0.toNm ?? 0) - ($0.fromNm ?? 0)) >= R.arrowMinRainNm) }
            .sorted { ($0.isCore ? 1 : 0, $0.peakDbz ?? 0) > ($1.isCore ? 1 : 0, $1.peakDbz ?? 0) }
        var out: [Arrow] = []
        for band in moving {
            guard let at = anchor(band, width), let deg = band.motionRelDeg else { continue }
            if out.contains(where: { hypot($0.at.x - at.x, $0.at.y - at.y) < R.arrowMinGap }) { continue }
            out.append(Arrow(id: band.id, at: at, deg: deg, color: band.isCore ? Theme.text : Theme.textMuted))
        }
        return out
    }

    private func arrowMark(_ a: Arrow) -> some View {
        Image(systemName: "arrow.right")
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(a.color)
            .shadow(color: Theme.surface, radius: 1)
            .rotationEffect(.degrees(a.deg))
            .position(a.at)
            .accessibilityHidden(true)
    }

    private struct StormTarget { let storm: LiveStorm; let at: CGPoint }

    private func stormTargets(_ width: CGFloat) -> [StormTarget] {
        let byId = Dictionary(storms.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var seen = Set<String>()
        return (ribbon.weather ?? []).compactMap { band in
            guard let sid = band.stormId, let storm = byId[sid], !seen.contains(sid),
                  let at = anchor(band, width) else { return nil }
            seen.insert(sid)
            return StormTarget(storm: storm, at: at)
        }
    }

    /// A cell's spot on its core band: invisible (the band is the mark; its
    /// outline shows the selection), here for VoiceOver and XCUI.
    private func stormTarget(_ t: StormTarget, _ width: CGFloat) -> some View {
        accessibleMark(Color.clear.frame(width: 28, height: 28),
                       key: .storm(t.storm.id), label: R.stormLabel(t.storm), at: t.at, width: width)
            .position(t.at)
    }

    // Without the cells feed: the radar max per stretch, hugging the line.
    @ViewBuilder
    private func radarStrip(_ seg: LiveRibbonSegment, _ width: CGFloat) -> some View {
        let x0 = x(seg.fromNm ?? 0, width), x1 = x(seg.toNm ?? 0, width)
        let center = CGPoint(x: (x0 + x1) / 2, y: R.trackY)
        let rect = Rectangle()
            .fill(R.radarColor(seg))
            .overlay {
                if isSelected(.segment(seg.index)) {
                    Rectangle().stroke(Theme.primary, lineWidth: 2)
                }
            }
            .frame(width: max(x1 - x0, 1), height: 16)
        accessibleMark(rect, key: .segment(seg.index), label: R.segmentLabel(seg), at: center, width: width)
            .position(center)
        if seg.lightning == true {
            Image(systemName: "bolt.fill")
                .font(.system(size: 9))
                .foregroundStyle(.yellow)
                .position(x: (x0 + x1) / 2, y: R.trackY - 14)
        }
    }

    // Without weather bands (an older server): storms as points.
    @ViewBuilder
    private func stormMark(_ storm: LiveStorm, _ width: CGFloat) -> some View {
        if let along = storm.alongNm {
            let cross = storm.crossNm ?? 0
            let at = CGPoint(x: x(along, width), y: R.y(cross: cross, corridor: max(corridorNm, 1)))
            let size = R.stormMarkSize(storm.peakDbz)
            let mark = ZStack {
                if isSelected(.storm(storm.id)) {
                    Circle().stroke(Theme.primary, lineWidth: 2).frame(width: size + 10, height: size + 10)
                }
                Circle()
                    .fill(R.dbzColor(storm.peakDbz).opacity(storm.ahead == false ? 0.35 : 0.9))
                    .frame(width: size, height: size)
                if let arrow = R.motionArrow(storm, cross: cross) {
                    Image(systemName: arrow)
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Theme.text)
                        .offset(y: cross >= 0 ? size * 0.9 : -size * 0.9)
                }
            }
            .frame(minWidth: 28, minHeight: 28)
            accessibleMark(mark, key: .storm(storm.id), label: R.stormLabel(storm), at: at, width: width)
                .position(at)
        }
    }

    // MARK: SIGMETs

    @ViewBuilder
    private func sigmetMark(_ s: RibbonSigmet, _ width: CGFloat) -> some View {
        if let lo = s.fromNm, let hi = s.toNm {
            let x0 = x(lo, width), x1 = x(hi, width)
            let center = CGPoint(x: (x0 + x1) / 2, y: R.sigmetY)
            let band = RoundedRectangle(cornerRadius: 2)
                .fill(Color.orange.opacity(s.pending == true ? 0.25 : 0.5))
                .overlay {
                    if isSelected(.sigmet(s.id)) {
                        RoundedRectangle(cornerRadius: 2).stroke(Theme.primary, lineWidth: 2)
                    }
                }
                .overlay(
                    Text(R.sigmetText(s))
                        .font(.system(size: 8, weight: .semibold))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .padding(.horizontal, 2)
                )
                .frame(width: max(x1 - x0, 4), height: 11)
            accessibleMark(band, key: .sigmet(s.id), label: s.label ?? R.sigmetText(s), at: center, width: width)
                .position(center)
        }
    }

    // MARK: Airports

    @ViewBuilder
    private func stationMark(_ st: RibbonStation, _ width: CGFloat) -> some View {
        if let at = I.stationPoint(st, routeNm: routeNm, width: width) {
            let size = R.stationMarkSize(st)
            let mark = ZStack {
                if isSelected(st.markKey) {
                    Circle().stroke(Theme.primary, lineWidth: 2.5).frame(width: size + 14, height: size + 14)
                }
                Self.airportCircle(st, size: size)
            }
            .frame(minWidth: 24, minHeight: 24)
            accessibleMark(mark, key: st.markKey, label: R.stationLabel(st), at: at, width: width)
                .position(at)
        }
    }

    private func axis(_ width: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array((ribbon.waypoints ?? []).enumerated()), id: \.offset) { _, wp in
                if let along = wp.alongNm {
                    VStack(spacing: 0) {
                        Text(wp.icao ?? "")
                            .font(.system(size: 10, weight: .semibold))
                        if let eta = wp.eta.flatMap(Date.parseISO8601) {
                            Text(LiveTime.zulu(eta))
                                .font(.system(size: 9))
                                .foregroundStyle(Theme.textMuted)
                        }
                    }
                    .foregroundStyle(Theme.text)
                    .fixedSize()
                    .position(x: x(along, width), y: R.axisY)
                }
            }
        }
    }

    /// Filled with the METAR category now, ringed with the TAF category at
    /// the airport's ETA (dashed when a PROB/TEMPO group sets it).
    static func airportCircle(_ st: RibbonStation, size: CGFloat) -> some View {
        let taf = st.tafTemporaryCategory ?? st.tafCategoryAtEta
        return ZStack {
            Circle()
                .stroke(taf.map { R.categoryColor($0) } ?? .clear,
                        style: StrokeStyle(lineWidth: 2.5, dash: st.tafTemporaryType != nil ? [2.5, 2] : []))
                .frame(width: size + 6, height: size + 6)
            Circle()
                .fill(R.categoryColor(st.metarCategory))
                .overlay(Circle().stroke(Color(white: 0.13), lineWidth: (st.convective ?? []).isEmpty ? 0.5 : 2))
                .frame(width: size, height: size)
        }
    }
}

/// The ribbon's key: what the circles, rings, bands and arrows mean.
struct RouteRibbonLegend: View {
    let weather: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 10) {
                item("METAR now · ring TAF at ETA") {
                    ZStack {
                        Circle().stroke(Color.blue, lineWidth: 2).frame(width: 12, height: 12)
                        Circle().fill(Color.green).frame(width: 7, height: 7)
                    }
                }
                item("SIGMET") {
                    RoundedRectangle(cornerRadius: 1).fill(Color.orange.opacity(0.5)).frame(width: 14, height: 7)
                }
            }
            HStack(spacing: 10) {
                if weather {
                    item("rain") {
                        RoundedRectangle(cornerRadius: 1).fill(Color.green.opacity(0.28)).frame(width: 14, height: 7)
                    }
                    item("cells 35/41/50 dBZ") {
                        HStack(spacing: 1) {
                            Rectangle().fill(Color.yellow).frame(width: 5, height: 7)
                            Rectangle().fill(Color.orange).frame(width: 5, height: 7)
                            Rectangle().fill(Color.red).frame(width: 5, height: 7)
                        }
                    }
                    item("motion vs course") {
                        Image(systemName: "arrow.right").font(.system(size: 9, weight: .heavy))
                    }
                } else {
                    item("radar ≤10 NM") {
                        Rectangle().fill(Color.green.opacity(0.6)).frame(width: 14, height: 7)
                    }
                }
            }
            Text("Left of course above the line, right below")
        }
        .font(.system(size: 10))
        .foregroundStyle(Theme.textMuted)
        .accessibilityElement(children: .combine)
    }

    private func item(_ label: String, @ViewBuilder icon: () -> some View) -> some View {
        HStack(spacing: 3) {
            icon()
            Text(label)
        }
    }
}

// MARK: - Inspector card

/// The card under the ribbon for the picked mark (#747): the web tooltip's
/// rows (`RouteRibbonInspectorRules`, mirrored from `ribbon-tooltip.ts`), the
/// raw METAR / TAF, and "Show on map". Chips at the top switch between the
/// marks the tap found within reach. A cell's card carries what its detail
/// sheet used to under "More", estimate included.
///
/// Phone: one column. Regular width: decoded rows on the left, the raw
/// reports in monospace on the right.
struct RibbonInspectorCard: View {
    struct Chip: Identifiable {
        let key: RibbonMarkKey
        let label: String
        var id: RibbonMarkKey { key }
    }

    let mark: RibbonMark
    let selected: RibbonMarkKey
    let chips: [Chip]
    let onSelect: (RibbonMarkKey) -> Void
    let onClose: () -> Void
    let onShowOnMap: (LiveFocus) -> Void

    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var showsMore = false

    private var content: RibbonCardContent { RouteRibbonInspectorRules.content(mark) }

    private var storm: LiveStorm? {
        if case .storm(let storm) = mark { return storm }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            if chips.count > 1 { chipRow }
            header
            if sizeClass == .regular && !content.raws.isEmpty {
                HStack(alignment: .top, spacing: Theme.spacingM) {
                    rows.frame(maxWidth: .infinity, alignment: .leading)
                    raws.frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                rows
                raws
            }
            if let storm {
                StormMoreSection(storm: storm, isExpanded: $showsMore)
            }
            if let focus = mark.focus {
                Button {
                    onShowOnMap(focus)
                } label: {
                    Label("Show on map", systemImage: "map")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier(storm != nil ? "stormShowOnMap" : "ribbonInspectorShowOnMap")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .onChange(of: selected) { showsMore = false }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(storm != nil ? "stormDetail" : "ribbonInspector")
    }

    private var chipRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Theme.spacingXS) {
                ForEach(chips) { chip in
                    let isOn = chip.key == selected
                    Button { onSelect(chip.key) } label: {
                        Text(chip.label)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                            .padding(.horizontal, 10)
                            .frame(minHeight: 32)
                            .foregroundStyle(isOn ? Theme.surface : Theme.text)
                            .background(Capsule().fill(isOn ? Theme.primary : Theme.bg))
                            .overlay(Capsule().stroke(Theme.border, lineWidth: 0.5))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(isOn ? .isSelected : [])
                    .accessibilityIdentifier("ribbonInspectorChip-\(chip.key.identifier)")
                }
            }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            if case .station(let st, _) = mark, let focus = st.focus {
                // The ICAO opens the map on the airport.
                Button { onShowOnMap(focus) } label: {
                    HStack(spacing: 3) {
                        Text(content.title).underline()
                        Image(systemName: "map").font(.caption)
                    }
                    .font(.headline)
                    .foregroundStyle(Theme.primary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(content.title), show on map")
                .accessibilityIdentifier("ribbonInspectorIcao")
            } else {
                Text(content.title)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let subtitle = content.subtitle {
                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            Button(action: onClose) {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(Theme.textMuted)
                    .frame(minWidth: 44, minHeight: 32)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close")
            .accessibilityIdentifier("ribbonInspectorClose")
        }
    }

    private var rows: some View {
        Grid(alignment: .leading, horizontalSpacing: Theme.spacingM, verticalSpacing: 4) {
            ForEach(Array(content.rows.enumerated()), id: \.offset) { _, row in
                GridRow(alignment: .firstTextBaseline) {
                    Text(row.label)
                        .foregroundStyle(Theme.textMuted)
                    Text(row.value)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
            }
        }
        .font(.subheadline)
    }

    @ViewBuilder
    private var raws: some View {
        if !content.raws.isEmpty {
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                ForEach(Array(content.raws.enumerated()), id: \.offset) { _, raw in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(raw.label)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Theme.textMuted)
                        Text(raw.value)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("ribbonInspectorRaw-\(raw.label)")
                }
            }
        }
    }
}

/// A cell's card, under "More": what the retired detail sheet showed beyond
/// the tooltip rows — the 30-min trend numbers, the off-track history, the
/// backing stations, and, set apart and labelled, the estimate.
struct StormMoreSection: View {
    let storm: LiveStorm
    @Binding var isExpanded: Bool

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                section("Trend (30 min)") {
                    row("Trend", storm.trend ?? "unknown")
                    if let d = storm.dPeakDb { row("Peak change", String(format: "%+.0f dB", d)) }
                    if let a = storm.areaRatio { row("Area", String(format: "×%.1f", a)) }
                    if let f = storm.dFlashes { row("Flashes change", String(format: "%+ld", f)) }
                }
                if storm.topFl == nil {
                    row("Cloud top", "unavailable")
                }
                if let history = storm.history, !history.isEmpty {
                    section("Off track, last 30 min") {
                        ForEach(Array(history.enumerated()), id: \.offset) { _, point in
                            row(point.at.flatMap(Date.parseISO8601).map(LiveTime.zulu) ?? "",
                                "\(Int((point.offtrackNm ?? 0).rounded())) NM")
                        }
                        if let now = storm.offtrackNm { row("Now", "\(Int(now.rounded())) NM") }
                    }
                }
                if let backing = storm.backing, !backing.isEmpty {
                    section("Stations") {
                        row("Backing", backing.joined(separator: "; "))
                    }
                }
                if let est = storm.estimate {
                    VStack(alignment: .leading, spacing: 4) {
                        section("Estimate at current motion") {
                            if let cpa = est.cpaNm {
                                row("Closest to your track",
                                    "\(Int(cpa.rounded())) NM"
                                    + (est.cpaTime.flatMap(Date.parseISO8601).map { " at \(LiveTime.zulu($0))" } ?? ""))
                            }
                            if let off = est.atEtaOfftrackNm {
                                row("Off track when you are abeam", "\(Int(off.rounded())) NM")
                            }
                        }
                        Text("A projection, not an observation: it assumes the cell keeps its current speed and heading, and that you fly the plan on time.")
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(Theme.spacingS)
                    .background(Theme.bg, in: RoundedRectangle(cornerRadius: 6))
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("stormEstimate")
                }
            }
            .padding(.top, Theme.spacingXS)
        } label: {
            Text("More")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.text)
        }
        .accessibilityIdentifier("stormMore")
    }

    private func section<Rows: View>(_ title: String, @ViewBuilder _ rows: () -> Rows) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
            rows()
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(Theme.textMuted)
            Spacer(minLength: Theme.spacingM)
            Text(value).foregroundStyle(Theme.text).multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
        .accessibilityElement(children: .combine)
    }
}
