import Foundation

/// Response of `GET /api/maps/historical?at=&lead=` (#629): for one past instant
/// (30-min grid, UTC), every watchlist airport's METAR, its TAF re-read at that
/// instant, and each model's forecast from the run fetched before
/// `at - lead days`.
///
/// **All selection is server-side** (`tasks/historical_map.py`): which report,
/// which TAF reading, which run and valid time, and why a source is empty. The
/// client picks a block and colours it with the same served metric catalog as
/// the forecast map. Airport entries share the forecast map's shape
/// (`assemble_map_airports`), with METAR/TAF under `observed`; those carry the
/// same value keys and wind/alternate enrichment but never feed the consensus,
/// which is `null` for an airport with observations and no model data.
///
/// Decode with a **plain** `JSONDecoder` (`HistoricalMapResponse.decode(from:)`)
/// for the same reason as `ForecastMapResponse`: `.convertFromSnakeCase` would
/// rewrite the `models`/`sources`/`agreement` dictionary keys.
nonisolated struct HistoricalMapResponse: Decodable, Sendable {
    /// The snapped instant (ISO8601 UTC).
    let at: String
    let leadDays: Int
    /// `at - lead days`: runs fetched after this are excluded.
    let runCutoff: String?
    /// Per-source provenance, keyed metar/taf/gfs/icon/ecmwf.
    let sources: [String: HistoricalSourceInfo]
    let airports: [HistoricalAirport]

    enum CodingKeys: String, CodingKey {
        case at, sources, airports
        case leadDays = "lead_days"
        case runCutoff = "run_cutoff"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = try c.decode(String.self, forKey: .at)
        leadDays = try c.decodeIfPresent(Int.self, forKey: .leadDays) ?? 0
        runCutoff = try c.decodeIfPresent(String.self, forKey: .runCutoff)
        sources = try c.decodeIfPresent([String: HistoricalSourceInfo].self, forKey: .sources) ?? [:]
        airports = try c.decodeIfPresent([HistoricalAirport].self, forKey: .airports) ?? []
    }

    /// Decode a raw `/maps/historical` body with keys preserved verbatim.
    static func decode(from data: Data) throws -> HistoricalMapResponse {
        try JSONDecoder().decode(HistoricalMapResponse.self, from: data)
    }
}

/// What one source is based on, or why it has nothing.
nonisolated struct HistoricalSourceInfo: Decodable, Sendable, Equatable {
    let available: Bool
    /// Models only: `beyond_horizon` | `no_valid_time` | `no_run`.
    let reason: String?
    /// Airports carrying this source.
    let count: Int?
    /// METAR only: the oldest report still shown as current.
    let maxAgeMin: Int?
    let validTime: String?
    let modelInitTime: String?
    let fetchedAt: String?
    let leadHours: Int?

    enum CodingKeys: String, CodingKey {
        case available, reason, count
        case maxAgeMin = "max_age_min"
        case validTime = "valid_time"
        case modelInitTime = "model_init_time"
        case fetchedAt = "fetched_at"
        case leadHours = "lead_hours"
    }

    init(available: Bool, reason: String? = nil, count: Int? = nil, maxAgeMin: Int? = nil,
         validTime: String? = nil, modelInitTime: String? = nil, fetchedAt: String? = nil,
         leadHours: Int? = nil) {
        self.available = available
        self.reason = reason
        self.count = count
        self.maxAgeMin = maxAgeMin
        self.validTime = validTime
        self.modelInitTime = modelInitTime
        self.fetchedAt = fetchedAt
        self.leadHours = leadHours
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason)
        count = try c.decodeIfPresent(Int.self, forKey: .count)
        maxAgeMin = try c.decodeIfPresent(Int.self, forKey: .maxAgeMin)
        validTime = try c.decodeIfPresent(String.self, forKey: .validTime)
        modelInitTime = try c.decodeIfPresent(String.self, forKey: .modelInitTime)
        fetchedAt = try c.decodeIfPresent(String.self, forKey: .fetchedAt)
        leadHours = try c.decodeIfPresent(Int.self, forKey: .leadHours)
    }
}

