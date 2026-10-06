import SwiftUI

// The Observed tab's top: "what you need to know → details" (#690,
// designs/future/observed-tab-presentation.md §3–5).
//
// Everything here is rendered from the server's `glance` / `ribbon` / `storms`
// blocks (`tasks/live_glance.py`): the text is the server's, word for word, so
// iOS, web and the agent `live` block agree. Nothing is derived here beyond
// placing marks. Observations only: the storm estimate appears in the storm
// sheet alone, labelled "Estimate at current motion".

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
            .frame(height: RouteRibbonView.height)
            if storms?.isAvailable != true {
                Text("Radar storms unavailable")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
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
        if let corridor = storms?.corridorNm { parts.append("storms ≤\(Int(corridor.rounded())) NM") }
        if let r = ribbon.radarRadiusNm { parts.append("radar ≤\(Int(r.rounded())) NM") }
        if let t = ribbon.radarTime.flatMap(Date.parseISO8601) { parts.append(LiveTime.zulu(t)) }
        return parts.joined(separator: " · ")
    }
}

/// The ribbon drawing. Pure layout over the server's binned lanes.
struct RouteRibbonView: View {
    let ribbon: LiveRibbon
    let storms: [LiveStorm]
    let corridorNm: Double
    let onFocus: (LiveFocus) -> Void
    let onStorm: (LiveStorm) -> Void

    static let height: CGFloat = 230

