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
    /// NOTE: linear-in-altitude is an approximation — pressure falls ~log with
    /// altitude — but the error is only ~1–2 hPa over a 3000 ft inter-level gap,
    /// fine for marker placement in this Tier-0 fallback; not worth a log interp.
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
    /// Shared crosshair pressure (§4.8 Tier 2): two-way with the Skew-T and read
    /// by the side-panel so both views show one linked cursor + readout.
    @State private var selectedPressureHPa: Double?
    /// Selected side-panel variable(s) (§4.8 Tier 3): one on iPhone, up to two on iPad.
    @State private var primaryVarId: String?
    @State private var secondaryVarId: String?
    @Environment(\.horizontalSizeClass) private var hSizeClass

    // Web app pressure range (1050–250 hPa); shared by the plot and the panel so
    // their pressure rows line up (the panel requires an identical config).
    private let config = SkewTConfiguration(pTop: 250)
    private var isPad: Bool { hSizeClass == .regular }

    var body: some View {
        Group {
            switch profileState {
            case .idle, .loading:
                ProgressView("Loading sounding...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .loaded(let response):
                plotSection(response)
            case .error(let error):
                ContentUnavailableView("Sounding Unavailable",
                                       systemImage: "chart.xyaxis.line",
                                       description: Text(error.localizedDescription))
            }
        }
        // New point → drop the stale cursor before its sounding loads.
        .onChange(of: pointIndex) { selectedPressureHPa = nil }
        .task(id: pointIndex) {
            await loadProfile()
        }
    }

    @ViewBuilder
    private func plotSection(_ response: SoundingProfileResponse) -> some View {
        let profile = response.toSoundingProfile()
        let available = SkewTVariableCatalog.variables(for: response, levels: profile.levels)
        let shown = shownVariables(available)
        VStack(spacing: Theme.spacingXS) {
            header(response)
            if !available.isEmpty {
                variablePicker(available)
            }
            HStack(spacing: 0) {
                SkewTView(profile: profile, config: config, selectedPressureHPa: $selectedPressureHPa)
                if !shown.isEmpty {
                    SkewTVariablePanel(profile: profile, variables: shown, config: config,
                                       selectedPressureHPa: selectedPressureHPa)
                        .frame(width: isPad ? 220 : 96)
                }
            }
        }
        .onAppear { ensureDefaults(available) }
        .onChange(of: isPad) { ensureDefaults(available) }
    }

    private func header(_ response: SoundingProfileResponse) -> some View {
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
    }

    // MARK: Side-panel variable selection (§4.8 Tier 3)

    private func shownVariables(_ available: [SkewTVariable]) -> [SkewTVariable] {
        let ids = isPad ? [primaryVarId, secondaryVarId] : [primaryVarId]
        // compactMap also de-dups nils; allow the same id twice to collapse to one.
        var seen = Set<String>()
        return ids.compactMap { id -> SkewTVariable? in
            guard let id, seen.insert(id).inserted else { return nil }
            return available.first { $0.id == id }
        }
    }

    private func ensureDefaults(_ available: [SkewTVariable]) {
        guard !available.isEmpty else { return }
        if primaryVarId == nil || !available.contains(where: { $0.id == primaryVarId }) {
            primaryVarId = available.first?.id
        }
        if isPad, secondaryVarId == nil || !available.contains(where: { $0.id == secondaryVarId }) {
            secondaryVarId = available.dropFirst().first?.id ?? available.first?.id
        }
    }

    @ViewBuilder
    private func variablePicker(_ available: [SkewTVariable]) -> some View {
        HStack(spacing: Theme.spacingS) {
            varMenu(fallback: "Variable", selection: $primaryVarId, available: available)
            if isPad {
                varMenu(fallback: "2nd", selection: $secondaryVarId, available: available)
            }
            Spacer()
        }
        .padding(.horizontal, Theme.cardPadding)
    }

    private func varMenu(fallback: String, selection: Binding<String?>, available: [SkewTVariable]) -> some View {
        let current = available.first { $0.id == selection.wrappedValue }
        return Menu {
            ForEach(available) { v in
                Button(v.unit.isEmpty ? v.label : "\(v.label) (\(v.unit))") { selection.wrappedValue = v.id }
            }
        } label: {
            HStack(spacing: 4) {
                Circle().fill(current?.color ?? .clear).frame(width: 8, height: 8)
                Text(current?.label ?? fallback).font(.caption)
                Image(systemName: "chevron.down").font(.caption2)
            }
            .foregroundStyle(Theme.text)
        }
        .buttonStyle(.plain)
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
