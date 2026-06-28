import RZSkewT
import SwiftUI

/// Host-supplied Skew-T side-panel variables (§4.8 Tier 3).
///
/// Per the §1E split, derivations and units stay on the host: the RZSkewT package
/// only plots a `value` closure against pressure. The package's `SoundingLevel`
/// carries just pressure/altitude/T/Td/wind, so closures that need an extended
/// field (RH, θe, Ri, ω, cloud, icing…) look it up by pressure from the host
/// response — keyed on `pressureHpa`, exactly how the package level's
/// `pressureHPa` was built in `toSoundingProfile()`.
enum SkewTVariableCatalog {
    /// Offerable variables for a sounding, in display order. Variables with no
    /// data for *this* sounding are dropped so the picker only lists ones that
    /// actually plot. `levels` are the package levels of the built profile, used
    /// both for the closures' key space and the presence filter.
    static func variables(for response: SoundingProfileResponse, levels: [SoundingLevel]) -> [SkewTVariable] {
        let byPressure = Dictionary(
            response.levels.map { (Int($0.pressureHpa), $0) },
            uniquingKeysWith: { first, _ in first }
        )
        func ext(_ level: SoundingLevel) -> SoundingProfileLevel? { byPressure[Int(level.pressureHPa.rounded())] }

        let all: [SkewTVariable] = [
            SkewTVariable(id: "rh", label: "RH", unit: "%", color: .blue, range: 0...100) { ext($0)?.relativeHumidityPct },
            SkewTVariable(id: "thetae", label: "θe", unit: "K", color: .orange) { ext($0)?.thetaEK },
            SkewTVariable(id: "wind", label: "Wind", unit: "kt", color: .purple) { $0.windSpeedKt },
            SkewTVariable(id: "lapse", label: "Lapse", unit: "°C/km", color: .teal) { ext($0)?.lapseRateCPerKm },
            SkewTVariable(id: "ri", label: "Ri", color: .brown) { ext($0)?.richardsonNumber },
            SkewTVariable(id: "omega", label: "ω", unit: "Pa/s", color: .indigo) { ext($0)?.omegaPaS },
            SkewTVariable(id: "cloud", label: "Cloud", unit: "%", color: .gray, range: 0...100) { ext($0)?.cloudAreaFractionPct },
            // Icing kept source-explicit (matches the web panel): Ogimet NWP vs
            // DD vs SFIP are distinct variables, not a silent NWP→DD fallback.
            // Absent ones are dropped per-sounding by the presence filter below
            // (e.g. GFS without NWP icing just shows fewer entries).
            SkewTVariable(id: "icing-nwp", label: "Icing (NWP)", color: .cyan) { ext($0)?.icingIndexNwp },
            SkewTVariable(id: "icing-dd", label: "Icing (DD)", color: .cyan.opacity(0.6)) { ext($0)?.icingIndex },
            SkewTVariable(id: "sfip", label: "SFIP", color: .purple) { ext($0)?.sfip100 },
            SkewTVariable(id: "ice", label: "ICE", unit: "g/kg", color: .cyan) { ext($0)?.iceMixingRatioGKg },
            SkewTVariable(id: "clw", label: "CLW", unit: "g/m³", color: .mint) { ext($0)?.cloudLiquidWaterGM3 },
        ]
        // Reuse each variable's own closure for the presence check (no duplicated
        // field mapping): keep it only if some level yields a value.
        return all.filter { v in levels.contains { v.value($0) != nil } }
    }
}
