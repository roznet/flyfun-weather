import Foundation

/// Response of `GET /api/maps/forecast/days` — the ragged day/hour/model grid the
/// pickers are drawn from. **Never hardcode the grid**: the horizon runs D+0…D+6,
/// sample hours are `06/09/12/15/18Z` on the near days but only `06/12/18Z` on
/// D+6, and ICON is absent on D+5/D+6. Each day reports exactly what it holds so
/// a model that can't reach the selected day reads as "greyed, not agreement".
///
/// Decoded with `JSONDecoder.weatherBrief` (snake→camel) — no dynamic-key dicts
/// here, so the key-conversion trap that bites `ForecastMapResponse` doesn't apply.
struct ForecastDaysResponse: Decodable, Sendable {
    let days: [ForecastDay]
    let maxDay: Int
}

/// One relative day's availability. `day` is a relative integer (0 = today) —
/// the same value `fc.day` carries in a share link, so links keep resolving.
struct ForecastDay: Decodable, Sendable, Identifiable {
    /// Relative day offset, 0 = today.
    let day: Int
    /// ISO date "YYYY-MM-DD".
    let date: String
    /// False when the day is beyond the current model horizon.
    let available: Bool
    /// Sorted UTC sample hours that actually have data.
    let hours: [Int]
    /// Sorted distinct model names present across those hours (gfs/icon/ecmwf).
    let models: [String]

    var id: Int { day }
}
