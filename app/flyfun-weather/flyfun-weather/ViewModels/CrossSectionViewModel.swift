import Foundation

/// Extracts VizRouteData from API responses for a selected model.
/// Port of web's data-extract.ts extractVizData().
///
/// Also owns the cross-section's layer state (#605): the enabled-layer map and
/// the two lens selectors that compose over it. **Emulate** says whose
/// conventions — which methods and which look (GRAMET / Windy / ForeFlight, or
/// FlyFun for the methods this briefing graded with). **Focus** says what you
/// are looking for — which groups are on (the advisory lenses). A lens never
/// names a method: it asks for "the preferred layer of this group", which
/// resolves through whatever Emulate chose, so the two cannot contradict each
/// other. Before #605 each was a whole layer map and applying one wiped the
/// other. Port of the web split (#591).
@Observable
@MainActor
final class CrossSectionViewModel {
    private(set) var vizData: VizRouteData?
    private(set) var enabledLayers: [String: Bool] = CrossSectionPresets.bootDefaults
    /// Active Focus lens id (e.g. "icing"), or nil for Custom. Any manual layer
    /// edit drops it — the view is no longer that lens.
    private(set) var activeAdvisoryPreset: String?
    /// Active emulation id (`gramet` / `windy` / `foreflight`), or nil for FlyFun.
    /// Recorded rather than derived from the layer map: an emulation is a look
    /// and a method set, so switching one band off does not stop the chart being
    /// GRAMET-shaped — and clearing it on a manual edit would silently drop the
    /// methods the Focus lens resolves against (web `toggleVizLayer`).
    private(set) var activeEmulation: String?
    /// The methods this briefing actually graded with, per method group, read
    /// from the advisories manifest's `primary_method_id`; engine defaults until
    /// the manifest loads. FlyFun and the advisory chip resolve through these.
    /// Not persisted: it belongs to the pack, not to the user.
    private(set) var gradedMethods: [LayerGroup: String] = CrossSectionPresets.engineMethodDefaults
    /// The cloud style last chosen explicitly. Only consulted while no cloud layer
    /// is on — otherwise the style is read off the drawn layer (`cloudStyle`), so
    /// the control can never disagree with the chart. Persisted.
    private(set) var cloudStylePreference: CloudStyle
    /// Monotonic identity for `vizData`: bumped only when the data is rebuilt
    /// (model/route/elevation change). `VizRouteData` is a deep value type with no
    /// `Equatable` conformance, so the static cross-section scene keys its
    /// `Equatable` redraw gate on this counter instead of diffing the whole struct
    /// each scrub tick (#303).
    private(set) var dataVersion: Int = 0
    /// Active cross-section colour theme (#320). Defaults to GRAMET to match the
    /// booted GRAMET emulation, so the chart and the emulation agree on boot.
    /// Theme is orthogonal to the layers: picking an emulation sets its theme,
    /// but changing the theme never touches the layers or the emulation label —
    /// mirrors the web, where `setVizTheme` leaves the preset alone. Persisted
    /// across launches via `UserDefaults`.
    private(set) var themeId: CrossSectionThemeID

    /// Advisory whose cross-section highlight (scrim + verdict ribbon, #374) is
    /// tracked, or nil for none. Only the id is stored — the geometry is
    /// re-derived from (advisories manifest × selected model) at render time, so
    /// model switches and recalcs update the highlight with no stale-copy bugs,
    /// and it no-ops gracefully when the advisory has no data (old pack).
    /// Model/point changes do NOT clear it; lens application, emulation changes
    /// and manual layer edits do.
    private(set) var activeHighlightAdvisoryId: String?
    /// Visibility of the active highlight. Deliberately NOT part of
    /// `enabledLayers`: toggling it is a visibility control, not a lens edit —
    /// it must neither drop the Focus lens nor clear the highlight.
    private(set) var highlightVisible = true

