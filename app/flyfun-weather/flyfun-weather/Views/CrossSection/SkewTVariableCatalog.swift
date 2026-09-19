import Foundation
import RZSkewT
import SwiftUI

/// One labelled group of side-panel variables (mirrors the web's optgroups).
struct SkewTVarGroup: Identifiable {
    let label: String
    let variables: [SkewTVariable]
    var id: String { label }
}

// =============================================================================
// SYNC — keep this catalog in lockstep with the web Skew-T side panel:
//   web/ts/visualization/skewt/variable-panel.ts
//     (VARIABLE_REGISTRY, VARIABLE_GROUPS)
//
// Compare the variable set, the group membership and within-group order, the
// fixed ranges, the zero lines, the colours, the short labels, and the
// `metricId` → `helpMetricId` pointers. The web file carries the reciprocal
// comment.
//
// KNOWN DIVERGENCE — the variable IDs differ from the web's (`rh` vs
// `relative_humidity`, `w` vs `vertical_velocity`, `ri` vs `richardson`, …).
// Harmless while nothing crosses the boundary, but the web advisory lenses name
// WEB ids in their `skewtSidePanel` directive, so that directive cannot be
// ported until the ids are reconciled. Do not add new ids in the iOS-short
// style; use the web id.
// =============================================================================

/// Host-supplied Skew-T side-panel variables (§4.8 Tier 3), kept in sync with the
/// web side panel (`web/ts/visualization/skewt/variable-panel.ts`).
///
/// Per the §1E split, derivations/units stay on the host: the RZSkewT package only
/// plots `value` (and an optional `secondaryValue`) against pressure. The package
/// `SoundingLevel` carries p/alt/T/Td/wind, so closures needing an extended field
/// (RH, θe, icing, cloud…) look it up by pressure from the host response; HW/XW is
/// derived from the level's own wind + the route point's track.
enum SkewTVariableCatalog {
    /// Variables grouped for display, in the same order/grouping as the web panel.
    /// Variables with no data for *this* sounding are dropped (and empty groups
    /// with them) so the picker only lists ones that actually plot.
    static func grouped(for response: SoundingProfileResponse,
                        levels: [SoundingLevel],
                        trackDeg: Double?) -> [SkewTVarGroup] {
        let byPressure = Dictionary(
            response.levels.map { (Int($0.pressureHpa), $0) },
            uniquingKeysWith: { first, _ in first }
        )
        func ext(_ level: SoundingLevel) -> SoundingProfileLevel? { byPressure[Int(level.pressureHPa.rounded())] }

        // Headwind / crosswind component relative to track (positive HW = into
        // the nose; positive XW = from the right). nil when track or wind absent.
        func component(_ level: SoundingLevel, cross: Bool) -> Double? {
            guard let ws = level.windSpeedKt, let wd = level.windDirectionDeg, let track = trackDeg else { return nil }
            let rel = (wd - track) * .pi / 180
            return ws * (cross ? sin(rel) : cos(rel))
        }

        let groups: [SkewTVarGroup] = [
            SkewTVarGroup(label: "Wind", variables: [
                SkewTVariable(id: "headwind", label: "Headwind / Crosswind", unit: "kt", color: Self.webColor(0xd04040), zeroLine: true,
                              secondaryValue: { component($0, cross: true) },
                              secondaryColor: Self.webColor(0x2080d0)) {
                    component($0, cross: false)
                },
                SkewTVariable(id: "wind_speed", label: "Wind Speed", unit: "kt",
                              color: Self.webColor(0x6060c0)) { $0.windSpeedKt },
            ]),
            SkewTVarGroup(label: "Moisture & Cloud", variables: [
                SkewTVariable(id: "dewpoint_depression", label: "Dewpoint Depression", unit: "°C",
                              color: Self.webColor(0xe07020), range: 0...15) {
                    ext($0)?.dewpointDepressionC
                },
                SkewTVariable(id: "rh", label: "Relative Humidity", unit: "%",
                              color: Self.webColor(0x2090d0), range: 0...100) { ext($0)?.relativeHumidityPct },
                SkewTVariable(id: "cloud", label: "Cloud Cover", unit: "%",
                              color: Self.webColor(0x20c0e0), range: 0...100) { ext($0)?.cloudAreaFractionPct },
                SkewTVariable(id: "clw", label: "Cloud Liquid Water", unit: "g/m³",
                              color: Self.webColor(0x20a0a0)) { ext($0)?.cloudLiquidWaterGM3 },
                SkewTVariable(id: "ice", label: "Ice Mixing Ratio", unit: "g/kg",
                              color: Self.webColor(0x8080d0)) { ext($0)?.iceMixingRatioGKg },
            ]),
            SkewTVarGroup(label: "Icing", variables: [
                SkewTVariable(id: "icing-dd", label: "Icing (Ogimet-DD)",
                              color: Self.webColor(0x6495ed), range: 0...100) { ext($0)?.icingIndex },
                SkewTVariable(id: "icing-nwp", label: "Icing (Ogimet-NWP)",
                              color: Self.webColor(0x4080d0), range: 0...100) { ext($0)?.icingIndexNwp },
                SkewTVariable(id: "sfip", label: "SFIP Index",
                              color: Self.webColor(0xd08020), range: 0...100) { ext($0)?.sfip100 },
            ]),
            SkewTVarGroup(label: "Stability & Vertical", variables: [
                SkewTVariable(id: "lapse", label: "Lapse Rate", unit: "°C/km",
                              color: Self.webColor(0xc04040), zeroLine: true) { ext($0)?.lapseRateCPerKm },
                // Web hides Ri ≥ 100 (effectively "very stable / no shear signal").
                SkewTVariable(id: "ri", label: "Richardson Number", color: Self.webColor(0xd0a020)) {
                    if let r = ext($0)?.richardsonNumber, r < 100 { return r }
                    return nil
                },
                SkewTVariable(id: "w", label: "Vertical Velocity", unit: "ft/min",
                              color: Self.webColor(0x40a040), zeroLine: true) { ext($0)?.wFpm },
                SkewTVariable(id: "thetae", label: "Equiv. Pot. Temp.", unit: "K",
                              color: Self.webColor(0xa04080)) { ext($0)?.thetaEK },
            ]),
        ]

        // Drop variables with no plottable data for this sounding (primary OR the
        // optional secondary line), then drop any group left empty.
        return groups.compactMap { group in
            let kept = group.variables.filter { v in
                levels.contains { v.value($0) != nil || (v.secondaryValue?($0) != nil) }
            }
            return kept.isEmpty ? nil : SkewTVarGroup(label: group.label, variables: kept)
        }
    }

