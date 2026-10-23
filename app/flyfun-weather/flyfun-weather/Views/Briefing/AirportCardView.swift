import SwiftUI

// Departure / arrival airport cards (#882, part of #737) — the drawing. The
// agreed design is the "Airport Card Lab" mockup (Now → at ETA, category
// meter on, TAF strip on, dial large, departure folded after Start). All
// picking and ordering lives in `AirportCardRules.swift`; every value is the
// server's (#880).
//
// SYNC — web counterpart: web/ts/visualization/observed/airport-card-view.ts
// (#881). Deliberate divergences:
//  - Category colours are the app's own (`RouteRibbonRules.categoryColor`,
//    MVFR blue as on the ribbon right below and every other iOS badge); the
//    web card uses its amber MVFR.
//  - Each card is one VoiceOver element with a spoken summary and a "Show on
//    map" action (the web exposes its rows).

private typealias R = AirportCardRules

// MARK: - Both cards

/// The cards in the server's order: departure then arrival, or the arrival
/// first and the folded departure line after it. Side by side on a regular
/// width (iPad), stacked on an iPhone.
struct AirportCardsSection: View {
    let viewModel: BriefingViewModel
    let departure: LiveGlanceDeparture?
    let destination: LiveGlanceDestination?
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    var body: some View {
        let slots = R.slots(departure: departure, destination: destination)
        Group {
            if horizontalSizeClass == .regular {
                HStack(alignment: .top, spacing: Theme.spacingM) {
                    ForEach(slots, id: \.self) { slot($0) }
                }
            } else {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    ForEach(slots, id: \.self) { slot($0) }
                }
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("airportCards")
    }

    @ViewBuilder
    private func slot(_ slot: AirportCardRules.Slot) -> some View {
        switch slot {
        case .departure:
            if let departure { AirportCard(viewModel: viewModel, block: departure, role: .departure) }
        case .arrival:
            if let destination { AirportCard(viewModel: viewModel, block: destination, role: .arrival) }
        case .foldedDeparture:
            if let departure {
                FoldedDepartureCard(viewModel: viewModel, departure: departure, flightId: viewModel.flight.id)
            }
        }
    }
}

// MARK: - One card

struct AirportCard: View {
    let viewModel: BriefingViewModel
    let block: any LiveGlanceTerminal
    let role: AirportCardRules.Role
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            AirportCardHeader(block: block, role: role)
            HStack(alignment: .top, spacing: Theme.spacingS) {
                VStack(alignment: .leading, spacing: 6) { rows }
                    .frame(maxWidth: .infinity, alignment: .leading)
                if let dial {
                    // Fixed, never stretched: iPad ~120 pt, iPhone 92 pt.
                    RunwayWindCanvas(dial: dial, size: .compact)
                        .frame(width: dialSide, height: dialSide)
                }
            }
            if let hours = block.airport?.tafHours, !hours.isEmpty {
                TafStrip(hours: hours, role: role)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(alignment: .leading) {
            // An alert-tier change at this field: a red bar, as on the lines.
            if block.alert == true {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Theme.red)
                    .frame(width: 4)
                    .padding(.vertical, Theme.spacingS)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .contentShape(Rectangle())
        .onTapGesture { openMap(block.focus) }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(R.accessibilitySummary(block, role: role))
        .accessibilityIdentifier("airportCard-\(role == .departure ? "dep" : "arr")")
        .accessibilityAction(named: Text("Show on map")) { openMap(block.focus) }
    }

    private var dialSide: CGFloat { horizontalSizeClass == .regular ? 120 : 92 }

    private var dial: RunwayWindRules.Dial? {
        guard let airport = block.airport, airport.runwayWind != nil else { return nil }
        return RunwayWindRules.dial(role: role.rawValue, icao: block.icao ?? airport.icao, airport: airport, ghost: true)
    }

    @ViewBuilder
    private var rows: some View {
        if let a = block.airport {
            CategoryMeterRow(symbol: a.metarCavok == true ? "sun.max" : "cloud",
                             text: a.metarSkyText, position: R.meterPosition(a.metarCeilingMeter))
            CategoryMeterRow(symbol: "eye", text: a.metarVisibilityText,
                             position: R.meterPosition(a.metarVisibilityMeter))
            if let words = a.metarWeatherWords, !words.isEmpty {
                FlowLayout(spacing: Theme.spacingS) {
                    ForEach(words, id: \.code) { word in
                        Label(word.text, systemImage: R.weatherSymbol(word.icon))
                            .font(.subheadline)
                            .foregroundStyle(Theme.text)
                    }
                }
            }
            windRow(a)
        } else {
            // No airport row (an older server): the block's own lines.
            if let metar = block.metarText { rowText(metar) }
            if let wind = block.windText { rowText(wind) }
        }
        nearRows
        provenance
        if let caption = block.proxyCaption {
            Text(caption)
                .font(.caption2)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func windRow(_ a: AirportObservation) -> some View {
        if let wind = a.metarWindShort {
            FlowLayout(spacing: Theme.spacingS) {
                Label {
                    Text(wind).font(.subheadline.monospaced().weight(.semibold))
                } icon: {
                    Image(systemName: "wind")
                }
                .foregroundStyle(Theme.text)
                if let x = a.metarCrosswind {
                    Text("RWY \(x.runway)")
                        .font(.caption)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Theme.border.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
                        .foregroundStyle(Theme.text)
                    let tone = R.crosswindTone(a)
                    Text(R.crosswindText(x))
                        .font(.subheadline.monospacedDigit().weight(tone == nil ? .regular : .semibold))
                        .foregroundStyle(tone == "red" ? Theme.red : tone == "amber" ? Theme.amber : Theme.text)
                }
            }
        }
    }

    /// Cells and lightning near the field, in the server's words; amber only
    /// when the server says an alert-tier change stands behind the row.
    @ViewBuilder
    private var nearRows: some View {
        if let cells = block.cellsText {
            nearRow(symbol: "circle.circle", text: cells, alert: block.cellsAlert == true)
        }
        ForEach(block.cells ?? []) { cell in
            Button { openMap(cell.focus ?? block.focus) } label: {
                HStack(spacing: Theme.spacingXS) {
                    Text(cell.text ?? "")
                        .font(.caption)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if cell.focus != nil {
                        Image(systemName: "map").font(.caption2).foregroundStyle(Theme.textMuted)
                    }
                }
                .padding(.leading, 21)
                .frame(minHeight: 28, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        if let lightning = block.lightningText {
            nearRow(symbol: "bolt", text: lightning, alert: block.lightningAlert == true)
        }
    }

    private func nearRow(symbol: String, text: String, alert: Bool) -> some View {
        Label {
            Text(text)
                .font(.subheadline.weight(alert ? .semibold : .regular))
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: symbol)
        }
        .foregroundStyle(alert ? Theme.amber : Theme.text)
    }

    private func rowText(_ text: String) -> some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(Theme.text)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// "METAR 09:30Z · 8 min ago" (orange past the stale age) · SPECI ·
    /// trend · the TAF's reason when it has no category at the ETA · ETD/ETA.
    private var provenance: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            FlowLayout(spacing: 6) {
                Image(systemName: "clock").foregroundStyle(Theme.textMuted)
                if let at = (block.airport?.metarTime ?? block.metarTime).flatMap(Date.parseISO8601) {
                    Text("METAR \(LiveTime.zulu(at)) · \(LiveTime.ageLabel(from: at, now: context.date))")
                        .foregroundStyle(context.date.timeIntervalSince(at) > LiveTime.staleAfter ? Color.orange : Theme.textMuted)
                }
                if block.airport?.metarReportType == "SPECI" {
                    Text("SPECI")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(Theme.amber)
                        .padding(.horizontal, 4)
                        .overlay(RoundedRectangle(cornerRadius: 3).stroke(Theme.amber, lineWidth: 1))
                }
                if let trend = R.trend(block.airport) {
                    Text("\(trend.arrow) \(trend.text)")
                        .foregroundStyle(trend.worse ? Theme.amber : Theme.green)
                }
                if let note = R.tafNote(block) {
                    Text(note).foregroundStyle(Theme.textMuted)
                }
                if let eta = R.etaText(block, role: role) {
                    Text(eta).foregroundStyle(Theme.textMuted)
                }
            }
            .font(.caption)
        }
    }

    private func openMap(_ focus: LiveFocus?) {
        guard let focus else { return }
        viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
    }
}

// MARK: - Header

/// Role, ICAO, name, then the category boxes: METAR now → TAF at ETD/ETA,
/// and a dashed box for a worse TEMPO/PROB group.
private struct AirportCardHeader: View {
    let block: any LiveGlanceTerminal
    let role: AirportCardRules.Role

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(role == .departure ? String(localized: "DEPARTURE") : String(localized: "ARRIVAL"))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
            HStack(alignment: .center, spacing: Theme.spacingS) {
                Text(block.icao ?? "")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(Theme.text)
                if let name = block.name {
                    Text(name)
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 0)
                boxes
            }
        }
    }

    private var boxes: some View {
        let boxes = R.boxes(block, role: role)
        return HStack(spacing: 4) {
            ForEach(Array(boxes.enumerated()), id: \.offset) { index, box in
                if index == 1, !box.dashed {
                    Text("→").font(.caption).foregroundStyle(Theme.textMuted)
                }
                CategoryBoxView(box: box)
            }
        }
        .fixedSize()
    }
}

/// A category box: filled = prevailing, dashed outline = TEMPO/PROB.
struct CategoryBoxView: View {
    let box: AirportCardRules.CategoryBox
    var small = false