    /// `UserDefaults` key for the persisted theme choice.
    nonisolated private static let themeDefaultsKey = "crossSectionThemeId"
    /// `UserDefaults` key for the persisted layer enablement map.
    nonisolated private static let layersDefaultsKey = "crossSectionEnabledLayers"
    /// `UserDefaults` key for the persisted Focus lens id (absent when none).
    /// Keeps its pre-#605 name so an existing lens survives the upgrade.
    nonisolated private static let advisoryPresetDefaultsKey = "crossSectionAdvisoryPreset"
    /// `UserDefaults` key for the persisted highlight advisory id (absent when
    /// none). Mirrors the web, which persists `activeHighlightAdvisoryId` in its
    /// viz settings; visibility intentionally resets to shown on relaunch.
    nonisolated private static let highlightAdvisoryDefaultsKey = "crossSectionHighlightAdvisory"
    /// `UserDefaults` key for the persisted emulation id.
    nonisolated private static let emulationDefaultsKey = "crossSectionEmulation"
    /// `UserDefaults` key for the persisted cloud style preference.
    nonisolated private static let cloudStyleDefaultsKey = "crossSectionCloudStyle"
    /// Stored for FlyFun, so "our own conventions" can be told apart from "never
    /// stored" — the one-time migration path in `restoredEmulation`.
    private static let ownConventionsSentinel = "flyfun"

    /// Every key this view model persists, for tests that need a clean slate.
    nonisolated static let persistedDefaultsKeys = [
        themeDefaultsKey, layersDefaultsKey, advisoryPresetDefaultsKey,
        highlightAdvisoryDefaultsKey, emulationDefaultsKey, cloudStyleDefaultsKey,
        observedRadiusDefaultsKey,
    ]

    init() {
        // Restore the last-chosen theme; fall back to GRAMET (the boot
        // emulation's theme) when nothing is stored or the value is unknown.
        let stored = UserDefaults.standard.string(forKey: Self.themeDefaultsKey)
        themeId = stored.flatMap(CrossSectionThemeID.init(rawValue:)) ?? .gramet
        cloudStylePreference = UserDefaults.standard.string(forKey: Self.cloudStyleDefaultsKey)
            .flatMap(CloudStyle.init(rawValue:)) ?? .natural
        // Sync the module-level active theme so the very first frame (and the
        // layer bar's swatches) render in the right palette even before the
        // renderer runs.
        CrossSectionTheme.setActive(themeId)

        // Restore the last layer config so a relaunch keeps the user's layers
        // (not just colours) — mirrors the web, which persists the whole viz
        // config (#9, iOS testing feedback). Keep only ids the current build
        // still knows about (a renamed/removed layer can't resurrect a stale id),
        // and merge restored values over the boot defaults so a newly-added layer
        // gets its default state rather than vanishing.
        var restoredLayers = false
        if let data = UserDefaults.standard.data(forKey: Self.layersDefaultsKey),
           let decoded = try? JSONDecoder().decode([String: Bool].self, from: data) {
            var merged = CrossSectionPresets.bootDefaults
            for (id, on) in decoded where merged[id] != nil {
                merged[id] = on
            }
            enabledLayers = merged
            restoredLayers = true
        }
        // A stored 0 means "absent" here: `double(forKey:)` cannot distinguish a
        // missing key from a stored zero, and zero is not a sampled radius.
        let storedRadius = UserDefaults.standard.double(forKey: Self.observedRadiusDefaultsKey)
        observedRadiusNm = storedRadius > 0 ? storedRadius : nil
        activeAdvisoryPreset = UserDefaults.standard.string(forKey: Self.advisoryPresetDefaultsKey)
        activeHighlightAdvisoryId = UserDefaults.standard.string(forKey: Self.highlightAdvisoryDefaultsKey)
        activeEmulation = Self.restoredEmulation(layers: enabledLayers, restoredLayers: restoredLayers)
        recomputeEffectiveLayers()
    }

    /// The stored emulation. Before #605 there was none to store — the preset was
    /// inferred from an exact layer-map match — so infer it once the same way: a
    /// fresh install boots GRAMET, a stored map that still matches an emulation
    /// keeps that label, and anything hand-tuned reads as FlyFun.
    private static func restoredEmulation(layers: [String: Bool], restoredLayers: Bool) -> String? {
        if let stored = UserDefaults.standard.string(forKey: emulationDefaultsKey) {
            return stored == ownConventionsSentinel ? nil : CrossSectionPresets.emulation(stored)?.id
        }
        guard restoredLayers else { return CrossSectionPresets.bootEmulationId }
        return CrossSectionPresets.all.first { preset in
            preset.enabledLayers.allSatisfy { layers[$0.key] == $0.value }
        }?.id
    }

