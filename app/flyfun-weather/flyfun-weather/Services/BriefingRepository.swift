import Foundation

/// Who triggered a briefing refresh, reported to the server via `?source=`.
/// Drives email suppression: the user's own in-app manual refresh (`manual` →
/// wire "user") is user-present and does NOT email, while Siri (`siri`) is
/// non-user-present and does — closing the Siri refresh-intent loop.
enum RefreshSource: String, Sendable {
    case manual = "user"
    case siri = "siri"
}

/// Abstraction over briefing data access.
protocol BriefingRepository: Sendable {
    func flights() async throws -> [FlightResponse]
    /// Fetch one flight by id, independent of the viewer's list. Backs the
    /// `briefing.html?flight=<id>` Universal Link fallback for flights that
    /// aren't in the list (e.g. a feedback email about another pilot's flight):
    /// the server allows the owner always and any authenticated viewer for a
    /// non-private flight (role comes back `subscriber`), and 404s otherwise.
    func flight(id: String) async throws -> FlightResponse
    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse
    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse
    /// Permanently delete one of the viewer's own flights and all of its briefing
    /// history (server 204, owner-only — a subscriber unsubscribes instead).
    /// Online-only; the caching layer also drops the flight's local pack cache,
    /// since a deleted flight can never be refreshed or re-downloaded.
    func deleteFlight(id: String) async throws
    /// Permanently delete several of the viewer's own flights in one round trip
    /// (the flight list's multi-select). Owner-scoped and forgiving: ids the
    /// viewer doesn't own come back in `notFound` rather than failing the call,
    /// so callers must surface a partial result. Requests longer than the server
    /// cap are chunked (`BulkDeleteResponse.maxIdsPerRequest`). Online-only; the
    /// caching layer evicts the local packs of every *confirmed* deleted id.
    func bulkDeleteFlights(ids: [String]) async throws -> BulkDeleteResponse
    func aircraft() async throws -> [AircraftResponse]
    func profiles() async throws -> [ProfileResponse]
    /// Account usage summary — the iOS app reads only the durable timing-scan
    /// flags (`timeScanUsed`) that gate the first-time Flexibility explainer.
    func usageSummary() async throws -> UsageSummaryResponse
    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse]
    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse
    func parseFpl(_ text: String) async throws -> ParseFplResponse
    func interpretRoute(rawRoute: String) async throws -> InterpretRouteResponse
    func routeDistance(waypoints: [String]) async throws -> RouteDistanceResponse
    func autorouterRoutes(limit: Int) async throws -> [AutorouterRoute]
    func packs(flightId: String) async throws -> [PackMetaResponse]
    func latestPack(flightId: String) async throws -> PackMetaResponse
    // Flight sharing (#446) — all online-only.
    /// Resolve a share code (`/s/{code}`) to its flight for the preview-before-
    /// subscribe on-ramp. Throws `APIError.notFound` (404) for an unknown/invalid
    /// code or a private flight the viewer can't see. A non-owner gets a
    /// `FlightResponse` with `role == .subscriber`, `ownerDisplayName`, and
    /// `isSubscribed`.
    func flightByShareCode(_ code: String) async throws -> FlightResponse
    /// Subscribe the viewer to another pilot's flight. Idempotent 200; throws
    /// `APIError.notFound` (404 private/not-visible) or `APIError.serverError(409,…)`
    /// (own flight).
    func subscribeFlight(id: String) async throws
    /// Remove the viewer's subscription. Idempotent (204 either way).
    func unsubscribeFlight(id: String) async throws
    /// Quick forecast + observation for one airport (mirrors the MCP
    /// `get_airport_weather` tool). Online-only — not part of the offline bundle.
    /// `day`: 0=today…3, `hour`: forecast hour UTC (snapped server-side).
    func airportWeather(icao: String, day: Int, hour: Int) async throws -> AirportWeatherResponse
    // Forecast map (#420) — all online-only, one payload per (day, hour).
    /// Every watchlist airport with per-model + baked consensus for one slot. The
    /// only call that hits the network on a day/hour change (metric/model switches
    /// are a pure client recolour).
    func forecastMap(day: Int, hour: Int) async throws -> ForecastMapResponse
    /// The ragged day/hour/model grid the pickers are drawn from. Never hardcode it.
    func forecastDays() async throws -> ForecastDaysResponse
    /// Top-5 departure/destination airports from history — cold-open map centring (#419).
    func frequentAirports() async throws -> FrequentAirportsResponse
    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse
    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse
    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws
    // Timing scenarios (#357) — all online-only, poll-driven.
    /// Poll status + scan result for a pack. Throws `APIError.notFound` (404)
    /// when Flexibility is `none` and no scan ever ran.
    func timeOptions(flightId: String, timestamp: String) async throws -> TimeOptionsResponse
    /// Queue the on-tap multi-model check of one provisional candidate (202).
    /// Surfaces the server's `429` as `APIError.serverError(429, …)` when a
    /// confirm already runs for this pack.
    func confirmTimeOption(flightId: String, timestamp: String, departureTime: String) async throws
    /// Re-queue the timing scan for a pack (used after "Set as alternate time").
    func rescanTimeOptions(flightId: String, timestamp: String) async throws
    func digest(flightId: String, timestamp: String) async throws -> DigestResponse
    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse
    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse
    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse
    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data
    func grametImage(flightId: String, timestamp: String) async throws -> Data
    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse
    func refreshStream(flightId: String, source: RefreshSource) async -> AsyncThrowingStream<RefreshEvent, Error>
    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse
    /// Flights whose briefing is currently queued/refreshing server-side, for the
    /// live flight-list "Updating…" indicator. Online-only.
    func activeRefreshes() async throws -> [ActiveRefreshResponse]

    // PIREP
    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse
    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse]
    func fetchPireps(flightId: String) async throws -> PirepListResponse

    // Debrief (post-flight judgement on a past flight, owner-only)
    /// Load a flight's debrief. Throws `APIError.notFound` (404) when none exists.
    func fetchDebrief(flightId: String) async throws -> DebriefResponse
    /// Insert or update a flight's debrief.
    func upsertDebrief(flightId: String, request: DebriefRequest) async throws -> DebriefResponse
    /// Remove a flight's debrief. Idempotent.
    func deleteDebrief(flightId: String) async throws

    // Digest feedback (👍/👎 on the AI digest)
    /// Submit a thumb rating (+ optional comment) for a briefing pack's digest.
    func submitDigestFeedback(_ request: DigestFeedbackRequest) async throws
    /// Submit categorized free-text feedback (the web help page's twin).
    func submitGeneralFeedback(_ request: GeneralFeedbackRequest) async throws
}

