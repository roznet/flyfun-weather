import Foundation

// =============================================================================
// SYNC — keep this file in lockstep with the web preset definitions:
//   • Emulations (GRAMET / Windy / ForeFlight) and the method tables:
//       web/ts/visualization/cross-section/layer-registry.ts  (PRESETS, *_ENABLED,
//       PREFERRED_METHOD_LAYER, METHOD_GROUPS, methodsFromPreset,
//       getCompactLayerOverrides)
//   • Focus lenses (Basic / Icing / Clouds / Convective / Turbulence / VFR / IFR):
//       web/ts/visualization/cross-section/advisory-presets.ts (ADVISORY_PRESETS,
//       ADVISORY_TO_PRESET, ADVISORY_OVERRIDES, getPresetForAdvisory)
//   • Graded methods:
//       web/ts/visualization/cross-section/advisory-highlights.ts
//       (ADVISORY_METHOD_GROUP, deriveGradedMethods, advisoryMethodOverrides)
//       web/ts/adapters/preferences-adapter.ts (ENGINE_METHOD_DEFAULTS_FALLBACK)
//   • Cloud source×style axes:
//       web/ts/visualization/cross-section/layers/cloud-bands-factory.ts
//       (CLOUD_LAYER_BY_AXES, parseCloudLayerId)
//
// iOS uses its own layer IDs and lacks a few web layers (ieng-icing-bands,
// e-shear-bands, sld-bands, surface-obscuration-bands). Ids that don't exist on
// iOS are dropped when applied, and a graded method with no iOS layer (IENG,
// E-shear) falls back to the group's default layer. When the web presets change,
// update this file — both web files carry the reciprocal SYNC comment.
// =============================================================================

/// A tool emulation — another tool's look AND its method set. Port of web
/// `LayerPreset`. `enabledLayers` is a MERGE map over the layers a preset owns:
/// every group except the observed conditions, which no emulation touches (the
/// web `*_ENABLED` maps don't name them either), so picking GRAMET never turns
/// the radar strip off.
struct LayerPreset: Identifiable, Equatable {
    let id: String
    let label: String
    let themeId: CrossSectionThemeID
    let enabledLayers: [String: Bool]
}

/// What an emulation actually chooses, once its flat layer map is read as an
/// intent rather than a result. Port of web `PresetMethods`.
struct PresetMethods: Equatable {
    /// Method group → method id, in the vocabulary `CrossSectionPresets.preferredLayer`
    /// takes. Clouds carry a bare source (`dd` / `nwp`).
    let methods: [LayerGroup: String]
    /// The render style the preset's cloud layer implies, if it enables one.
    let cloudStyle: CloudStyle?
    let themeId: CrossSectionThemeID
}

/// Focus lens: what the pilot is looking for. Port of web `AdvisoryPreset`
/// (cross-section directives only — the web's route-graph / map / Skew-T
/// directives are not wired on iOS). `groups` enables the preferred layer of each
/// method group on a clean slate — resolved through the active emulation's
/// methods — and `lines` force-enables explicit layer IDs.
struct AdvisoryPreset: Identifiable, Equatable {
    let id: String
    let label: String
    let caption: String
    var groups: [LayerGroup] = []
    var lines: [String] = []
}

/// Cloud rendering axes (orthogonal): which data feed, and how it's drawn.
/// Mirrors web's `cloud-bands-factory.ts`.
enum CloudSource: String, CaseIterable, Identifiable {
    case nwp, dd
    var id: String { rawValue }
    var label: String { self == .nwp ? "NWP" : "DD" }

    /// A graded or preferred cloud method → the band it renders on. The
    /// backend's `nwp_synthesized` draws on the NWP band. Mirrors web
    /// `cloudSourceFromPreferred`.
    init?(methodId: String) {
        switch methodId {
        case "dd": self = .dd
        case "nwp", "nwp_synthesized": self = .nwp
        default: return nil
        }
    }

