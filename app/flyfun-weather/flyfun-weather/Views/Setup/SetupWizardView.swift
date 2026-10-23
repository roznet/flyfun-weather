import SwiftUI

/// First-run setup (#900), shown once after sign-in while the account's
/// `setup_completed` is false: welcome → profile → aircraft → done. A port of
/// the web welcome wizard; the copy is the web's `wizard.*` English strings.
/// Skip (any step) and Get Started both mark setup done, so it never returns.
struct SetupWizardView: View {
    @State private var model: SetupWizardModel
    let onFinished: () -> Void

    init(repository: any SetupRepository, onFinished: @escaping () -> Void) {
        _model = State(initialValue: SetupWizardModel(repository: repository))
        self.onFinished = onFinished
    }

    var body: some View {
        NavigationStack {
            Group {
                switch model.step {
                case .welcome: welcomeStep
                case .profile: profileStep
                case .aircraft: aircraftStep
                case .done: doneStep
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if model.step != .done {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Skip") { finish() }
                            .accessibilityIdentifier("setupSkipButton")
                    }
                }
            }
            .safeAreaInset(edge: .bottom) { actionBar }
        }
        .interactiveDismissDisabled()
        .task { await model.load() }
    }

    private var title: String {
        switch model.step {
        case .welcome: "Welcome"
        case .profile: "Default Profile"
        case .aircraft: "Your Aircraft & Mission"
        case .done: "All Set"
        }
    }

    // MARK: - Steps

