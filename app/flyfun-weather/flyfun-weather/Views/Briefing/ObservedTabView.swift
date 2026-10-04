import SwiftUI

/// The **Observed** tab (#661): everything *measured* on flight day, in the
/// order a pilot reads it in the cockpit — on the day, and in the aircraft,
/// this is the screen the app is opened for (planning happens on a computer).
///
/// 1. **At a glance** — departure / destination category from their latest
///    METAR, the alert-tier changes, matched SIGMETs and convective cores near
///    the route, with one button that opens the map with radar and cells on.
/// 2. **Since this briefing** — the live layer's significant changes (#637).
/// 3. **Radar & lightning now** — the server's per-source summary clauses
///    (#574), each with its own frame age; missing sources are named.
/// 4. **Cells** — the experimental cell analysis for the route box (#656).
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
                ObservedGlanceCard(viewModel: viewModel)
                    .spyAnchor("glance")
                if let liveChanges = viewModel.liveChanges {
                    LiveChangesView(changes: liveChanges, baseline: viewModel.liveBaselineDate)
                        .spyAnchor("live")
                }
                if let observed = observedConditions, observed.hasAnyField {
                    ObservedNowView(observed: observed)
                        .spyAnchor("radar")
                }
                if showsCells {
                    ObservedCellsSection(viewModel: viewModel)
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
        .task(id: cellsPollKey) {
            // Online-only; polls while this tab is on screen. No radar is drawn
            // here, so the newest current overlay is the one listed.
            guard showsCells else { return }
            let box = viewModel.cellsBox
            await viewModel.cellsModel.poll(radarStamp: { nil }, box: box)
        }
    }

    private var snapshot: SnapshotResponse? {
        if case .loaded(let snapshot) = viewModel.snapshotState { return snapshot }
        return nil
    }

    private var observedConditions: ObservedConditions? { snapshot?.observedConditions }

    /// Cells are offered wherever the radar is (web: the route map's Cells
    /// toggle appears with the observed layers).
    private var showsCells: Bool { observedConditions?.hasAnyField ?? false }

    private var cellsPollKey: String {
        guard showsCells, let box = viewModel.cellsBox else { return "off" }
        return "\(box.south),\(box.west),\(box.north),\(box.east)"
    }

    private var hasObservations: Bool {
        !(snapshot?.routeObservations?.reportingAirports.isEmpty ?? true)
    }

    private var hasSigmets: Bool {
        !(snapshot?.routeSigmets?.matched.isEmpty ?? true)
    }

    private var spySections: [SpySection] {
        var sections = [SpySection("glance", "Now")]
        if viewModel.liveChanges != nil { sections.append(SpySection("live", "Changes")) }
        if observedConditions?.hasAnyField ?? false { sections.append(SpySection("radar", "Radar")) }
        if showsCells { sections.append(SpySection("cells", "Cells")) }
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
        let obs = snapshot?.routeObservations?.airports?.first { $0.icao == icao }
        let metarTime = obs?.metarTime.flatMap(Date.parseISO8601)
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
                Text("METAR \(LiveTime.zulu(metarTime)) · \(LiveTime.ageLabel(from: metarTime, now: now))")
                    .font(.caption)
                    .foregroundStyle(stale ? Color.orange : Theme.textMuted)
            } else {
                Text("No METAR")
                    .font(.caption)
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

    /// Cores in the route box from the shared cell model — attention, not a
    /// verdict, so neutral when there are none.
    private var cellsChip: Chip? {
        let model = viewModel.cellsModel
        guard case .ok = model.match, let display = model.display else { return nil }
        let cores = CellsOverlay.listedCells(display).count
        if cores == 0 {
            return Chip(text: "No radar cores near route", icon: "cloud", tint: Theme.textMuted)
        }
        return Chip(text: cores == 1 ? "1 radar core near route" : "\(cores) radar cores near route",
                    icon: "cloud.bolt", tint: Theme.primary)
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
                if let corridor = observed.corridorNm {
                    Text("Within \(Int(corridor.rounded())) NM of the route")
                        .font(.caption)
                        .foregroundStyle(Theme.textMuted)
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

/// The experimental cell analysis for the route box: convective cores,
/// strongest first, each with where it is, how it is evolving and where it is
/// heading. Describes evolution, not safety — labelled experimental.
struct ObservedCellsSection: View {
    let viewModel: BriefingViewModel
    @State private var showsAll = false

    private static let compactCap = 5

    var body: some View {
        let model = viewModel.cellsModel
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
            content(model)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedCellsSection")
    }

    @ViewBuilder
    private func content(_ model: RouteCellsModel) -> some View {
        if !model.hasLoaded {
            if viewModel.isOnline {
                ProgressView("Checking radar cells…")
                    .font(.subheadline)
            } else {
                muted("Offline — radar cells need a connection")
            }
        } else if case .ok = model.match, let display = model.display {
            if let badge = model.badge { muted(badge, font: .caption) }
            let cells = CellsOverlay.listedCells(display)
            if cells.isEmpty {
                muted("No convective cores near the route")
            } else {
                let shown = showsAll ? cells : Array(cells.prefix(Self.compactCap))
                ForEach(shown) { cell in
                    CellRow(cell: cell, location: CellsOverlay.locationLabel(cell, waypoints: viewModel.routeWaypoints))
                }
                if cells.count > Self.compactCap {
                    Button(showsAll ? "Show fewer" : "Show all \(cells.count)") { showsAll.toggle() }
                        .font(.subheadline.weight(.semibold))
                        .frame(minHeight: 44)
                }
            }
            muted(CellsOverlay.caveat, font: .caption2)
        } else if !viewModel.isOnline {
            muted("Offline — radar cells need a connection")
        } else if let badge = model.badge {
            muted(badge)
        }
    }

    private func muted(_ text: String, font: Font = .subheadline) -> some View {
        Text(text)
            .font(font)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// One core: trend dot, strength, where it is, then evolution and motion.
private struct CellRow: View {
    let cell: DisplayCell
    let location: String?

    var body: some View {
        HStack(alignment: .top, spacing: Theme.spacingS) {
            Circle()
                .fill(Color(uiColor: CellsOverlay.trendColor(cell.trend?.state)))
                .overlay(Circle().stroke(Color(white: 0.13), lineWidth: 1))
                .frame(width: 14, height: 14)
                .padding(.top, 3)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Theme.spacingS) {
                    Text(cell.peakDbz.map { "\(CellsOverlay.number($0)) dBZ" } ?? CellsOverlay.tierLabel(cell.tier))
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    if let location {
                        Text(location)
                            .font(.subheadline)
                            .foregroundStyle(Theme.text)
                    }
                }
                Text(secondLine)
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private var secondLine: String {
        var parts = [cell.trend?.state ?? "trend unknown", CellsOverlay.motionText(cell.motion)]
        if let flashes = cell.flashes, flashes > 0 { parts.append("\(CellsOverlay.number(flashes)) flashes") }
        if cell.flashes == nil, cell.flashesPending == true { parts.append("lightning pending") }
        if let top = cell.topFl { parts.append("top FL\(Int(top.rounded()))") }
        if cell.truncated == true { parts.append("partly outside radar coverage") }
        return parts.joined(separator: " · ")
    }
}