    /// One-line hint for the source pill (web `viz.cloudSourceHint.*`).
    var hint: String {
        switch self {
        case .nwp: "NWP — the model's own cloud-layer field. What the graded advisories run on."
        case .dd: "DD — cloud inferred from dewpoint depression in the sounding. Available for every model."
        }
    }
}

enum CloudStyle: String, CaseIterable, Identifiable {
    case soft, natural, square
    var id: String { rawValue }
    var label: String {
        switch self {
        case .soft: "Soft"
        case .natural: "Natural"
        case .square: "Square"
        }
    }
}

enum CrossSectionPresets {
    // MARK: - Emulations (ported from web *_ENABLED, translated to iOS IDs)

    /// Every layer an emulation owns: all but the observed conditions.
    private static let presetOwnedIds: [String] = CrossSectionLayer.allLayers
        .filter { $0.group != .conditions }
        .map(\.id)

    /// Merge map over `presetOwnedIds` with the given IDs on, the rest off.
    private static func layers(on onIds: Set<String>) -> [String: Bool] {
        var m: [String: Bool] = [:]
        for id in presetOwnedIds { m[id] = onIds.contains(id) }
        return m
    }

    /// GRAMET — Natural NWP clouds + Ogimet-NWP icing + CAT (Ri) + NWP convective.
    static let gramet = layers(on: [
        "nwp-cloud-bands", "nwp-convective-bg", "icing-ogimet-nwp-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    /// Windy — Natural NWP clouds + SFIP-NWP icing + CAT (Ri) + NWP convective.
    static let windy = layers(on: [
        "nwp-cloud-bands", "nwp-convective-bg", "sfip-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    /// ForeFlight — Square DD clouds + Ogimet-DD icing + CAT (Ri) + NWP convective.
    static let foreflight = layers(on: [
        "square-cloud-bands", "nwp-convective-bg", "icing-bands",
        "cat-bands", "terrain", "freezing-level", "reference-lines",
    ])

    static let all: [LayerPreset] = [
        LayerPreset(id: "gramet", label: "GRAMET", themeId: .gramet, enabledLayers: gramet),
        LayerPreset(id: "windy", label: "Windy", themeId: .light, enabledLayers: windy),
        LayerPreset(id: "foreflight", label: "ForeFlight", themeId: .highContrast, enabledLayers: foreflight),
    ]

    /// The emulation for an id, or nil for none / an unknown id.
    static func emulation(_ id: String?) -> LayerPreset? {
        guard let id else { return nil }
        return all.first { $0.id == id }
    }

    /// The emulation a fresh install boots into — the chart has always opened
    /// GRAMET-shaped on iOS.
    static let bootEmulationId = "gramet"

    /// What "no emulation" is called: our own conventions, i.e. the methods this
    /// briefing graded with (web `viz.emulateNone`). Not an absence.
    static let ownConventionsLabel = "FlyFun"

    /// Boot state, and the base every restored map merges over: GRAMET plus the
    /// observed layers at their web defaults. Covers every layer, so a merge can
    /// never leave an id missing.
    static let bootDefaults: [String: Bool] = {
        var m = gramet
        for layer in CrossSectionLayer.allLayers where m[layer.id] == nil {
            m[layer.id] = CrossSectionLayer.defaultEnabled.contains(layer.id)
        }
        return m
    }()

    // MARK: - Method groups

    /// The groups that are a CHOICE OF METHOD rather than a feature switch: each
    /// holds several ways of computing the same thing, so exactly one is "the
    /// preferred one". Compact family chips and the Basic lens both resolve
    /// exactly these, which is what keeps "Basic shows one of each" true.
    /// Mirrors web `METHOD_GROUPS`.
    static let methodGroups: [LayerGroup] = [.clouds, .icing, .turbulence, .convection]

    /// Method id → layer id per method group (clouds are keyed separately, by
    /// source × style). Ordered pairs so reading a preset back is deterministic.
    /// Mirrors web `PREFERRED_METHOD_LAYER` minus the layers iOS lacks.
    static let methodLayers: [LayerGroup: [(method: String, layerId: String)]] = [
        .icing: [
            ("ogimet_dd", "icing-bands"),
            ("ogimet_nwp", "icing-ogimet-nwp-bands"),
            ("sfip_nwp", "sfip-bands"),
        ],
        .turbulence: [("ri", "cat-bands")],
        .convection: [("thermo", "thermo-convective-bg"), ("nwp", "nwp-convective-bg")],
    ]

    /// A method group's layer when the preferred method is unknown or has no iOS
    /// layer (IENG, E-shear) — the web's `defaultEnabled ?? first` fallback.
    private static let fallbackLayer: [LayerGroup: String] = [
        .icing: "icing-ogimet-nwp-bands",
        .turbulence: "cat-bands",
        .convection: "nwp-convective-bg",
    ]

    /// The layer that shows `method` for `group`. Clouds fuse the bare source
    /// with the render style. Mirrors web `getPreferredLayerForGroup`.
    static func preferredLayer(for group: LayerGroup, method: String?, cloudStyle: CloudStyle) -> String? {
        if group == .clouds {
            let source = method.flatMap { CloudSource(methodId: $0) } ?? .nwp
            return cloudLayerId(source: source, style: cloudStyle)
        }
        if let method, let hit = methodLayers[group]?.first(where: { $0.method == method }) {
            return hit.layerId
        }
        return fallbackLayer[group]
    }

    /// One layer per method group — the preferred one on, its alternatives off.
    /// What FlyFun applies. Mirrors web `getCompactLayerOverrides`.
    static func compactOverrides(methods: [LayerGroup: String], cloudStyle: CloudStyle) -> [String: Bool] {
        var overrides: [String: Bool] = [:]
        for group in methodGroups {
            let preferred = preferredLayer(for: group, method: methods[group], cloudStyle: cloudStyle)
            for id in CrossSectionLayer.layerIds(in: group) {
                overrides[id] = id == preferred
            }
        }
        return overrides
    }

    /// Read an emulation's layer map back as the method choices it represents.
    /// This is the only reason Emulate and Focus compose: a lens asks for "the
    /// preferred layer of this group", and that has to resolve through what the
    /// emulation chose. A group the preset leaves off yields no entry. Mirrors
    /// web `methodsFromPreset`.
    static func methods(from preset: LayerPreset) -> PresetMethods {
        var methods: [LayerGroup: String] = [:]
        for group in methodGroups where group != .clouds {
            if let hit = methodLayers[group]?.first(where: { preset.enabledLayers[$0.layerId] == true }) {
                methods[group] = hit.method
            }
        }
        var style: CloudStyle?
        for id in cloudLayerIds where preset.enabledLayers[id] == true {
            if let axes = parseCloudLayerId(id) {
                methods[.clouds] = axes.source.rawValue
                style = axes.style
                break
            }
        }
        return PresetMethods(methods: methods, cloudStyle: style, themeId: preset.themeId)
    }

    // MARK: - Graded methods (ported from web advisory-highlights.ts)

    /// The engine's declared methods, used until (or where) the manifest is
    /// silent. Mirrors web `ENGINE_METHOD_DEFAULTS_FALLBACK`.
    static let engineMethodDefaults: [LayerGroup: String] = [
        .clouds: "nwp", .icing: "ogimet_nwp", .convection: "nwp",
    ]

    /// Which method group an advisory's `primary_method_id` speaks for. Only the
    /// evaluators that grade off a selectable method appear. Mirrors web
    /// `ADVISORY_METHOD_GROUP`.
    static let advisoryMethodGroup: [String: LayerGroup] = [
        "vmc_cruise": .clouds,
        "cloud_top": .clouds,
        "vfr_feasibility": .clouds,     // composite; clouds are its only method axis
        "icing_escape": .icing,
        "fiki_icing": .icing,
        "ifr_feasibility": .icing,      // composite; icing is the axis it badges
        "convective": .convection,
        "convective_character": .convection,
    ]

    /// The methods this briefing actually graded with, per group: the first
    /// method-bearing advisory's `primary_method_id` on its representative model
    /// (already reflecting any backend fallback), else the engine default.
    /// Mirrors web `deriveGradedMethods`.
    static func gradedMethods(from manifest: AdvisoriesResponse?) -> [LayerGroup: String] {
        var result = engineMethodDefaults
        guard let manifest else { return result }
        var found: Set<LayerGroup> = []
        for advisory in manifest.advisories {
            guard let group = advisoryMethodGroup[advisory.advisoryId], !found.contains(group) else { continue }
            let rep = advisory.resolvedRepresentativeModel
            let pm = advisory.perModel.first(where: { $0.model == rep }) ?? advisory.perModel.first
            guard let method = pm?.primaryMethodId else { continue }
            result[group] = normalizedMethod(method, for: group)
            found.insert(group)
        }
        return result
    }

    /// The methods to resolve an advisory's lens with, so the chart shows the
    /// configuration the ADVISORY was graded under — which diverges from the
    /// user's exactly when the requested method could not run and the backend
    /// fell back. Only the advisory's own group is overridden. Mirrors web
    /// `advisoryMethodOverrides`.
    static func advisoryMethodOverrides(
        _ advisory: RouteAdvisoryResult, model: String?, methods: [LayerGroup: String]
    ) -> [LayerGroup: String] {
        guard let group = advisoryMethodGroup[advisory.advisoryId],
              let method = advisory.perModel.first(where: { $0.model == model })?.primaryMethodId
        else { return methods }
        var out = methods
        out[group] = normalizedMethod(method, for: group)
        return out
    }

    /// Clouds carry only a bare source; every other group keys on the full id.
    private static func normalizedMethod(_ method: String, for group: LayerGroup) -> String {
        guard group == .clouds else { return method }
        return CloudSource(methodId: method)?.rawValue ?? CloudSource.nwp.rawValue
    }

    // MARK: - Focus lenses (ported from web ADVISORY_PRESETS)

    /// Groups reset to OFF before applying a lens, so the view shows only what the
    /// lens specifies (plus always-on terrain + cruise reference). Observed
    /// conditions are deliberately absent, as on the web.
    static let resetGroups: Set<LayerGroup> = [
        .clouds, .icing, .convection, .turbulence, .stability, .temperature,
    ]

    /// Display order for the lens picker.
    static let advisoryOrder = ["basic", "icing", "clouds", "convective", "turbulence", "vfr", "ifr"]

    static let advisory: [String: AdvisoryPreset] = [
        "basic": AdvisoryPreset(
            id: "basic", label: "Basic / Learn",
            caption: "One of everything — cloud, icing, convection and turbulence, each on its default method.",
            // Exactly what the compact chips resolve to: the preferred layer of
            // each method group, one of each. Sharing `methodGroups` keeps that
            // true. Plus the 0 °C line any icing shading is read against.
            groups: methodGroups, lines: ["freezing-level"]),
        "icing": AdvisoryPreset(
            id: "icing", label: "Icing",
            caption: "Icing bands vs the 0 °C line and terrain — is there an ice-free descent?",
            groups: [.icing, .clouds], lines: ["freezing-level"]),
        "clouds": AdvisoryPreset(
            id: "clouds", label: "Clouds",
            caption: "Cloud tops & coverage vs your cruise level.",
            groups: [.clouds], lines: ["freezing-level"]),
        "convective": AdvisoryPreset(
            id: "convective", label: "Convective",
            caption: "Towers framed by LCL→LFC→EL and instability along route.",
            groups: [.convection, .clouds],
            lines: ["lcl", "lfc", "el", "freezing-level", "minus-10c", "minus-20c"]),
        "turbulence": AdvisoryPreset(
            id: "turbulence", label: "Turbulence",
            caption: "CAT/shear layers near cruise; terrain + wind for orographic risk.",
            groups: [.turbulence], lines: ["inversion-bands"]),
        "vfr": AdvisoryPreset(
            id: "vfr", label: "VFR feasibility",
            caption: "VMC picture: clouds & obscuration vs cruise and airports.",
            // `surface-obscuration-bands` has no iOS layer: kept for parity with
            // the web lens, dropped at apply time like `sld-bands`.
            groups: [.clouds], lines: ["surface-obscuration-bands", "freezing-level"]),
        "ifr": AdvisoryPreset(
            id: "ifr", label: "IFR feasibility",
            caption: "IFR hazards: icing + convection + cloud along route.",
            groups: [.icing, .convection, .clouds], lines: ["freezing-level", "minus-10c"]),
    ]

    static var advisoryList: [AdvisoryPreset] { advisoryOrder.compactMap { advisory[$0] } }

    /// advisory_id → lens id (card chips). Mirrors web ADVISORY_TO_PRESET.
    static let advisoryToPreset: [String: String] = [
        "icing_escape": "icing", "fiki_icing": "icing", "freezing_precip": "icing",
        "cloud_top": "clouds", "vmc_cruise": "clouds",
        "convective": "convective",
        "turbulence": "turbulence", "mountain_wind": "turbulence",
        "vfr_feasibility": "vfr", "ifr_feasibility": "ifr",
        // enroute_precip is a visibility proxy → the VFR lens; the web
        // override's routeGraph swap has no iOS equivalent (#375).
        "enroute_precip": "vfr",
    ]

    /// Per-advisory extras unioned onto the base lens. Mirrors web
    /// `ADVISORY_OVERRIDES`: e.g. FIKI icing adds warm-nose isotherms. iOS-missing
    /// line ids (e.g. `sld-bands`) are kept here for parity and dropped at apply
    /// time.
    static let advisoryOverrides: [String: (groups: [LayerGroup], lines: [String])] = [
        "fiki_icing": (groups: [], lines: ["minus-10c", "minus-20c", "sld-bands"]),
        "freezing_precip": (groups: [], lines: ["sld-bands"]),
    ]

    /// The lens a given advisory's chip should apply, or nil if it has no chip.
    /// Unions `advisoryOverrides` onto the base lens (mirrors web
    /// `getPresetForAdvisory`).
    static func preset(forAdvisory advisoryId: String) -> AdvisoryPreset? {
        guard let presetId = advisoryToPreset[advisoryId], var p = advisory[presetId] else { return nil }
        if let o = advisoryOverrides[advisoryId] {
            p.groups += o.groups
            p.lines += o.lines
        }
        return p
    }

    // MARK: - Cloud axes (source × style ⇄ layer id)

    /// Every cloud band id, NWP first.
    static let cloudLayerIds: [String] = CloudSource.allCases.flatMap { source in
        CloudStyle.allCases.map { cloudLayerId(source: source, style: $0) }
    }

    /// (source, style) → cloud layer id.
    static func cloudLayerId(source: CloudSource, style: CloudStyle) -> String {
        switch (style, source) {
        case (.soft, .nwp): "soft-nwp-cloud-bands"
        case (.soft, .dd): "soft-cloud-bands"
        case (.natural, .nwp): "nwp-cloud-bands"
        case (.natural, .dd): "cloud-bands"
        case (.square, .nwp): "square-nwp-cloud-bands"
        case (.square, .dd): "square-cloud-bands"
        }
    }

    /// cloud layer id → (source, style), or nil if not a cloud layer.
    static func parseCloudLayerId(_ id: String) -> (source: CloudSource, style: CloudStyle)? {
        for source in CloudSource.allCases {
            for style in CloudStyle.allCases where cloudLayerId(source: source, style: style) == id {
                return (source, style)
            }
        }
        return nil
    }
}