    /// Flat list of offerable variables, in display order.
    static func variables(for response: SoundingProfileResponse,
                          levels: [SoundingLevel],
                          trackDeg: Double?) -> [SkewTVariable] {
        grouped(for: response, levels: levels, trackDeg: trackDeg).flatMap(\.variables)
    }

    /// Terse label for the collapsed chip and the plotted axis, mirroring the
    /// web's `VariableDef.shortLabel`. The descriptive `SkewTVariable.label` is
    /// what the picker menu shows — the web dropdown renders both, as
    /// "<shortLabel> — <label>", and one field cannot serve a 110px axis caption
    /// and a menu row at once.
    static let shortLabel: [String: String] = [
        "headwind": "HW/XW",
        "wind_speed": "Wind",
        "dewpoint_depression": "DD",
        "rh": "RH",
        "cloud": "CC",
        "clw": "CLW",
        "ice": "ICE",
        "icing-dd": "Ice-DD",
        "icing-nwp": "Ice-NWP",
        "sfip": "SFIP",
        "lapse": "Γ",
        "ri": "Ri",
        "w": "w",
        "thetae": "θe",
    ]

    /// A web hex colour, so the two panels draw a variable in the same ink.
    /// Taken verbatim from `VARIABLE_REGISTRY`; SwiftUI's semantic colours drifted
    /// far enough that SFIP was orange on the web and indigo here, and `ICE` and
    /// `Ice-NWP` shared one cyan.
    static func webColor(_ hex: UInt32) -> Color {
        Color(.sRGB,
              red: Double((hex >> 16) & 0xff) / 255,
              green: Double((hex >> 8) & 0xff) / 255,
              blue: Double(hex & 0xff) / 255)
    }

    /// Maps a side-panel variable id to its help-catalog metric id (#311).
    /// These are id→id pointers only — the help *content* lives in the catalog,
    /// never here. A variable with no mapping (or whose metric isn't cached) just
    /// shows no (i) button.
    static let helpMetricId: [String: String] = [
        "headwind": "skewt_headwind_crosswind",
        "wind_speed": "wind_speed_kt",
        "dewpoint_depression": "dewpoint_depression_c",
        "rh": "skewt_relative_humidity",
        // NOT `cloud_cover_pct`: that entry is the TOTAL-COLUMN cover, and its own
        // limitations text says it "doesn't tell you at which altitude the clouds
        // are" — exactly the wrong help for a per-level variable.
        "cloud": "skewt_cloud_area_fraction",
        "clw": "skewt_cloud_liquid_water",
        "ice": "skewt_ice_mixing_ratio",
        "icing-dd": "icing_risk",
        "icing-nwp": "icing_ogimet_nwp_risk",
        "sfip": "sfip_risk",
        "lapse": "lapse_rate_c_km",
        "ri": "richardson_number",
        "w": "skewt_vertical_velocity",
        "thetae": "equivalent_potential_temperature_k",
    ]
}
