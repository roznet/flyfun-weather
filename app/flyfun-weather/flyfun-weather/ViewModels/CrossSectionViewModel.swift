import Foundation

/// Extracts VizRouteData from API responses for a selected model.
/// Port of web's data-extract.ts extractVizData().
@Observable
@MainActor
final class CrossSectionViewModel {
    private(set) var vizData: VizRouteData?
    private(set) var enabledLayers: [String: Bool] = CrossSectionPresets.gramet
    /// Currently-applied advisory lens id (e.g. "icing"), or nil when none/Custom.
    private(set) var activeAdvisoryPreset: String?
    /// Monotonic identity for `vizData`: bumped only when the data is rebuilt
    /// (model/route/elevation change). `VizRouteData` is a deep value type with no
    /// `Equatable` conformance, so the static cross-section scene keys its
    /// `Equatable` redraw gate on this counter instead of diffing the whole struct
    /// each scrub tick (#303).
    private(set) var dataVersion: Int = 0
    /// Active cross-section colour theme (#320). Defaults to GRAMET to match the
    /// booted GRAMET layer preset above, so the chart and the preset agree on
    /// boot. Theme is orthogonal to the layer preset (changing one doesn't reset
    /// the other) — mirrors the web, where `setVizTheme` leaves the preset alone.
    /// Persisted across launches via `UserDefaults`.
    private(set) var themeId: CrossSectionThemeID

    /// `UserDefaults` key for the persisted theme choice.
    private static let themeDefaultsKey = "crossSectionThemeId"
    /// `UserDefaults` key for the persisted layer enablement map.
    private static let layersDefaultsKey = "crossSectionEnabledLayers"
    /// `UserDefaults` key for the persisted advisory-lens id (absent when none).
    private static let advisoryPresetDefaultsKey = "crossSectionAdvisoryPreset"

    init() {
        // Restore the last-chosen theme; fall back to GRAMET (the boot preset's
        // theme) when nothing is stored or the stored value is unknown.
        let stored = UserDefaults.standard.string(forKey: Self.themeDefaultsKey)
        themeId = stored.flatMap(CrossSectionThemeID.init(rawValue:)) ?? .gramet
        // Sync the module-level active theme so the very first frame (and the
        // config sheet's legend swatches) render in the right palette even before
        // the renderer runs.
        CrossSectionTheme.setActive(themeId)

        // Restore the last layer config so a relaunch keeps the user's layers
        // (not just colours) — mirrors the web, which persists the whole viz
        // config (#9, iOS testing feedback). Keep only ids the current build
        // still knows about (a renamed/removed layer can't resurrect a stale id),
        // and merge restored values over the GRAMET defaults so a newly-added
        // layer gets its default state rather than vanishing.
        if let data = UserDefaults.standard.data(forKey: Self.layersDefaultsKey),
           let decoded = try? JSONDecoder().decode([String: Bool].self, from: data) {
            var merged = CrossSectionPresets.gramet
            for (id, on) in decoded where merged[id] != nil {
                merged[id] = on
            }
            enabledLayers = merged
        }
        activeAdvisoryPreset = UserDefaults.standard.string(forKey: Self.advisoryPresetDefaultsKey)
    }

    /// Switch the colour theme. Independent of the layer preset. Persisted.
    func setTheme(_ id: CrossSectionThemeID) {
        themeId = id
        CrossSectionTheme.setActive(id)
        UserDefaults.standard.set(id.rawValue, forKey: Self.themeDefaultsKey)
    }

    /// Persist the current layer set + active advisory lens. Called after every
    /// mutation so the config survives relaunch (#9, iOS testing feedback).
    private func persistLayerConfig() {
        if let data = try? JSONEncoder().encode(enabledLayers) {
            UserDefaults.standard.set(data, forKey: Self.layersDefaultsKey)
        }
        if let id = activeAdvisoryPreset {
            UserDefaults.standard.set(id, forKey: Self.advisoryPresetDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.advisoryPresetDefaultsKey)
        }
    }

    func update(routeAnalyses: RouteAnalysesResponse, elevation: ElevationResponse?, model: String) {
        vizData = Self.extractVizData(from: routeAnalyses, model: model, elevation: elevation)
        dataVersion += 1
    }

    // MARK: - NWP availability & fallback (port of web getUnavailableLayers +
    // applyNwpFallback). See `NwpFallback`. All three are pure functions of the
    // already-observed `vizData` + `enabledLayers`, so reading them inside the
    // Canvas / config sheet registers the right `@Observable` dependencies.