    /// Switch the colour theme. Independent of the layers and the emulation. Persisted.
    func setTheme(_ id: CrossSectionThemeID) {
        themeId = id
        CrossSectionTheme.setActive(id)
        UserDefaults.standard.set(id.rawValue, forKey: Self.themeDefaultsKey)
    }

    /// Persist the layer set, both lens selectors and the highlight. Called after
    /// every mutation so the config survives relaunch (#9, iOS testing feedback).
    private func persistLayerConfig() {
        if let data = try? JSONEncoder().encode(enabledLayers) {
            UserDefaults.standard.set(data, forKey: Self.layersDefaultsKey)
        }
        if let id = activeAdvisoryPreset {
            UserDefaults.standard.set(id, forKey: Self.advisoryPresetDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.advisoryPresetDefaultsKey)
        }
        UserDefaults.standard.set(activeEmulation ?? Self.ownConventionsSentinel, forKey: Self.emulationDefaultsKey)
        if let id = activeHighlightAdvisoryId {
            UserDefaults.standard.set(id, forKey: Self.highlightAdvisoryDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.highlightAdvisoryDefaultsKey)
        }
        // This is the single funnel for every `enabledLayers` mutation, so
        // refresh the effective-layer cache here rather than at each call site.
        recomputeEffectiveLayers()
    }

    /// Corridor width the observed discs are resolved at, in NM. Persisted, and
    /// applied only when the pack actually sampled that radius (all three ship
    /// together, so switching is a client-side re-resolve with no request).
    /// nil → the widest sampled disc, matching the web's default.
    private(set) var observedRadiusNm: Double?

    nonisolated private static let observedRadiusDefaultsKey = "crossSectionObservedRadiusNm"

    /// Re-resolve the observed discs at a new corridor width. Cheap: every radius
    /// is already in the payload, so this touches no network. Deliberately NOT a
    /// layer edit — it must not drop the Focus lens.
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
    /// etc.) — struck through and disabled on the layer bar. Empty until
    /// `vizData` loads.
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

    /// Layers drawn as a DD stand-in for a wanted-but-unavailable NWP layer:
    /// shown on the bar as on-but-substituted, so the pills match the chart.
    var substitutedLayers: Set<String> {
        Set(effectiveEnabledLayers.compactMap { id, on in
            on && enabledLayers[id] != true ? id : nil
        })
    }

    // MARK: - Manual layer edits (the layer bar)

    func isLayerOn(_ id: String) -> Bool { enabledLayers[id] == true }

    /// Pick-any toggle of one layer. Every layer family is pick-any: two icing
    /// methods overlaid is a comparison, not an error.
    func toggleLayer(_ id: String) {
        enabledLayers[id] = !(enabledLayers[id] ?? false)
        markManualEdit()
    }

    /// Force-enable a known layer (e.g. a deep-link focus intent turning on the
    /// advisory's layer). No-op for an unknown id.
    func enableLayer(_ id: String) {
        guard enabledLayers[id] != nil else { return }
        guard enabledLayers[id] != true else { return }  // already on — skip the redundant write
        enabledLayers[id] = true
        persistLayerConfig()
    }

    /// Whether anything in the family is on — the compact chip's state.
    func isFamilyOn(_ family: LayerFamily) -> Bool {
        family.layerIds.contains { enabledLayers[$0] == true }
    }

    /// The compact chip: one on/off per family with the method decision made for
    /// you. On enables the preferred layer of each METHOD group (through the
    /// effective methods) and the default lines of every other group — never the
    /// first line alone, which is how the web once silently dropped −10/−20 °C
    /// and LFC/EL for good. Off switches everything in the family off.
    func setFamily(_ family: LayerFamily, on: Bool) {
        if on {
            let methods = effectiveMethods
            let style = resolutionCloudStyle
            for group in family.groups {
                if CrossSectionPresets.methodGroups.contains(group) {
                    if let id = CrossSectionPresets.preferredLayer(for: group, method: methods[group], cloudStyle: style) {
                        enabledLayers[id] = true
                    }
                } else {
                    for id in CrossSectionLayer.layerIds(in: group) where CrossSectionLayer.defaultEnabled.contains(id) {
                        enabledLayers[id] = true
                    }
                }
            }
            // A family with no default line still has to show something.
            if !isFamilyOn(family), let first = family.layerIds.first {
                enabledLayers[first] = true
            }
        } else {
            for id in family.layerIds { enabledLayers[id] = false }
        }
        markManualEdit()
    }

