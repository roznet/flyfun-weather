import FlyFunCommon
import Foundation
import OSLog

/// Errors from the WeatherBrief API.
enum APIError: LocalizedError {
    case unauthorized
    case forbidden(String)
    case notFound
    case serverError(Int, String?)
    case networkError(Error)
    case decodingError(Error)

    /// True for transient network errors that should trigger offline queuing.
    var isTransientNetwork: Bool {
        guard case .networkError(let inner) = self,
              let urlError = inner as? URLError else { return false }
        return [.notConnectedToInternet, .timedOut, .networkConnectionLost, .cannotConnectToHost]
            .contains(urlError.code)
    }

    /// True when the underlying request was cancelled (Swift Concurrency task
    /// cancellation surfaces as `URLError.cancelled` / `-999`). Callers should
    /// treat this as benign — the consumer went away, not a real failure.
    var isCancellation: Bool {
        guard case .networkError(let inner) = self else { return false }
        if inner is CancellationError { return true }
        return (inner as? URLError)?.code == .cancelled
    }

    var errorDescription: String? {
        switch self {
        case .unauthorized: "Session expired. Please sign in again."
        case .forbidden(let msg): "Access denied: \(msg)"
        case .notFound: "Resource not found."
        case .serverError(let code, let msg): "Server error \(code): \(msg ?? "Unknown")"
        case .networkError(let err): "Network error: \(err.localizedDescription)"
        case .decodingError(let err): "Data error: \(err.localizedDescription)"
        }
    }
}

