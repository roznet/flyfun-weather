import Foundation

/// Hour + 15-minute split of a flight duration.
///
/// Swift mirror of `web/ts/utils/duration.ts` — a hand-copied surface, so keep
/// the two in step (`/sync-ios-web` exists to catch drift). The model stores
/// `flight_duration_hours` as decimal hours; both clients present it as a
/// whole-hour picker plus a quarter-hour picker, so a 1h15 flight round-trips as
/// 1.25 rather than being coerced to the nearest half hour.
enum FlightDuration {
    /// Selectable minute values — quarter-hour granularity.
    static let minuteOptions = [0, 15, 30, 45]

    /// Largest whole hour the hour picker offers.
    static let maxHours = 12

    /// Largest duration the pickers can represent, in decimal hours (12h45).
    static let maxDecimalHours = Double(maxHours) + 45.0 / 60.0

    struct Parts: Equatable {
        var hours: Int
        var minutes: Int
    }

    /// Split decimal hours into hours + minutes, rounding **up** to the next
    /// quarter hour (never shorter) and clamping to the 12h45 picker ceiling.
    ///
    /// A still-air estimate of 1h02 therefore shows as 1h15: we never advertise a
    /// flight window shorter than the computed time. Non-positive / non-finite
    /// input → 0h00.
    static func split(_ decimalHours: Double) -> Parts {
        guard decimalHours.isFinite, decimalHours > 0 else { return Parts(hours: 0, minutes: 0) }
        let capped = Swift.min(decimalHours, maxDecimalHours)
        // Count quarter-hour units, rounding up. The epsilon absorbs float error
        // so exact multiples (0.75 h → 3.0 quarters) don't tip into the next unit.
        let quarters = Int((capped * 4 - 1e-9).rounded(.up))
        let totalMinutes = quarters * 15
        return Parts(hours: totalMinutes / 60, minutes: totalMinutes % 60)
    }

    /// Recombine hours + minutes into the decimal hours the model stores.
    static func combine(hours: Int, minutes: Int) -> Double {
        Double(hours) + Double(minutes) / 60.0
    }

    /// Compact "1h15" / "2h" label for read-only display. Rounds up via `split`,
    /// so the label always agrees with what the pickers show for the same value.
    static func label(_ decimalHours: Double) -> String {
        let parts = split(decimalHours)
        return parts.minutes > 0
            ? String(format: "%dh%02d", parts.hours, parts.minutes)
            : "\(parts.hours)h"
    }
}