/// One airport at the historical instant.
nonisolated struct HistoricalAirport: Decodable, Sendable, Identifiable {
    let icao: String
    let lat: Double
    let lon: Double
    let approachType: String?
    /// Present models only. Keys: gfs/icon/ecmwf.
    let models: [String: ForecastModelEntry]
    /// Run + valid time per present model (the entry's provenance).
    let modelRuns: [String: HistoricalModelRun]
    /// Consensus over the NWP models only; nil when no model has data here.
    let consensus: ForecastConsensus?
    let consensusMajority: ForecastConsensus?
    let metar: HistoricalMetar?
    let taf: HistoricalTaf?

    var id: String { icao }

    enum CodingKeys: String, CodingKey {
        case icao, lat, lon, models, consensus, observed
        case approachType = "approach_type"
        case consensusMajority = "consensus_majority"
    }

    private struct Observed: Decodable {
        let metar: HistoricalMetar?
        let taf: HistoricalTaf?
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        icao = try c.decode(String.self, forKey: .icao)
        lat = try c.decode(Double.self, forKey: .lat)
        lon = try c.decode(Double.self, forKey: .lon)
        approachType = try c.decodeIfPresent(String.self, forKey: .approachType)
        models = try c.decodeIfPresent([String: ForecastModelEntry].self, forKey: .models) ?? [:]
        modelRuns = try c.decodeIfPresent([String: HistoricalModelRun].self, forKey: .models) ?? [:]
        consensus = try c.decodeIfPresent(ForecastConsensus.self, forKey: .consensus)
        consensusMajority = try c.decodeIfPresent(ForecastConsensus.self, forKey: .consensusMajority) ?? consensus
        let observed = try c.decodeIfPresent(Observed.self, forKey: .observed)
        metar = observed?.metar
        taf = observed?.taf
    }

    /// The value block a source reads, nil when this airport has nothing for it.
    /// Consensus sources return nil too: they have no single entry.
    func entry(for source: HistoricalSource) -> ForecastModelEntry? {
        switch source {
        case .metar: return metar?.values
        case .taf: return taf?.values
        case .worst, .majority: return nil
        case .gfs, .icon, .ecmwf: return models[source.rawValue]
        }
    }

    /// This airport as the marker layer's `ForecastAirport`, presenting one
    /// source, or nil when it has nothing for that source (left off the map
    /// rather than drawn as a misleading default, as on the web).
    ///
    /// METAR/TAF are presented as the one "model" being shown and read through
    /// `.model("metar")`/`.model("taf")`, so the catalog colours them exactly as
    /// it colours a model. Consensus sources keep the NWP models only: the
    /// alternate-required aggregation runs over `models`, and an observation must
    /// never vote in it.
    func forecastAirport(for source: HistoricalSource) -> ForecastAirport? {
        let models: [String: ForecastModelEntry]
        switch source {
        case .metar, .taf:
            guard let observed = entry(for: source) else { return nil }
            models = [source.rawValue: observed]
        case .worst, .majority:
            guard consensus != nil else { return nil }
            models = self.models
        case .gfs, .icon, .ecmwf:
            guard self.models[source.rawValue] != nil else { return nil }
            models = self.models
        }
        return makeForecastAirport(models: models)
    }

    /// Every source side by side, keyed by its `HistoricalSource` raw value, for
    /// the airport card: each cell is coloured through `.model(source)`.
    var allSourcesAirport: ForecastAirport {
        var all = models
        if let metar { all[HistoricalSource.metar.rawValue] = metar.values }
        if let taf { all[HistoricalSource.taf.rawValue] = taf.values }
        return makeForecastAirport(models: all)
    }

    private func makeForecastAirport(models: [String: ForecastModelEntry]) -> ForecastAirport {
        ForecastAirport(
            icao: icao, lat: lat, lon: lon, approachType: approachType, models: models,
            consensus: consensus ?? .empty,
            consensusMajority: consensusMajority ?? consensus ?? .empty
        )
    }
}

/// Provenance of one model entry.
nonisolated struct HistoricalModelRun: Decodable, Sendable {
    let validTime: String?
    let modelInitTime: String?

    enum CodingKeys: String, CodingKey {
        case validTime = "valid_time"
        case modelInitTime = "model_init_time"
    }
}

