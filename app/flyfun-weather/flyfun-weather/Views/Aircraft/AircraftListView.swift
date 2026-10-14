import SwiftUI

/// Settings → My Aircraft (#778): every saved aircraft, default first. Tap a
/// row to edit it; swipe (or long-press) to make it the default or delete it;
/// + adds one. The iOS counterpart of the web Settings Aircraft tab.
struct AircraftListView: View {
    @State private var viewModel: AircraftListViewModel
    @State private var showForm = false
    /// The aircraft awaiting delete confirmation.
    @State private var pendingDelete: AircraftResponse?

    init(repository: any BriefingRepository) {
        _viewModel = State(initialValue: AircraftListViewModel(repository: repository))
    }

    var body: some View {
        List {
            Section {
                if viewModel.aircraft.isEmpty {
                    if viewModel.isLoading || !(viewModel.hasLoaded || viewModel.loadFailed) {
                        HStack {
                            ProgressView()
                            Text("Loading aircraft\u{2026}")
                                .foregroundStyle(.secondary)
                        }
                    } else if viewModel.loadFailed {
                        Text("Couldn't load your aircraft. Pull to refresh.")
                            .foregroundStyle(.secondary)
                    } else {
                        // The server always keeps a default aircraft, so this
                        // only shows against an older server.
                        Text("No aircraft added yet.")
                            .foregroundStyle(.secondary)
                    }
                }
                ForEach(viewModel.aircraft) { aircraft in
                    row(for: aircraft)
                }
            } footer: {
                Text("The selected aircraft's cruise speed sets the flight duration estimate, and you are warned when a flight's ceiling is above its service ceiling. Swipe a row to make it the default or delete it.")
            }
        }
        .navigationTitle("My Aircraft")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    viewModel.prepareNew()
                    showForm = true
                } label: {
                    Label("Add Aircraft", systemImage: "plus")
                }
                .accessibilityIdentifier("addAircraftButton")
            }
        }
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
        .sheet(isPresented: $showForm) {
            AircraftFormSheet(form: viewModel.form, save: { await viewModel.save() }) {
                showForm = false
            }
        }
        .confirmationDialog(
            pendingDelete.map { AircraftListViewModel.deleteConfirmationTitle(for: $0) } ?? "",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }
            ),
            titleVisibility: .visible,
            presenting: pendingDelete
        ) { aircraft in
            Button("Delete", role: .destructive) {
                Task { await viewModel.delete(aircraft) }
            }
        }
        .alert(
            "Aircraft",
            isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    private func row(for aircraft: AircraftResponse) -> some View {
        Button {
            viewModel.prepareEdit(aircraft)
            showForm = true
        } label: {
            AircraftRow(aircraft: aircraft, isBusy: viewModel.busyIds.contains(aircraft.id))
        }
        .foregroundStyle(.primary)
        .accessibilityIdentifier("aircraftRow-\(aircraft.id)")
        // Not `role: .destructive`: that animates the row away at once, and the
        // server may refuse (last aircraft, 409) — the row would vanish and
        // come back. The row goes only once the delete has succeeded.
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                pendingDelete = aircraft
            } label: {
                Label("Delete", systemImage: "trash")
            }
            .tint(.red)

            if !aircraft.isDefault {
                Button {
                    Task { await viewModel.makeDefault(aircraft) }
                } label: {
                    Label("Make Default", systemImage: "star")
                }
                .tint(.orange)
            }
        }
        .contextMenu {
            Button {
                viewModel.prepareEdit(aircraft)
                showForm = true
            } label: {
                Label(aircraft.isPlaceholder ? "Set Up" : "Edit", systemImage: "pencil")
            }
            if !aircraft.isDefault {
                Button {
                    Task { await viewModel.makeDefault(aircraft) }
                } label: {
                    Label("Make Default", systemImage: "star")
                }
            }
            Button(role: .destructive) {
                pendingDelete = aircraft
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
        .disabled(viewModel.busyIds.contains(aircraft.id))
    }
}

/// One saved aircraft: name, type, detail line, badges, and the placeholder's
/// set-up hint (mirrors the web Settings aircraft card).
private struct AircraftRow: View {
    let aircraft: AircraftResponse
    let isBusy: Bool

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(aircraft.listTitle)
                        .font(.body.weight(.medium))
                    if aircraft.isDefault {
                        badge("Default", color: .green)
                    }
                    if aircraft.isIfr {
                        badge("IFR", color: .secondary)
                    }
                    if aircraft.isFiki {
                        badge("FIKI", color: .secondary)
                    }
                }
                if let subtitle = aircraft.listSubtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                if let detail = aircraft.listDetail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if aircraft.isPlaceholder {
                    Label("Set up your aircraft: add its type, cruise speed and service ceiling.",
                          systemImage: "exclamationmark.circle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            Spacer(minLength: 8)
            if isBusy {
                ProgressView()
            } else {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .contentShape(Rectangle())
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.15), in: Capsule())
    }
}
