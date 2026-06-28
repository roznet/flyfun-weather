import SwiftUI

/// Form for creating or editing a flight, with options to paste an ICAO flight
/// plan and pick (or create) an aircraft.
struct AddFlightView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AddFlightViewModel
    @State private var autocomplete = RouteAutocompleteController()
    @FocusState private var routeFieldFocused: Bool
    @State private var showFplSheet = false
    @State private var showAircraftSheet = false
    @State private var showRebriefConfirm = false
    @State private var showInterpretSheet = false
    @State private var showAutorouterSheet = false
    /// True when the interpret sheet is shown as a pre-create confirmation
    /// (because the route dropped tokens), so "Accept" proceeds to create.
    @State private var confirmingInterpretBeforeCreate = false

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
                importSection
                profileSection
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
                    Button(viewModel.submitTitle) { submit() }
                        .disabled(!viewModel.canSubmit)
                        .accessibilityIdentifier("submitFlightButton")
                }
            }
            .confirmationDialog(
                "This change requires a new briefing",
                isPresented: $showRebriefConfirm,
                titleVisibility: .visible
            ) {
                Button("Save & Regenerate") {
                    Task { await submitEdit(regenerate: true) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Route, time, altitude or duration changes affect the forecast, so saving regenerates the briefing. Cancel to leave the flight and its briefing unchanged.")
            }
            .task {
                await viewModel.loadAircraft()
            }
            .task {
                await viewModel.loadProfiles()
            }
            .task {
                await viewModel.loadRecentRoutes()
            }
            .sheet(isPresented: $showAircraftSheet) {
                AircraftFormSheet(viewModel: viewModel) {
                    showAircraftSheet = false
                }
            }
            .sheet(isPresented: $showAutorouterSheet) {
                AutorouterPickerSheet(viewModel: viewModel) { route in
                    showAutorouterSheet = false
                    Task { await viewModel.importAutorouterRoute(route) }
                } onCancel: {
                    showAutorouterSheet = false
                }
            }
            .sheet(isPresented: $showInterpretSheet) {
                RouteInterpretSheet(
                    interpretation: viewModel.routeInterpretation,
                    rawRoute: viewModel.waypointsText,
                    isResolving: viewModel.isInterpreting,
                    acceptTitle: confirmingInterpretBeforeCreate ? "Accept & Create" : nil,
                    onAccept: {
                        showInterpretSheet = false
                        Task { await performCreate() }
                    },
                    onResolve: { await viewModel.resolveRoute() }
                )
            }
        }
    }

    // MARK: - Submit

    private func submit() {
        if viewModel.isEditing {
            // Only a forecast-affecting change triggers the re-briefing cost
            // confirm (§4.4); aircraft-only edits save silently.
            if viewModel.hasForecastAffectingChange {
                showRebriefConfirm = true
            } else {
                Task { await submitEdit(regenerate: false) }
            }
        } else if viewModel.routeHasDroppedTokens {
            // The route dropped tokens — show the interpretation so the pilot can
            // confirm what we understood before creating (mirrors the web).
            confirmingInterpretBeforeCreate = true
            showInterpretSheet = true
        } else {
            Task { await performCreate() }
        }
    }

    private func performCreate() async {
        if let flight = await viewModel.createFlight() {
            onCreated(flight)
            dismiss()
        }
    }

    private func submitEdit(regenerate: Bool) async {
        if let flight = await viewModel.saveEditedFlight(regenerate: regenerate) {
            onCreated(flight)
            dismiss()
        }
    }

    // MARK: - Sections

    private var importSection: some View {
        Section {
            if !viewModel.recentRoutes.isEmpty {
                Menu {
                    ForEach(Array(viewModel.recentRoutes.enumerated()), id: \.offset) { _, route in
                        Button(route.joined(separator: " ")) {
                            viewModel.applyRecentRoute(route)
                        }
                    }
                } label: {
                    Label("Recent routes", systemImage: "clock.arrow.circlepath")
                }
            }

            Button {
                showFplSheet = true
            } label: {
                Label("Paste Flight Plan", systemImage: "doc.on.clipboard")
            }

            Button {
                showAutorouterSheet = true
                Task { await viewModel.loadAutorouterRoutes() }
            } label: {
                Label("Import from Autorouter", systemImage: "arrow.down.doc")
            }
        } header: {
            Text("Import")
        } footer: {
            Text("Start from a recent route, an ICAO flight plan, or your Autorouter history.")
        }
        .sheet(isPresented: $showFplSheet) {
            FplPasteSheet(viewModel: viewModel) {
                showFplSheet = false
            }
        }
    }

    @ViewBuilder
    private var profileSection: some View {
        if !viewModel.profileOptions.isEmpty {
            Section {
                Picker("Profile", selection: Binding<Int?>(
                    get: { viewModel.selectedProfileId },
                    set: { viewModel.applyProfile($0) }
                )) {
                    ForEach(viewModel.profileOptions) { profile in
                        Text(profile.name).tag(Optional(profile.id))
                    }
                }
                .pickerStyle(.menu)
            } header: {
                Text("Profile")
            } footer: {
                Text("Presets cruise altitude and the weather models used for the briefing.")
            }
        } else if viewModel.isLoadingProfiles {
            Section {
                HStack {
                    ProgressView()
                    Text("Loading profiles\u{2026}").foregroundStyle(.secondary)
                }
            }
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
                    Text("Loading aircraft\u{2026}")
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
                .focused($routeFieldFocused)
                .accessibilityIdentifier("waypointsField")
                // Debounced autocomplete: the keystroke only cancels the prior
                // pending search, so typing is never blocked. After a short pause
                // we look up completions for the *last* token.
                .task(id: viewModel.waypointsText) {
                    try? await Task.sleep(for: .milliseconds(140))
                    guard !Task.isCancelled else { return }
                    autocomplete.update(for: viewModel.waypointsText)
                }

            if routeFieldFocused, !autocomplete.suggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(autocomplete.suggestions) { suggestion in
                            Button {
                                viewModel.completeLastToken(with: suggestion.icao)
                                autocomplete.clear()
                            } label: {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(suggestion.icao)
                                        .font(.caption.bold().monospaced())
                                    Text(suggestion.name)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color.accentColor.opacity(0.12), in: Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 2)
                }
                .listRowInsets(EdgeInsets(top: 4, leading: 12, bottom: 4, trailing: 12))
            }

            if !viewModel.waypoints.isEmpty {
                Text(viewModel.waypoints.joined(separator: " \u{2192} "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if viewModel.waypoints.count >= 2 {
                Button {
                    confirmingInterpretBeforeCreate = false
                    showInterpretSheet = true
                } label: {
                    HStack {
                        Label("Interpret route", systemImage: "map")
                        if viewModel.isInterpreting {
                            Spacer()
                            ProgressView()
                        } else if viewModel.routeHasDroppedTokens {
                            Spacer()
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                                .font(.caption)
                        }
                    }
                }
            }
        } header: {
            Text("Route")
        } footer: {
            Text("Enter waypoints separated by spaces (ICAO codes, navaids, or fixes). Tap Interpret to see what was understood and view it on the map.")
        }
        // Resolve the route (interpretation + timezones) a bit after typing
        // settles. Longer debounce than autocomplete since it's a network call.
        .task(id: viewModel.waypointsText) {
            try? await Task.sleep(for: .milliseconds(500))
            guard !Task.isCancelled else { return }
            await viewModel.resolveRoute()
        }
    }

    private var departureSection: some View {
        Section {
            DatePicker(
                "Date",
                selection: Binding(
                    get: { viewModel.departureTime.dateProxy },
                    set: { viewModel.departureTime.dateProxy = $0 }
                ),
                displayedComponents: .date
            )

            HStack {
                Text("Time")
                Spacer()
                Picker("Hour", selection: Binding(
                    get: { viewModel.departureTime.hour },
                    set: { viewModel.departureTime.setHour($0) }
                )) {
                    ForEach(0..<24, id: \.self) { h in
                        Text(String(format: "%02d", h)).tag(h)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                Text(":").foregroundStyle(.secondary)
                Picker("Minute", selection: Binding(
                    get: { viewModel.departureTime.minuteOption },
                    set: { viewModel.departureTime.setMinute($0) }
                )) {
                    ForEach(DepartureTimeModel.minuteOptions, id: \.self) { m in
                        Text(String(format: "%02d", m)).tag(m)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }

            Picker("Timezone", selection: Binding(
                get: { viewModel.departureTime.timeZoneId },
                set: { viewModel.departureTime.timeZoneId = $0 }
            )) {
                ForEach(viewModel.departureTime.options) { option in
                    Text(option.label).tag(option.identifier)
                }
            }
            .pickerStyle(.menu)
        } header: {
            Text("Departure")
        } footer: {
            Text("The time is interpreted in the selected timezone. Enter a route to offer each airport's local time.")
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
                    Text(viewModel.statusMessage ?? "Working\u{2026}")
                        .foregroundStyle(.secondary)
                }
            }
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

/// Inline aircraft create flow with search-as-you-type ICAO-type suggestions.
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
                            Text("Searching aircraft types\u{2026}")
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
            // Debounce the type search. `.task(id:)` cancels the previous run on
            // each keystroke; the guard after the sleep makes that cancellation
            // actually stop the superseded search (the `try?` swallows the
            // CancellationError, so without this guard every keystroke would fire
            // a network request).
            .task(id: viewModel.newAircraftIcaoType) {
                try? await Task.sleep(nanoseconds: 250_000_000)
                guard !Task.isCancelled else { return }
                await viewModel.searchAircraftTypes()
            }
        }
    }
}

// MARK: - Autorouter Picker Sheet

/// Lists the user's recent Autorouter routes; selecting one imports its flight
/// plan. Shows a helpful message when the account isn't linked or has no routes.
private struct AutorouterPickerSheet: View {
    @Bindable var viewModel: AddFlightViewModel
    let onSelect: (AutorouterRoute) -> Void
    let onCancel: () -> Void

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoadingAutorouter {
                    ProgressView("Loading routes\u{2026}")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = viewModel.autorouterError {
                    ContentUnavailableView {
                        Label("Autorouter", systemImage: "arrow.down.doc")
                    } description: {
                        Text(error)
                    }
                } else {
                    List(viewModel.autorouterRoutes) { route in
                        Button {
                            onSelect(route)
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(route.title)
                                    .font(.headline)
                                if !route.subtitle.isEmpty {
                                    Text(route.subtitle)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Import from Autorouter")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { onCancel() }
                }
            }
        }
    }
}
