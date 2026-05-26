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
actor APIClient {
    let baseURL: URL
    private let session: URLSession
    private var jwt: String

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "APIClient")

    init(baseURL: URL, jwt: String) {
        self.baseURL = baseURL
        self.jwt = jwt
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func updateJWT(_ newJWT: String) {
        self.jwt = newJWT
    }

    // MARK: - Generic request methods

    /// Fetch and decode a JSON response.
    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil) async throws -> T {
        let data = try await requestData(path, method: method, body: body)
        do {
            return try JSONDecoder.weatherBrief.decode(T.self, from: data)
        } catch {
            Self.logger.error("Decoding failed for \(path): \(error)")
            throw APIError.decodingError(error)
        }
    }

    /// Stream SSE events from the server.
    func streamSSE(_ path: String, method: String = "POST") -> AsyncThrowingStream<RefreshEvent, Error> {
        // Capture actor state before creating the stream
        let url = baseURL.appendingPathComponent(path)
        let currentJwt = jwt
        let currentSession = session

        return AsyncThrowingStream { continuation in
            Task {
                var request = URLRequest(url: url)
                request.httpMethod = method
                request.setValue("Bearer \(currentJwt)", forHTTPHeaderField: "Authorization")
                request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                request.timeoutInterval = 300

                // Local decoder to avoid MainActor-isolated JSONDecoder.weatherBrief
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase

                do {
                    let (bytes, response) = try await currentSession.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                        continuation.finish(throwing: APIError.serverError(code, "SSE stream failed"))
                        return
                    }

                    var buffer = ""
                    for try await line in bytes.lines {
                        if line.hasPrefix("data: ") {
                            buffer = String(line.dropFirst(6))
                        } else if line.isEmpty && !buffer.isEmpty {
                            if let data = buffer.data(using: .utf8),
                               let event = try? decoder.decode(RefreshEvent.self, from: data) {
                                continuation.yield(event)
                                if event.type == "complete" || event.type == "error" {
                                    continuation.finish()
                                    return
                                }
                            }
                            buffer = ""
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: APIError.networkError(error))
                }
            }
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
    func requestData(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        let url = baseURL.appendingPathComponent(path)
        return try await _fetch(url: url, method: method, body: body, label: path)
    }

    /// Fetch raw data while reporting transfer progress as bytes arrive.
    ///
    /// `progress` receives `(receivedBytes, totalBytes)`. The total comes from the server's
    /// `X-Uncompressed-Length` header: `Content-Length` reflects the gzip-compressed size, but
    /// URLSession transparently decompresses, so we count decompressed bytes against the
    /// decompressed total. If the header is absent, `totalBytes` is `-1` (size unknown).
    func requestDataStreaming(
        _ path: String,
        progress: @Sendable @escaping (_ receivedBytes: Int64, _ totalBytes: Int64) -> Void
    ) async throws -> Data {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")

        Self.logger.debug("GET \(path) (streaming)")

        return try await withCheckedThrowingContinuation { continuation in
            let delegate = StreamingDownloadDelegate(onProgress: progress, continuation: continuation)
            let task = session.dataTask(with: request)
            task.delegate = delegate
            task.resume()
        }
    }

    private func _fetch(url: URL, method: String, body: Data?, label: String) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        Self.logger.debug("\(method) \(label)")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.networkError(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }

        switch http.statusCode {
        case 200...299:
            return data
        case 401:
            throw APIError.unauthorized
        case 403:
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
            throw APIError.forbidden(msg ?? "Forbidden")
        case 404:
            throw APIError.notFound
        default:
            let msg = String(data: data, encoding: .utf8)
            throw APIError.serverError(http.statusCode, msg)
        }
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
