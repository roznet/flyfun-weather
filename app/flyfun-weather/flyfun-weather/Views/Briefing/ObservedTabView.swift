import SwiftUI

/// The **Observed** tab (#661): everything *measured* on flight day, in the
/// order a pilot reads it in the cockpit — on the day, and in the aircraft,
/// this is the screen the app is opened for (planning happens on a computer).
///
/// 1. **At a glance** (#697, reordered around the pilot's in-flight
///    questions in #816) — the flight strip (where the flight is, its ETA),
///    alert-tier nutshell lines and change rows (never folded), the
///    model-written highlight (or the nutshell headline in its place), the
///    nutshell lines still ahead and "Destination at your ETA" (unfolded),
///    the departure / destination runway + wind dials (#758), the route
///    ribbon, then a **Details** fold (collapsed by default, remembered) with
///    the headline, the passed lines and the other "Since this briefing"
///    rows (#637). A layer without a nutshell (older server, a tick that
///    could not build it) falls back to the departure / destination tiles
///    and chips, with every change row in the panel below them.
/// 2. **Since this briefing** — only in that fallback; otherwise in Details.
/// 3. **Radar & lightning now** — the server's per-source summary clauses
///    (#574), each with its own frame age; missing sources are named.
/// 4. **Cells** — the server's radar storms against the route (`/live`
///    `storms`, #688; #816: the same list as the ribbon and the alerts).
/// 5. **METAR/TAF** — observations vs model (#492).
/// 6. **SIGMET** — route area hazards (#493).
///
/// Observations only: nothing here re-grades the briefing. Every age is the
/// source's own — never one shared "as of" (current-conditions invariant #4).
struct ObservedTabView: View {
    let viewModel: BriefingViewModel