/// The METAR shown for the instant: the latest report no older than the
/// server's max age. `values` carries the model-shaped keys (category, ceiling,
/// visibility, wind, runway components, alternate-required).
nonisolated struct HistoricalMetar: Decodable, Sendable {
    let values: ForecastModelEntry
    let observationTime: String?
    let ageMin: Int?
    /// "METAR" or "SPECI".
    let reportType: String?
    let raw: String?
    let weather: [String]
    let dewpointC: Double?
    let qnh: Double?

    enum CodingKeys: String, CodingKey {
        case raw, weather, qnh
        case observationTime = "observation_time"
        case ageMin = "age_min"
        case reportType = "report_type"
        case dewpointC = "dewpoint_c"
    }

    init(from decoder: Decoder) throws {
        values = try ForecastModelEntry(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        observationTime = try c.decodeIfPresent(String.self, forKey: .observationTime)
        ageMin = try c.decodeIfPresent(Int.self, forKey: .ageMin)
        reportType = try c.decodeIfPresent(String.self, forKey: .reportType)
        raw = try c.decodeIfPresent(String.self, forKey: .raw)
        weather = try c.decodeIfPresent([String].self, forKey: .weather) ?? []
        dewpointC = try c.decodeIfPresent(Double.self, forKey: .dewpointC)
        qnh = try c.decodeIfPresent(Double.self, forKey: .qnh)
    }
}

/// The TAF read at the instant (`analysis/taf_reading.read_taf_at`): prevailing
/// conditions with the worst TEMPO/PROB group, the governing ceiling/visibility
/// and the strongest wind. `values.flightCategory` is the governing category.
nonisolated struct HistoricalTaf: Decodable, Sendable {
    let values: ForecastModelEntry
    let prevailingCategory: String?
    let temporaryCategory: String?
    /// e.g. "TEMPO", "PROB30"; nil when only the prevailing group applies.
    let temporaryType: String?
    let trendType: String?
    let significantWeather: [String]
    let issueTime: String?
    let raw: String?

    enum CodingKeys: String, CodingKey {
        case raw
        case prevailingCategory = "prevailing_category"
        case temporaryCategory = "temporary_category"
        case temporaryType = "temporary_type"
        case trendType = "trend_type"
        case significantWeather = "significant_weather"
        case issueTime = "issue_time"
    }

    init(from decoder: Decoder) throws {
        values = try ForecastModelEntry(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        prevailingCategory = try c.decodeIfPresent(String.self, forKey: .prevailingCategory)
        temporaryCategory = try c.decodeIfPresent(String.self, forKey: .temporaryCategory)
        temporaryType = try c.decodeIfPresent(String.self, forKey: .temporaryType)
        trendType = try c.decodeIfPresent(String.self, forKey: .trendType)
        significantWeather = try c.decodeIfPresent([String].self, forKey: .significantWeather) ?? []
        issueTime = try c.decodeIfPresent(String.self, forKey: .issueTime)
        raw = try c.decodeIfPresent(String.self, forKey: .raw)
    }
}

/// Response of `GET /api/maps/historical/range`: what the pickers can offer.
/// Decoded with `JSONDecoder.weatherBrief` (snake→camel); no dynamic-key dicts.
nonisolated struct HistoricalRangeResponse: Decodable, Sendable {
    /// Latest selectable instant (the server's 30-min floor of "now").
    let latest: String
    /// Oldest METAR/TAF; nil when there are none.
    let earliestObservation: String?
    /// Oldest model snapshot; days before it are METAR/TAF only.
    let earliestModel: String?
    let stepMinutes: Int
    let modelSampleHours: [Int]
    let metarMaxAgeMin: Int
    let modelMaxAgeMin: Int
    let models: [String]
    let observedSources: [String]
    /// Which models are stored at each lead (a model beyond its map horizon isn't).
    let leads: [HistoricalLead]
}

nonisolated struct HistoricalLead: Decodable, Sendable {
    let leadDays: Int
    let models: [String]
}

// MARK: - Source

/// What the historical map colours from. Raw values are the web's
/// `hist.source` tokens (and the payload's source keys).
nonisolated enum HistoricalSource: String, CaseIterable, Sendable {
    case metar, taf, worst, majority, gfs, icon, ecmwf

    static let observed: [HistoricalSource] = [.metar, .taf]
    static let consensusModes: [HistoricalSource] = [.worst, .majority]
    static let nwpModels: [HistoricalSource] = [.gfs, .icon, .ecmwf]

    var label: String {
        switch self {
        case .metar: return "METAR"
        case .taf: return "TAF"
        case .worst: return "Worst"
        case .majority: return "Majority"
        case .gfs, .icon, .ecmwf: return rawValue.uppercased()
        }
    }

    var isObserved: Bool { self == .metar || self == .taf }
    var isConsensus: Bool { self == .worst || self == .majority }

    /// The mode the shared marker layer and catalog read this source through.
    var mapMode: ForecastModelMode {
        switch self {
        case .worst: return .worst
        case .majority: return .majority
        default: return .model(rawValue)
        }
    }
}
