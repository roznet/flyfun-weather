import CoreLocation
import SwiftUI

/// In-flight PIREP reporting card — one-tap severity buttons, no pre-selected values.
struct PirepReportingView: View {
    @Bindable var viewModel: PirepViewModel
    var trackingService: FlightTrackingService
    @Environment(\.dismiss) private var dismiss

    /// Read tracking service location in body so SwiftUI observes it via @Observable.
    /// This is the same pattern CrossSectionView uses for projectedPosition.
    private var currentLocation: CLLocation? {
        trackingService.currentLocation
    }

    private var gpsAltitudeFt: Int? {
        guard let loc = currentLocation, loc.verticalAccuracy >= 0 else { return nil }
        return Int(loc.altitude * 3.28084)
    }

    /// Seed the reported-altitude field from the current GPS fix, but only once
    /// and only while the pilot hasn't typed their own value.
    private func prefillAltitude() {
        guard viewModel.reportedAltitudeFt == nil, let gps = gpsAltitudeFt else { return }
        viewModel.reportedAltitudeFt = gps
    }

    var body: some View {
        NavigationStack {
            Form {
                altitudeSection
                icingSection
                turbulenceSection
                cloudSection
                optionalSection
                remarksSection
                submitSection
            }
            .navigationTitle("Report PIREP")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Skip") { dismiss() }
                }
            }
            .onAppear {
                // Smart pre-fill: grab a one-shot GPS fix so lat/lon/altitude
                // populate without the pilot tapping "Start" first (the entry
                // point isn't gated on an active track any more). No-op while a
                // live track is already feeding positions.
                trackingService.requestOneShotLocation()
                prefillAltitude()
            }
            // The one-shot fix arrives asynchronously — pre-fill altitude when it
            // lands. `locationUpdateCount` bumps on every fix (@Observable).
            .onChange(of: trackingService.locationUpdateCount) {
                prefillAltitude()
            }
        }
    }

    // MARK: - Altitude

    private var altitudeSection: some View {
        Section("Altitude") {
            HStack {
                Text("GPS altitude")
                Spacer()
                if let gps = gpsAltitudeFt {
                    Text("\(gps) ft")
                        .foregroundStyle(.secondary)
                } else if currentLocation != nil {
                    Text("No altitude")
                        .foregroundStyle(.tertiary)
                } else {
                    Text("No GPS")
                        .foregroundStyle(.tertiary)
                }
            }
            HStack {
                Text("Reported altitude")
                Spacer()
                TextField("ft", value: $viewModel.reportedAltitudeFt, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 80)
                Text("ft")
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Icing

    private var icingSection: some View {
        Section("Icing") {
            severityPicker(
                selection: $viewModel.icingIntensity,
                options: ["none", "trace", "light", "moderate", "severe"]
            )
            if let intensity = viewModel.icingIntensity, intensity != "none" {
                typePicker(
                    selection: $viewModel.icingType,
                    options: ["rime", "clear", "mixed"]
                )
            }
        }
    }

    // MARK: - Turbulence

    private var turbulenceSection: some View {
        Section("Turbulence") {
            severityPicker(
                selection: $viewModel.turbulenceIntensity,
                options: ["none", "light", "moderate", "severe"]
            )
        }
    }

    // MARK: - Cloud

    private var cloudSection: some View {
        Section("Cloud") {
            HStack {
                Text("In cloud?")
                Spacer()
                optionalToggle(value: $viewModel.inCloud, labels: ["Yes", "No"])
            }
        }
    }

    // MARK: - Optional fields

    private var optionalSection: some View {
        Section("Optional") {
            HStack {
                Text("Cloud tops")
                Spacer()
                TextField("ft MSL", value: $viewModel.topsMslFt, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 80)
            }
            if viewModel.topsMslFt != nil {
                Picker("Basis", selection: topsBasisBinding) {
                    Text("Select...").tag("")
                    Text("Climbed through").tag("crossed")
                    Text("Above, estimated").tag("estimated_above")
                    Text("Below, at least").tag("below_min")
                }
            }
            HStack {
                Text("Ceiling")
                Spacer()
                TextField("ft MSL", value: $viewModel.ceilingMslFt, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 80)
            }
            HStack {
                Text("Wind dir")
                TextField("°", value: $viewModel.windDir, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 50)
                Text("Speed")
                TextField("kt", value: $viewModel.windSpeedKt, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 50)
            }
            HStack {
                Text("Temperature")
                Spacer()
                TextField("°C", value: $viewModel.tempC, format: .number)
                    .keyboardType(.numbersAndPunctuation)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 60)
                Text("°C")
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Remarks

    private var remarksSection: some View {
        Section("Remarks") {
            TextField("Optional free text", text: $viewModel.remarks, axis: .vertical)
                .lineLimit(3...6)
        }
    }

    // MARK: - Submit

    private var submitSection: some View {
        Section {
            switch viewModel.submitState {
            case .idle, .error:
                Button(action: {
                    Task { await viewModel.submit(location: currentLocation) }
                }) {
                    Label("Submit Report", systemImage: "paperplane.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)

                if let error = viewModel.errorMessage {
                    Text(error)
                        .foregroundStyle(.red)
                        .font(.caption)
                }

            case .loading:
                ProgressView("Submitting...")
                    .frame(maxWidth: .infinity)

            case .loaded:
                if viewModel.queuedOffline {
                    Label("Saved offline — will sync when connected", systemImage: "arrow.clockwise.icloud")
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity)
                } else {
                    Label("Report submitted", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .frame(maxWidth: .infinity)
                }

                Button("Submit Another") {
                    viewModel.resetForm()
                    // Fetch a fresh position for the next report. The onAppear
                    // one-shot fix has already been consumed (requestLocation
                    // auto-stops after one delivery), so without this a second
                    // PIREP filed without an active track would silently carry the
                    // first report's lat/lon/altitude. No-op while tracking (the
                    // live track keeps currentLocation fresh on its own).
                    trackingService.requestOneShotLocation()
                }

                Button("Done") { dismiss() }
                    .buttonStyle(.borderedProminent)
            }
        } footer: {
            Text("Your observation will be stored permanently for weather research. If you delete your account, reports will be anonymized.")
                .font(.caption2)
        }
    }

    // MARK: - Reusable components

    private func severityPicker(selection: Binding<String?>, options: [String]) -> some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.self) { option in
                Button(option.capitalized) {
                    selection.wrappedValue = selection.wrappedValue == option ? nil : option
                }
                .buttonStyle(.bordered)
                .tint(selection.wrappedValue == option ? severityColor(option) : .gray)
                .font(.caption)
            }
        }
    }

    private func typePicker(selection: Binding<String?>, options: [String]) -> some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.self) { option in
                Button(option.capitalized) {
                    selection.wrappedValue = selection.wrappedValue == option ? nil : option
                }
                .buttonStyle(.bordered)
                .tint(selection.wrappedValue == option ? .blue : .gray)
                .font(.caption)
            }
        }
    }

    private func optionalToggle(value: Binding<Bool?>, labels: [String]) -> some View {
        HStack(spacing: 6) {
            ForEach(Array(labels.enumerated()), id: \.offset) { idx, label in
                let expected = idx == 0
                Button(label) {
                    value.wrappedValue = value.wrappedValue == expected ? nil : expected
                }
                .buttonStyle(.bordered)
                .tint(value.wrappedValue == expected ? .blue : .gray)
                .font(.caption)
            }
        }
    }

    private func severityColor(_ severity: String) -> Color {
        switch severity {
        case "none": .green
        case "trace", "light": .yellow
        case "moderate": .orange
        case "severe": .red
        default: .gray
        }
    }

    private var topsBasisBinding: Binding<String> {
        Binding(
            get: { viewModel.topsBasis ?? "" },
            set: { viewModel.topsBasis = $0.isEmpty ? nil : $0 }
        )
    }
}