    var body: some View {
        let colour = RouteRibbonRules.categoryColor(box.category)
        VStack(spacing: 0) {
            Text(box.category.uppercased())
                .font(.caption.weight(.bold))
            if !small {
                Text(box.caption.uppercased())
                    .font(.system(size: 9, weight: .medium))
                    .opacity(0.9)
            }
        }
        .lineLimit(1)
        .foregroundStyle(box.dashed ? colour : .white)
        .padding(.horizontal, small ? 6 : 7)
        .padding(.vertical, small ? 2 : 3)
        .frame(minWidth: small ? 0 : 44)
        .background(box.dashed ? Color.clear : colour, in: RoundedRectangle(cornerRadius: 5))
        .overlay {
            if box.dashed {
                RoundedRectangle(cornerRadius: 5)
                    .stroke(colour, style: StrokeStyle(lineWidth: 1.5, dash: [3, 2]))
            }
        }
    }
}

// MARK: - Category meter

/// Icon, the server's words, then four equal bands LIFR | IFR | MVFR | VFR
/// with a dot at the server's 0…1 position.
private struct CategoryMeterRow: View {
    let symbol: String
    let text: String?
    let position: Double?

    private static let bands = ["LIFR", "IFR", "MVFR", "VFR"]

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: symbol)
                .font(.subheadline)
                .foregroundStyle(Theme.textMuted)
                .frame(width: 18)
            Text(text ?? "—")
                .font(.subheadline)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .frame(minWidth: 70, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    HStack(spacing: 1) {
                        ForEach(Self.bands, id: \.self) { band in
                            RouteRibbonRules.categoryColor(band).opacity(0.35)
                        }
                    }
                    .frame(height: 6)
                    .clipShape(Capsule())
                    if let position {
                        Circle()
                            .fill(Theme.text)
                            .overlay(Circle().stroke(Theme.surface, lineWidth: 2))
                            .frame(width: 10, height: 10)
                            .offset(x: geo.size.width * position - 5)
                    }
                }
                .frame(maxHeight: .infinity)
            }
            .frame(height: 12)
        }
    }
}

