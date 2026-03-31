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
