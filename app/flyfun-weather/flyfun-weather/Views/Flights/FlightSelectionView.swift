import SwiftUI

/// Multi-select + bulk delete for the pilot's own flights (#553), the iOS twin
/// of the web's per-card checkbox + floating selection bar.
///
/// **Why a dedicated sheet rather than an edit mode on the flight list.** Bulk
/// delete is a rare, destructive, power-user action; the flight list is the app's
/// primary surface and drives the iPad detail pane through its own
/// `List(selection: $selection)` of `SidebarSelection`. Putting a second
/// selection binding and the system `\.editMode` onto that same List is the flaky
/// corner of the API (`editMode` multi-select over `NavigationLink(value:)` rows
/// inside a `NavigationSplitView` sidebar), and a stale sidebar selection on exit
/// can strand the detail pane. This sheet is a *fresh* `List(selection:)` with
/// zero relationship to the sidebar list, so nothing here can regress
/// tap-to-open-briefing, the swipe actions, or the split view.
struct FlightSelectionView: View {
    /// The already-loaded flight list — the sheet never re-fetches.
    let flights: [FlightResponse]
    /// The user's Future ordering preference, so the sections read exactly like
    /// the main list.
    let order: FlightOrder
    let repository: any BriefingRepository
    /// Ids the server confirmed deleted. The parent reloads the list and clears a
    /// detail selection that pointed at one of them.
    let onDeleted: ([String]) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var selectedIDs: Set<String> = []
    @State private var confirmingDelete = false
    @State private var isDeleting = false
    /// Partial-success message ("Deleted N; M could not be deleted"). A silent
    /// partial success on a destructive action is the worst outcome, so it is
    /// always surfaced.
    @State private var partialMessage: String?
    @State private var errorMessage: String?
    /// Ids this sheet has already deleted. The parent reloads its list, but the
    /// sheet keeps the snapshot it was handed — on a partial result it stays open,
    /// so without this the rows it just deleted would still be listed.
    @State private var locallyDeleted: Set<String> = []

    /// Only the viewer's own flights are selectable: the server 404s a
    /// subscriber's delete (they'd land in `not_found`), and a shared flight is
    /// dropped with Unsubscribe instead. Filtering here — rather than letting the
    /// row fail on delete — means a subscriber's row is never even tickable.
    /// Static + pure so the gate is unit-testable.
    static func selectable(_ flights: [FlightResponse]) -> [FlightResponse] {
        flights.filter(\.isEditable)
    }

    /// Ids of the selectable flights in the "Past" group — what **Select All
    /// Past** ticks. Mirrors the web's `selectablePast`.
    static func pastIDs(_ flights: [FlightResponse], order: FlightOrder = .furthestFirst,
                        now: Date = Date()) -> [String] {
        FlightListView.groupedFlights(selectable(flights), order: order, now: now)
            .first { $0.title == "Past" }?
            .flights.map(\.id) ?? []
    }

    /// The snapshot minus anything this sheet has already deleted.
    private var remaining: [FlightResponse] {
        flights.filter { !locallyDeleted.contains($0.id) }
    }

    private var groups: [FlightListView.FlightGroup] {
        FlightListView.groupedFlights(Self.selectable(remaining), order: order)
    }

    private var allIDs: [String] { Self.selectable(remaining).map(\.id) }
    private var pastIDs: [String] { Self.pastIDs(remaining, order: order) }