/// HTTP client for the WeatherBrief REST API.
///
/// All authenticated requests flow through the shared `RollingBearerSession`
/// from `flyfun-common`, so the JWT is read from the keychain on every call,
/// rolled forward when the server emits `X-Renewed-Token`, and cleared on 401
/// (which fires `AppState.handleUnauthorized` to bounce the user to login).
actor APIClient {
    let baseURL: URL
    private let session: URLSession
    private let tokenStore: any BearerTokenStore
    private let rollingSession: RollingBearerSession

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "APIClient")

    init(
        baseURL: URL,
        tokenStore: any BearerTokenStore,
        rollingSession: RollingBearerSession
    ) {
        self.baseURL = baseURL
        self.tokenStore = tokenStore
        self.rollingSession = rollingSession
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    // MARK: - Help catalog (ETag-cached)

    /// Outcome of a conditional help-catalog fetch.
    enum HelpCatalogFetchResult: Sendable {
        /// Server returned `304` — the caller's cached copy is current.
        case notModified
        /// Fresh payload (raw JSON bytes) plus its `ETag`, if any. The caller
        /// decodes (two-pass, see `HelpCatalogResponse`) and persists the bytes.
        case updated(data: Data, etag: String?)
    }

    /// Conditionally fetch the help catalog, honoring `ETag`/`304`.
    ///
    /// Pass the cached `ETag` as `ifNoneMatch`; a `304` returns `.notModified`
    /// and the caller keeps its cache. The endpoint is public, but the request
    /// still flows through `rollingSession` so a signed-in user's token rolls
    /// forward like every other call.
    func fetchHelpCatalog(lang: String = "en", ifNoneMatch etag: String? = nil) async throws -> HelpCatalogFetchResult {
        // Build via URLComponents so `lang` is percent-encoded rather than
        // interpolated raw (robust if a non-ASCII / reserved-char value is ever passed).
        guard var components = URLComponents(url: baseURL.appendingPathComponent("/api/help/catalog"),
                                             resolvingAgainstBaseURL: true) else {
            throw APIError.networkError(URLError(.badURL))
        }
        components.queryItems = [URLQueryItem(name: "lang", value: lang)]
        guard let url = components.url else {
            throw APIError.networkError(URLError(.badURL))
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }

        Self.logger.debug("GET /api/help/catalog (etag: \(etag ?? "none"))")

        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await rollingSession.data(for: request)
        } catch FlyFunAPIError.unauthorized {
            throw APIError.unauthorized
        } catch let FlyFunAPIError.networkError(inner) {
            throw APIError.networkError(inner)
        } catch {
            throw APIError.networkError(error)
        }

        if http.statusCode == 304 {
            return .notModified
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.serverError(http.statusCode, String(data: data, encoding: .utf8))
        }
        return .updated(data: data, etag: http.value(forHTTPHeaderField: "ETag"))
    }

    // MARK: - Airports DB (ETag-cached)

    /// Outcome of a conditional airports-DB fetch (mirrors the help-catalog flow).
    enum AirportsDBFetchResult: Sendable {
        case notModified
        case updated(data: Data, etag: String?)
    }

    /// Conditionally download the slim airports SQLite used for local ICAO
    /// autocomplete. Pass the cached `ETag`; a `304` returns `.notModified` and
    /// the caller keeps its on-disk copy. The DB is a few MB and changes only on
    /// an AIRAC cycle, so this is a rare full download.
    func fetchAirportsDB(ifNoneMatch etag: String? = nil) async throws -> AirportsDBFetchResult {
        let url = baseURL.appendingPathComponent("/api/nav/airports-db")
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 120   // multi-MB body on a slow link
        if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }

        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await rollingSession.data(for: request)
        } catch FlyFunAPIError.unauthorized {
            throw APIError.unauthorized
        } catch let FlyFunAPIError.networkError(inner) {
            throw APIError.networkError(inner)
        } catch {
            throw APIError.networkError(error)
        }

        if http.statusCode == 304 {
            return .notModified
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.serverError(http.statusCode, String(data: data, encoding: .utf8))
        }
        return .updated(data: data, etag: http.value(forHTTPHeaderField: "ETag"))
    }

    // MARK: - Generic request methods

    /// Fetch and decode a JSON response. `quietLog` suppresses the per-request log
    /// line for high-frequency background polls (e.g. `/api/refresh/active` every
    /// 5s) so they don't flood the console.
    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil, quietLog: Bool = false) async throws -> T {
        let data = try await requestData(path, method: method, body: body, quietLog: quietLog)
        do {
            return try JSONDecoder.weatherBrief.decode(T.self, from: data)
        } catch {
            Self.logger.error("Decoding failed for \(path): \(error)")
            throw APIError.decodingError(error)
        }
    }

    /// Stream SSE events from the server's refresh endpoint.
    ///
    /// We deliberately use a delegate-based `URLSessionDataTask` rather than
    /// `URLSession.bytes(for:)`. On-device, `bytes(for:)` buffered the entire
    /// `text/event-stream` response and delivered *zero* lines until the server
    /// closed the connection — verified in production logs as "SSE stream ended
    /// after 0 events" across a full 3-minute refresh the server definitely
    /// streamed. `urlSession(_:dataTask:didReceive:)` delivers each chunk as it
    /// arrives, which is what SSE requires.
    ///
    /// The Bearer token is attached directly from the store (the delegate
    /// session isn't wrapped by `RollingBearerSession`); a non-2xx surfaces as
    /// `serverError`, which the caller already handles.
    func streamSSE(_ path: String, method: String = "POST") -> AsyncThrowingStream<RefreshEvent, Error> {
        // `path` may carry a query string (e.g. `?source=user`); build the URL with
        // `URL(string:relativeTo:)` so `?` is preserved. `appendingPathComponent`
        // percent-encodes it into `%3F`, mangling the query into the path and yielding
        // a 405 (no route matches that verb on the encoded path). Same rule as `requestDataURL`.
        let url = URL(string: path, relativeTo: baseURL)
        let token = tokenStore.token

        return AsyncThrowingStream { continuation in
            guard let url else {
                continuation.finish(throwing: APIError.networkError(URLError(.badURL)))
                return
            }
            var request = URLRequest(url: url)
            request.httpMethod = method
            if let token {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            request.timeoutInterval = 300

            // Own ephemeral session per stream so the long idle windows between
            // pipeline phases don't tear the connection down, and so the delegate
            // (and its retain cycle with the session) is released on invalidate.
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 300    // idle between SSE events
            config.timeoutIntervalForResource = 3600  // whole-stream ceiling
            config.requestCachePolicy = .reloadIgnoringLocalCacheData
            config.waitsForConnectivity = true

            let delegate = SSEStreamDelegate(continuation: continuation, path: path, logger: Self.logger)
            let queue = OperationQueue()
            queue.maxConcurrentOperationCount = 1   // serialize delegate callbacks
            let session = URLSession(configuration: config, delegate: delegate, delegateQueue: queue)
            let task = session.dataTask(with: request)

            continuation.onTermination = { _ in
                task.cancel()
                session.invalidateAndCancel()
            }
            task.resume()
        }
    }

    /// Decode a JSON response from a path that may contain query parameters.
    /// Use this instead of `request(_:)` when the path includes `?key=value`.
    func requestURL<T: Decodable>(_ pathAndQuery: String, method: String = "GET", body: Data? = nil) async throws -> T {
        let data = try await requestDataURL(pathAndQuery, method: method, body: body)
        do {
            return try JSONDecoder.weatherBrief.decode(T.self, from: data)
        } catch {
            Self.logger.error("Decoding failed for \(pathAndQuery): \(error)")
            throw APIError.decodingError(error)
        }
    }

    /// Fetch raw data from a path that may contain query parameters.
    func requestDataURL(_ pathAndQuery: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        guard let url = URL(string: pathAndQuery, relativeTo: baseURL) else {
            throw APIError.networkError(URLError(.badURL))
        }
        return try await _fetch(url: url, method: method, body: body, label: pathAndQuery)
    }

    /// Perform a request that returns no body (e.g. DELETE).
    func requestVoid(_ path: String, method: String = "DELETE") async throws {
        _ = try await requestData(path, method: method)
    }

    /// Fetch raw data (for images, file downloads).
    func requestData(_ path: String, method: String = "GET", body: Data? = nil, quietLog: Bool = false) async throws -> Data {
        let url = baseURL.appendingPathComponent(path)
        return try await _fetch(url: url, method: method, body: body, label: path, quietLog: quietLog)
    }

    /// Fetch raw data while reporting transfer progress as bytes arrive.
    ///
    /// `progress` receives `(receivedBytes, totalBytes)`. The total comes from the server's
    /// `X-Uncompressed-Length` header: `Content-Length` reflects the gzip-compressed size, but
    /// URLSession transparently decompresses, so we count decompressed bytes against the
    /// decompressed total. If the header is absent, `totalBytes` is `-1` (size unknown).
    ///
    /// Like `streamSSE`, this needs a custom URLSession delegate (for byte-level progress) that
    /// `RollingBearerSession` doesn't wrap, so the Bearer token is attached directly from the
    /// store. Token renewal (`X-Renewed-Token`) is skipped and a 401 surfaces as
    /// `APIError.unauthorized` rather than firing the rolling session's `onUnauthorized`; the
    /// caller handles the error. Acceptable because this is a one-shot, user-initiated fetch.
    func requestDataStreaming(
        _ path: String,
        progress: @Sendable @escaping (_ receivedBytes: Int64, _ totalBytes: Int64) -> Void
    ) async throws -> Data {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        if let token = tokenStore.token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        // The session's default 30s is an idle timeout; the server can spend longer than
        // that building the bundle (sounding analysis per point × model) before sending any
        // bytes, which would otherwise abort the download during the "Preparing…" phase.
        request.timeoutInterval = 300

        Self.logger.debug("GET \(path) (streaming)")

        return try await withCheckedThrowingContinuation { continuation in
            let delegate = StreamingDownloadDelegate(onProgress: progress, continuation: continuation)
            let task = session.dataTask(with: request)
            task.delegate = delegate
            task.resume()
        }
    }

    private func _fetch(url: URL, method: String, body: Data?, label: String, quietLog: Bool = false) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        if !quietLog { Self.logger.debug("\(method) \(label)") }

        let data: Data
        let http: HTTPURLResponse
        do {
            (data, http) = try await rollingSession.data(for: request)
        } catch FlyFunAPIError.unauthorized {
            throw APIError.unauthorized
        } catch let FlyFunAPIError.networkError(inner) {
            throw APIError.networkError(inner)
        } catch {
            throw APIError.networkError(error)
        }

        switch http.statusCode {
        case 200...299:
            return data
        case 403:
            throw APIError.forbidden(Self.detailMessage(from: data) ?? "Forbidden")
        case 404:
            throw APIError.notFound
        default:
            throw APIError.serverError(http.statusCode, Self.detailMessage(from: data))
        }
    }

    /// Pull FastAPI's human sentence out of an error body so callers can show it
    /// verbatim instead of raw JSON (`{"detail":"Cannot change the flight
    /// date…"}` used to reach the pilot with the braces still on).
    ///
    /// `detail` has two shapes: a plain string from `HTTPException`, and a list
    /// of `{loc, msg, type}` objects from a pydantic request-validation 422 —
    /// join the `msg`s for the latter. Anything else falls back to the raw body,
    /// so no information is ever lost, and substring matchers on the message
    /// (e.g. the `autorouter_not_linked` 409) keep working because a
    /// single-string `detail` passes through unchanged.
    private static func detailMessage(from data: Data) -> String? {
        let raw = String(data: data, encoding: .utf8)
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any] else { return raw }
        if let detail = dict["detail"] as? String { return detail }
        if let items = dict["detail"] as? [[String: Any]] {
            let messages = items.compactMap { $0["msg"] as? String }
            if !messages.isEmpty { return messages.joined(separator: "; ") }
        }
        return raw
    }
}

