import RZSkewT
import SwiftUI

/// Converts API response to RZSkewT's SoundingProfile model.
extension SoundingProfileResponse {
    /// Build the package profile. The `include*` flags gate which overlay-band
    /// categories are drawn (#310 — host-side overlay-highlight toggles), so the
    /// host can rebuild the profile with a category turned off without refetching.
    func toSoundingProfile(includeClouds: Bool = true,
                           includeIcing: Bool = true,
                           includeInversions: Bool = true) -> SoundingProfile {
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

        let overlayCloudLayers = includeClouds ? (cloudLayers ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.coverage)
        } : []
        let overlayIcing = includeIcing ? (icingZones ?? []).map {
            OverlayBand(baseFt: $0.baseFt, topFt: $0.topFt, label: $0.risk)
        } : []
        let overlayInversions = includeInversions ? (inversionLayers ?? []).map {
            InversionBand(baseFt: $0.baseFt, topFt: $0.topFt, strengthC: $0.strengthC)
        } : []

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
    /// Raw response, kept so the display profile can be rebuilt when an overlay
    /// toggle flips without refetching.
    @State private var loadedResponse: SoundingProfileResponse?
    /// Profile + offerable variables, built ONCE per sounding in `loadProfile`
    /// and cached here. `plotSection` runs in `body` on every cursor drag frame,
    /// so rebuilding these there (each ~50 structs + 9 closures + a filter pass)
    /// would jank interaction — build once, read many.
    @State private var cachedProfile: SoundingProfile?
    @State private var availableGroups: [SkewTVarGroup] = []
    private var availableVars: [SkewTVariable] { availableGroups.flatMap(\.variables) }
    /// Shared crosshair pressure (§4.8 Tier 2): two-way with the Skew-T and read
    /// by the side-panel so both views show one linked cursor + readout.
    @State private var selectedPressureHPa: Double?
    /// Selected side-panel variable(s) (§4.8 Tier 3): one on iPhone, up to two on iPad.
    @State private var primaryVarId: String?
    @State private var secondaryVarId: String?
    /// Overlay-band highlight toggles (#310): which categories are drawn on the
    /// plot. Mirrors the web's grouped overlay checkboxes.
    @State private var showClouds = true
    @State private var showIcing = true
    @State private var showInversions = true
    @Environment(\.horizontalSizeClass) private var hSizeClass
    @Environment(AppState.self) private var appState

    // Web app pressure range (1050–250 hPa); shared by the plot and the panel so
    // their pressure rows line up (the panel requires an identical config). Wind
    // barbs are off (#310 — wind is surfaced via the HW/XW side-panel variable),
    // so the right margin is trimmed to just fit the FL labels.
    private let config = SkewTConfiguration(
        pTop: 250,
        margins: .init(left: 40, right: 46, top: 20, bottom: 25),
        showWindBarbs: false
    )
    private var isPad: Bool { hSizeClass == .regular }

    /// Track (°true) at this route point, for the HW/XW side-panel variable.
    private var trackDeg: Double? {
        if case .loaded(let analyses) = viewModel.routeAnalysesState {
            return analyses.analyses.first { $0.pointIndex == pointIndex }?.trackDeg
        }
        return nil
    }