    var body: some View {
        NavigationStack {
            Group {
                if allIDs.isEmpty {
                    ContentUnavailableView(
                        "Nothing to Delete",
                        systemImage: "checkmark.circle",
                        description: Text("Only flights you own can be deleted here. Shared flights are removed with Unsubscribe.")
                    )
                } else {
                    flightList
                }
            }
            .navigationTitle("Select Flights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .accessibilityIdentifier("bulkSelectDoneButton")
                }
            }
            // Permanently in select mode — the sheet has exactly one purpose, so
            // an `EditButton` here would only offer a state in which nothing on
            // screen does anything. `.constant` is safe precisely because nothing
            // else toggles it.
            .environment(\.editMode, .constant(.active))
            .safeAreaInset(edge: .bottom) {
                if !allIDs.isEmpty { actionBar }
            }
        }
        // An `alert`, NOT a `confirmationDialog`: on iPad the latter renders as a
        // popover, and a popover drops the cancel-role button — leaving Delete as
        // the only visible choice on an irreversible action. Same reasoning as the
        // single-flight delete in `FlightListView`.
        .alert("Delete Flights?", isPresented: $confirmingDelete) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) { performDelete() }
                .accessibilityIdentifier("confirmBulkDeleteButton")
        } message: {
            Text("Delete \(selectedIDs.count) selected flight\(selectedIDs.count == 1 ? "" : "s")? This removes all their briefing history.")
        }
        .alert("Partly deleted", isPresented: Binding(
            get: { partialMessage != nil },
            set: { if !$0 { partialMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(partialMessage ?? "")
        }
        .alert("Couldn’t delete flights", isPresented: Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private var flightList: some View {
        // Unlike the main list, Past is *expanded*: it is the section this feature
        // exists for (clearing out an old logbook).
        List(selection: $selectedIDs) {
            ForEach(groups, id: \.title) { group in
                Section(group.title) {
                    ForEach(group.flights) { flight in
                        FlightSelectionRow(flight: flight)
                            .tag(flight.id)
                    }
                }
            }
        }
        .accessibilityIdentifier("flightSelectionList")
    }

    private var actionBar: some View {
        VStack(spacing: 8) {
            Text("\(selectedIDs.count) selected")
                .font(.subheadline.weight(.medium))
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("bulkSelectionCount")

            HStack(spacing: 12) {
                Button("Select all") { selectedIDs = Set(allIDs) }
                    .buttonStyle(.bordered)
                if !pastIDs.isEmpty {
                    Button("Select all past") { selectedIDs = Set(pastIDs) }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("selectAllPastButton")
                }
                Button("Clear") { selectedIDs.removeAll() }
                    .buttonStyle(.bordered)
                    .disabled(selectedIDs.isEmpty)

                Spacer(minLength: 0)

                Button(role: .destructive) {
                    confirmingDelete = true
                } label: {
                    if isDeleting {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Delete selected")
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(selectedIDs.isEmpty || isDeleting)
                .accessibilityIdentifier("bulkDeleteButton")
            }
            .font(.subheadline)
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.bar)
    }

    /// Delete the ticked flights, then hand the *confirmed* ids back to the
    /// parent. The sheet stays open on a partial result so the user can see which
    /// rows survived; a clean full delete dismisses it.
    private func performDelete() {
        let ids = Array(selectedIDs)
        guard !ids.isEmpty else { return }
        isDeleting = true
        Task {
            defer { isDeleting = false }
            do {
                let response = try await repository.bulkDeleteFlights(ids: ids)
                selectedIDs.subtract(response.deleted)
                locallyDeleted.formUnion(response.deleted)
                if !response.deleted.isEmpty { onDeleted(response.deleted) }
                if response.notFound.isEmpty {
                    dismiss()
                } else {
                    partialMessage = "Deleted \(response.deleted.count) flight\(response.deleted.count == 1 ? "" : "s"); \(response.notFound.count) could not be deleted."
                }
            } catch let error as APIError {
                errorMessage = error.errorDescription ?? "Couldn’t delete the selected flights."
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

/// Compact selection row: route, date, assessment dot. Deliberately *not*
/// `FlightCardView` — that card carries swipe actions and a context menu that
/// fight the selection gesture, and none of it is useful while selecting.
private struct FlightSelectionRow: View {
    let flight: FlightResponse

    var body: some View {
        HStack(spacing: 10) {
            assessmentDot
            VStack(alignment: .leading, spacing: 2) {
                Text(flight.shortTitle)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                if let date = flight.departureDate {
                    Text("\(DateFormatter.shortDate.string(from: date)) \(DateFormatter.utcTime.string(from: date))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .accessibilityIdentifier("selectFlightRow-\(flight.id)")
    }

    /// GREEN / AMBER / RED at a glance, grey when the flight has no assessment
    /// (never briefed, or beyond the forecast horizon). A dot, not the full
    /// badge — the row is a picker, not a briefing summary.
    private var assessmentDot: some View {
        let assessment: Assessment = flight.latestBriefing?.assessment
            .flatMap { Assessment(rawValue: $0.lowercased()) } ?? .unavailable
        return Circle()
            .fill(assessment.color)
            .frame(width: 10, height: 10)
            .accessibilityLabel(assessment.label)
    }
}