    /// The `None` pill: switch every layer in one group off in one tap.
    func clearGroup(_ group: LayerGroup) {
        for id in CrossSectionLayer.layerIds(in: group) { enabledLayers[id] = false }
        markManualEdit()
    }

    /// A user edit: the view no longer is the named Focus lens, and the advisory
    /// highlight goes with it (#374). The emulation label stays — see
    /// `activeEmulation`.
    private func markManualEdit() {
        activeAdvisoryPreset = nil
        activeHighlightAdvisoryId = nil
        persistLayerConfig()
    }

    // MARK: - Clouds (per-source pills × one shared style)

    /// The style clouds are drawn in: read off the enabled cloud layer, else the
    /// stored preference.
    var cloudStyle: CloudStyle {
        for id in CrossSectionPresets.cloudLayerIds where enabledLayers[id] == true {
            if let axes = CrossSectionPresets.parseCloudLayerId(id) { return axes.style }
        }
        return cloudStylePreference
    }

    /// Whether any style of a cloud source is drawn.
    func isCloudSourceOn(_ source: CloudSource) -> Bool {
        CloudStyle.allCases.contains {
            enabledLayers[CrossSectionPresets.cloudLayerId(source: source, style: $0)] == true
        }
    }

    /// Toggle one cloud source, in the current style. Both on at once is the
    /// cross-check the family's About panel describes.
    func toggleCloudSource(_ source: CloudSource) {
        if isCloudSourceOn(source) {
            for style in CloudStyle.allCases {
                enabledLayers[CrossSectionPresets.cloudLayerId(source: source, style: style)] = false
            }
        } else {
            enabledLayers[CrossSectionPresets.cloudLayerId(source: source, style: cloudStyle)] = true
        }
        markManualEdit()
    }

    /// Redraw every enabled cloud source in a new style, and remember it.
    func setCloudStyle(_ style: CloudStyle) {
        cloudStylePreference = style
        UserDefaults.standard.set(style.rawValue, forKey: Self.cloudStyleDefaultsKey)
        for source in CloudSource.allCases where isCloudSourceOn(source) {
            for s in CloudStyle.allCases {
                enabledLayers[CrossSectionPresets.cloudLayerId(source: source, style: s)] = s == style
            }
        }
        markManualEdit()
    }

    // MARK: - Emulate (whose conventions)

    /// The methods a lens or a compact chip resolves through: the graded ones,
    /// overlaid by whatever the active emulation chose — GRAMET means Ogimet-NWP
    /// icing and natural NWP cloud, whatever the briefing graded with.
    var effectiveMethods: [LayerGroup: String] {
        guard let preset = CrossSectionPresets.emulation(activeEmulation) else { return gradedMethods }
        return gradedMethods.merging(CrossSectionPresets.methods(from: preset).methods) { $1 }
    }

    /// The cloud style a lens or chip draws clouds in: the one already on the
    /// chart, else the emulation's, else the stored preference. Read before a
    /// lens's clean slate wipes the cloud layer it is read from.
    private var resolutionCloudStyle: CloudStyle {
        if CrossSectionPresets.cloudLayerIds.contains(where: { enabledLayers[$0] == true }) {
            return cloudStyle
        }
        if let preset = CrossSectionPresets.emulation(activeEmulation),
           let style = CrossSectionPresets.methods(from: preset).cloudStyle {
            return style
        }
        return cloudStylePreference
    }

    /// Update the graded methods from a freshly loaded (or recalculated)
    /// advisories manifest. A FlyFun Focus lens that is still intact is
    /// re-resolved through them, so a lens applied before the manifest landed
    /// does not keep showing the engine defaults for the whole session. Nothing
    /// else moves: a manual edit has already dropped the lens, an emulation
    /// supplies every method group itself, and an advisory-chip lens (highlight
    /// active) was resolved through that advisory's own methods on purpose — so
    /// a late manifest never clobbers a view the pilot has tuned.
    func setGradedMethods(_ methods: [LayerGroup: String]) {
        guard methods != gradedMethods else { return }
        gradedMethods = methods
        guard activeEmulation == nil, activeHighlightAdvisoryId == nil,
              let lens = activeAdvisoryPreset.flatMap({ CrossSectionPresets.advisory[$0] })
        else { return }
        applyLens(lens, methods: effectiveMethods)
        persistLayerConfig()
    }