    // Lane geometry (points from the top).
    private let stationY: CGFloat = 14
    private let tafY: CGFloat = 32
    private let sigmetY: CGFloat = 52
    private let radarY: CGFloat = 72
    private let trackY: CGFloat = 140
    private let stormHalf: CGFloat = 52
    private let axisY: CGFloat = 212
    private let inset: CGFloat = 16

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .topLeading) {
                trackLine(width)
                if let flown = ribbon.flownNm, flown > 0 {
                    aircraftMark(x: x(flown, width))
                }
                ForEach(ribbon.segments ?? []) { seg in segmentMark(seg, width) }
                ForEach(ribbon.sigmets ?? []) { s in sigmetMark(s, width) }
                ForEach(ribbon.stations ?? []) { st in stationMark(st, width) }
                ForEach(storms) { storm in stormMark(storm, width) }
                axis(width)
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var routeNm: Double { max(ribbon.routeNm ?? 1, 1) }

    private func x(_ nm: Double, _ width: CGFloat) -> CGFloat {
        inset + CGFloat(min(max(nm / routeNm, 0), 1)) * (width - 2 * inset)
    }

    private func trackLine(_ width: CGFloat) -> some View {
        Path { p in
            p.move(to: CGPoint(x: inset, y: trackY))
            p.addLine(to: CGPoint(x: width - inset, y: trackY))
        }
        .stroke(Theme.textMuted.opacity(0.6), style: StrokeStyle(lineWidth: 2))
    }

    private func aircraftMark(x: CGFloat) -> some View {
        Image(systemName: "airplane")
            .font(.caption)
            .foregroundStyle(Theme.primary)
            .position(x: x, y: trackY)
            .accessibilityLabel("Planned position now")
    }

    // Radar per segment, lightning glyph above it.
    @ViewBuilder
    private func segmentMark(_ seg: LiveRibbonSegment, _ width: CGFloat) -> some View {
        let x0 = x(seg.fromNm ?? 0, width), x1 = x(seg.toNm ?? 0, width)
        let rect = Rectangle()
            .fill(Self.radarColor(seg))
            .overlay(Rectangle().stroke(Theme.border, lineWidth: 0.5))
            .frame(width: max(x1 - x0, 1), height: 14)
            .position(x: (x0 + x1) / 2, y: radarY)
        if let focus = seg.focus {
            Button { onFocus(focus) } label: { rect }
                .buttonStyle(.plain)
                .accessibilityLabel(Self.segmentLabel(seg))
        } else {
            rect
        }
        if seg.lightning == true {
            Image(systemName: "bolt.fill")
                .font(.system(size: 9))
                .foregroundStyle(.yellow)
                .position(x: (x0 + x1) / 2, y: radarY - 12)
        }
    }

    @ViewBuilder
    private func sigmetMark(_ s: RibbonSigmet, _ width: CGFloat) -> some View {
        if let lo = s.fromNm, let hi = s.toNm {
            let x0 = x(lo, width), x1 = x(hi, width)
            let band = RoundedRectangle(cornerRadius: 3)
                .fill(Color.orange.opacity(s.pending == true ? 0.25 : 0.55))
                .overlay(
                    Text(Self.sigmetText(s))
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .padding(.horizontal, 2)
                )
                .frame(width: max(x1 - x0, 4), height: 14)
                .position(x: (x0 + x1) / 2, y: sigmetY)
            if let focus = s.focus {
                Button { onFocus(focus) } label: { band }
                    .buttonStyle(.plain)
                    .accessibilityLabel(s.label ?? s.id)
            } else {
                band
            }
        }
    }

    @ViewBuilder
    private func stationMark(_ st: RibbonStation, _ width: CGFloat) -> some View {
        if let along = st.alongNm {
            let px = x(along, width)
            let mark = VStack(spacing: 4) {
                Circle()
                    .fill(Self.categoryColor(st.metarCategory))
                    .overlay(Circle().stroke(Color(white: 0.13), lineWidth: (st.convective ?? []).isEmpty ? 0.5 : 2))
                    .frame(width: st.role == "route" ? 9 : 13, height: st.role == "route" ? 9 : 13)
                // TAF at ETA beneath; hatched (dashed) when PROB/TEMPO sets it.
                if let taf = st.tafTemporaryCategory ?? st.tafCategoryAtEta {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Self.categoryColor(taf).opacity(st.tafTemporaryType != nil ? 0.35 : 0.8))
                        .overlay(RoundedRectangle(cornerRadius: 2).stroke(
                            Self.categoryColor(taf),
                            style: StrokeStyle(lineWidth: 1, dash: st.tafTemporaryType != nil ? [2, 2] : [])))
                        .frame(width: 9, height: 7)
                }
            }
            .position(x: px, y: (stationY + tafY) / 2)
            if let focus = st.focus {
                Button { onFocus(focus) } label: { mark }
                    .buttonStyle(.plain)
                    .accessibilityLabel(Self.stationLabel(st))
            } else {
                mark
            }
        }
    }

    @ViewBuilder
    private func stormMark(_ storm: LiveStorm, _ width: CGFloat) -> some View {
        if let along = storm.alongNm {
            let cross = storm.crossNm ?? 0
            // Facing the direction of flight on a left-to-right ribbon, right of
            // track is below the line.
            let y = trackY + CGFloat(max(-1, min(1, cross / max(corridorNm, 1)))) * stormHalf
            let size: CGFloat = (storm.peakDbz ?? 0) >= 50 ? 20 : (storm.peakDbz ?? 0) >= 41 ? 15 : 11
            Button { onStorm(storm) } label: {
                ZStack {
                    Circle()
                        .fill(Self.motionColor(storm).opacity(storm.ahead == false ? 0.35 : 0.85))
                        .overlay(Circle().stroke(Color(white: 0.13), lineWidth: 1))
                        .frame(width: size, height: size)
                    if (storm.flashes ?? 0) > 0 {
                        Image(systemName: "bolt.fill")
                            .font(.system(size: size * 0.5))
                            .foregroundStyle(.yellow)
                    }
                    if let arrow = Self.motionArrow(storm, cross: cross) {
                        Image(systemName: arrow)
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(Self.motionColor(storm))
                            .offset(y: cross >= 0 ? size * 0.85 : -size * 0.85)
                    }
                }
                .frame(minWidth: 28, minHeight: 28)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .position(x: x(along, width), y: y)
            .accessibilityLabel(Self.stormLabel(storm))
            .accessibilityIdentifier("ribbonStorm-\(storm.id)")
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
                    .position(x: x(along, width), y: axisY)
                }
            }
        }
    }

    // MARK: Styling (no judgement: categories and observed motion only)

    static func categoryColor(_ category: String?) -> Color {
        switch category?.lowercased() {
        case "vfr": .green
        case "mvfr": .blue
        case "ifr": .red
        case "lifr": .purple
        default: .gray
        }
    }

    static func radarColor(_ seg: LiveRibbonSegment) -> Color {
        switch seg.radarStatus {
        case "no_coverage": return Color.gray.opacity(0.35)
        case "measured":
            guard let dbz = seg.radarMaxDbz else { return Color.green.opacity(0.08) }
            if dbz >= 50 { return .red }
            if dbz >= 41 { return .orange }
            if dbz >= 30 { return .yellow }
            if dbz >= 20 { return .green.opacity(0.6) }
            return .green.opacity(0.25)
        default: return .clear
        }
    }

    static func motionColor(_ storm: LiveStorm) -> Color {
        switch storm.relativeMotion {
        case "closing": .red
        case "moving_away": .teal
        default: .gray
        }
    }

    /// An arrow toward the track when closing, away from it when moving away.
    static func motionArrow(_ storm: LiveStorm, cross: Double) -> String? {
        let below = cross >= 0
        switch storm.relativeMotion {
        case "closing": return below ? "arrow.up" : "arrow.down"
        case "moving_away": return below ? "arrow.down" : "arrow.up"
        default: return nil
        }
    }

    static func sigmetText(_ s: RibbonSigmet) -> String {
        let hazard = [s.qualifier, s.hazard].compactMap { $0 }.joined(separator: " ")
        return hazard.isEmpty ? "SIGMET" : hazard
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
        return parts.joined(separator: " ")
    }

    static func stormLabel(_ storm: LiveStorm) -> String {
        var parts = ["Storm \(Int((storm.peakDbz ?? 0).rounded())) dBZ"]
        parts.append(StormDetailSheet.positionText(storm))
        parts.append(StormDetailSheet.motionText(storm))
        if let flashes = storm.flashes, flashes > 0 { parts.append(flashes == 1 ? "1 flash" : "\(flashes) flashes") }
        return parts.joined(separator: ", ")
    }
}

