import Foundation

/// Response of `GET /api/maps/forecast?day=&hour=` — every watchlist airport
/// (~619) with per-model forecasts plus **both** server-baked consensus blocks
/// (worst + majority). This is the one payload the forecast map colours from; a
/// metric or model switch is a pure client recolour off this data, only a
/// day/hour change refetches (see `designs/forecast-page.md`).
///
/// A superset of the trimmed `AirportWeatherResponse` (the Siri intent shape):
/// same airport object, widened with the full per-model block and the two
/// consensus blocks (#420).
///
/// ## Decoding note
/// Decode with a **plain** `JSONDecoder` (`ForecastMapResponse.decode(from:)`),
/// NOT `JSONDecoder.weatherBrief`. The `.convertFromSnakeCase` strategy rewrites
/// **dictionary keys**, which would turn the `agreement` map's field keys
/// (`wind_speed_kt` → `windSpeedKt`) and break the per-metric agreement lookup —
/// the same trap `HelpCatalogResponse` documents. Every struct here carries
/// explicit snake_case `CodingKeys` so a plain decoder reads it verbatim.
struct ForecastMapResponse: Decodable, Sendable {
    /// Valid time of this slot (ISO8601 UTC).
    let forecastTime: String?
    /// Per-model init time (ISO8601 UTC); keys are a subset of gfs/icon/ecmwf.
    let modelInitTimes: [String: String]
    let airports: [ForecastAirport]

    enum CodingKeys: String, CodingKey {
        case forecastTime = "forecast_time"
        case modelInitTimes = "model_init_times"
        case airports
    }

    init(forecastTime: String?, modelInitTimes: [String: String], airports: [ForecastAirport]) {
        self.forecastTime = forecastTime
        self.modelInitTimes = modelInitTimes
        self.airports = airports
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        forecastTime = try c.decodeIfPresent(String.self, forKey: .forecastTime)
        modelInitTimes = try c.decodeIfPresent([String: String].self, forKey: .modelInitTimes) ?? [:]
        airports = try c.decodeIfPresent([ForecastAirport].self, forKey: .airports) ?? []
    }

    /// Decode a raw `/maps/forecast` body with keys preserved verbatim.
    static func decode(from data: Data) throws -> ForecastMapResponse {
        try JSONDecoder().decode(ForecastMapResponse.self, from: data)
    }
}

/// One airport in the forecast map payload.
struct ForecastAirport: Decodable, Sendable, Identifiable {
    let icao: String
    let lat: Double
    let lon: Double
    /// Most-precise approach type, or nil when the field has no IAP/nav data.
    let approachType: String?
    /// Present models only (ICON is absent on D+5/D+6). Keys: gfs/icon/ecmwf.
    let models: [String: ForecastModelEntry]
    /// Worst-mode consensus (always present).
    let consensus: ForecastConsensus
    /// Majority-mode consensus (always present).
    let consensusMajority: ForecastConsensus
    /// D-0 only; carried on the wire but not rendered in v1.
    let observation: ForecastObservation?

    var id: String { icao }

    enum CodingKeys: String, CodingKey {
        case icao, lat, lon, models, consensus, observation
        case approachType = "approach_type"
        case consensusMajority = "consensus_majority"
    }

    /// The consensus block for a mode; falls back to worst when majority absent
    /// (mirrors the web `getConsensus`).
    func consensus(mode: ForecastModelMode) -> ForecastConsensus {
        mode == .majority ? consensusMajority : consensus
    }

    /// The cell data the active model/mode reads: a consensus block in a
    /// consensus mode, else the individual model entry (nil when that model is
    /// absent for this airport/hour).
    func cell(for mode: ForecastModelMode) -> (any ForecastCellData)? {
        switch mode {
        case .worst: return consensus
        case .majority: return consensusMajority
        case .model(let name): return models[name].map { $0 as any ForecastCellData }
        }
    }
}

