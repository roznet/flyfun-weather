import Foundation

/// One climb / descent preset, from `GET /api/aircraft/presets` (#1005). The
/// table lives on the server only; the app never copies its values.
nonisolated struct AircraftPerformancePreset: Codable, Hashable, Identifiable, Sendable {
    let id: String
    let climbIasKt: Double
    let climbRateFpm: Double
    let descentMode: String
    let descentRateFpm: Double?
    let descentAngleDeg: Double?
    let turbo: Bool
}

/// `GET /api/aircraft/presets`: the table and the preset the ICAO type suggests.
nonisolated struct AircraftPresetsResponse: Codable, Sendable {
    let presets: [AircraftPerformancePreset]
    let suggested: String
}

/// The resolved climb / descent performance the briefing's path flies with.
/// Only `preset` and `descentMode` are required: this block is nested in
/// `AircraftResponse`, so a missing figure must not stop the aircraft list loading.
nonisolated struct AircraftPerformanceResponse: Codable, Hashable, Sendable {
    let preset: String
    let source: String?
    let climbIasKt: Double?
    let climbRateFpm: Double?
    let ceilingFt: Double?
    let descentMode: String
    let descentRateFpm: Double?
    let descentAngleDeg: Double?
    let turbo: Bool?
}

/// What the form's four number fields and the turbo switch hold. nil = empty.
nonisolated struct AircraftPerformanceValues: Equatable, Sendable {
    var climbIasKt: Double?
    var climbRateFpm: Double?
    var descentRateFpm: Double?
    var descentAngleDeg: Double?
    var turbo: Bool
}

/// The save payload: the base preset, and only the values that differ from it.
nonisolated struct AircraftPerformanceOverrides: Equatable, Sendable {
    let performancePreset: String
    let isTurbo: Bool
    let climbIasKt: Int
    let climbRateFpm: Int
    let descentRateFpm: Int
    let descentAngleDeg: Double
}

/// Port of the pure rules in web `aircraft-performance-form.ts`.
/// SYNC: `web/ts/components/aircraft-performance-form.ts` and Android
/// `model/rules/AircraftPerformanceForm.kt`. Change the three together.
nonisolated enum AircraftPerformanceForm {
    /// A preset's own values, as the form shows them (the unused descent field empty).
    static func values(of preset: AircraftPerformancePreset) -> AircraftPerformanceValues {
        AircraftPerformanceValues(
            climbIasKt: preset.climbIasKt,
            climbRateFpm: preset.climbRateFpm,
            descentRateFpm: preset.descentMode == "rate" ? preset.descentRateFpm : nil,
            descentAngleDeg: preset.descentMode == "angle" ? preset.descentAngleDeg : nil,
            turbo: preset.turbo
        )
    }

    /// The resolved performance of a saved aircraft, as the form opens on it.
    static func values(of performance: AircraftPerformanceResponse) -> AircraftPerformanceValues {
        AircraftPerformanceValues(
            climbIasKt: performance.climbIasKt,
            climbRateFpm: performance.climbRateFpm,
            descentRateFpm: performance.descentMode == "rate" ? performance.descentRateFpm : nil,
            descentAngleDeg: performance.descentMode == "angle" ? performance.descentAngleDeg : nil,
            turbo: performance.turbo ?? false
        )
    }

    /// True when a filled field or the turbo switch differs from the preset. An
    /// empty field means "the preset's value", so it never makes it custom.
    static func isCustom(_ values: AircraftPerformanceValues, base: AircraftPerformancePreset) -> Bool {
        let own = Self.values(of: base)
        if values.turbo != own.turbo { return true }
        let pairs = [
            (values.climbIasKt, own.climbIasKt),
            (values.climbRateFpm, own.climbRateFpm),
            (values.descentRateFpm, own.descentRateFpm),
            (values.descentAngleDeg, own.descentAngleDeg),
        ]
        return pairs.contains { value, preset in value != nil && value != preset }
    }

    /// 0 = no override, which also clears an earlier one on update.
    static func overrides(for values: AircraftPerformanceValues,
                          base: AircraftPerformancePreset) -> AircraftPerformanceOverrides {
        let own = Self.values(of: base)
        func diff(_ value: Double?, _ preset: Double?) -> Double {
            guard let value, value > 0, value != preset else { return 0 }
            return value
        }
        return AircraftPerformanceOverrides(
            performancePreset: base.id,
            isTurbo: values.turbo,
            climbIasKt: Int(diff(values.climbIasKt, own.climbIasKt).rounded()),
            climbRateFpm: Int(diff(values.climbRateFpm, own.climbRateFpm).rounded()),
            descentRateFpm: Int(diff(values.descentRateFpm, own.descentRateFpm).rounded()),
            descentAngleDeg: diff(values.descentAngleDeg, own.descentAngleDeg)
        )
    }

    /// A field's number: nil when empty, unreadable or not positive. Accepts the
    /// decimal comma a French or German keypad types.
    static func number(from text: String) -> Double? {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".")
        guard let value = Double(cleaned), value.isFinite, value > 0 else { return nil }
        return value
    }

    /// A value as the field shows it: at most one decimal, no trailing `.0`.
    static func text(from value: Double?) -> String {
        guard let value else { return "" }
        let rounded = (value * 10).rounded() / 10
        return rounded == rounded.rounded() ? String(Int(rounded)) : String(rounded)
    }

    static func presetName(_ id: String) -> String {
        switch id {
        case "trainer_piston": "Trainer piston"
        case "hp_piston": "High-performance piston"
        case "turbo_piston": "Turbocharged piston"
        case "twin_piston": "Twin piston"
        case "single_turboprop": "Single turboprop"
        case "twin_turboprop": "Twin turboprop"
        case "light_jet": "Light jet"
        default:
            id.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}