// MARK: - Storm detail

/// A storm's detail: what it is, where it is against the route, how it moved
/// over the last frames, and — set apart and labelled — the estimate.
struct StormDetailSheet: View {
    let storm: LiveStorm
    let onShowOnMap: (LiveFocus) -> Void

    var body: some View {
        NavigationStack {
            List {
                Section("Observed") {
                    row("Peak", "\(Int((storm.peakDbz ?? 0).rounded())) dBZ" + (storm.intensity.map { " (\($0))" } ?? ""))
                    row("Position", Self.positionText(storm))
                    if let eta = storm.abeamEta.flatMap(Date.parseISO8601), storm.end == nil {
                        row("Abeam at plan", LiveTime.zulu(eta))
                    }
                    row("Motion", Self.motionText(storm))
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
                        Text("A projection, not an observation: it assumes the storm keeps its current speed and heading, and that you fly the plan on time.")
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
            .navigationTitle(storm.intensity.map { "\($0.capitalized) storm" } ?? "Storm")
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

    /// "8 NM right of track at 85 NM", or from the airport past the route's
    /// ends — the server's `storm_position_text`.
    static func positionText(_ storm: LiveStorm) -> String {
        let off = "\(Int((storm.offtrackNm ?? 0).rounded())) NM"
        if storm.end != nil {
            let place = storm.endIcao ?? storm.end ?? ""
            return [off, storm.endBearing, "of", place].compactMap { $0 }.joined(separator: " ")
        }
        guard let side = storm.side else { return "on track at \(Int((storm.alongNm ?? 0).rounded())) NM" }
        return "\(off) \(side) of track at \(Int((storm.alongNm ?? 0).rounded())) NM"
    }

    /// Observed motion against the track — the server's wording.
    static func motionText(_ storm: LiveStorm) -> String {
        switch storm.relativeMotion {
        case "closing": return storm.closingKt.map { "closing \(Int($0.rounded())) kt" } ?? "closing"
        case "moving_away": return storm.closingKt.map { "moving away \(Int((-$0).rounded())) kt" } ?? "moving away"
        case "parallel": return "moving along the track"
        case "stationary": return "nearly stationary"
        default: return "motion not yet measured"
        }
    }
}
