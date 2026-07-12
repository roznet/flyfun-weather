import SwiftUI

/// Read-only list of PIREPs for a flight, shown as a briefing tab.
struct PirepListView: View {
    let pirepsState: LoadingState<[PirepResponse]>
    var retryAction: () async -> Void = {}

    var body: some View {
        LoadingStateView(state: pirepsState, retryAction: retryAction) { pireps in
            if pireps.isEmpty {
                ContentUnavailableView(
                    "No PIREPs",
                    systemImage: "cloud.sun",
                    description: Text("No pilot reports for this flight yet.")
                )
            } else {
                List(pireps) { pirep in
                    PirepRowView(pirep: pirep)
                }
                .listStyle(.plain)
            }
        }
    }
}

/// Single PIREP row with expandable detail.
struct PirepRowView: View {
    let pirep: PirepResponse
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Summary
            HStack {
                Text(ageLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                hazardIcons

                Spacer()

                if let alt = pirep.altitude {
                    Text("\(alt) ft")
                        .font(.caption.monospacedDigit())
                }

                if pirep.isOwn {
                    Text("own")
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.blue)
                        .foregroundStyle(.white)
                        .clipShape(Capsule())
                }
            }

            // Detail (expanded)
            if expanded {
                detailView
                    .transition(.opacity)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture { withAnimation { expanded.toggle() } }
        .listRowBackground(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.clear)
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(severityColor)
                        .frame(width: 4)
                }
        )
    }

    private var hazardIcons: some View {
        HStack(spacing: 4) {
            if let ic = pirep.icingIntensity, ic != "none" {
                Image(systemName: "snowflake")
                    .foregroundStyle(.cyan)
            }
            if let tb = pirep.turbulenceIntensity, tb != "none" {
                Image(systemName: "wind")
                    .foregroundStyle(.orange)
            }
            if pirep.inCloud == true || pirep.ceilingMslFt != nil {
                Image(systemName: "cloud.fill")
                    .foregroundStyle(.gray)
            }
            if isAllClear {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            }
        }
        .font(.caption)
    }

    private var detailView: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let ic = pirep.icingIntensity {
                let suffix = pirep.icingType.map { " (\($0))" } ?? ""
                detailRow("Icing", ic.capitalized + suffix)
            }
            if let tb = pirep.turbulenceIntensity {
                detailRow("Turbulence", tb.capitalized)
            }
            if let inCloud = pirep.inCloud {
                detailRow("In cloud", inCloud ? "Yes" : "No")
            }
            if let ceil = pirep.ceilingMslFt {
                detailRow("Ceiling", "\(ceil) ft MSL")
            }
            if let tops = pirep.topsMslFt {
                let basis = pirep.topsBasis.map { " (\($0.replacingOccurrences(of: "_", with: " ")))" } ?? ""
                detailRow("Tops", "\(tops) ft MSL" + basis)
            }
            if let temp = pirep.tempC {
                detailRow("Temp", String(format: "%.0f°C", temp))
            }
            if let dir = pirep.windDir, let spd = pirep.windSpeedKt {
                detailRow("Wind", "\(dir.paddedHeading)° / \(spd) kt")
            }
            if let ac = pirep.aircraftType {
                detailRow("Aircraft", ac)
            }
            if let rem = pirep.remarks, !rem.isEmpty {
                detailRow("Remarks", rem)
            }
        }
        .font(.caption)
        .padding(.top, 4)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 80, alignment: .leading)
            Text(value)
        }
    }

    private var severityColor: Color {
        switch pirep.maxSeverity {
        case "none": .green
        case "trace", "light": .yellow
        case "moderate": .orange
        case "severe": .red
        default: .gray
        }
    }

    private var isAllClear: Bool {
        let ic = pirep.icingIntensity ?? "none"
        let tb = pirep.turbulenceIntensity ?? "none"
        return ic == "none" && tb == "none" && pirep.inCloud != true
    }

    private var ageLabel: String {
        guard let date = pirep.observedDate else { return pirep.observedAt }
        let mins = Int(-date.timeIntervalSinceNow / 60)
        if mins < 1 { return "just now" }
        if mins < 60 { return "\(mins)m ago" }
        let hrs = mins / 60
        return "\(hrs)h ago"
    }
}