/// Fields common to a per-model entry and a consensus block, addressed by the
/// catalog's string field names so colour evaluation is data-driven.
protocol ForecastCellData: Sendable {
    /// A banded numeric field (wind_speed_kt, ceiling_ft, …), nil when absent.
    func numericField(_ name: String) -> Double?
    /// A categorical field (flight_category / convective_risk).
    func categoryField(_ name: String) -> String?
    /// FAA/EASA alternate-required flags for this cell, nil when unknown.
    var altRequired: AltRequired? { get }
}

/// One model's forecast for an airport. Numeric fields are nullable; the wind
/// components and `alt_required` are key-absent (not null) when unavailable.
struct ForecastModelEntry: Decodable, Sendable, ForecastCellData {
    let ceilingFt: Double?
    let visibilityM: Double?
    let windSpeedKt: Double?
    let windDirDeg: Double?
    let windGustKt: Double?
    let cloudCoverPct: Double?
    let capeJkg: Double?
    /// Never null server-side ("none" fallback).
    let convectiveRisk: String?
    let temperatureC: Double?
    let flightCategory: String?
    let crosswindKt: Double?
    let headwindKt: Double?
    let bestRunwayId: String?
    let gustCrosswindKt: Double?
    let gustHeadwindKt: Double?
    let altRequired: AltRequired?

    enum CodingKeys: String, CodingKey {
        case ceilingFt = "ceiling_ft"
        case visibilityM = "visibility_m"
        case windSpeedKt = "wind_speed_kt"
        case windDirDeg = "wind_dir_deg"
        case windGustKt = "wind_gust_kt"
        case cloudCoverPct = "cloud_cover_pct"
        case capeJkg = "cape_jkg"
        case convectiveRisk = "convective_risk"
        case temperatureC = "temperature_c"
        case flightCategory = "flight_category"
        case crosswindKt = "crosswind_kt"
        case headwindKt = "headwind_kt"
        case bestRunwayId = "best_runway_id"
        case gustCrosswindKt = "gust_crosswind_kt"
        case gustHeadwindKt = "gust_headwind_kt"
        case altRequired = "alt_required"
    }

    func numericField(_ name: String) -> Double? {
        switch name {
        case "wind_speed_kt": return windSpeedKt
        case "crosswind_kt": return crosswindKt
        case "headwind_kt": return headwindKt
        case "ceiling_ft": return ceilingFt
        case "cape_jkg": return capeJkg
        case "visibility_m": return visibilityM
        case "cloud_cover_pct": return cloudCoverPct
        case "wind_dir_deg": return windDirDeg
        case "temperature_c": return temperatureC
        // Gusts — the card renders these alongside the steady value (as web does).
        case "wind_gust_kt": return windGustKt
        case "gust_crosswind_kt": return gustCrosswindKt
        case "gust_headwind_kt": return gustHeadwindKt
        default: return nil
        }
    }

    func categoryField(_ name: String) -> String? {
        switch name {
        case "flight_category": return flightCategory
        case "convective_risk": return convectiveRisk
        default: return nil
        }
    }
}

/// Cross-model consensus (worst or majority, both baked server-side). Numeric
/// fields are key-absent when no model supplied the value; `flight_category`
/// and `agreement` are always present.
struct ForecastConsensus: Decodable, Sendable, ForecastCellData {
    let flightCategory: String?
    /// field name → "consistent" | "mixed" | "divergent"; variable key set.
    let agreement: [String: String]
    let convectiveRisk: String?
    let windSpeedKt: Double?
    let ceilingFt: Double?
    let capeJkg: Double?
    let visibilityM: Double?
    let crosswindKt: Double?
    let headwindKt: Double?
    let cloudCoverPct: Double?
    let windDirDeg: Double?

    var altRequired: AltRequired? { nil }  // aggregated from models, not baked here

