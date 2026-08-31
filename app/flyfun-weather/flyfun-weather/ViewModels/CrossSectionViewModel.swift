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

    /// Advisory whose cross-section highlight (scrim + verdict ribbon, #374) is
    /// tracked, or nil for none. Only the id is stored — the geometry is
    /// re-derived from (advisories manifest × selected model) at render time, so
    /// model switches and recalcs update the highlight with no stale-copy bugs,
    /// and it no-ops gracefully when the advisory has no data (old pack).
    /// Model/point changes do NOT clear it; lens application and manual layer
    /// edits do (see `applyAdvisoryPreset` / `toggleLayer` / `setMethod` /
    /// `applyPreset`).
    private(set) var activeHighlightAdvisoryId: String?
    /// Visibility of the active highlight. Deliberately NOT part of
    /// `enabledLayers`: toggling it is a visibility control, not a lens edit —
    /// it must neither flip the preset to Custom nor clear the highlight (and
    /// keeping it out preserves the exact-map `currentPreset` comparison).
    private(set) var highlightVisible = true

    /// `UserDefaults` key for the persisted theme choice.
    private static let themeDefaultsKey = "crossSectionThemeId"
    /// `UserDefaults` key for the persisted layer enablement map.
    private static let layersDefaultsKey = "crossSectionEnabledLayers"
    /// `UserDefaults` key for the persisted advisory-lens id (absent when none).
    private static let advisoryPresetDefaultsKey = "crossSectionAdvisoryPreset"
    /// `UserDefaults` key for the persisted highlight advisory id (absent when
    /// none). Mirrors the web, which persists `activeHighlightAdvisoryId` in its
    /// viz settings; visibility intentionally resets to shown on relaunch.
    private static let highlightAdvisoryDefaultsKey = "crossSectionHighlightAdvisory"

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
        // A stored 0 means "absent" here: `double(forKey:)` cannot distinguish a
        // missing key from a stored zero, and zero is not a sampled radius.
        let storedRadius = UserDefaults.standard.double(forKey: Self.observedRadiusDefaultsKey)
        observedRadiusNm = storedRadius > 0 ? storedRadius : nil
        activeAdvisoryPreset = UserDefaults.standard.string(forKey: Self.advisoryPresetDefaultsKey)
        activeHighlightAdvisoryId = UserDefaults.standard.string(forKey: Self.highlightAdvisoryDefaultsKey)
        recomputeEffectiveLayers()
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
        if let id = activeHighlightAdvisoryId {
            UserDefaults.standard.set(id, forKey: Self.highlightAdvisoryDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.highlightAdvisoryDefaultsKey)
        }
        // This is the single funnel for every `enabledLayers` mutation
        // (toggle/enable/preset/method/advisory-lens), so refresh the effective-
        // layer cache here rather than at each call site.
        recomputeEffectiveLayers()
    }

    /// Corridor width the observed discs are resolved at, in NM. Persisted, and
    /// applied only when the pack actually sampled that radius (all three ship
    /// together, so switching is a client-side re-resolve with no request).
    /// nil → the widest sampled disc, matching the web's default.
    private(set) var observedRadiusNm: Double?

    private static let observedRadiusDefaultsKey = "crossSectionObservedRadiusNm"

    /// Re-resolve the observed discs at a new corridor width. Cheap: every radius
    /// is already in the payload, so this touches no network. Deliberately NOT a
    /// layer edit — it must not flip the preset to Custom.
    func setObservedRadius(_ radiusNm: Double?, snapshot: SnapshotResponse?) {
        observedRadiusNm = radiusNm
        if let radiusNm {
            UserDefaults.standard.set(radiusNm, forKey: Self.observedRadiusDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.observedRadiusDefaultsKey)
        }
        guard let vizData else { return }
        var updated = vizData
        let observed = ObservedResolver.resolve(
            snapshot?.observedConditions, radiusOverrideNm: radiusNm)
        updated.observed = observed
        ObservedResolver.merge(into: &updated.points, observed: observed)
        self.vizData = updated
        dataVersion += 1
    }

    func update(
        routeAnalyses: RouteAnalysesResponse,
        elevation: ElevationResponse?,
        model: String,
        observed: ObservedConditions? = nil
    ) {
        vizData = Self.extractVizData(
            from: routeAnalyses, model: model, elevation: elevation,
            observed: observed, observedRadiusNm: observedRadiusNm
        )
        dataVersion += 1
        recomputeEffectiveLayers()  // model/route/elevation changed → refresh the cache
    }

    // MARK: - NWP availability & fallback (port of web getUnavailableLayers +
    // applyNwpFallback). See `NwpFallback`. Both are derived from `vizData` +
    // `enabledLayers`, but cached as `@Observable` stored properties (refreshed by
    // `recomputeEffectiveLayers()` only when those inputs change) rather than
    // recomputed on each access — the Canvas reads them inside `body` at scrub-drag
    // frequency, so they must not carry an O(points) scan on the render path.

    /// Layer ids the currently-rendered model can't provide (no native NWP data,
    /// etc.) — greyed / disabled in the config sheet. Empty until `vizData` loads.
    ///
    /// Cached, not computed: recomputed by `recomputeEffectiveLayers()` only when
    /// `vizData` or `enabledLayers` actually change. `effectiveEnabledLayers` is
    /// read inside `CrossSectionView.crossSectionCanvas`, a `@ViewBuilder` var
    /// SwiftUI re-evaluates as plain Swift on every `body` invalidation — including
    /// each scrub-drag tick. Recomputing the O(points) `NwpFallback` scan +
    /// `Set`/`Dictionary` allocation there (before the `StaticCrossSectionScene`
    /// `Equatable` gate is even checked) would re-introduce exactly the per-tick
    /// jank #303 exists to prevent, so the work is hoisted off the render path.
    private(set) var unavailableLayers: Set<String> = []

    /// Render-time enabled map: the stored preference with unavailable layers
    /// disabled and DD substituted for any wanted-but-unavailable NWP layer. The
    /// stored `enabledLayers` preference is never mutated (switching back to an
    /// NWP-capable model auto-restores NWP). Mirrors web `briefing-main.ts`.
    /// Cached alongside `unavailableLayers` — see its note.
    private(set) var effectiveEnabledLayers: [String: Bool] = [:]

    /// Refresh the cached `unavailableLayers` / `effectiveEnabledLayers`. Called
    /// only from the two mutation funnels — `update()` (data rebuilt) and
    /// `persistLayerConfig()` (any layer edit) — plus once at the end of `init`, so
    /// the expensive scan runs on real changes, never per render frame.
    private func recomputeEffectiveLayers() {
        unavailableLayers = vizData.map { NwpFallback.unavailableLayers(in: $0) } ?? []
        effectiveEnabledLayers = NwpFallback.applyFallback(
            enabledLayers: enabledLayers, unavailable: unavailableLayers)
    }

    func toggleLayer(_ id: String) {
        enabledLayers[id] = !(enabledLayers[id] ?? false)
        activeAdvisoryPreset = nil  // a manual edit is no longer a named lens
        activeHighlightAdvisoryId = nil  // …and drops the advisory highlight (#374)
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
        activeHighlightAdvisoryId = nil  // a layer preset drops the highlight (#374)
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
        // Applying a lens clears any prior highlight (web parity, #374): a bare
        // lens from the picker therefore ends with no highlight, while the
        // advisory-chip path re-sets it via `setHighlightAdvisory` AFTER this
        // call — which is also what makes a same-chip re-tap toggle it off.
        activeHighlightAdvisoryId = nil
        persistLayerConfig()
    }

    /// Clear the active lens (the picker's "None") without otherwise touching the
    /// layer config. Also drops the advisory highlight, like any lens change.
    func clearAdvisoryPreset() {
        activeAdvisoryPreset = nil
        activeHighlightAdvisoryId = nil
        persistLayerConfig()
    }

    // MARK: - Advisory highlight (scrim + verdict ribbon, #374)

    /// Track (or clear with nil) the advisory whose highlight the cross-section
    /// renders. Setting a non-nil id force-shows the highlight (fresh intent — an
    /// invisible highlight right after a chip tap looks broken); clearing leaves
    /// the visibility flag alone.
    func setHighlightAdvisory(_ advisoryId: String?) {
        activeHighlightAdvisoryId = advisoryId
        if advisoryId != nil { highlightVisible = true }
        persistLayerConfig()
    }

    /// Show/hide the active highlight. A visibility control, NOT a lens edit —
    /// it must not clear the highlight or the active lens (contrast
    /// `toggleLayer`).
    func setHighlightVisible(_ visible: Bool) {
        highlightVisible = visible
    }

    /// The representative model for an advisory — read from the server's
    /// `representative_model`, which names the model holding `aggregateStatus`
    /// with the largest flagged extent. The chip switches the cross-section to
    /// it, so the highlight shows the geometry behind the sentence the card
    /// prints. Mirrors web `advisory-highlights.ts`.
    ///
    /// This used to re-derive the rule here ("first entry matching the aggregate
    /// status"), which is why it drifted: the server moved off first-match and
    /// the app kept highlighting whichever model happened to sort first, while
    /// the card beside it quoted a different one. The scan survives only as the
    /// old-pack fallback, where the field is absent.
    static func representativeModel(for advisory: RouteAdvisoryResult) -> String? {
        if let published = advisory.representativeModel {
            return published
        }
        if let match = advisory.perModel.first(where: { $0.status == advisory.aggregateStatus }) {
            return match.model
        }
        return advisory.perModel.first?.model
    }

    /// Derive the highlight geometry to render for (manifest × advisory × model),
    /// or nil when the advisory is not highlighted / no longer exists / the model
    /// has no entry / the pack carries no highlight data (old pack) — in every
    /// case the highlight layer and its visibility toggle stay hidden. Mirrors
    /// web `deriveHighlights`.
    static func deriveHighlights(
        manifest: AdvisoriesResponse?,
        advisoryId: String?,
        model: String
    ) -> VizAdvisoryHighlights? {
        guard let manifest, let advisoryId,
              let advisory = manifest.advisories.first(where: { $0.advisoryId == advisoryId }),
              let highlights = advisory.perModel.first(where: { $0.model == model })?.highlights
        else { return nil }
        return VizAdvisoryHighlights(from: highlights)
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
        activeHighlightAdvisoryId = nil  // a manual method edit drops the highlight (#374)
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
        elevation: ElevationResponse?,
        observed observedConditions: ObservedConditions? = nil,
        observedRadiusNm: Double? = nil
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

        // Observed discs (#574) resolve to the same route the analyses walk, so
        // an observed value and the model column above it describe one place.
        let observed = ObservedResolver.resolve(
            observedConditions, radiusOverrideNm: observedRadiusNm)
        ObservedResolver.merge(into: &points, observed: observed)

        return VizRouteData(
            points: points,
            cruiseAltitudeFt: Double(manifest.cruiseAltitudeFt),
            ceilingAltitudeFt: actualCeiling,
            flightCeilingFt: max(actualCeiling, Double(manifest.cruiseAltitudeFt)) + 5000,
            totalDistanceNm: manifest.totalDistanceNm,
            waypointMarkers: waypointMarkers,
            departureTime: manifest.departureTime,
            flightDurationHours: manifest.flightDurationHours,
            terrainProfile: terrainProfile,
            observed: observed
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