    var body: some View {
        ScrollSpyScroll(sections: spySections) {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                if let glance = nutshell {
                    VStack(alignment: .leading, spacing: Theme.spacingM) {
                        if let flight = glance.flight {
                            ObservedFlightStrip(flight: flight)
                        }
                        ObservedAlertsCard(viewModel: viewModel, glance: glance, changes: viewModel.liveChanges)
                        ObservedHighlightCard(viewModel: viewModel, glance: glance)
                    }
                    .spyAnchor("glance")
                    if hasAhead(glance) {
                        VStack(alignment: .leading, spacing: Theme.spacingM) {
                            if !glance.aheadItems.isEmpty {
                                ObservedNutshellCard(
                                    viewModel: viewModel, glance: glance, lines: glance.aheadItems,
                                    showsHeadline: false, offersMap: false, identifier: "observedAhead"
                                )
                            }
                            if let destination = glance.destination {
                                DestinationAtETACard(viewModel: viewModel, destination: destination)
                            }
                        }
                        .spyAnchor("ahead")
                    }
                    if !runwayWindDials.isEmpty {
                        RunwayWindPairCard(dials: runwayWindDials)
                            .spyAnchor("winds")
                    }
                    if let ribbon = viewModel.liveLayerForPack?.ribbon {
                        RouteRibbonCard(viewModel: viewModel, ribbon: ribbon, storms: viewModel.liveLayerForPack?.storms,
                                        markerLabel: glance.flight?.markerLabel)
                            .spyAnchor("ribbon")
                    }
                    ObservedDetailsFold(viewModel: viewModel, glance: glance,
                                        changes: viewModel.liveChanges, baseline: viewModel.liveBaselineDate)
                        .spyAnchor("details")
                } else {
                    if !runwayWindDials.isEmpty {
                        RunwayWindPairCard(dials: runwayWindDials)
                            .spyAnchor("winds")
                    }
                    ObservedGlanceCard(viewModel: viewModel)
                        .spyAnchor("glance")
                    if let liveChanges = viewModel.liveChanges {
                        LiveChangesView(changes: liveChanges, baseline: viewModel.liveBaselineDate)
                            .spyAnchor("live")
                    }
                }
                if let observed = observedConditions, observed.hasAnyField {
                    ObservedNowView(observed: observed)
                        .spyAnchor("radar")
                }
                if let storms = liveStorms {
                    ObservedCellsSection(viewModel: viewModel, storms: storms)
                        .spyAnchor("cells")
                }
                if hasObservations {
                    RouteObservationsView(viewModel: viewModel, liveChanges: viewModel.liveChanges)
                        .spyAnchor("observations")
                }
                if hasSigmets {
                    RouteSigmetsView(viewModel: viewModel, liveChanges: viewModel.liveChanges)
                        .spyAnchor("sigmets")
                }
            }
            .padding(.vertical, Theme.cardPadding)
        }
        .background(Theme.bg)
    }

    /// #816: lines still ahead or a destination block to show unfolded.
    private func hasAhead(_ glance: LiveGlance) -> Bool {
        !glance.aheadItems.isEmpty || glance.destination != nil
    }

    /// The server's nutshell for the pack on screen, when it has lines.
    private var nutshell: LiveGlance? {
        guard let glance = viewModel.liveLayerForPack?.glance, !glance.items.isEmpty else { return nil }
        return glance
    }

    private var snapshot: SnapshotResponse? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot }
        return nil
    }

    private var observedConditions: ObservedConditions? { snapshot?.observedConditions }

    /// Departure + destination runway/wind dials (#758). Flight day only (a
    /// live layer exists); the snapshot's airports, which the live layer
    /// patches each tick. Empty on an older server — the tab is as before.
    private var runwayWindDials: [RunwayWindRules.Dial] {
        guard viewModel.liveLayerForPack != nil else { return [] }
        let waypoints = viewModel.flight.waypoints
        return RunwayWindRules.pair(
            airports: snapshot?.routeObservations?.airports,
            departure: waypoints.first,
            destination: waypoints.count > 1 ? waypoints.last : nil
        )
    }

    /// The server's storms for the pack on screen (#816): the Cells list is
    /// shown whenever the live layer carries them, a dark feed included (the
    /// list says why). No live layer, no list: nothing is measured here.
    private var liveStorms: LiveStorms? { viewModel.liveLayerForPack?.storms }

    private var hasObservations: Bool {
        !(snapshot?.routeObservations?.reportingAirports.isEmpty ?? true)
    }

    private var hasSigmets: Bool {
        snapshot?.routeSigmets?.showsSection ?? false
    }

    private var spySections: [SpySection] {
        // The fallback draws the winds above the glance card.
        var sections = nutshell == nil && !runwayWindDials.isEmpty
            ? [SpySection("winds", "Winds"), SpySection("glance", "Now")]
            : [SpySection("glance", "Now")]
        if let glance = nutshell, hasAhead(glance) { sections.append(SpySection("ahead", "Ahead")) }
        if nutshell != nil, !runwayWindDials.isEmpty { sections.append(SpySection("winds", "Winds")) }
        if nutshell != nil {
            if viewModel.liveLayerForPack?.ribbon != nil { sections.append(SpySection("ribbon", "Route")) }
            sections.append(SpySection("details", "Details"))
        } else if viewModel.liveChanges != nil {
            sections.append(SpySection("live", "Changes"))
        }
        if observedConditions?.hasAnyField ?? false { sections.append(SpySection("radar", "Radar")) }
        if liveStorms != nil { sections.append(SpySection("cells", "Cells")) }
        if hasObservations { sections.append(SpySection("observations", "METAR/TAF")) }
        if hasSigmets { sections.append(SpySection("sigmets", "SIGMET")) }
        return sections
    }
}

// MARK: - At a glance

