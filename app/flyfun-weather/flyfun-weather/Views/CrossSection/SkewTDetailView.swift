import Charts
import RZSkewT
import SwiftUI

/// Converts API response to RZSkewT's SoundingProfile model.
extension SoundingProfileResponse {
    func toSoundingProfile() -> SoundingProfile {
        let levels = self.levels.map {
            SoundingLevel(
                pressureHPa: Double($0.pressureHpa),
                altitudeFt: $0.altitudeFt,
                temperatureC: $0.temperatureC,
                dewpointC: $0.dewpointC,
                windSpeedKt: $0.windSpeedKt,
                windDirectionDeg: $0.windDirectionDeg
            )
        }

        let skewTIndices = indices.map {
            SkewTIndices(
                lclPressureHPa: $0.lclPressureHpa,
                lfcPressureHPa: $0.lfcPressureHpa,
                elPressureHPa: $0.elPressureHpa,
                capeSurfaceJkg: $0.capeSurfaceJkg,
                cinSurfaceJkg: $0.cinSurfaceJkg,
                freezingLevelFt: $0.freezingLevelFt,
                liftedIndex: $0.liftedIndex
            )
        }

        // Match the cross-section default: NWP cloud envelope and Ogimet-NWP icing
        // when available, with DD/Ogimet as fallback for older packs.
        let overlayCloudLayers = (nwpCloudLayers ?? cloudLayers ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.coverage)
        }
        let overlayIcing = (icingOgimetNwpZones ?? icingZones ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.risk)
        }
        let overlayInversions = (inversionLayers ?? []).map {
            InversionBand(baseFt: $0.baseFt, topFt: $0.topFt, strengthC: $0.strengthC)
        }
        let convectiveLfcFt = indices?.lfcAltitudeFt ?? convective?.baseFt
        let convectiveElFt = indices?.elAltitudeFt ?? convective?.topFt

        return SoundingProfile(
            levels: levels,
            indices: skewTIndices,
            overlays: SkewTOverlays(
                cloudLayers: overlayCloudLayers,
                icingZones: overlayIcing,
                inversions: overlayInversions,
                cruiseAltitudeFt: cruiseAltitudeFt.map(Double.init),
                convectiveLfcFt: convectiveLfcFt,
                convectiveElFt: convectiveElFt
            )
        )
    }
}

/// Shows a Skew-T plot for a selected route point, loaded from the API.
struct SkewTDetailView: View {
    let viewModel: BriefingViewModel
    let pointIndex: Int

    @State private var profileState: LoadingState<SoundingProfileResponse> = .idle