    enum CodingKeys: String, CodingKey {
        case flightCategory = "flight_category"
        case agreement
        case convectiveRisk = "convective_risk"
        case windSpeedKt = "wind_speed_kt"
        case ceilingFt = "ceiling_ft"
        case capeJkg = "cape_jkg"
        case visibilityM = "visibility_m"
        case crosswindKt = "crosswind_kt"
        case headwindKt = "headwind_kt"
        case cloudCoverPct = "cloud_cover_pct"
        case windDirDeg = "wind_dir_deg"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        flightCategory = try c.decodeIfPresent(String.self, forKey: .flightCategory)
        agreement = try c.decodeIfPresent([String: String].self, forKey: .agreement) ?? [:]
        convectiveRisk = try c.decodeIfPresent(String.self, forKey: .convectiveRisk)
        windSpeedKt = try c.decodeIfPresent(Double.self, forKey: .windSpeedKt)
        ceilingFt = try c.decodeIfPresent(Double.self, forKey: .ceilingFt)
        capeJkg = try c.decodeIfPresent(Double.self, forKey: .capeJkg)
        visibilityM = try c.decodeIfPresent(Double.self, forKey: .visibilityM)
        crosswindKt = try c.decodeIfPresent(Double.self, forKey: .crosswindKt)
        headwindKt = try c.decodeIfPresent(Double.self, forKey: .headwindKt)
        cloudCoverPct = try c.decodeIfPresent(Double.self, forKey: .cloudCoverPct)
        windDirDeg = try c.decodeIfPresent(Double.self, forKey: .windDirDeg)
    }

    func numericField(_ name: String) -> Double? {
        switch name {
        case "wind_speed_kt": return windSpeedKt
        case "crosswind_kt": return crosswindKt
        case "headwind_kt": return headwindKt
        case "ceiling_ft": return ceilingFt
        case "cape_jkg": return capeJkg
        case "visibility_m": return visibilityM
        case "cloud_cover_pct": return cloudCoverPct
        case "wind_dir_deg": return windDirDeg
        default: return nil
        }
    }

    func categoryField(_ name: String) -> String? {
        switch name {
        case "flight_category": return flightCategory
        case "convective_risk": return convectiveRisk
        default: return nil
        }
    }

    /// Agreement bucket for a metric (per-active-metric ring). nil when the
    /// consensus carries no agreement label for the metric's proxy field.
    func agreement(forMetric metric: String) -> String? {
        agreement[ForecastMapCatalog.agreementKey(forMetric: metric)]
    }
}

/// FAA/EASA alternate-required flags.
struct AltRequired: Decodable, Sendable {
    let faa: Bool
    let easa: Bool
}

/// D-0 METAR/TAF block carried on the wire. Decoded for a future card row
/// (see design "Free data already on the wire"); not rendered in v1.
struct ForecastObservation: Decodable, Sendable {
    let metarRaw: String?
    let observationTime: String?
    let flightCategory: String?
    let windSpeedKt: Double?
    let windDirDeg: Double?
    let tafRaw: String?

    enum CodingKeys: String, CodingKey {
        case metarRaw = "metar_raw"
        case observationTime = "observation_time"
        case flightCategory = "flight_category"
        case windSpeedKt = "wind_speed_kt"
        case windDirDeg = "wind_dir_deg"
        case tafRaw = "taf_raw"
    }
}

// MARK: - Model mode

/// How the map/card colours: a consensus reduction or one individual model.
/// Raw values are the wire/URL tokens (`fc.model`): worst/majority/gfs/icon/ecmwf.
enum ForecastModelMode: Equatable, Sendable, Hashable {
    case worst
    case majority
    case model(String)

    /// The `fc.model` token.
    var token: String {
        switch self {
        case .worst: return "worst"
        case .majority: return "majority"
        case .model(let m): return m
        }
    }

    init(token: String) {
        switch token {
        case "worst": self = .worst
        case "majority": self = .majority
        default: self = .model(token)
        }
    }

    /// Worst/majority are consensus modes (agreement ring shown).
    var isConsensus: Bool {
        switch self {
        case .worst, .majority: return true
        case .model: return false
        }
    }

    /// The card's consensus column mirrors the map mode, falling back to worst
    /// when an individual model is active (web `panelConsensusMode`).
    var consensusMode: ForecastModelMode {
        isConsensus ? self : .worst
    }
}
