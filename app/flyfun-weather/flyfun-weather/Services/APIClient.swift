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

    /// Fetch raw data (for images, file downloads).
    func requestData(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        Self.logger.debug("\(method) \(path)")

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