    /// Layer ids the currently-rendered model can't provide (no native NWP data,
    /// etc.) — greyed / disabled in the config sheet. Empty until `vizData` loads.
    var unavailableLayers: Set<String> {
        guard let vizData else { return [] }
        return NwpFallback.unavailableLayers(in: vizData)
    }

    /// Render-time enabled map: the stored preference with unavailable layers
    /// disabled and DD substituted for any wanted-but-unavailable NWP layer. The
    /// stored `enabledLayers` preference is never mutated (switching back to an
    /// NWP-capable model auto-restores NWP). Mirrors web `briefing-main.ts`.
    var effectiveEnabledLayers: [String: Bool] {
        NwpFallback.applyFallback(enabledLayers: enabledLayers, unavailable: unavailableLayers)
    }

    /// Layers auto-substituted at render (DD standing in for an unavailable NWP
    /// method) — the config sheet flags these so the user knows why NWP looks off.
    var substitutedLayers: Set<String> {
        NwpFallback.substitutedLayers(enabledLayers: enabledLayers, effective: effectiveEnabledLayers)
    }

    func toggleLayer(_ id: String) {
        enabledLayers[id] = !(enabledLayers[id] ?? false)
        activeAdvisoryPreset = nil  // a manual edit is no longer a named lens
        persistLayerConfig()
    }

    /// Force-enable a known layer (e.g. a deep-link focus intent turning on the
    /// advisory's layer). No-op for an unknown id.
    func enableLayer(_ id: String) {
        guard enabledLayers[id] != nil else { return }
        guard enabledLayers[id] != true else { return }  // already on — skip the redundant write
        enabledLayers[id] = true
        persistLayerConfig()
    }

    // MARK: - Layer presets (§4.5; ported from web — see CrossSectionPresets).
    // A preset sets every layer; touching any control flips to Custom.

    enum Preset: String, CaseIterable, Identifiable {
        case gramet = "GRAMET"
        case windy = "Windy"
        case foreFlight = "ForeFlight"
        case custom = "Custom"
        var id: String { rawValue }

        /// The colour theme each preset carries (#320). Mirrors the web preset
        /// `themeId` mapping (gramet→gramet, windy→light, foreflight→high-contrast);
        /// Custom carries none (leave the current theme as-is).
        var themeId: CrossSectionThemeID? {
            switch self {
            case .gramet: .gramet
            case .windy: .light
            case .foreFlight: .highContrast
            case .custom: nil
            }
        }
    }

    func applyPreset(_ preset: Preset) {
        let map = presetMap(preset)
        if !map.isEmpty { enabledLayers = map }
        if let tid = preset.themeId { setTheme(tid) }
        activeAdvisoryPreset = nil
        persistLayerConfig()
    }

    /// The preset matching the current layer set, or `.custom` if it's been
    /// hand-tuned away from any preset.
    var currentPreset: Preset {
        for p in [Preset.gramet, .windy, .foreFlight] where enabledLayers == presetMap(p) {
            return p
        }
        return .custom
    }

    private func presetMap(_ p: Preset) -> [String: Bool] {
        switch p {
        case .gramet: return CrossSectionPresets.gramet
        case .windy: return CrossSectionPresets.windy
        case .foreFlight: return CrossSectionPresets.foreflight
        case .custom: return [:]
        }
    }

    // MARK: - Advisory lenses (ported from web ADVISORY_PRESETS)

    /// Apply a hazard lens: clean-slate the managed groups, enable the preferred
    /// layer of each named method group, then force the lens's explicit lines on.
    /// Terrain + cruise reference stay (always-on / not in resetGroups).
    func applyAdvisoryPreset(_ preset: AdvisoryPreset) {
        var m = enabledLayers
        for layer in CrossSectionLayer.allLayers where CrossSectionPresets.resetGroups.contains(layer.group) {
            m[layer.id] = false
        }
        for group in preset.groups {
            if let preferred = CrossSectionLayer.methodGroupOrder[group]?.first {
                m[preferred] = true
            }
        }
        for id in preset.lines where m[id] != nil {  // drop ids iOS doesn't have
            m[id] = true
        }
        enabledLayers = m
        activeAdvisoryPreset = preset.id
        persistLayerConfig()
    }

    /// Clear the active lens (the picker's "None") without otherwise touching the
    /// layer config.
    func clearAdvisoryPreset() {
        activeAdvisoryPreset = nil
        persistLayerConfig()
    }

    // MARK: - Methods (clouds/icing/turbulence/convection — one method per group)