    private var welcomeStep: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Welcome to Flyfun Weather")
                        .font(.title2.bold())
                    Text("Aviation weather assessment for cross-country GA flights")
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
            Section {
                feature("AI synopsis & advisories", "An AI-generated overview plus go/no-go factors for your route")
                feature("Route advisories", "Icing, turbulence, clouds and airport conditions rated GREEN/AMBER/RED")
                feature("Multi-model comparison", "See where GFS, ECMWF, ICON and others agree or diverge")
                feature("Interactive cross-section", "Vertical weather layers along your route with terrain profile")
                feature("Sounding analysis", "Skew-T diagrams, thermodynamic indices and stability at every waypoint")
            }
            Section {
                Text("Flyfun Weather is currently in **beta**. We appreciate your time trying the tool. Please help us improve by sending feedback when a briefing appears suspicious or you encounter any issues.")
                    .font(.callout)
            } footer: {
                Text("Let's set up your aircraft defaults. You can always change these later in Settings.")
            }
        }
    }

    private func feature(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.headline)
            Text(detail).font(.subheadline).foregroundStyle(.secondary)
        }
    }

    private var profileStep: some View {
        Form {
            Section {
                Text("Each profile stores how you fly: cruise altitude, ceiling, advisory preferences, and model selections.")
                    .font(.callout)
            } footer: {
                Text("Tap a profile to make it your default. You can switch or customize profiles later in Settings.")
            }
            if model.loadFailed {
                Section {
                    Text("Couldn't load your profiles and aircraft. Check your connection and try again.")
                        .foregroundStyle(.red)
                    Button("Retry") { Task { await model.load() } }
                        .accessibilityIdentifier("setupRetryButton")
                }
            } else if model.systemProfiles.isEmpty {
                Section {
                    Text(model.isLoading
                         ? "Loading profiles\u{2026}"
                         : "No system profiles available. You can configure your settings on the next step.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Section {
                    ForEach(model.systemProfiles) { profile in
                        profileCard(profile)
                    }
                }
            }
        }
    }

    private func profileCard(_ profile: SetupProfile) -> some View {
        let isSelected = profile.id == model.selectedProfile?.id
        return Button {
            model.selectedProfileId = profile.id
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Color.accentColor : .secondary)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 4) {
                    Text(profile.name).font(.headline).foregroundStyle(.primary)
                    let description = model.templateDescription(for: profile)
                    if !description.isEmpty {
                        Text(description).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("setupProfileCard-\(profile.systemTemplateKey ?? String(profile.id))")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var aircraftStep: some View {
        Form {
            Section {
                TextField("ICAO type, e.g. SR22, C172", text: $model.icaoType)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("setupIcaoTypeField")
                if let type = model.selectedType, !type.manufacturer.isEmpty {
                    Text(type.displayName).font(.caption).foregroundStyle(.secondary)
                }
                if model.icaoTypeMissing {
                    Text("Enter your aircraft's ICAO type (use ZZZZ if it has none).")
                        .font(.caption)
                        .foregroundStyle(.red)
                        .accessibilityIdentifier("setupIcaoTypeError")
                }
                ForEach(model.typeSuggestions) { type in
                    Button {
                        model.selectType(type)
                    } label: {
                        Text(type.displayName)
                    }
                }
                TextField("Name, e.g. Club SR22", text: $model.nickname)
                    .accessibilityIdentifier("setupNicknameField")
                TextField("Cruise speed (IAS in kt)", text: $model.cruiseSpeedKt)
                    .keyboardType(.numberPad)
                TextField("Service ceiling (ft)", text: $model.serviceCeilingFt)
                    .keyboardType(.numberPad)
                Toggle("FIKI equipped", isOn: $model.isFiki)
                    .accessibilityIdentifier("setupFikiToggle")
            } header: {
                Text("Your aircraft")
            } footer: {
                Text("What the machine is and can do. Saved as your default aircraft. Icing advisories are graded for FIKI: without it, any icing at cruise counts against the flight.")
            }

            Section {
                Picker("Flight rules", selection: $model.flightRules) {
                    Text("VFR + IFR capable").tag("vfr_ifr")
                    Text("VFR only").tag("vfr_only")
                }
                LabeledContent("Cruise altitude (ft)") {
                    TextField("8000", text: $model.cruiseAltitudeFt)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Flight ceiling (ft)") {
                    TextField("18000", text: $model.flightCeilingFt)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
                if let warning = model.ceilingWarning {
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            } header: {
                Text("Your usual mission")
            } footer: {
                Text("How you usually fly it. Saved to your profile; you can change it for each flight. You can edit these, add more aircraft or create more profiles later in Settings.")
            }

            if let error = model.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            }
        }
        // Debounced type search: `.task(id:)` cancels the previous run on
        // each keystroke, and the guard stops the superseded search.
        .task(id: model.icaoType) {
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            await model.searchTypes()
        }
    }

    private var doneStep: some View {
        Form {
            Section {
                Text(summary)
                    .accessibilityIdentifier("setupSummary")
            } header: {
                Text("Saved")
            }
            Section {
                tip("Create a flight", "Enter your waypoints, date and time. Your aircraft and usual cruise altitude and ceiling are pre-filled from what you just set up.")
                tip("Read the briefing", "Red/amber/green advisories, an AI synopsis, the cross-section and sounding analysis for each waypoint.")
                tip("Refresh for updates", "Come back and refresh as the flight date approaches. Each refresh captures a new snapshot you can compare.")
            } header: {
                Text("Getting started")
            }
        }
    }

    private func tip(_ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.headline)
            Text(detail).font(.subheadline).foregroundStyle(.secondary)
        }
    }

    /// What the server now holds: the default profile, and the aircraft as
    /// the server returned it (so the FIKI line is the stored flag).
    private var summary: String {
        var lines: [String] = []
        if let profile = model.selectedProfile {
            lines.append("Default profile: \(profile.name)")
        }
        if let aircraft = model.savedAircraft {
            lines.append("Aircraft: \(aircraft.displayName) (\(aircraft.icaoType)), \(aircraft.isFiki ? "FIKI equipped" : "not FIKI")")
        }
        return lines.joined(separator: "\n")
    }

    // MARK: - Actions

    private var actionBar: some View {
        HStack {
            if model.step != .welcome && model.step != .done {
                Button("Back") { model.back() }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("setupBackButton")
            }
            Spacer()
            if model.step == .done {
                Button("Get Started") { finish() }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("setupFinishButton")
            } else {
                Button {
                    Task { await model.next() }
                } label: {
                    if model.isSaving {
                        ProgressView()
                    } else {
                        Text("Next")
                    }
                }
                .buttonStyle(.borderedProminent)
                // The welcome step needs nothing loaded; the others wait for the profiles.
                .disabled(model.isSaving || ((model.isLoading || model.loadFailed) && model.step != .welcome))
                .accessibilityIdentifier("setupNextButton")
            }
        }
        .padding()
        .background(.bar)
    }

    private func finish() {
        Task {
            await model.complete()
            onFinished()
        }
    }
}
