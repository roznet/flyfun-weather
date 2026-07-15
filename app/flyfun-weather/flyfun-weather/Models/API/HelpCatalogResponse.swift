import Foundation

/// The unified help catalog served by `GET /api/help/catalog` (issue #311).
///
/// One payload carries every (i)-popup's content so the app can cache it and
/// render help offline — the web app stays the single source of truth and no
/// help text is hand-duplicated in Swift.
///
/// - `metrics`: keyed by metric id (e.g. `cape_surface_jkg`). English-only.
/// - `advisories`: the same `AdvisoryCatalogEntry` shape already used by
///   `AdvisoriesResponse`, reused here rather than re-declared.
///
/// ## Decoding note
/// `JSONDecoder.weatherBrief` uses `.convertFromSnakeCase`, which also rewrites
/// **dictionary keys** — it would turn metric ids like `cape_surface_jkg` into
/// `capeSurfaceJkg` and break every lookup. So the payload is decoded in two
/// passes: the metric map with a plain decoder (metric ids preserved verbatim;
/// `MetricHelp` carries explicit snake_case `CodingKeys`), and the advisory list
/// with `JSONDecoder.weatherBrief` (so `short_description` → `shortDescription`
/// matches `AdvisoryCatalogEntry`). See `decode(from:)`.
struct HelpCatalogResponse: Sendable {
    /// Content-hash version, mirrored by the server's `ETag`.
    let version: String
    /// Metric help keyed by metric id.
    let metrics: [String: MetricHelp]
    /// Forecast-map metric catalog (colours/thresholds/labels/legends), served in
    /// the `maps` section (#419, B2). nil on the bundled baseline before first
    /// sync — the forecast map falls back to `ForecastMapCatalog.bundledBaseline`.
    let maps: ForecastMapCatalog?
    /// Advisory help (same shape as `AdvisoriesResponse.catalog`).
    let advisories: [AdvisoryCatalogEntry]
    /// O(1) advisory lookup by id, built once at construction — mirrors the
    /// metrics dict so both popup call sites have the same access cost.
    let advisoriesById: [String: AdvisoryCatalogEntry]
    /// Debrief taxonomy (decision buttons, condition tags, outcome values,
    /// advisory→tag map). Served so the iOS debrief form renders from the same
    /// vocabulary the web bundle imports. nil on the bundled baseline (metrics
    /// only) before the first online sync — callers fall back to
    /// `DebriefTaxonomy.bundledBaseline`.
    let debrief: DebriefTaxonomy?

    init(version: String, metrics: [String: MetricHelp], maps: ForecastMapCatalog?,
         advisories: [AdvisoryCatalogEntry], debrief: DebriefTaxonomy?) {
        self.version = version
        self.metrics = metrics
        self.maps = maps
        self.advisories = advisories
        self.advisoriesById = Dictionary(advisories.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        self.debrief = debrief
    }

    /// Decode a full server payload (`{version, metrics, maps, advisories, debrief}`).
    static func decode(from data: Data) throws -> HelpCatalogResponse {
        let plain = JSONDecoder()  // no key strategy → metric/scale/field ids stay verbatim
        let envelope = try plain.decode(MetricsEnvelope.self, from: data)
        // Advisory property names need snake→camel; the weatherBrief decoder only
        // touches the `advisories` key here (this struct ignores metrics/version).
        let adv = try JSONDecoder.weatherBrief.decode(AdvisoriesEnvelope.self, from: data)
        return HelpCatalogResponse(
            version: envelope.version,
            metrics: envelope.metrics,
            maps: envelope.maps,
            advisories: adv.advisories,
            // The debrief section carries snake_case keys (`outcome_category`,
            // `advisory_tag_map`, …); it's decoded with the plain decoder and
            // `DebriefTaxonomy`'s explicit CodingKeys, NOT the snake-converting one.
            debrief: envelope.debrief
        )
    }

    /// Decode the bundled baseline, which is the raw `metrics-catalog.json` — a
    /// bare `{ "<id>": {...} }` map with no version/advisories wrapper.
    static func decodeBaseline(from data: Data) throws -> HelpCatalogResponse {
        let plain = JSONDecoder()
        let metrics = try plain.decode([String: MetricHelp].self, from: data)
        return HelpCatalogResponse(version: "baseline", metrics: metrics, maps: nil, advisories: [], debrief: nil)
    }

    private struct MetricsEnvelope: Decodable {
        let version: String
        let metrics: [String: MetricHelp]
        /// The `maps` section (#419); optional so an old server without it decodes.
        let maps: ForecastMapCatalog?
        /// The `debrief` section; optional so an old server without it decodes.
        let debrief: DebriefTaxonomy?
    }

    private struct AdvisoriesEnvelope: Decodable {
        let advisories: [AdvisoryCatalogEntry]
    }
}

/// Help content for one weather metric (the `metrics-catalog.json` entry shape).
/// English-only; `CodingKeys` map snake_case so a plain decoder reads it.
struct MetricHelp: Codable, Sendable {
    let name: String
    let unit: String?
    let vibe: String?
    let primaryGoal: String?
    let bestUsedFor: String?
    let limitations: String?
    let theory: String?
    let wikipedia: String?
    let thresholds: [MetricThreshold]?
    let llmPrompt: String?

    enum CodingKeys: String, CodingKey {
        case name, unit, vibe, limitations, theory, wikipedia, thresholds
        case primaryGoal = "primary_goal"
        case bestUsedFor = "best_used_for"
        case llmPrompt = "llm_prompt"
    }
}

/// One band in a metric's threshold ladder (e.g. CAPE None/Low/Moderate/…).
struct MetricThreshold: Codable, Sendable, Identifiable {
    let label: String
    let min: Double?
    let max: Double?
    let meaning: String
    let risk: String

    /// Stable identity for `ForEach` — label is unique within a metric's ladder.
    var id: String { label }
}