    var body: some View {
        Group {
            switch profileState {
            case .idle, .loading:
                ProgressView("Loading sounding...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .loaded(let response):
                VStack(spacing: 0) {
                    // Header
                    HStack {
                        if let icao = response.waypointIcao {
                            Text(icao).font(.headline)
                        }
                        Text("\(Int(response.distanceFromOriginNm)) nm")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Text(response.model.uppercased())
                            .font(.caption.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.blue.opacity(0.15), in: Capsule())
                        Spacer()
                        Text("\(response.levels.count) levels")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 4)

                    // Skew-T plot — match web app's pressure range (1050–250 hPa)
                    // Landscape aspect ~9:5 to match metpy figsize
                    SkewTView(
                        profile: response.toSoundingProfile(),
                        config: SkewTConfiguration(pTop: 250)
                    )
                    .aspectRatio(9.0 / 5.0, contentMode: .fit)

                    Divider()
                    SkewTVariableProfileView(response: response)
                        .frame(minHeight: 160)
                }
            case .error(let error):
                ContentUnavailableView("Sounding Unavailable",
                                       systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            }
        }
        .task(id: "\(pointIndex)-\(viewModel.selectedModel)") {
            await loadProfile()
        }
    }

    private func loadProfile() async {
        guard viewModel.pack != nil else { return }
        profileState = .loading
        do {
            let response = try await viewModel.fetchSoundingProfile(pointIndex: pointIndex)
            profileState = .loaded(response)
        } catch {
            profileState = .error(error)
        }
    }
}

private struct SkewTVariableProfileView: View {
    let response: SoundingProfileResponse
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var primaryMetricId = "relative-humidity"
    @State private var secondaryMetricId = "theta-e"

    private var primaryMetric: SkewTVariableMetric {
        SkewTVariableMetrics.metric(byId: primaryMetricId) ?? SkewTVariableMetrics.all[0]
    }

    private var secondaryMetric: SkewTVariableMetric {
        SkewTVariableMetrics.metric(byId: secondaryMetricId) ?? SkewTVariableMetrics.all[1]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: WeatherTheme.Spacing.sm) {
            HStack {
                metricMenu(title: "Variable", selection: $primaryMetricId)
                Spacer()
                if horizontalSizeClass == .regular {
                    metricMenu(title: "Compare", selection: $secondaryMetricId)
                }
            }

            if horizontalSizeClass == .regular {
                HStack(spacing: WeatherTheme.Spacing.lg) {
                    variableChart(metric: primaryMetric)
                    variableChart(metric: secondaryMetric)
                }
            } else {
                variableChart(metric: primaryMetric)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, WeatherTheme.Spacing.sm)
    }

    private func metricMenu(title: String, selection: Binding<String>) -> some View {
        Menu {
            ForEach(SkewTVariableMetrics.all) { metric in
                Button {
                    selection.wrappedValue = metric.id
                } label: {
                    if selection.wrappedValue == metric.id {
                        Label(metric.label, systemImage: "checkmark")
                    } else {
                        Text(metric.label)
                    }
                }
            }
        } label: {
            Label(SkewTVariableMetrics.metric(byId: selection.wrappedValue)?.label ?? title,
                  systemImage: "waveform.path.ecg")
                .font(.caption.bold())
        }
        .buttonStyle(.bordered)
    }

    private func variableChart(metric: SkewTVariableMetric) -> some View {
        let data = chartData(for: metric)
        return VStack(alignment: .leading, spacing: WeatherTheme.Spacing.xs) {
            Text("\(metric.label) \(metric.unit)")
                .font(.caption.bold())
                .foregroundStyle(metric.color)

            if data.isEmpty {
                ContentUnavailableView("No \(metric.label)", systemImage: "chart.line.uptrend.xyaxis")
                    .frame(height: 120)
            } else {
                Chart(data) { point in
                    LineMark(
                        x: .value(metric.label, point.value),
                        y: .value("Altitude", point.altitudeFt)
                    )
                    .foregroundStyle(metric.color)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                }
                .chartYAxisLabel("ft")
                .chartXAxisLabel(metric.unit)
                .frame(height: 120)
            }
        }
    }

    private func chartData(for metric: SkewTVariableMetric) -> [SkewTVariablePoint] {
        response.levels.compactMap { level in
            guard let altitudeFt = level.altitudeFt,
                  let value = metric.value(level)
            else { return nil }
            return SkewTVariablePoint(pressureHpa: Double(level.pressureHpa), altitudeFt: altitudeFt, value: value)
        }
        .sorted { $0.altitudeFt < $1.altitudeFt }
    }
}

private struct SkewTVariablePoint: Identifiable {
    let pressureHpa: Double
    let altitudeFt: Double
    let value: Double

    var id: String { "\(Int(pressureHpa))-\(Int(altitudeFt))" }
}

private struct SkewTVariableMetric: Identifiable {
    let id: String
    let label: String
    let unit: String
    let color: Color
    let value: (SoundingProfileLevel) -> Double?
}

private enum SkewTVariableMetrics {
    static let all: [SkewTVariableMetric] = [
        .init(id: "relative-humidity", label: "RH", unit: "%", color: .blue) { $0.relativeHumidityPct },
        .init(id: "theta-e", label: "Theta-e", unit: "K", color: .orange) { $0.thetaEK },
        .init(id: "lapse-rate", label: "Lapse", unit: "C/km", color: .purple) { $0.lapseRateCPerKm },
        .init(id: "icing-index", label: "Icing", unit: "", color: .cyan) { $0.icingIndexNwp ?? $0.icingIndex },
        .init(id: "cloud-fraction", label: "Cloud", unit: "%", color: .gray) { $0.cloudAreaFractionPct },
        .init(id: "richardson", label: "Ri", unit: "", color: .green) { $0.richardsonNumber },
        .init(id: "vertical-motion", label: "w", unit: "fpm", color: .red) { $0.wFpm },
    ]

    static func metric(byId id: String) -> SkewTVariableMetric? {
        all.first { $0.id == id }
    }
}