    /// Pick an emulation (nil = FlyFun). An emulation merges its method set and
    /// sets its theme; FlyFun applies the graded methods, one layer per method
    /// group, and leaves the theme alone. Either way an active Focus lens is
    /// re-applied on top, so "Windy, focused on icing" means Windy's icing method
    /// with only the icing groups on — rather than the lens label surviving over
    /// a layer set that no longer matches it.
    func applyEmulation(_ id: String?) {
        if let preset = CrossSectionPresets.emulation(id) {
            enabledLayers.merge(preset.enabledLayers) { $1 }
            setTheme(preset.themeId)
            activeEmulation = preset.id
        } else {
            enabledLayers.merge(
                CrossSectionPresets.compactOverrides(methods: gradedMethods, cloudStyle: resolutionCloudStyle)
            ) { $1 }
            activeEmulation = nil
        }
        activeHighlightAdvisoryId = nil  // an emulation change drops the highlight (#374)
        if let focus = activeAdvisoryPreset.flatMap({ CrossSectionPresets.advisory[$0] }) {
            applyLens(focus, methods: effectiveMethods)
        }
        persistLayerConfig()
    }

    // MARK: - Focus (what am I looking for — ported from web ADVISORY_PRESETS)

    /// Apply a Focus lens: clean-slate the managed groups, enable the preferred
    /// layer of each named method group, then force the lens's explicit lines on.
    /// Methods default to the effective ones (graded, overlaid by the emulation);
    /// the advisory chip passes the advisory's own graded methods instead, so it
    /// shows the configuration the advisory was graded under. The emulation is
    /// left alone — the two compose.
    func applyAdvisoryPreset(_ preset: AdvisoryPreset, methods: [LayerGroup: String]? = nil) {
        applyLens(preset, methods: methods ?? effectiveMethods)
        activeAdvisoryPreset = preset.id
        // Applying a lens clears any prior highlight (web parity, #374): a bare
        // lens from the picker therefore ends with no highlight, while the
        // advisory-chip path re-sets it via `setHighlightAdvisory` AFTER this
        // call — which is also what makes a same-chip re-tap toggle it off.
        activeHighlightAdvisoryId = nil
        persistLayerConfig()
    }

    /// Focus → Custom: clear the lens label only. It must not touch the
    /// emulation or the layers — dropping the other selector's choice is exactly
    /// the confusion the split exists to end. Drops the highlight, like any lens
    /// change.
    func clearAdvisoryPreset() {
        activeAdvisoryPreset = nil
        activeHighlightAdvisoryId = nil
        persistLayerConfig()
    }

    /// The layer half of a lens, shared with `applyEmulation`'s re-apply.
    private func applyLens(_ preset: AdvisoryPreset, methods: [LayerGroup: String]) {
        let style = resolutionCloudStyle
        var m = enabledLayers
        for layer in CrossSectionLayer.allLayers where CrossSectionPresets.resetGroups.contains(layer.group) {
            m[layer.id] = false
        }
        for group in preset.groups {
            if let id = CrossSectionPresets.preferredLayer(for: group, method: methods[group], cloudStyle: style),
               m[id] != nil {
                m[id] = true
            }
        }
        for id in preset.lines where m[id] != nil {  // drop ids iOS doesn't have
            m[id] = true
        }
        enabledLayers = m
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
    /// it must not clear the highlight or the Focus lens (contrast `toggleLayer`).
    func setHighlightVisible(_ visible: Bool) {
        highlightVisible = visible
    }

    /// The representative model for an advisory — the server's
    /// `representative_model`, which names the model holding `aggregateStatus`
    /// with the largest flagged extent. The chip switches the cross-section to
    /// it, so the highlight shows the geometry behind the sentence the card
    /// prints. Mirrors web `advisory-highlights.ts`.
    ///
    /// This used to re-derive the rule here ("first entry matching the aggregate
    /// status"), which is why it drifted: the server moved off first-match and
    /// the app kept highlighting whichever model happened to sort first, while
    /// the card beside it quoted a different one. The scan survives only as the
    /// old-pack fallback, in `RouteAdvisoryResult.resolvedRepresentativeModel`.
    static func representativeModel(for advisory: RouteAdvisoryResult) -> String? {
        advisory.resolvedRepresentativeModel
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
