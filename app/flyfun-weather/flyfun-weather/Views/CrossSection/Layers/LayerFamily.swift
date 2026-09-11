import SwiftUI

/// One control on the cross-section layer bar: a question the pilot asks, which
/// may span more than one registry group. Port of web `LayerFamily` /
/// `FAMILY_GROUPS` / `familySummary` (#591; iOS #605).
///
/// `LayerGroup` stays the finer tier — a family's detail row shows one
/// sub-heading per group it owns. `terrain` (always drawn) and `highlight`
/// (driven by an advisory, not the user) belong to no family, deliberately.
///
/// iOS has no obscuration / sun / fronts groups and no IENG, E-shear or SLD
/// layers, so some families are shorter here; all seven chips stay, so the two
/// clients use the same vocabulary.
enum LayerFamily: String, CaseIterable, Identifiable {
    case clouds, convection, icing, turbulence, levels, stability, observed

    var id: String { rawValue }

    var label: String {
        switch self {
        case .clouds: "Clouds"
        case .convection: "Convection"
        case .icing: "Icing"
        case .turbulence: "Turbulence"
        case .levels: "Levels"
        case .stability: "Stability"
        case .observed: "Observed"
        }
    }

    /// The groups this family owns, in detail-row reading order.
    var groups: [LayerGroup] {
        switch self {
        case .clouds: [.clouds]
        case .convection: [.convection]
        case .icing: [.icing]
        case .turbulence: [.turbulence]
        case .levels: [.temperature, .reference]
        case .stability: [.stability]
        case .observed: [.conditions]
        }
    }

    /// Groups that intentionally belong to no family — kept explicit so the
    /// completeness test can tell "excluded on purpose" from "forgotten".
    static let familylessGroups: Set<LayerGroup> = [.terrain, .highlight]

    /// Which family owns a group, or nil for the deliberately family-less ones.
    static func family(for group: LayerGroup) -> LayerFamily? {
        allCases.first { $0.groups.contains(group) }
    }

    /// Every layer id in the family, in pill order.
    var layerIds: [String] { groups.flatMap { CrossSectionLayer.layerIds(in: $0) } }

    /// One sentence on what the family is: the detail row's hint line. Ported
    /// from web `viz.familyHint.*`, with the counts corrected to the layers iOS
    /// actually has.
    var hint: String {
        switch self {
        case .clouds: "Model cloud along the route. Two independent derivations — where they disagree is information."
        case .convection: "Two independent schemes. Where they dissent is itself the signal."
        case .icing: "Three indices of one hazard. They split on where the cloud comes from."
        case .turbulence: "Clear-air turbulence read off the wind shear."
        case .levels: "Reference lines. Everything else on the chart is measured against these."
        case .stability: "Parcel levels read off the sounding: where it clouds up, and how far it gets."
        case .observed: "Ground truth from radar and satellite — valid now, not a forecast."
        }
    }

    /// The About panel's framing paragraph (web `viz.familyAbout.*`) — nil where
    /// there is nothing comparative to say (one layer, or reference lines).
    var aboutIntro: String? {
        switch self {
        case .clouds: "Neither source is the truth. DD is what the sounding says about moisture; NWP is what the model says about cloud. Switch both on and the overlap is the cross-check."
        case .convection: "The two schemes disagree in a patterned way, and the pattern is diagnostic: NWP tends to fire early on the day, the thermo scheme late."
        case .icing: "They split on one question: where does the cloud come from? Some read moisture out of the sounding, some take the model's word for it. Overlaying one of each is the useful comparison."
        case .stability: "LCL, LFC and EL are one narrative: where a rising parcel clouds up, where it stops needing help, and where it runs out."
        case .observed: "Each instrument has its own age. They are not alternatives to each other, and never to the model — they are the check on it."
        case .turbulence, .levels: nil
        }
    }

    /// The colour the family paints in, so the bar doubles as a key for the chart.
    var dotColor: Color {
        switch self {
        case .clouds: Color(.sRGB, red: 0.55, green: 0.55, blue: 0.6, opacity: 0.9)
        case .convection: ColorScales.convectiveTowerFill("high")
        case .icing: ColorScales.icingRiskColor("moderate")
        case .turbulence: ColorScales.catRiskColor("moderate")
        case .levels: ColorScales.freezingLevelColor
        case .stability: ColorScales.lclColor
        // Mid-ramp green: one swatch standing for the whole dBZ scale.
        case .observed: ObservedSurfaceLayer.echoColor(25)
        }
    }

    /// What the family's chip reads on the bar. Names the answers while they fit
    /// — up to two, joined with "+" — then falls back to a count; nothing on reads
    /// "off", so "nothing here" and "I have not looked" cannot be confused.
    /// Clouds collapse their six band ids to the source, with the style as a
    /// qualifier (`NWP + DD · Square`). Mirrors web `familySummary`.
    func summary(enabledLayers: [String: Bool], cloudStyle: CloudStyle) -> (text: String, off: Bool) {
        let on = layerIds.filter { enabledLayers[$0] == true }
        guard !on.isEmpty else { return ("off", true) }

        var names: [String] = []
        var sources: [String] = []
        for id in on {
            if let axes = CrossSectionPresets.parseCloudLayerId(id) {
                if !sources.contains(axes.source.label) { sources.append(axes.source.label) }
            } else {
                names.append(CrossSectionLayer.shortLabel(id))
            }
        }

        var parts: [String] = []
        if !sources.isEmpty { parts.append(sources.joined(separator: " + ") + " · " + cloudStyle.label) }
        if names.count > 2 {
            parts.append("\(names.count) on")
        } else if !names.isEmpty {
            parts.append(names.joined(separator: " + "))
        }
        return (parts.joined(separator: " · "), false)
    }
}
