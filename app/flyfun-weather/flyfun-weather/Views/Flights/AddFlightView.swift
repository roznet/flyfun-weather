import SwiftUI

/// Form for creating a new flight, with option to paste an ICAO flight plan.
struct AddFlightView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AddFlightViewModel
    @State private var showFplSheet = false
    @State private var showAircraftSheet = false
    @State private var showRebriefConfirmation = false

    let onSaved: (FlightResponse) -> Void

    init(
        repository: any BriefingRepository,
        editing flight: FlightResponse? = nil,
        onSaved: @escaping (FlightResponse) -> Void
    ) {
        _viewModel = State(initialValue: AddFlightViewModel(repository: repository, editing: flight))
        self.onSaved = onSaved
    }

    var body: some View {
        NavigationStack {
            Form {
                fplSection
                aircraftSection
                waypointsSection
                departureSection
                altitudeSection
                durationSection
                statusSection

                if let error = viewModel.errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle(viewModel.navigationTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(viewModel.submitTitle) {
                        submit()
                    }
                    .disabled(!viewModel.canSubmit)
                }
            }
            .alert("Regenerate briefing?", isPresented: $showRebriefConfirmation) {
                Button("Continue") {
                    Task { await submitEdit(regenerate: true) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This will regenerate the briefing. Continue?")
            }
            .task {
                await viewModel.loadAircraft()
            }
            .sheet(isPresented: $showAircraftSheet) {
                AircraftFormSheet(viewModel: viewModel) {
                    showAircraftSheet = false
                }
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

    private var aircraftSection: some View {
        Section {
            if viewModel.aircraftOptions.isEmpty && !viewModel.isLoadingAircraft {
                Label("No saved aircraft", systemImage: "airplane")
                    .foregroundStyle(.secondary)
            } else {
                Picker("Aircraft", selection: Binding(
                    get: { viewModel.selectedAircraftId ?? 0 },
                    set: { viewModel.selectedAircraftId = $0 == 0 ? nil : $0 }
                )) {
                    Text("No aircraft").tag(0)
                    ForEach(viewModel.aircraftOptions) { aircraft in
                        Text(aircraft.pickerTitle).tag(aircraft.id)
                    }
                }
                .pickerStyle(.menu)

                if let aircraft = viewModel.selectedAircraft {
                    Text(aircraft.detailText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if viewModel.isLoadingAircraft {
                HStack {
                    ProgressView()
                    Text("Loading aircraft…")
                        .foregroundStyle(.secondary)
                }
            }

            Button {
                viewModel.prepareNewAircraftForm()
                showAircraftSheet = true
            } label: {
                Label("Add Aircraft", systemImage: "plus")
            }
        } header: {
            Text("Aircraft")
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
            if let range = viewModel.departureRange {
                DatePicker("Date & Time", selection: $viewModel.departureDate, in: range)
            } else {
                DatePicker("Date & Time", selection: $viewModel.departureDate)
            }
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

    @ViewBuilder
    private var statusSection: some View {
        if viewModel.isSubmitting || viewModel.statusMessage != nil {
            Section {
                HStack(spacing: 12) {
                    if viewModel.isSubmitting {
                        ProgressView()
                    }
                    Text(viewModel.statusMessage ?? "Working…")
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func submit() {
        if viewModel.isEditing {
            if viewModel.requiresRebriefConfirmation {
                showRebriefConfirmation = true
            } else {
                Task { await submitEdit(regenerate: false) }
            }
        } else {
            Task {
                if let flight = await viewModel.createFlight() {
                    onSaved(flight)
                    dismiss()
                }
            }
        }
    }

    private func submitEdit(regenerate: Bool) async {
        if let flight = await viewModel.saveEditedFlight(regenerate: regenerate) {
            onSaved(flight)
            dismiss()
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

// MARK: - Aircraft Sheet

/// Compact aircraft create flow used by the flight form when no presets exist yet.
private struct AircraftFormSheet: View {
    @Bindable var viewModel: AddFlightViewModel
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("ICAO type, e.g. C172", text: $viewModel.newAircraftIcaoType)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()

                    if viewModel.isSearchingAircraftTypes {
                        HStack {
                            ProgressView()
                            Text("Searching aircraft types…")
                                .foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("Aircraft Type")
                }

                if !viewModel.aircraftTypeSuggestions.isEmpty {
                    Section {
                        ForEach(viewModel.aircraftTypeSuggestions) { type in
                            Button {
                                viewModel.selectAircraftType(type)
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(type.displayName)
                                    if let category = type.category, !category.isEmpty {
                                        Text(category)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    } header: {
                        Text("Matches")
                    }
                }

                Section {
                    TextField("Tail number", text: $viewModel.newAircraftTailNumber)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                    TextField("Nickname", text: $viewModel.newAircraftNickname)
                    TextField("Cruise speed (kt)", text: $viewModel.newAircraftCruiseSpeedKt)
                        .keyboardType(.numberPad)
                    TextField("Ceiling (ft)", text: $viewModel.newAircraftCeilingFt)
                        .keyboardType(.numberPad)
                } header: {
                    Text("Details")
                }

                Section {
                    Toggle("IFR equipped", isOn: $viewModel.newAircraftIsIfr)
                    Toggle("FIKI", isOn: $viewModel.newAircraftIsFiki)
                    Toggle("Make default", isOn: $viewModel.newAircraftIsDefault)
                }

                if let error = viewModel.aircraftFormError {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Add Aircraft")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { onDone() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await viewModel.createAircraft() {
                                onDone()
                            }
                        }
                    }
                    .disabled(!viewModel.canSaveAircraft)
                }
            }
            .task(id: viewModel.newAircraftIcaoType) {
                try? await Task.sleep(nanoseconds: 250_000_000)
                await viewModel.searchAircraftTypes()
            }
        }
    }
}