// MARK: - TAF strip

/// One cell per TAF hour: prevailing fill, hatched with the TEMPO/PROB
/// category when worse, the ETD/ETA hour outlined, hour labels "10Z".
private struct TafStrip: View {
    let hours: [TafHour]
    let role: AirportCardRules.Role

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 2) {
                ForEach(hours, id: \.time) { hour in
                    VStack(spacing: 2) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(hour.prevailingCategory.map { RouteRibbonRules.categoryColor($0) } ?? Theme.border)
                            .overlay {
                                if let temp = hour.temporaryCategory {
                                    hatch(RouteRibbonRules.categoryColor(temp))
                                        .clipShape(RoundedRectangle(cornerRadius: 2))
                                }
                            }
                            .frame(height: 10)
                            .overlay {
                                if hour.isEta == true {
                                    RoundedRectangle(cornerRadius: 3)
                                        .stroke(Theme.text, lineWidth: 2)
                                        .padding(-2)
                                }
                            }
                        Text(R.hourLabel(hour.time))
                            .font(.system(size: 9, weight: hour.isEta == true ? .bold : .regular).monospacedDigit())
                            .foregroundStyle(hour.isEta == true ? Theme.text : Theme.textMuted)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .padding(.top, 7)
        }
        .accessibilityIdentifier("airportCardTafStrip")
    }

    /// Diagonal stripes across the cell: the TEMPO/PROB hatch.
    private func hatch(_ colour: Color) -> some View {
        Canvas { context, size in
            var path = Path()
            var x = -size.height
            while x < size.width {
                path.move(to: CGPoint(x: x, y: size.height))
                path.addLine(to: CGPoint(x: x + size.height, y: 0))
                x += 5
            }
            context.stroke(path, with: .color(colour), lineWidth: 2)
        }
    }
}

