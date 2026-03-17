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
                lclPressureHPa: $0.lclAltitudeFt != nil ? nil : nil, // pressure not in ThermodynamicIndices
                capeSurfaceJkg: $0.capeSurfaceJkg,
                freezingLevelFt: $0.freezingLevelFt
            )
        }

        let overlayCloudLayers = (cloudLayers ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.coverage)
        }
        let overlayIcing = (icingZones ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.risk)
        }
        let overlayInversions = (inversionLayers ?? []).map {
            InversionBand(baseFt: $0.baseFt, topFt: $0.topFt, strengthC: $0.strengthC)
        }

        return SoundingProfile(
            levels: levels,
            indices: skewTIndices,
            overlays: SkewTOverlays(
                cloudLayers: overlayCloudLayers,
                icingZones: overlayIcing,
                inversions: overlayInversions,
                cruiseAltitudeFt: cruiseAltitudeFt.map(Double.init)
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

                    // Skew-T plot
                    SkewTView(profile: response.toSoundingProfile())
                        .frame(minHeight: 300)
                        .aspectRatio(0.8, contentMode: .fit)
                }
            case .error(let error):
                ContentUnavailableView("Sounding Unavailable",
                                       systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            }
        }
        .task {
            await loadProfile()
        }
    }

    private func loadProfile() async {
        guard let pack = viewModel.pack else { return }
        profileState = .loading
        do {
            let response = try await viewModel.fetchSoundingProfile(pointIndex: pointIndex)
            profileState = .loaded(response)
        } catch {
            profileState = .error(error)
        }
    }
}