/// URLSession delegate that streams a response into memory while reporting progress.
/// State is touched only on URLSession's serial per-task delegate queue; `keepAlive`
/// retains the delegate (task-delegate retention is not guaranteed) until completion.
private final class StreamingDownloadDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private var buffer = Data()
    private var total: Int64 = -1
    private var lastReportedBytes: Int64 = 0
    private let onProgress: @Sendable (Int64, Int64) -> Void
    private var continuation: CheckedContinuation<Data, Error>?
    private var keepAlive: StreamingDownloadDelegate?

    init(onProgress: @escaping @Sendable (Int64, Int64) -> Void,
         continuation: CheckedContinuation<Data, Error>) {
        self.onProgress = onProgress
        self.continuation = continuation
        super.init()
        self.keepAlive = self
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        if let http = response as? HTTPURLResponse,
           let header = http.value(forHTTPHeaderField: "X-Uncompressed-Length"),
           let len = Int64(header) {
            total = len
            if len > 0, len < 64 * 1024 * 1024 { buffer.reserveCapacity(Int(len)) }
            // Report the total up front so the UI can show the size before bytes arrive.
            onProgress(0, len)
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        let received = Int64(buffer.count)
        // Throttle to ~128 KB steps (or the final byte when the total is known)
        // so we don't spawn a UI update per network chunk.
        if received - lastReportedBytes >= 131_072 || (total > 0 && received >= total) {
            lastReportedBytes = received
            onProgress(received, total)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        defer { keepAlive = nil }
        guard let continuation else { return }
        self.continuation = nil

        if let error {
            continuation.resume(throwing: APIError.networkError(error))
            return
        }
        guard let http = task.response as? HTTPURLResponse else {
            continuation.resume(throwing: APIError.networkError(URLError(.badServerResponse)))
            return
        }
        switch http.statusCode {
        case 200...299:
            continuation.resume(returning: buffer)
        case 401:
            continuation.resume(throwing: APIError.unauthorized)
        case 403:
            continuation.resume(throwing: APIError.forbidden("Forbidden"))
        case 404:
            continuation.resume(throwing: APIError.notFound)
        default:
            continuation.resume(throwing: APIError.serverError(http.statusCode, String(data: buffer, encoding: .utf8)))
        }
    }
}

/// Delegate that turns an SSE (`text/event-stream`) response into a stream of
/// `RefreshEvent`s, parsing frames incrementally as data arrives.
///
/// Callbacks are serialized on a single-concurrency delegate queue, so the
/// mutable parse state (`buffer`, `eventCount`, `finished`) needs no extra
/// locking — hence the `@unchecked Sendable` conformance.
private final class SSEStreamDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let continuation: AsyncThrowingStream<RefreshEvent, Error>.Continuation
    private let path: String
    private let logger: Logger
    private let decoder: JSONDecoder
    private var buffer = Data()
    private var eventCount = 0
    private var finished = false

    /// SSE frames are separated by a blank line, i.e. a double newline.
    private static let frameSeparator = Data([0x0a, 0x0a]) // "\n\n"

    init(
        continuation: AsyncThrowingStream<RefreshEvent, Error>.Continuation,
        path: String,
        logger: Logger
    ) {
        self.continuation = continuation
        self.path = path
        self.logger = logger
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        self.decoder = decoder
        super.init()
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(code) else {
            logger.error("SSE stream failed: HTTP \(code) for \(self.path, privacy: .public)")
            finish(throwing: APIError.serverError(code, "SSE stream failed"))
            completionHandler(.cancel)
            return
        }
        logger.info("SSE connected (HTTP \(code)) for \(self.path, privacy: .public)")
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        while let range = buffer.range(of: Self.frameSeparator) {
            let frame = buffer.subdata(in: buffer.startIndex..<range.lowerBound)
            buffer.removeSubrange(buffer.startIndex..<range.upperBound)
            handleFrame(frame)
            if finished { return }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        defer { session.finishTasksAndInvalidate() }
        if let urlError = error as? URLError, urlError.code == .cancelled {
            finish(throwing: nil) // consumer stopped iterating — not an error
            return
        }
        if let error {
            logger.error("SSE network error for \(self.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
            finish(throwing: APIError.networkError(error))
        } else {
            logger.info("SSE stream ended after \(self.eventCount) event(s) for \(self.path, privacy: .public)")
            finish(throwing: nil)
        }
    }

    /// Parse one SSE frame: keep the last `data:` payload, decode, and yield.
    private func handleFrame(_ frame: Data) {
        guard let text = String(data: frame, encoding: .utf8) else { return }
        var payload = ""
        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            guard line.hasPrefix("data:") else { continue } // ignore `event:`/`: ping`
            let rest = line.dropFirst(5)
            payload = rest.first == " " ? String(rest.dropFirst()) : String(rest)
        }
        guard !payload.isEmpty, let data = payload.data(using: .utf8) else { return }
        do {
            let event = try decoder.decode(RefreshEvent.self, from: data)
            eventCount += 1
            logger.debug("SSE event #\(self.eventCount) type=\(event.type, privacy: .public) stage=\(event.stage ?? "-", privacy: .public)")
            continuation.yield(event)
            if event.type == "complete" || event.type == "error" {
                finish(throwing: nil)
            }
        } catch {
            // Never silently swallow a frame. Log the raw payload, and if it was
            // a terminal frame whose nested `pack` failed to decode, still end the
            // stream so the UI doesn't hang on the spinner forever.
            logger.error("SSE decode failed: \(error.localizedDescription, privacy: .public) raw=\(payload, privacy: .public)")
            if let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
               let type = obj["type"] as? String,
               type == "complete" || type == "error" {
                // Salvage frame: the terminal event's own decode failed, so the
                // fresh D-0 payloads it may have carried are gone too. Nil is the
                // honest value — the caller keeps what it already had rather than
                // blanking a panel.
                continuation.yield(RefreshEvent(
                    type: type, stage: nil, detail: nil, label: nil, progress: nil,
                    pack: nil, elapsedSeconds: nil, refreshDecision: nil,
                    observations: nil, sigmets: nil, observed: nil,
                    message: obj["message"] as? String))
                finish(throwing: nil)
            }
        }
    }

    private func finish(throwing error: Error?) {
        guard !finished else { return }
        finished = true
        if let error {
            continuation.finish(throwing: error)
        } else {
            continuation.finish()
        }
    }
}
