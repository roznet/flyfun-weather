import SwiftUI

// The two in-flight answers at the top of the Observed tab (#816):
//
//   - the flight strip: where the flight is and when it lands
//     (`glance.flight`), always first;
//   - "Destination at your ETA" (`glance.destination`), unfolded under the
//     lines still ahead.
//
// Every word is the server's (`tasks/live_glance.py::flight_strip` /
// `destination_block`). The client adds the title, the ETA, the wind
// advisory's colour and the taps that open the map.
//
// SYNC — web counterparts: `flightStripHtml` / `destinationHtml` in
// web/ts/visualization/observed/nutshell-view.ts.

// MARK: - Flight strip

struct ObservedFlightStrip: View {
    let flight: LiveGlanceFlight

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Image(systemName: flight.isLanded ? "airplane.arrival" : "airplane")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.primary)
                .accessibilityHidden(true)
            Text(flight.text ?? "")
                .font(.subheadline.weight(flight.phase == "planned" ? .regular : .semibold))
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.spacingM)
        .padding(.vertical, Theme.spacingS)
        .background(
            flight.phase == "planned" ? Theme.surface : Theme.primary.opacity(0.1),
            in: RoundedRectangle(cornerRadius: Theme.cornerRadius)
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cornerRadius)
                .stroke(Theme.border, style: StrokeStyle(lineWidth: 0.5, dash: flight.phase == "planned" ? [4, 3] : []))
        )
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("observedFlightStrip")
        .accessibilityValue(flight.phase ?? "")
    }
}

// MARK: - Destination at ETA

struct DestinationAtETACard: View {
    let viewModel: BriefingViewModel
    let destination: LiveGlanceDestination

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            title
            row("METAR", destination.metarText)
            if let caption = destination.proxyCaption {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            row("TAF", destination.tafText)
            if let wind = destination.windText {
                row("Wind", wind, tint: windTint)
            }
            row("Cells", destination.cellsText)
            ForEach(destination.cells ?? []) { cell in
                cellRow(cell)
            }
            if let lightning = destination.lightningText {
                row("Lightning", lightning)
            }
            if let missing = destination.unavailable, !missing.isEmpty {
                Text("\(missing.joined(separator: ", ")) unavailable")
                    .font(.caption)
                    .foregroundStyle(Theme.amber)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(alignment: .leading) {
            // An alert-tier change at the arrival: a red bar, as on the lines.
            RoundedRectangle(cornerRadius: 2)
                .fill(destination.alert == true ? Theme.red : Theme.border)
                .frame(width: 4)
                .padding(.vertical, Theme.spacingS)
        }
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("observedDestination")
    }

    private var titleText: String {
        var parts = [String(localized: "Destination at your ETA")]
        let place = [destination.icao, destination.name].compactMap { $0 }.joined(separator: " ")
        if !place.isEmpty { parts.append(place) }
        if let eta = destination.eta.flatMap(Date.parseISO8601) { parts.append(LiveTime.zulu(eta)) }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var title: some View {
        let label = HStack {
            Text(titleText)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if destination.focus != nil {
                Image(systemName: "map")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .frame(minHeight: 32)
        .contentShape(Rectangle())
        if let focus = destination.focus {
            Button {
                viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
            } label: { label }
            .buttonStyle(.plain)
            .accessibilityIdentifier("observedDestinationTitle")
        } else {
            label
        }
    }

    private var windTint: Color {
        switch destination.windAdvisory {
        case "red": Theme.red
        case "amber": Theme.amber
        default: Theme.text
        }
    }

    private func row(_ label: String, _ text: String?, tint: Color = Theme.text) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Text(label.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textMuted)
                .frame(width: 72, alignment: .leading)
            Text(text ?? "")
                .font(.subheadline)
                .foregroundStyle(tint)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func cellRow(_ cell: LiveDestinationCell) -> some View {
        let label = HStack(spacing: Theme.spacingS) {
            Text(cell.text ?? "")
                .font(.caption)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if cell.focus != nil {
                Image(systemName: "map")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .padding(.leading, 80)
        .frame(minHeight: 32)
        .contentShape(Rectangle())
        if let focus = cell.focus {
            Button {
                viewModel.setFocusIntent(FocusIntent(target: .map, mapFocus: focus))
            } label: { label }
            .buttonStyle(.plain)
            .accessibilityIdentifier("observedDestinationCell-\(cell.id)")
        } else {
            label
        }
    }
}