    /// Currently-active method layer ID for a method group (clouds/icing/etc),
    /// or nil if all methods in the group are off.
    func activeMethod(for group: LayerGroup) -> String? {
        guard let order = CrossSectionLayer.methodGroupOrder[group] else { return nil }
        return order.first(where: { enabledLayers[$0] == true })
    }

    /// Set the active method for a group. Passing nil disables all methods in the group.
    /// Other methods in the same group are turned off — only one is rendered at a time.
    func setMethod(_ layerId: String?, for group: LayerGroup) {
        guard let order = CrossSectionLayer.methodGroupOrder[group] else { return }
        for id in order {
            enabledLayers[id] = (id == layerId)
        }
        activeAdvisoryPreset = nil
        persistLayerConfig()
    }

    // MARK: - Cloud axes (source × style — two independent controls, #7)

    /// Active cloud source/style, or nil when clouds are off.
    var cloudAxes: (source: CloudSource, style: CloudStyle)? {
        activeMethod(for: .clouds).flatMap { CrossSectionPresets.parseCloudLayerId($0) }
    }

    /// Turn the cloud layer on (defaulting to Soft NWP) or off.
    func setCloudEnabled(_ on: Bool) {
        if on {
            let axes = cloudAxes ?? (.nwp, .soft)
            setMethod(CrossSectionPresets.cloudLayerId(source: axes.source, style: axes.style), for: .clouds)
        } else {
            setMethod(nil, for: .clouds)
        }
    }

    /// Change one cloud axis, keeping the other (and keeping clouds on).
    func setCloud(source: CloudSource? = nil, style: CloudStyle? = nil) {
        let current = cloudAxes ?? (.nwp, .soft)
        let newId = CrossSectionPresets.cloudLayerId(
            source: source ?? current.source,
            style: style ?? current.style)
        setMethod(newId, for: .clouds)
    }

    // MARK: - Data extraction (port of data-extract.ts)

    static func extractVizData(
        from manifest: RouteAnalysesResponse,
        model: String,
        elevation: ElevationResponse?
    ) -> VizRouteData {
        var points: [VizPoint] = []
        var waypointMarkers: [WaypointMarker] = []

        for rpa in manifest.analyses {
            let sounding = rpa.sounding[model]
            let wind = rpa.windComponents[model]
            points.append(extractPoint(rpa: rpa, sounding: sounding, wind: wind, model: model))

            if let icao = rpa.waypointIcao {
                waypointMarkers.append(WaypointMarker(
                    distanceNm: rpa.distanceFromOriginNm,
                    icao: icao,
                    lat: rpa.lat,
                    lon: rpa.lon
                ))
            }
        }

        let actualCeiling = Double(manifest.cruiseAltitudeFt)
        let terrainProfile = elevation?.points.map {
            TerrainPoint(distanceNm: $0.distanceNm, elevationFt: $0.elevationFt)
        }

        return VizRouteData(
            points: points,
            cruiseAltitudeFt: Double(manifest.cruiseAltitudeFt),
            ceilingAltitudeFt: actualCeiling,
            flightCeilingFt: max(actualCeiling, Double(manifest.cruiseAltitudeFt)) + 5000,
            totalDistanceNm: manifest.totalDistanceNm,
            waypointMarkers: waypointMarkers,
            departureTime: manifest.departureTime,
            flightDurationHours: manifest.flightDurationHours,
            terrainProfile: terrainProfile
        )
    }

    private static func extractPoint(
        rpa: RoutePointAnalysis,
        sounding: SoundingAnalysis?,
        wind: WindComponent?,
        model: String
    ) -> VizPoint {
        let indices = sounding?.indices

        let altitudeLines = AltitudeLines(
            freezingLevelFt: indices?.freezingLevelFt,
            minus10cLevelFt: indices?.minus10cLevelFt,
            minus20cLevelFt: indices?.minus20cLevelFt,
            lclAltitudeFt: indices?.lclAltitudeFt,
            lfcAltitudeFt: indices?.lfcAltitudeFt,
            elAltitudeFt: indices?.elAltitudeFt
        )

        let cloudLayers = (sounding?.cloudLayers ?? []).map {
            VizCloudLayer(baseFt: $0.baseFt, topFt: $0.topFt, coverage: $0.coverage, meanDewpointDepressionC: $0.meanDewpointDepressionC, meanCloudCoverPct: $0.meanCloudCoverPct)
        }

        // nwp_cloud_layers: nil = no NWP source for this model; [] = clear sky.
        // Mirrors web's data-extract semantics so layer toggles can distinguish
        // "no data" (disable) from "clear sky" (render nothing).
        let nwpCloudLayers: [VizCloudLayer]? = sounding?.nwpCloudLayers.map { layers in
            layers.map {
                VizCloudLayer(baseFt: $0.baseFt, topFt: $0.topFt, coverage: $0.coverage, meanDewpointDepressionC: $0.meanDewpointDepressionC, meanCloudCoverPct: $0.meanCloudCoverPct)
            }
        }

        let icingZones = (sounding?.icingZones ?? []).map {
            VizIcingZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType)
        }

