import Foundation
import RZSkewT
import SwiftUI

/// One labelled group of side-panel variables (mirrors the web's optgroups).
struct SkewTVarGroup: Identifiable {
    let label: String
    let variables: [SkewTVariable]
    var id: String { label }
}

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
                SkewTVariable(id: "headwind", label: "HW/XW", unit: "kt", color: .red, zeroLine: true,
                              secondaryValue: { component($0, cross: true) }, secondaryColor: .blue) {
                    component($0, cross: false)
                },
                SkewTVariable(id: "wind_speed", label: "Wind", unit: "kt", color: .purple) { $0.windSpeedKt },
            ]),
            SkewTVarGroup(label: "Moisture & Cloud", variables: [
                SkewTVariable(id: "dewpoint_depression", label: "DD", unit: "°C", color: .orange, range: 0...15) {
                    ext($0)?.dewpointDepressionC
                },
                SkewTVariable(id: "rh", label: "RH", unit: "%", color: .blue, range: 0...100) { ext($0)?.relativeHumidityPct },
                SkewTVariable(id: "cloud", label: "Cloud", unit: "%", color: .gray, range: 0...100) { ext($0)?.cloudAreaFractionPct },
                SkewTVariable(id: "clw", label: "CLW", unit: "g/m³", color: .mint) { ext($0)?.cloudLiquidWaterGM3 },
                SkewTVariable(id: "ice", label: "ICE", unit: "g/kg", color: .cyan) { ext($0)?.iceMixingRatioGKg },
            ]),
            SkewTVarGroup(label: "Icing", variables: [
                SkewTVariable(id: "icing-dd", label: "Icing (DD)", color: .cyan.opacity(0.6), range: 0...100) { ext($0)?.icingIndex },
                SkewTVariable(id: "icing-nwp", label: "Icing (NWP)", color: .cyan, range: 0...100) { ext($0)?.icingIndexNwp },
                SkewTVariable(id: "sfip", label: "SFIP", color: .indigo, range: 0...100) { ext($0)?.sfip100 },
            ]),
            SkewTVarGroup(label: "Stability & Vertical", variables: [
                SkewTVariable(id: "lapse", label: "Lapse", unit: "°C/km", color: .teal, zeroLine: true) { ext($0)?.lapseRateCPerKm },
                // Web hides Ri ≥ 100 (effectively "very stable / no shear signal").
                SkewTVariable(id: "ri", label: "Ri", color: .brown) {
                    if let r = ext($0)?.richardsonNumber, r < 100 { return r }
                    return nil
                },
                SkewTVariable(id: "w", label: "Vert. vel.", unit: "ft/min", color: .green, zeroLine: true) { ext($0)?.wFpm },
                SkewTVariable(id: "thetae", label: "θe", unit: "K", color: .pink) { ext($0)?.thetaEK },
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
}