extension BriefingRepository {
    /// Default in-app manual refresh (source = "user"), so existing call sites
    /// that don't name a source stay unchanged; Siri passes `.siri` explicitly.
    func refreshStream(flightId: String) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await refreshStream(flightId: flightId, source: .manual)
    }
}

/// Online-only implementation — every call hits the API.
final class OnlineBriefingRepository: BriefingRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func flights() async throws -> [FlightResponse] {
        try await client.request("/api/flights")
    }

    func flight(id: String) async throws -> FlightResponse {
        try await client.request("/api/flights/\(id)")
    }

    func createFlight(_ request: CreateFlightRequest) async throws -> FlightResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/flights", method: "POST", body: body)
    }

    func updateFlight(flightId: String, request: UpdateFlightRequest) async throws -> UpdateFlightResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        // Server returns the updated flight plus an invalidation hint describing
        // how much of the briefing the edit invalidated.
        return try await client.request("/api/flights/\(flightId)", method: "PATCH", body: body)
    }

    func deleteFlight(id: String) async throws {
        try await client.requestVoid("/api/flights/\(id)")
    }

    func bulkDeleteFlights(ids: [String]) async throws -> BulkDeleteResponse {
        try await BulkDeleteResponse.sendChunked(ids: ids) { chunk in
            let body = try JSONEncoder.weatherBrief.encode(BulkDeleteRequest(ids: chunk))
            return try await client.request("/api/flights/bulk-delete", method: "POST", body: body)
        }
    }

    func aircraft() async throws -> [AircraftResponse] {
        try await client.request("/api/aircraft")
    }

    func profiles() async throws -> [ProfileResponse] {
        try await client.request("/api/user/profiles")
    }

    func usageSummary() async throws -> UsageSummaryResponse {
        try await client.request("/api/user/usage")
    }

    func searchAircraftTypes(_ query: String) async throws -> [AircraftTypeResponse] {
        return try await client.requestURL("/api/aircraft/types?q=\(Self.queryValueEncoded(query))")
    }

    func createAircraft(_ request: CreateAircraftRequest) async throws -> AircraftResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/aircraft", method: "POST", body: body)
    }

    func parseFpl(_ text: String) async throws -> ParseFplResponse {
        let body = try JSONEncoder.weatherBrief.encode(ParseFplRequest(fplText: text))
        return try await client.request("/api/flights/parse-fpl", method: "POST", body: body)
    }

    func interpretRoute(rawRoute: String) async throws -> InterpretRouteResponse {
        let body = try JSONEncoder.weatherBrief.encode(InterpretRouteRequest(rawRoute: rawRoute))
        return try await client.request("/api/flights/interpret-route", method: "POST", body: body)
    }

    func routeDistance(waypoints: [String]) async throws -> RouteDistanceResponse {
        let body = try JSONEncoder.weatherBrief.encode(RouteDistanceRequest(waypoints: waypoints))
        return try await client.request("/api/flights/route-distance", method: "POST", body: body)
    }

    func autorouterRoutes(limit: Int) async throws -> [AutorouterRoute] {
        let response: AutorouterRoutesResponse = try await client.requestURL(
            "/api/flights/autorouter-routes?limit=\(limit)"
        )
        return response.routes
    }

    func packs(flightId: String) async throws -> [PackMetaResponse] {
        try await client.request("/api/flights/\(flightId)/packs")
    }

    func latestPack(flightId: String) async throws -> PackMetaResponse {
        try await client.request("/api/flights/\(flightId)/packs/latest")
    }

    func flightByShareCode(_ code: String) async throws -> FlightResponse {
        // The code is validated client-side before we get here (deep-link parse),
        // but percent-encode it anyway so it's a single opaque path segment.
        try await client.requestURL("/api/flights/by-share/\(Self.queryValueEncoded(code))")
    }

    func subscribeFlight(id: String) async throws {
        _ = try await client.requestData("/api/flights/\(id)/subscribe", method: "POST")
    }

    func unsubscribeFlight(id: String) async throws {
        try await client.requestVoid("/api/flights/\(id)/subscribe")
    }

    func airportWeather(icao: String, day: Int, hour: Int) async throws -> AirportWeatherResponse {
        let code = Self.queryValueEncoded(icao)
        return try await client.requestURL("/api/maps/airport-weather?icao=\(code)&day=\(day)&hour=\(hour)")
    }

    func forecastMap(day: Int, hour: Int) async throws -> ForecastMapResponse {
        // Decode with a plain decoder (NOT weatherBrief): `.convertFromSnakeCase`
        // would rewrite the `agreement`/`models` dictionary keys. See
        // `ForecastMapResponse.decode(from:)`.
        let data = try await client.requestDataURL("/api/maps/forecast?day=\(day)&hour=\(hour)")
        do {
            return try ForecastMapResponse.decode(from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    func forecastDays() async throws -> ForecastDaysResponse {
        try await client.request("/api/maps/forecast/days")
    }

    func frequentAirports() async throws -> FrequentAirportsResponse {
        try await client.request("/api/flights/frequent-airports")
    }

    func advisories(flightId: String, timestamp: String) async throws -> AdvisoriesResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/advisories")
    }

    func advisoryDetail(flightId: String, timestamp: String, advisoryId: String) async throws -> AdvisoryDetailResponse {
        // Pass the RAW timestamp: `client.request` → `requestData` builds the URL
        // with `appendingPathComponent`, which already percent-encodes the segment
        // (`:` → `%3A`). Pre-encoding here would double-encode (`%3A` → `%253A`),
        // and the server's `fromisoformat` would 500 on the still-encoded string.
        // Matches `advisories` above. (The `requestDataURL`/`requestURL` variants —
        // e.g. `recalculateAdvisories` — do NOT re-encode, so those DO pre-encode.)
        return try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/advisories/\(advisoryId)/detail")
    }

    func recalculateAdvisories(flightId: String, timestamp: String, cruiseAltitudeFt: Int?) async throws {
        let encodedTimestamp = timestamp.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? timestamp
        var path = "/api/flights/\(flightId)/packs/\(encodedTimestamp)/advisories/recalculate"
        if let cruiseAltitudeFt {
            path += "?cruise_altitude_ft=\(cruiseAltitudeFt)"
        }
        _ = try await client.requestDataURL(path, method: "POST")
    }

    // NOTE: the timing endpoints pass the RAW timestamp. `client.request` /
    // `client.requestData` build the URL with `appendingPathComponent`, which
    // already percent-encodes the path segment (`:` → `%3A`). Pre-encoding first
    // double-encoded it (`%3A` → `%253A`); the server then received a literal
    // `%3A`, `datetime.fromisoformat` raised `ValueError`, and every poll got a
    // 500 → the panel stayed hidden. Matches `advisories`/`snapshot`.
    func timeOptions(flightId: String, timestamp: String) async throws -> TimeOptionsResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/time-options")
    }

    func confirmTimeOption(flightId: String, timestamp: String, departureTime: String) async throws {
        let body = try JSONEncoder.weatherBrief.encode(ConfirmTimeOptionRequest(departureTime: departureTime))
        _ = try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/time-options/confirm",
                                         method: "POST", body: body)
    }

    func rescanTimeOptions(flightId: String, timestamp: String) async throws {
        _ = try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/time-options/rescan",
                                         method: "POST")
    }

    func digest(flightId: String, timestamp: String) async throws -> DigestResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/digest/json")
    }

    func snapshot(flightId: String, timestamp: String) async throws -> SnapshotResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/snapshot")
    }

    func routeAnalyses(flightId: String, timestamp: String) async throws -> RouteAnalysesResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/route-analyses")
    }

    func elevation(flightId: String, timestamp: String) async throws -> ElevationResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/elevation")
    }

    func skewtImage(flightId: String, timestamp: String, icao: String, model: String) async throws -> Data {
        try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/skewt/\(icao)/\(model)")
    }

    func grametImage(flightId: String, timestamp: String) async throws -> Data {
        try await client.requestData("/api/flights/\(flightId)/packs/\(timestamp)/gramet.png")
    }

    func soundingProfile(flightId: String, timestamp: String, pointIndex: Int, model: String) async throws -> SoundingProfileResponse {
        try await client.request("/api/flights/\(flightId)/packs/\(timestamp)/sounding-profile/\(pointIndex)/\(model)")
    }

    func refreshStream(flightId: String, source: RefreshSource) async -> AsyncThrowingStream<RefreshEvent, Error> {
        await client.streamSSE("/api/flights/\(flightId)/packs/refresh/stream?source=\(source.rawValue)")
    }

    func refreshStatus(flightId: String) async throws -> RefreshStatusResponse {
        try await client.request("/api/flights/\(flightId)/packs/refresh/status")
    }

    func activeRefreshes() async throws -> [ActiveRefreshResponse] {
        // Polled every 5s while the list is visible — suppress its request log
        // so it doesn't flood the console.
        try await client.request("/api/refresh/active", quietLog: true)
    }

    func submitPirep(_ request: SubmitPirepRequest) async throws -> PirepResponse {
        let body = try JSONEncoder.weatherBrief.encode(request)
        return try await client.request("/api/pireps", method: "POST", body: body)
    }

    func submitPirepsBatch(_ requests: [SubmitPirepRequest]) async throws -> [PirepResponse] {
        let body = try JSONEncoder.weatherBrief.encode(requests)
        return try await client.request("/api/pireps/batch", method: "POST", body: body)
    }

    func fetchPireps(flightId: String) async throws -> PirepListResponse {
        return try await client.requestURL("/api/pireps?flight_id=\(Self.queryValueEncoded(flightId))")
    }

    func fetchDebrief(flightId: String) async throws -> DebriefResponse {
        try await client.request("/api/flights/\(flightId)/debrief")
    }

    func upsertDebrief(flightId: String, request: DebriefRequest) async throws -> DebriefResponse {
        // Plain-encoded body so the `outcomes` tag-id keys aren't snake-cased.
        let body = try request.encoded()
        return try await client.request("/api/flights/\(flightId)/debrief", method: "PUT", body: body)
    }

    func deleteDebrief(flightId: String) async throws {
        try await client.requestVoid("/api/flights/\(flightId)/debrief")
    }

    func submitDigestFeedback(_ request: DigestFeedbackRequest) async throws {
        let body = try JSONEncoder.weatherBrief.encode(request)
        _ = try await client.requestData("/api/feedback", method: "POST", body: body)
    }

    func submitGeneralFeedback(_ request: GeneralFeedbackRequest) async throws {
        let body = try JSONEncoder.weatherBrief.encode(request)
        _ = try await client.requestData("/api/feedback", method: "POST", body: body)
    }

    /// Percent-encode a query *value*. `.urlQueryAllowed` permits the `& = + ? #`
    /// delimiters (legal in a query as a whole), so encoding a value with it
    /// leaves those intact and allows parameter injection from free-text input.
    /// Remove them from the allowed set so the value is a single opaque token.
    private static func queryValueEncoded(_ value: String) -> String {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "&=+?#")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }
}