        let icingOgimetNwpZones = (sounding?.icingOgimetNwpZones ?? []).map {
            VizIcingZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType)
        }

        let sfipZones = (sounding?.sfipZones ?? []).map {
            VizSfipZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType, meanSfip100: $0.meanSfip100, variant: $0.variant)
        }

        let catLayers = (sounding?.verticalMotion?.catRiskLayers ?? []).map {
            VizCATLayer(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk)
        }

        let inversions = (sounding?.inversionLayers ?? []).map {
            VizInversionLayer(baseFt: $0.baseFt, topFt: $0.topFt, strengthC: $0.strengthC)
        }

        let low = sounding?.cloudCoverLowPct ?? 0
        let mid = sounding?.cloudCoverMidPct ?? 0
        let high = sounding?.cloudCoverHighPct ?? 0
        let cloudCoverTotalPct = min(100, low + mid + high)

        var worstModelAgreement = "good"
        for d in rpa.modelDivergence {
            if d.agreement == "poor" { worstModelAgreement = "poor"; break }
            if d.agreement == "moderate" { worstModelAgreement = "moderate" }
        }

        let diag = sounding?.nwpCloudDiagnostics
        let nwpCloudDiag: VizCloudDiag? = diag.map {
            VizCloudDiag(
                low: VizCloudDiagBand(coverPct: $0.low.coverPct, baseFt: $0.low.baseFt, topFt: $0.low.topFt),
                mid: VizCloudDiagBand(coverPct: $0.mid.coverPct, baseFt: $0.mid.baseFt, topFt: $0.mid.topFt),
                high: VizCloudDiagBand(coverPct: $0.high.coverPct, baseFt: $0.high.baseFt, topFt: $0.high.topFt),
                ceilingFt: $0.ceilingFt
            )
        }

        let temperatureC = divergenceValue(rpa.modelDivergence, variable: "temperature_c", model: model)
        let precipitationMm = divergenceValue(rpa.modelDivergence, variable: "precipitation_mm", model: model)

        let convNwp = sounding?.convectiveNwp

        return VizPoint(
            distanceNm: rpa.distanceFromOriginNm,
            lat: rpa.lat,
            lon: rpa.lon,
            time: rpa.interpolatedTime,
            altitudeLines: altitudeLines,
            cloudLayers: cloudLayers,
            nwpCloudLayers: nwpCloudLayers,
            icingZones: icingZones,
            icingOgimetNwpZones: icingOgimetNwpZones,
            sfipZones: sfipZones,
            catLayers: catLayers,
            inversions: inversions,
            convectiveRisk: sounding?.convective?.riskLevel ?? "none",
            convectiveBaseFt: sounding?.convective?.baseFt,
            convectiveTopFt: sounding?.convective?.topFt,
            nwpConvectiveRisk: convNwp?.riskLevel ?? "none",
            nwpConvectiveBaseFt: convNwp?.baseFt,
            nwpConvectiveTopFt: convNwp?.topFt,
            nwpConvectiveCoverPct: convNwp?.coverPct,
            nwpConvectiveMethod: convNwp?.method,
            hasNwpConvective: convNwp != nil,
            cloudCoverTotalPct: cloudCoverTotalPct,
            cloudCoverLowPct: sounding?.cloudCoverLowPct ?? 0,
            cloudCoverMidPct: sounding?.cloudCoverMidPct ?? 0,
            headwindKt: wind?.headwindKt ?? 0,
            crosswindKt: wind?.crosswindKt ?? 0,
            capeSurfaceJkg: indices?.capeSurfaceJkg ?? 0,
            worstModelAgreement: worstModelAgreement,
            nwpCloudDiag: nwpCloudDiag,
            temperatureC: temperatureC,
            precipitationMm: precipitationMm
        )
    }

    private static func divergenceValue(_ divergence: [ModelDivergence], variable: String, model: String) -> Double? {
        for d in divergence {
            if d.variable == variable {
                return d.modelValues[model]
            }
        }
        return nil
    }
}