/// The cockpit summary: the few facts that decide whether to look further,
/// each a tap-free read. Large type, no tables.
struct ObservedGlanceCard: View {
    let viewModel: BriefingViewModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: Theme.spacingM) {
                HStack(spacing: Theme.spacingM) {
                    if let departure = viewModel.flight.waypoints.first {
                        airportTile(role: "Departure", icao: departure, now: context.date)
                    }
                    if let destination = viewModel.flight.waypoints.last,
                       viewModel.flight.waypoints.count > 1 {
                        airportTile(role: "Destination", icao: destination, now: context.date)
                    }
                }
                FlowLayout(spacing: Theme.spacingS) {
                    ForEach(chips, id: \.text) { chip in
                        Label(chip.text, systemImage: chip.icon)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(chip.tint)
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(chip.tint.opacity(0.12), in: Capsule())
                    }
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
        }
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedGlance")
    }

    private var snapshot: SnapshotResponse? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot }
        return nil
    }

    private var showsMapButton: Bool { snapshot?.observedConditions?.hasAnyField ?? false }

    /// One airport: ICAO, its METAR category in large type, and the METAR's age.
    private func airportTile(role: String, icao: String, now: Date) -> some View {
        let airports = snapshot?.routeObservations?.airports
        let field = airports?.first { $0.icao == icao }
        // #767: a field with no METAR of its own reads its proxy station's,
        // named as such; the station's report keeps its own ICAO.
        let station = field?.metarProxy?.icao
        let obs = station.flatMap { s in airports?.first { $0.icao == s } } ?? field
        let metarTime = obs?.metarTime.flatMap(Date.parseISO8601)
        let stationPrefix = station.map { "\($0) " } ?? ""
        return VStack(alignment: .leading, spacing: 4) {
            Text(role.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
            HStack(spacing: Theme.spacingS) {
                Text(icao)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(Theme.text)
                if let category = obs?.metarFlightCategory {
                    FlightCategoryBadge(category: category)
                }
            }
            if let metarTime {
                let stale = now.timeIntervalSince(metarTime) > LiveTime.staleAfter
                Text("METAR \(stationPrefix)\(LiveTime.zulu(metarTime)) · \(LiveTime.ageLabel(from: metarTime, now: now))")
                    .font(.caption)
                    .foregroundStyle(stale ? Color.orange : Theme.textMuted)
            } else {
                Text("No METAR")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
            if let caption = field?.proxyCaption {
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private struct Chip {
        let text: String
        let icon: String
        let tint: Color
    }

    private var chips: [Chip] {
        var chips: [Chip] = []
        if let changes = viewModel.liveChanges {
            let alerts = changes.items.filter(\.isAlert).count
            let worse = changes.items.filter { $0.directionValue == .worse }.count
            if alerts > 0 {
                chips.append(Chip(text: alerts == 1 ? "1 alert" : "\(alerts) alerts",
                                  icon: "exclamationmark.triangle.fill", tint: Theme.red))
            }
            if worse > 0 {
                chips.append(Chip(text: "\(worse) worse since briefing", icon: "arrow.down.right", tint: .orange))
            }
            if changes.isEmpty {
                chips.append(Chip(text: "No significant change", icon: "checkmark", tint: Theme.textMuted))
            }
        }
        if let sigmets = snapshot?.routeSigmets, !sigmets.matched.isEmpty {
            let n = sigmets.sigmetCount
            chips.append(Chip(text: (n == 1 ? "1 SIGMET" : "\(n) SIGMETs") + (sigmets.severe ? " · SEV" : ""),
                              icon: "exclamationmark.octagon", tint: sigmets.severe ? Theme.red : .orange))
        }
        if let cells = cellsChip { chips.append(cells) }
        return chips
    }

    /// Storms still ahead within the server's corridor (#816: `/live`
    /// storms, the ribbon's list) — attention, not a verdict, so neutral when
    /// there are none. Nothing while the feed is down: the Cells list says why.
    private var cellsChip: Chip? {
        let storms = viewModel.liveLayerForPack?.storms
        guard let text = LiveStormListRules.chipText(storms), let storms else { return nil }
        let none = LiveStormListRules.ahead(storms).isEmpty
        return Chip(text: text,
                    icon: none ? "cloud" : "cloud.bolt",
                    tint: none ? Theme.textMuted : Theme.primary)
    }
}

// MARK: - Advisory teaser

/// On the Advisory tab, under the hero: what moved since the briefing, as one
/// row that switches to the Observed tab (#661) — the full list lives there.
struct ObservedTeaserRow: View {
    let viewModel: BriefingViewModel

    var body: some View {
        Button {
            viewModel.selectedTab = .observed
        } label: {
            HStack(spacing: Theme.spacingS) {
                Image(systemName: "dot.radiowaves.left.and.right")
                    .foregroundStyle(tint)
                Text(text)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textMuted)
            }
            .padding(Theme.spacingM)
            .frame(minHeight: 44)
            .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityIdentifier("observedTeaser")
    }

    private var alerts: Int { viewModel.liveChanges?.items.filter(\.isAlert).count ?? 0 }

    private var tint: Color { alerts > 0 ? Theme.red : Theme.primary }

    private var text: String {
        guard let changes = viewModel.liveChanges else { return String(localized: "Observed conditions") }
        if changes.isEmpty { return String(localized: "Observed: no significant change since the briefing") }
        let n = changes.items.count
        let base = n == 1 ? String(localized: "1 change since the briefing")
                          : String(localized: "\(n) changes since the briefing")
        if alerts == 0 { return base }
        return alerts == 1 ? String(localized: "\(base) · 1 alert") : String(localized: "\(base) · \(alerts) alerts")
    }
}

// MARK: - Radar & lightning now

/// The server's "observed now" clauses (#574), one card each with its own
/// source's frame age; sources that are missing are named, so "no radar" never
/// reads as "no weather". The prose is the server's — not re-worded here (web
/// `renderObservedConditions`, the PDF and the digest quote the same string).
struct ObservedNowView: View {
    let observed: ObservedConditions

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                Text("Radar & lightning now")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                // Under the heading, not at the foot of the section: there it
                // read as the filter of the cells list below (#689).
                if let corridor = observed.corridorNm {
                    Text("Within \(Int(corridor.rounded())) NM of the route")
                        .font(.caption)
                        .foregroundStyle(Theme.textMuted)
                }
                ForEach(entries, id: \.text) { entry in
                    HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
                        Text(entry.text)
                            .font(.body)
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        if let valid = validTime(entry.kind).flatMap(Date.parseISO8601) {
                            Text(LiveTime.ageLabel(from: valid, now: context.date))
                                .font(.caption)
                                .foregroundStyle(context.date.timeIntervalSince(valid) > LiveTime.staleAfter
                                                 ? Color.orange : Theme.textMuted)
                        }
                    }
                    .padding(Theme.spacingM)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
                }
                ForEach(unavailable, id: \.source) { source in
                    Text("\(Self.sourceLabel(source.source)) — \(source.reason ?? String(localized: "unavailable"))")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedNowSection")
    }

    private var entries: [(kind: String, text: String)] {
        if let e = observed.summaryEntries, !e.isEmpty { return e.map { ($0.kind, $0.text) } }
        return (observed.summaryLines ?? []).map { ("", $0) }
    }

    private var unavailable: [ObservedSourceStatus] {
        (observed.sources ?? []).filter { !$0.available }
    }

    /// Each clause pairs with its OWN source's frame time (coverage is the
    /// reflectivity frame's), never a shared clock.
    private func validTime(_ kind: String) -> String? {
        switch kind {
        case "reflectivity", "coverage": observed.reflectivity?.validTime
        case "rain_rate": observed.rainRate?.validTime
        case "cloud_tops": observed.cloudTops?.validTime
        case "lightning": observed.lightning?.validTime
        default: nil
        }
    }

    static func sourceLabel(_ source: String) -> String {
        switch source {
        case "opera_dbzh": String(localized: "Radar reflectivity")
        case "opera_rate": String(localized: "Radar rain rate")
        case "eumetsat_li": String(localized: "Lightning")
        case "eumetsat_ctth": String(localized: "Satellite cloud tops")
        default: source
        }
    }
}

// MARK: - Cells

/// The radar cells near the route (#816): the server's `/live` storms — the
/// same list the ribbon, the alerts, the web and Android show — ahead first in
/// route order, each with where it is against the track, how it moves toward
/// it and when the flight is abeam. Nothing is measured on the phone; the map
/// keeps the raw cell feed. Describes evolution, not safety: experimental.
struct ObservedCellsSection: View {
    let viewModel: BriefingViewModel
    let storms: LiveStorms
    @State private var showsAll = false

    private static let compactCap = 5
    private typealias L = LiveStormListRules

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack(alignment: .firstTextBaseline) {
                Text("Radar cells")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Text("experimental")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.textMuted)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .overlay(Capsule().stroke(Theme.border))
                Spacer(minLength: 0)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedCellsSection")
    }

    @ViewBuilder
    private var content: some View {
        if let status = L.statusText(storms) {
            muted(status)
        } else {
            TimelineView(.periodic(from: .now, by: 60)) { context in
                muted(L.caption(storms, now: context.date), font: .caption)
            }
            let ahead = L.ahead(storms)
            let behind = L.behind(storms)
            if ahead.isEmpty {
                muted("No cells ahead within \(L.corridorText(storms)) of track")
            } else {
                let shown = showsAll ? ahead : Array(ahead.prefix(Self.compactCap))
                ForEach(shown) { storm in
                    CellRow(viewModel: viewModel, storm: storm)
                }
            }
            if !behind.isEmpty {
                // Capped like the ahead list: a busy day on a long route
                // would otherwise bury the caveat under passed storms.
                muted(behind.count == 1 ? "1 cell behind" : "\(behind.count) cells behind", font: .caption)
                ForEach(showsAll ? behind : Array(behind.prefix(Self.compactCap))) { storm in
                    CellRow(viewModel: viewModel, storm: storm)
                        .opacity(0.6)
                }
            }
            if ahead.count > Self.compactCap || behind.count > Self.compactCap {
                Button(showsAll ? "Show fewer" : "Show all \(ahead.count + behind.count)") { showsAll.toggle() }
                    .font(.subheadline.weight(.semibold))
                    .frame(minHeight: 44)
            }
            muted(CellsOverlay.caveat, font: .caption2)
        }
    }

    private func muted(_ text: String, font: Font = .subheadline) -> some View {
        Text(text)
            .font(font)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// One storm: trend dot, strength, where it is against the track, then
/// motion, abeam time and what it is. Taps open the map on it.
private struct CellRow: View {
    let viewModel: BriefingViewModel
    let storm: LiveStorm

    var body: some View {
        if let focus = storm.focus {
            Button {
                viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
            } label: { row(showsMap: true) }
            .buttonStyle(.plain)
            .accessibilityIdentifier("observedCell-\(storm.id)")
        } else {
            row(showsMap: false)
        }
    }

    private func row(showsMap: Bool) -> some View {
        HStack(alignment: .top, spacing: Theme.spacingS) {
            Circle()
                .fill(Color(uiColor: CellsOverlay.trendColor(storm.trend)))
                .overlay(Circle().stroke(Color(white: 0.13), lineWidth: 1))
                .frame(width: 14, height: 14)
                .padding(.top, 3)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Theme.spacingS) {
                    Text(LiveStormListRules.title(storm))
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    if let dbz = storm.peakDbz {
                        Text(CellsOverlay.dbzText(dbz))
                            .font(.subheadline)
                            .foregroundStyle(Theme.text)
                    }
                }
                Text(RouteRibbonRules.stormPositionText(storm))
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Text(LiveStormListRules.detailLine(storm))
                    .font(.subheadline)
                    .foregroundStyle(storm.isClosing ? Color.orange : Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if showsMap {
                Image(systemName: "map")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .padding(.top, 3)
            }
        }
        .padding(.vertical, 4)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
