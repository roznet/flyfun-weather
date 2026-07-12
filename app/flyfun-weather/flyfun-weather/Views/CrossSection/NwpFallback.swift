import Foundation

// =============================================================================
// SYNC — keep this file in lockstep with the web NWP-fallback logic:
//   • Availability detection (`unavailableLayers`):
//       web/ts/visualization/data-extract.ts (getUnavailableLayers)
//   • Substitution + fallback map (`applyFallback` / `ddSubstituteId` / maps):
//       web/ts/visualization/cross-section/nwp-fallback.ts
//         (applyNwpFallback, getDdSubstituteId, SINGLE_LAYER_FALLBACK,
//          NWP_CLOUDS_SIGNAL, ALL_CLOUD_LAYER_IDS)
//
// iOS uses its own layer IDs and lacks a few web layers (ieng / sld / e-shear /
// current-conditions / fronts) — those are intentionally absent here. When the
// web fallback logic changes, update this file, and note it on the web side
// (both files carry the reciprocal SYNC comment).
// =============================================================================

/// Render-time NWP→fallback substitution + availability detection for models
/// without native NWP data. Port of web's `data-extract.ts:getUnavailableLayers`
/// and `cross-section/nwp-fallback.ts`.
///
/// When the selected model can't provide a method the user (or a preset) asked
/// for — e.g. ECMWF far enough out that no native 3-D cloud fraction was
/// enriched, so `nwpCloudLayers` is nil at every point — we (a) mark the layer
/// unavailable so the config sheet can grey it out, and (b) substitute the
/// method shown instead so the layer never silently renders nothing: NWP clouds
/// → same-style DD clouds, NWP/SFIP icing → Ogimet-DD (sounding-derived, always
/// available), NWP convective → thermodynamic (CAPE/CIN) scheme. This mirrors
/// the backend's `_resolve_analyses` (advise.py) and the web client, so iOS,
/// web, and the advisories all agree.
///
/// Works purely on a throwaway effective-enable map — the stored `enabledLayers`
/// preference is never mutated, so switching back to an NWP-capable model
/// auto-restores NWP with no persisted "downgraded" flag.
enum NwpFallback {
    /// Canonical "NWP clouds unavailable" signal — one id that stands for every
    /// NWP cloud variant (soft/natural/square all read the same native feed).
    /// Mirrors web `NWP_CLOUDS_SIGNAL`.
    static let nwpCloudsSignal = "nwp-cloud-bands"
    static let ogimetDd = "icing-bands"

    /// NWP→fallback substitutions for single (non-cloud) layers. Icing falls back
    /// to Ogimet-DD (always available from the sounding); NWP convective falls
    /// back to the thermodynamic scheme — both mirror `_resolve_analyses`. Clouds
    /// are handled separately because they fan out across styles.
    /// (iOS has no IENG layer, so — like web — it is intentionally absent and
    /// simply stays off.)
    static let singleLayerFallback: [String: String] = [
        "icing-ogimet-nwp-bands": ogimetDd,        // Ogimet-NWP → Ogimet-DD
        "sfip-bands": ogimetDd,                     // SFIP-NWP   → Ogimet-DD
        "nwp-convective-bg": "thermo-convective-bg", // NWP convective → thermodynamic
    ]

    /// All six cloud layer ids (source × style).
    static let allCloudLayerIds: [String] = {
        var ids: [String] = []
        for source in CloudSource.allCases {
            for style in CloudStyle.allCases {
                ids.append(CrossSectionPresets.cloudLayerId(source: source, style: style))
            }
        }
        return ids
    }()

    /// Layer ids that lack data for the current model → disabled in the config
    /// sheet (and drive the DD substitution). Mirrors web `getUnavailableLayers`.
    ///
    /// iOS omits the web layers it doesn't have (ieng / sld / e-shear /
    /// current-conditions / fronts).
    static func unavailableLayers(in data: VizRouteData) -> Set<String> {
        var unavailable = Set<String>()

        // Native NWP cloud info gates the NWP cloud toggle AND the NWP-cloud-gated
        // icing (Ogimet-NWP). `nwpCloudLayers == nil` = "no NWP enrichment";
        // `[]` = "model says clear sky" (still available). So gate on "any point
        // has non-nil native NWP cloud data".
        let hasNwpCloudData = data.points.contains { $0.nwpCloudLayers != nil }
        let hasSfip = data.points.contains { !$0.sfipZones.isEmpty }
        let hasNwpConvective = data.points.contains { $0.hasNwpConvective }

        if !hasNwpCloudData {
            unavailable.insert(nwpCloudsSignal)
            unavailable.insert("icing-ogimet-nwp-bands")
        }
        if !hasSfip { unavailable.insert("sfip-bands") }
        if !hasNwpConvective { unavailable.insert("nwp-convective-bg") }

        return unavailable
    }

    /// Substitute layer shown when the NWP layer `id` is unavailable, or nil if it
    /// has no fallback (DD layers, non-cloud/icing/convective layers). Covers the
    /// single-layer map above plus same-style DD clouds. Mirrors web
    /// `getDdSubstituteId`.
    static func ddSubstituteId(for id: String) -> String? {
        if let single = singleLayerFallback[id] { return single }
        if let axes = CrossSectionPresets.parseCloudLayerId(id), axes.source == .nwp {
            return CrossSectionPresets.cloudLayerId(source: .dd, style: axes.style)
        }
        return nil
    }

    /// Build the throwaway effective-enable map for one render: start from the
    /// stored pref, disable what the model can't provide, then substitute the
    /// fallback method for any wanted-but-unavailable NWP layer. Never mutates.
    /// Mirrors web `applyNwpFallback`.
    static func applyFallback(
        enabledLayers: [String: Bool],
        unavailable: Set<String>
    ) -> [String: Bool] {
        var effective = enabledLayers
        for id in unavailable { effective[id] = false }

        if unavailable.contains(nwpCloudsSignal) {
            for id in allCloudLayerIds where enabledLayers[id] == true {
                guard let ddId = ddSubstituteId(for: id) else { continue }
                effective[id] = false  // the replaced NWP variant must not stay enabled
                if enabledLayers[ddId] != true { effective[ddId] = true }
            }
        }

        // Single (non-cloud) NWP layers: substitute the fallback method when the
        // model can't provide the layer and the user hasn't already enabled that
        // fallback.
        for (nwpId, fallbackId) in singleLayerFallback
        where unavailable.contains(nwpId)
            && enabledLayers[nwpId] == true
            && enabledLayers[fallbackId] != true {
            effective[fallbackId] = true
        }

        return effective
    }
}
