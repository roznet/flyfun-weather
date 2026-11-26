import SwiftUI

/// The Past flights screen (#1032): search, hazard and outcome chips with the
/// server's counts, the list grouped by month while a filter is on, paged from
/// `GET /api/flights/archive`. Owned flights only; subscribed flights never
/// appear. Tapping a row opens its briefing through `onOpen`.
struct PastFlightsView: View {
    @State private var viewModel: PastFlightsViewModel
    @State private var searchText: String
    let onOpen: (String) -> Void

    init(repository: any BriefingRepository, filter: PastFilter = .all, onOpen: @escaping (String) -> Void) {
        _viewModel = State(initialValue: PastFlightsViewModel(repository: repository, filter: filter))
        _searchText = State(initialValue: filter.query)
        self.onOpen = onOpen
    }

    var body: some View {
        List {
            Section {
                PastChipRows(viewModel: viewModel)
                    .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
            }
            if viewModel.rows.isEmpty && !viewModel.isLoading {
                emptyState
            } else if viewModel.filter.isActive {
                ForEach(viewModel.monthSections) { section in
                    Section(section.title) { rows(section.rows) }
                }
            } else {
                Section { rows(viewModel.rows) }
            }
            if viewModel.isLoading {
                HStack { Spacer(); ProgressView(); Spacer() }
            }
        }
        .navigationTitle("Past flights")
        .searchable(text: $searchText, prompt: "ICAO, description or trip")
        .textInputAutocapitalization(.characters)
        .onChange(of: searchText) { _, value in viewModel.setQuery(value) }
        .refreshable { await viewModel.reload() }
        .task { if viewModel.rows.isEmpty { await viewModel.reload() } }
        .accessibilityIdentifier("pastFlightsList")
    }

    @ViewBuilder
    private func rows(_ rows: [PastArchiveRow]) -> some View {
        ForEach(rows) { row in
            Button { onOpen(row.id) } label: { PastFlightRow(row: row) }
                .buttonStyle(.plain)
                .task { await viewModel.loadMoreIfNeeded(after: row) }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        if let error = viewModel.error {
            ContentUnavailableView("Couldn't load past flights", systemImage: "wifi.exclamationmark",
                                   description: Text(error))
        } else if viewModel.filter.isActive {
            ContentUnavailableView {
                Label("No flights match", systemImage: "line.3.horizontal.decrease.circle")
            } description: {
                Text("No past flights match these filters.")
            } actions: {
                Button("Clear filters") {
                    searchText = ""
                    viewModel.update { $0 = .all }
                }
            }
        } else {
            ContentUnavailableView("No past flights", systemImage: "airplane")
        }
    }
}

/// The two chip rows (hazards + Reported, outcomes + Hide), horizontally
/// scrolling. Counts are the server's facets.
private struct PastChipRows: View {
    let viewModel: PastFlightsViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(PastChips.hazards, id: \.id) { chip in
                        PastChip(label: chip.label, count: viewModel.facets?.tags[chip.id],
                                 isOn: viewModel.filter.tags.contains(chip.id),
                                 dashed: viewModel.filter.reported) {
                            viewModel.toggleTag(chip.id)
                        }
                    }
                    PastChip(label: "Reported", count: nil, isOn: viewModel.filter.reported, dashed: true) {
                        viewModel.update { $0.reported.toggle() }
                    }
                }
                .padding(.horizontal)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(PastChips.outcomes, id: \.id) { chip in
                        PastChip(label: chip.label, count: viewModel.facets?.outcomes[chip.id],
                                 isOn: viewModel.filter.outcomes.contains(chip.id), dashed: false) {
                            viewModel.toggleOutcome(chip.id)
                        }
                    }
                    PastChip(label: "Hide cancelled and monitor only", count: nil,
                             isOn: viewModel.filter.hideInactive, dashed: false) {
                        viewModel.update { $0.hideInactive.toggle() }
                    }
                }
                .padding(.horizontal)
            }
        }
    }
}

private struct PastChip: View {
    let label: String
    let count: Int?
    let isOn: Bool
    let dashed: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(label)
                if let count {
                    Text("\(count)").foregroundStyle(isOn ? .white.opacity(0.8) : .secondary).monospacedDigit()
                }
            }
            .font(.footnote)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .foregroundStyle(isOn ? .white : .primary)
            .background(Capsule().fill(isOn ? Color.accentColor : Color.clear))
            .overlay(
                Capsule().strokeBorder(isOn ? Color.accentColor : Color.secondary.opacity(0.5),
                                       style: StrokeStyle(lineWidth: 1, dash: dashed ? [4, 3] : []))
            )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// One archive row: route, date, outcome, hazard dots, reported tags.
struct PastFlightRow: View {
    let row: PastArchiveRow

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(row.title).font(.headline)
                Spacer()
                if let date = row.departureDate {
                    Text(Self.dateFormatter.string(from: date)).font(.subheadline).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 6) {
                if let trip = row.trip {
                    Text(trip.name).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                if let decision = row.decision {
                    Text(PastChips.label(forOutcome: decision))
                        .font(.caption).padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(Color.secondary.opacity(0.15)))
                }
                if row.differsFromForecast {
                    Text("Differed from forecast").font(.caption).foregroundStyle(.secondary)
                }
            }
            hazards
            if !row.reportedTags.isEmpty {
                Text("Reported: " + row.reportedTags.map(PastChips.label(forTag:)).joined(separator: ", "))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var hazards: some View {
        if !row.tagsRecorded {
            Text("Hazards not recorded").font(.caption).foregroundStyle(.secondary)
        } else if row.forecastTags.isEmpty {
            Text("No amber or red hazard in the last briefing").font(.caption).foregroundStyle(.secondary)
        } else {
            HStack(spacing: 8) {
                ForEach(PastChips.hazards.filter { row.forecastTags[$0.id] != nil }, id: \.id) { chip in
                    let red = row.forecastTags[chip.id] == "red"
                    Label {
                        Text(chip.label)
                    } icon: {
                        Circle().fill(red ? Color.red : Color.orange).frame(width: 8, height: 8)
                    }
                    .font(.caption)
                    .accessibilityLabel("\(chip.label): \(red ? "red" : "amber")")
                }
            }
        }
    }
}