// MARK: - Folded departure

/// The departure once the server folds it: one tappable line ("[VFR] LFPN
/// 290/15G25 · RWY 25R · departed ~10:09Z ›") that opens the full card in
/// place. The open state is remembered per flight.
private struct FoldedDepartureCard: View {
    let viewModel: BriefingViewModel
    let departure: LiveGlanceDeparture
    @AppStorage private var open: Bool

    init(viewModel: BriefingViewModel, departure: LiveGlanceDeparture, flightId: String) {
        self.viewModel = viewModel
        self.departure = departure
        _open = AppStorage(wrappedValue: false, AirportCardRules.departureOpenKey(flightId: flightId))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { open.toggle() }
            } label: {
                HStack(spacing: Theme.spacingS) {
                    if let category = departure.metarCategory ?? departure.airport?.metarFlightCategory {
                        CategoryBoxView(box: .init(category: category, caption: "", dashed: false), small: true)
                    }
                    Text(departure.foldedText ?? departure.icao ?? "")
                        .font(.subheadline)
                        .foregroundStyle(Theme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.textMuted)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
                .padding(.horizontal, Theme.spacingM)
                .frame(minHeight: 44)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
                .opacity(open ? 1 : 0.85)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("airportCardFolded")
            .accessibilityValue(open ? "expanded" : "collapsed")
            if open {
                AirportCard(viewModel: viewModel, block: departure, role: .departure)
                    .transition(.opacity)
            }
        }
    }
}

// MARK: - Offline banner

/// "Offline · showing data from 08:30Z, 12 min ago" (#882, carried over from
/// #816): the last live read failed or there is no connection. Cleared by the
/// next successful read. The time is the layer's own `live_updated_at`.
struct ObservedOfflineBanner: View {
    let since: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            Label {
                Text("Offline · showing data from \(LiveTime.zulu(since)), \(LiveTime.ageLabel(from: since, now: context.date))")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: "wifi.slash").foregroundStyle(Theme.amber)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Theme.spacingM)
            .padding(.vertical, Theme.spacingS)
            .background(Theme.amber.opacity(0.12), in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.amber, lineWidth: 1))
        }
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("observedOffline")
    }
}
