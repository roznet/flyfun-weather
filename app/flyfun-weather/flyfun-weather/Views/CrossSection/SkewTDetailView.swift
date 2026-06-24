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

        let skewTIndices = indices.map { idx in
            SkewTIndices(
                // Prefer the server's pressure; else convert the altitude using
                // the sounding's own altitude↔pressure relationship (§4.8 Tier 0).
                lclPressureHPa: idx.lclPressureHpa ?? Self.pressure(atAltitudeFt: idx.lclAltitudeFt, levels: self.levels),
                lfcPressureHPa: idx.lfcPressureHpa ?? Self.pressure(atAltitudeFt: idx.lfcAltitudeFt, levels: self.levels),
                elPressureHPa: idx.elPressureHpa ?? Self.pressure(atAltitudeFt: idx.elAltitudeFt, levels: self.levels),
                capeSurfaceJkg: idx.capeSurfaceJkg,
                cinSurfaceJkg: idx.cinSurfaceJkg,
                freezingLevelFt: idx.freezingLevelFt,
                liftedIndex: idx.liftedIndex
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

    /// Interpolate the pressure (hPa) at a given altitude from the sounding's
    /// own levels (linear in altitude). Used only when the server didn't send a
    /// pressure for a parcel level. Returns nil if the altitude or levels are
    /// missing.
    static func pressure(atAltitudeFt altitudeFt: Double?, levels: [SoundingProfileLevel]) -> Double? {
        guard let alt = altitudeFt else { return nil }
        let pts = levels
            .compactMap { lvl -> (alt: Double, p: Double)? in
                guard let a = lvl.altitudeFt else { return nil }
                return (a, Double(lvl.pressureHpa))
            }
            .sorted { $0.alt < $1.alt }
        guard let first = pts.first, let last = pts.last else { return nil }
        if alt <= first.alt { return first.p }
        if alt >= last.alt { return last.p }
        for i in 0..<(pts.count - 1) where alt >= pts[i].alt && alt <= pts[i + 1].alt {
            let span = pts[i + 1].alt - pts[i].alt
            guard span > 0 else { return pts[i].p }
            let t = (alt - pts[i].alt) / span
            return pts[i].p + (pts[i + 1].p - pts[i].p) * t
        }
        return nil
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
                }
            case .error(let error):
                ContentUnavailableView("Sounding Unavailable",
                                       systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            }
        }
        .task(id: pointIndex) {
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
