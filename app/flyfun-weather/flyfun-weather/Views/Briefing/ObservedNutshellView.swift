import SwiftUI

// The Observed tab's top: "what you need to know → details" (#690,
// designs/future/observed-tab-presentation.md §3–5).
//
// Everything here is rendered from the server's `glance` / `ribbon` / `storms`
// blocks (`tasks/live_glance.py`): the text is the server's, word for word, so
// iOS, web and the agent `live` block agree. Nothing is derived here beyond
// placing marks. Observations only: the storm estimate appears in the storm
// sheet alone, labelled "Estimate at current motion".
//
// SYNC — the drawing's web counterparts are
// web/ts/visualization/observed/nutshell-view.ts (the nutshell card) and
// .../ribbon-view.ts (the ribbon as SVG, the legend, the cell detail). The
// rules both sides share live in RouteRibbonRules.swift ↔ ribbon-core.ts;
// nothing in this file decides a colour, a position or a label.

// MARK: - Nutshell

/// Layer 0: the headline comparing with the briefing, then one line per phase.
/// Each line opens the map on what it summarises (its `focus`).
struct ObservedNutshellCard: View {
    let viewModel: BriefingViewModel
    let glance: LiveGlance

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            if let headline = glance.headline {
                Text(headline)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("observedNutshellHeadline")
            }
            ForEach(glance.items) { line in
                lineRow(line)
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

    @ViewBuilder
    private func lineRow(_ line: LiveGlanceLine) -> some View {
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
/// segment, storms either side of the track line. Every mark with a `focus`
/// opens the map on it; a storm opens its detail sheet first.
struct RouteRibbonCard: View {
    let viewModel: BriefingViewModel
    let ribbon: LiveRibbon
    let storms: LiveStorms?
    @State private var selectedStorm: LiveStorm?

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
                ribbon: ribbon, storms: storms?.isAvailable == true ? storms?.items ?? [] : [],
                corridorNm: storms?.corridorNm ?? 30,
                onFocus: { focus in viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus)) },
                onStorm: { selectedStorm = $0 }
            )
            .frame(height: RouteRibbonRules.height)
            if !ribbon.weatherAvailable {
                Text("Rain and cells unavailable: radar strip within 10 NM of the route")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
            RouteRibbonLegend(weather: ribbon.weatherAvailable)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedRibbon")
        .sheet(item: $selectedStorm) { storm in
            StormDetailSheet(storm: storm) { focus in
                selectedStorm = nil
                viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
            }
            .presentationDetents([.medium, .large])
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
struct RouteRibbonView: View {
    let ribbon: LiveRibbon
    let storms: [LiveStorm]
    let corridorNm: Double
    let onFocus: (LiveFocus) -> Void
    let onStorm: (LiveStorm) -> Void

    /// All geometry, colours and wording live in `RouteRibbonRules` — the
    /// file the web's `ribbon-core.ts` is diffed against. Nothing in this
    /// view decides what a mark looks like or is called.
    private typealias R = RouteRibbonRules

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .topLeading) {
                if ribbon.weatherAvailable {
                    weatherCanvas(width)
                } else {
                    ForEach(ribbon.segments ?? []) { seg in radarStrip(seg, width) }
                }
                trackLine(width)
                ForEach(ribbon.sigmets ?? []) { s in sigmetMark(s, width) }
                if ribbon.weatherAvailable {
                    ForEach(arrows(width), id: \.id) { a in arrowMark(a) }
                    ForEach(stormTargets(width), id: \.storm.id) { t in stormTarget(t) }
                } else {
                    ForEach(storms) { storm in stormMark(storm, width) }
                }
                ForEach(ribbon.stations ?? []) { st in stationMark(st, width) }
                if let flown = ribbon.flownNm, flown > 0 {
                    aircraftMark(x: x(flown, width))
                }
                axis(width)
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var routeNm: Double { max(ribbon.routeNm ?? 1, 1) }
    private var corridor: Double { max(ribbon.weatherCorridorNm ?? corridorNm, 1) }

    private func x(_ nm: Double, _ width: CGFloat) -> CGFloat {
        R.inset + CGFloat(min(max(nm / routeNm, 0), 1)) * (width - 2 * R.inset)
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
        let bands = (ribbon.weather ?? []).sorted { !$0.isCore && $1.isCore }
        let half = (ribbon.weatherBinNm ?? 5) / 2
        return Canvas { ctx, _ in
            for band in bands {
                let color = R.bandColor(band)
                for bin in band.profile ?? [] where bin.count == 3 {
                    let x0 = x(bin[0] - half, width), x1 = x(bin[0] + half, width)
                    let y0 = R.y(cross: bin[1], corridor: corridor)
                    let y1 = R.y(cross: bin[2], corridor: corridor)
                    let rect = CGRect(x: x0, y: min(y0, y1), width: max(x1 - x0, 1.5),
                                      height: max(abs(y1 - y0), 3))
                    ctx.fill(Path(rect), with: .color(color))
                }
            }
        }
        .contentShape(Rectangle())
        .gesture(SpatialTapGesture().onEnded { tap in
            if let focus = segmentFocus(at: tap.location.x, width) { onFocus(focus) }
        })
        .accessibilityLabel(R.weatherSummary(ribbon.weather ?? []))
    }

    /// Tapping a band (or anywhere on the weather zones) frames the map on
    /// that stretch of route.
    private func segmentFocus(at px: CGFloat, _ width: CGFloat) -> LiveFocus? {
        let nm = Double((px - R.inset) / max(width - 2 * R.inset, 1)) * routeNm
        return (ribbon.segments ?? []).first { ($0.fromNm ?? 0) <= nm && nm < ($0.toNm ?? 0) }?.focus
            ?? (ribbon.segments ?? []).last?.focus
    }

    /// The point of a band its arrow and tap target sit on: its widest bin.
    private func anchor(_ band: RibbonWeather, _ width: CGFloat) -> CGPoint? {
        guard let bin = (band.profile ?? []).filter({ $0.count == 3 })
            .max(by: { ($0[2] - $0[1]) < ($1[2] - $1[1]) }) else { return nil }
        let mid = (bin[1] + bin[2]) / 2
        return CGPoint(x: x(bin[0], width), y: R.y(cross: mid, corridor: corridor))
    }

    private struct Arrow { let id: String; let at: CGPoint; let deg: Double; let color: Color }

    /// One arrow per moving band, strongest first, skipping one that would
    /// sit on top of another; rain only when it is a sizeable area.
    private func arrows(_ width: CGFloat) -> [Arrow] {
        let moving = (ribbon.weather ?? [])
            .filter { $0.motionRelDeg != nil && ($0.isCore || (($0.toNm ?? 0) - ($0.fromNm ?? 0)) >= 15) }
            .sorted { ($0.isCore ? 1 : 0, $0.peakDbz ?? 0) > ($1.isCore ? 1 : 0, $1.peakDbz ?? 0) }
        var out: [Arrow] = []
        for band in moving {
            guard let at = anchor(band, width), let deg = band.motionRelDeg else { continue }
            if out.contains(where: { hypot($0.at.x - at.x, $0.at.y - at.y) < 16 }) { continue }
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

    private func stormTarget(_ t: StormTarget) -> some View {
        Button { onStorm(t.storm) } label: {
            Color.clear.frame(width: 28, height: 28).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .position(t.at)
        .accessibilityLabel(R.stormLabel(t.storm))
        .accessibilityIdentifier("ribbonStorm-\(t.storm.id)")
    }

    // Without the cells feed: the radar max per stretch, hugging the line.
    @ViewBuilder
    private func radarStrip(_ seg: LiveRibbonSegment, _ width: CGFloat) -> some View {
        let x0 = x(seg.fromNm ?? 0, width), x1 = x(seg.toNm ?? 0, width)
        let rect = Rectangle()
            .fill(R.radarColor(seg))
            .frame(width: max(x1 - x0, 1), height: 16)
            .position(x: (x0 + x1) / 2, y: R.trackY)
        if let focus = seg.focus {
            Button { onFocus(focus) } label: { rect }
                .buttonStyle(.plain)
                .accessibilityLabel(R.segmentLabel(seg))
        } else {
            rect
        }
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
            let y = R.y(cross: cross, corridor: max(corridorNm, 1))
            let size: CGFloat = (storm.peakDbz ?? 0) >= 50 ? 18 : (storm.peakDbz ?? 0) >= 41 ? 14 : 10
            Button { onStorm(storm) } label: {
                ZStack {
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
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .position(x: x(along, width), y: y)
            .accessibilityLabel(R.stormLabel(storm))
            .accessibilityIdentifier("ribbonStorm-\(storm.id)")
        }
    }

    // MARK: SIGMETs

    @ViewBuilder
    private func sigmetMark(_ s: RibbonSigmet, _ width: CGFloat) -> some View {
        if let lo = s.fromNm, let hi = s.toNm {
            let x0 = x(lo, width), x1 = x(hi, width)
            let band = RoundedRectangle(cornerRadius: 2)
                .fill(Color.orange.opacity(s.pending == true ? 0.25 : 0.5))
                .overlay(
                    Text(R.sigmetText(s))
                        .font(.system(size: 8, weight: .semibold))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .padding(.horizontal, 2)
                )
                .frame(width: max(x1 - x0, 4), height: 11)
                .position(x: (x0 + x1) / 2, y: R.sigmetY)
            if let focus = s.focus {
                Button { onFocus(focus) } label: { band }
                    .buttonStyle(.plain)
                    .accessibilityLabel(s.label ?? s.id)
            } else {
                band
            }
        }
    }

    // MARK: Airports

    /// Departure / destination on the line's ends; the others in the row on
    /// their side of the course.
    private func stationPoint(_ st: RibbonStation, _ width: CGFloat) -> CGPoint? {
        switch st.role {
        case "departure": return CGPoint(x: R.inset, y: R.trackY)
        case "destination": return CGPoint(x: width - R.inset, y: R.trackY)
        default:
            guard let along = st.alongNm else { return nil }
            return CGPoint(x: x(along, width), y: (st.crossNm ?? 0) < 0 ? R.leftRowY : R.rightRowY)
        }
    }

    @ViewBuilder
    private func stationMark(_ st: RibbonStation, _ width: CGFloat) -> some View {
        if let at = stationPoint(st, width) {
            let end = st.role == "departure" || st.role == "destination"
            let mark = Self.airportCircle(st, size: end ? 16 : 10)
                .frame(minWidth: 24, minHeight: 24)
                .contentShape(Rectangle())
                .position(at)
            if let focus = st.focus {
                Button { onFocus(focus) } label: { mark }
                    .buttonStyle(.plain)
                    .accessibilityLabel(R.stationLabel(st))
            } else {
                mark.accessibilityLabel(R.stationLabel(st))
            }
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

// MARK: - Storm detail

/// A storm's detail: what it is, where it is against the route, how it moved
/// over the last frames, and — set apart and labelled — the estimate.
struct StormDetailSheet: View {
    let storm: LiveStorm
    let onShowOnMap: (LiveFocus) -> Void

    private typealias R = RouteRibbonRules

    var body: some View {
        NavigationStack {
            List {
                Section("Observed") {
                    row("Peak", "\(Int((storm.peakDbz ?? 0).rounded())) dBZ" + (storm.intensity.map { " (\($0))" } ?? ""))
                    row("Position", R.stormPositionText(storm))
                    if let eta = storm.abeamEta.flatMap(Date.parseISO8601), storm.end == nil {
                        row("Abeam at plan", LiveTime.zulu(eta))
                    }
                    row("Motion", R.stormMotionText(storm))
                    if let flashes = storm.flashes {
                        row("Lightning", flashes == 0 ? "none" : flashes == 1 ? "1 flash" : "\(flashes) flashes")
                    } else if storm.flashesPending == true {
                        row("Lightning", "pending")
                    }
                    row("Cloud top", storm.topFl.map { "FL\($0)" } ?? "unavailable")
                    if !(storm.backing ?? []).isEmpty {
                        row("Stations", (storm.backing ?? []).joined(separator: "; "))
                    }
                }
                Section("Trend (30 min)") {
                    row("Trend", storm.trend ?? "unknown")
                    if let d = storm.dPeakDb { row("Peak change", String(format: "%+.0f dB", d)) }
                    if let a = storm.areaRatio { row("Area", String(format: "×%.1f", a)) }
                    if let f = storm.dFlashes { row("Flashes change", String(format: "%+ld", f)) }
                }
                if let history = storm.history, !history.isEmpty {
                    Section("Off track, last 30 min") {
                        ForEach(Array(history.enumerated()), id: \.offset) { _, point in
                            row(point.at.flatMap(Date.parseISO8601).map(LiveTime.zulu) ?? "",
                                "\(Int((point.offtrackNm ?? 0).rounded())) NM")
                        }
                        if let now = storm.offtrackNm { row("Now", "\(Int(now.rounded())) NM") }
                    }
                }
                if let est = storm.estimate {
                    Section {
                        if let cpa = est.cpaNm {
                            row("Closest to your track",
                                "\(Int(cpa.rounded())) NM"
                                + (est.cpaTime.flatMap(Date.parseISO8601).map { " at \(LiveTime.zulu($0))" } ?? ""))
                        }
                        if let off = est.atEtaOfftrackNm {
                            row("Off track when you are abeam", "\(Int(off.rounded())) NM")
                        }
                    } header: {
                        Text("Estimate at current motion")
                    } footer: {
                        Text("A projection, not an observation: it assumes the cell keeps its current speed and heading, and that you fly the plan on time.")
                    }
                    .accessibilityIdentifier("stormEstimate")
                }
                if let focus = storm.focus {
                    Section {
                        Button {
                            onShowOnMap(focus)
                        } label: {
                            Label("Show on map", systemImage: "map")
                        }
                        .accessibilityIdentifier("stormShowOnMap")
                    }
                }
            }
            .navigationTitle(storm.intensity.map { "\($0.capitalized) cell" } ?? "Cell")
            .navigationBarTitleDisplayMode(.inline)
        }
        .accessibilityIdentifier("stormDetail")
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(Theme.textMuted)
            Spacer(minLength: Theme.spacingM)
            Text(value).foregroundStyle(Theme.text).multilineTextAlignment(.trailing)
        }
        .accessibilityElement(children: .combine)
    }

}
