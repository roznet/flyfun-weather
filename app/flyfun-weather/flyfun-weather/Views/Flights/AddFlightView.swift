import SwiftUI

/// Form for creating a new flight, with option to paste an ICAO flight plan.
struct AddFlightView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AddFlightViewModel
    @State private var showFplSheet = false
    @State private var showRebriefConfirm = false

    /// Called with the created OR updated flight.
    let onCreated: (FlightResponse) -> Void

    init(repository: any BriefingRepository, flight: FlightResponse? = nil,
         onCreated: @escaping (FlightResponse) -> Void) {
        _viewModel = State(initialValue: AddFlightViewModel(repository: repository, flight: flight))
        self.onCreated = onCreated
    }

    var body: some View {
        NavigationStack {
            Form {
                fplSection
                waypointsSection
                departureSection
                altitudeSection
                durationSection

                if let error = viewModel.errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle(viewModel.isEditing ? "Edit Flight" : "New Flight")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(viewModel.isEditing ? "Save" : "Create") {
                        // Saving a forecast-affecting change regenerates the
                        // briefing — never silently (§4.4).
                        if viewModel.isEditing && viewModel.hasForecastAffectingChange {
                            showRebriefConfirm = true
                        } else {
                            performSave()
                        }
                    }
                    .disabled(!viewModel.canSubmit)
                }
            }
            .confirmationDialog(
                "Regenerate briefing?",
                isPresented: $showRebriefConfirm,
                titleVisibility: .visible
            ) {
                Button("Save & Regenerate") { performSave() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This change affects the forecast and will regenerate the briefing.")
            }
        }
    }

    private func performSave() {
        Task {
            if let flight = await viewModel.save() {
                onCreated(flight)
                dismiss()
            }
        }
    }

    // MARK: - Sections

    private var fplSection: some View {
        Section {
            Button {
                showFplSheet = true
            } label: {
                Label("Paste Flight Plan", systemImage: "doc.on.clipboard")
            }
            .sheet(isPresented: $showFplSheet) {
                FplPasteSheet(viewModel: viewModel) {
                    showFplSheet = false
                }
            }
        } footer: {
            Text("Paste an ICAO flight plan to auto-fill all fields.")
        }
    }

    private var waypointsSection: some View {
        Section {
            TextField("LFBO TOU LFMT", text: $viewModel.waypointsText)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()

            if !viewModel.waypoints.isEmpty {
                Text(viewModel.waypoints.joined(separator: " \u{2192} "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Route")
        } footer: {
            Text("Enter waypoints separated by spaces (ICAO codes, navaids, or fixes).")
        }
    }

    private var departureSection: some View {
        Section {
            DatePicker("Date & Time", selection: $viewModel.departureDate)
        } header: {
            Text("Departure")
        }
    }

    private var altitudeSection: some View {
        Section {
            HStack {
                Text("FL\(viewModel.cruiseAltitudeFt / 100)")
                    .monospacedDigit()
                    .frame(width: 60)
                Slider(
                    value: Binding(
                        get: { Double(viewModel.cruiseAltitudeFt) },
                        set: { viewModel.cruiseAltitudeFt = Int($0) }
                    ),
                    in: 1000...45000,
                    step: 500
                )
            }
            Text("\(viewModel.cruiseAltitudeFt) ft")
                .font(.caption)
                .foregroundStyle(.secondary)
        } header: {
            Text("Cruise Altitude")
        }
    }

    private var durationSection: some View {
        Section {
            Stepper(
                String(format: "%.1f hours", viewModel.flightDurationHours),
                value: $viewModel.flightDurationHours,
                in: 0.5...12.0,
                step: 0.5
            )
        } header: {
            Text("Duration")
        }
    }
}

// MARK: - FPL Paste Sheet

/// Sheet for pasting and parsing an ICAO flight plan string.
private struct FplPasteSheet: View {
    @Bindable var viewModel: AddFlightViewModel
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextEditor(text: $viewModel.fplText)
                        .frame(minHeight: 120)
                        .font(.system(.body, design: .monospaced))
                } header: {
                    Text("ICAO Flight Plan")
                } footer: {
                    Text("Paste the full flight plan text including (FPL-...) block.")
                }

                Section {
                    Button {
                        Task {
                            await viewModel.parseFpl()
                            if viewModel.parseError == nil {
                                onDone()
                            }
                        }
                    } label: {
                        HStack {
                            Text("Parse & Fill")
                            if viewModel.isParsing {
                                Spacer()
                                ProgressView()
                            }
                        }
                    }
                    .disabled(viewModel.fplText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isParsing)
                }

                if let error = viewModel.parseError {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Paste Flight Plan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { onDone() }
                }
            }
        }
    }
}