    var body: some View {
        Group {
            switch profileState {
            case .idle, .loading:
                ProgressView("Loading sounding...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .loaded(let response):
                if let profile = cachedProfile {
                    plotSection(response, profile: profile)
                } else {
                    ProgressView("Loading sounding...")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
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
    private func plotSection(_ response: SoundingProfileResponse, profile: SoundingProfile) -> some View {
        // Reads the cached profile / variables — no recompute on cursor drag.
        let shown = shownVariables(availableVars)
        VStack(spacing: Theme.spacingXS) {
            header(response)
            overlayChips(response)
            if !availableVars.isEmpty {
                variablePicker(availableVars)
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
        // Re-pick defaults only when the size class flips (iPad gains a 2nd axis).
        // First-display defaults are set in `loadProfile` before `.loaded`, so the
        // panel renders on the first frame (no no-panel→panel flash).
        .onChange(of: isPad) { ensureDefaults(availableVars) }
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

    // MARK: Overlay-band highlight toggles (#310)

    /// Toggle chips for the cloud / icing / inversion bands drawn on the plot,
    /// mirroring the web's grouped overlay checkboxes. A category's chip appears
    /// only when that sounding actually has bands of that kind.
    @ViewBuilder
    private func overlayChips(_ response: SoundingProfileResponse) -> some View {
        let hasClouds = !(response.cloudLayers ?? []).isEmpty
        let hasIcing = !(response.icingZones ?? []).isEmpty
        let hasInversions = !(response.inversionLayers ?? []).isEmpty
        if hasClouds || hasIcing || hasInversions {
            HStack(spacing: Theme.spacingS) {
                Text("Highlight").font(.caption2).foregroundStyle(Theme.textMuted)
                if hasClouds {
                    overlayChip("Cloud", color: .gray, isOn: $showClouds)
                }
                if hasIcing {
                    overlayChip("Icing", color: .cyan, isOn: $showIcing)
                }
                if hasInversions {
                    overlayChip("Inversion", color: .orange, isOn: $showInversions)
                }
                Spacer()
            }
            .padding(.horizontal, Theme.cardPadding)
        }
    }

    private func overlayChip(_ label: String, color: Color, isOn: Binding<Bool>) -> some View {
        Button {
            isOn.wrappedValue.toggle()
            rebuildProfile()
        } label: {
            HStack(spacing: 4) {
                Circle().fill(isOn.wrappedValue ? color : Color.clear)
                    .overlay(Circle().stroke(color, lineWidth: 1))
                    .frame(width: 8, height: 8)
                Text(label).font(.caption2)
            }
            .foregroundStyle(isOn.wrappedValue ? Theme.text : Theme.textMuted)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(isOn.wrappedValue ? color.opacity(0.14) : Theme.surface, in: Capsule())
            .overlay(Capsule().stroke(Theme.border, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
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
            // (i) help for the selected primary variable — content from the
            // cached help catalog (#311). Hidden when the variable has no mapped
            // metric or it isn't in the cache yet.
            if let id = primaryVarId,
               let metricId = SkewTVariableCatalog.helpMetricId[id],
               appState.helpCatalog.metric(metricId) != nil {
                HelpInfoButton(topic: .metric(metricId))
            }
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
            ForEach(availableGroups) { group in
                Section(group.label) {
                    ForEach(group.variables) { v in
                        Button(v.unit.isEmpty ? v.label : "\(v.label) (\(v.unit))") { selection.wrappedValue = v.id }
                    }
                }
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
        guard viewModel.pack != nil else { return }
        profileState = .loading
        cachedProfile = nil
        loadedResponse = nil
        availableGroups = []
        do {
            let response = try await viewModel.fetchSoundingProfile(pointIndex: pointIndex)
            // Build the heavy profile + variable catalog ONCE here, and pick the
            // default variables, all before publishing `.loaded` so the first
            // render already has them (perf + no first-frame flash).
            let profile = response.toSoundingProfile(includeClouds: showClouds, includeIcing: showIcing,
                                                     includeInversions: showInversions)
            loadedResponse = response
            cachedProfile = profile
            availableGroups = SkewTVariableCatalog.grouped(for: response, levels: profile.levels, trackDeg: trackDeg)
            ensureDefaults(availableVars)
            profileState = .loaded(response)
        } catch {
            profileState = .error(error)
        }
    }

    /// Rebuild just the display profile when an overlay toggle flips (no refetch).
    /// Cheap (array maps); the expensive parcel-path/background work lives in the
    /// renderer, which only re-runs because the profile *value* changed here — and
    /// toggles are rare, not per-frame.
    private func rebuildProfile() {
        guard let response = loadedResponse else { return }
        cachedProfile = response.toSoundingProfile(includeClouds: showClouds, includeIcing: showIcing,
                                                   includeInversions: showInversions)
    }
}
