import Foundation

/// Request body for `POST /api/flights/bulk-delete`.
struct BulkDeleteRequest: Codable, Sendable {
    let ids: [String]
}

/// Result of a bulk delete. The endpoint is owner-scoped and forgiving: ids the
/// caller doesn't own (or that no longer exist) come back in `notFound` instead
/// of failing the request, so a *partial* success is a normal response shape, not
/// an error — callers must surface it. Keys arrive snake_case (`not_found`) and
/// map via the shared decoder's `.convertFromSnakeCase`.
struct BulkDeleteResponse: Codable, Sendable {
    let deleted: [String]
    let notFound: [String]
}

/// Thrown when a chunked bulk delete fails part-way through. Deletion is not
/// transactional across chunks: whatever earlier chunks confirmed is *already
/// gone server-side*, so a plain rethrow would strand those flights in the local
/// list (tappable, 404 on open) with their packs orphaned on disk. `partial`
/// carries the confirmed result so callers can still evict and drop them.
struct BulkDeletePartialFailure: Error {
    let partial: BulkDeleteResponse
    let underlying: Error
}

extension BulkDeleteResponse {
    /// Server cap on `ids` per request (`BulkDeleteRequest.ids`, `max_length=200`
    /// in `api/flights.py`). A "Select All" over a long logbook exceeds it, so
    /// requests are split rather than rejected.
    static let maxIdsPerRequest = 200

    static let empty = BulkDeleteResponse(deleted: [], notFound: [])

    /// Split `ids` into server-sized chunks, send each through `send`, and merge
    /// the results in request order.
    ///
    /// Pure control flow around an injected sender so the chunk boundaries and the
    /// merge are testable without a network layer. Chunks are sent sequentially:
    /// each one deletes rows and `rmtree`s pack directories server-side, so a
    /// failure part-way through throws `BulkDeletePartialFailure` carrying what
    /// the earlier chunks confirmed — those deletes are real and must not be lost
    /// to the error. A failure on the very first chunk rethrows as-is.
    static func sendChunked(
        ids: [String],
        chunkSize: Int = maxIdsPerRequest,
        send: ([String]) async throws -> BulkDeleteResponse
    ) async throws -> BulkDeleteResponse {
        guard !ids.isEmpty else { return .empty }
        let size = max(1, chunkSize)
        var deleted: [String] = []
        var notFound: [String] = []
        for start in stride(from: 0, to: ids.count, by: size) {
            let chunk = Array(ids[start..<min(start + size, ids.count)])
            do {
                let response = try await send(chunk)
                deleted.append(contentsOf: response.deleted)
                notFound.append(contentsOf: response.notFound)
            } catch {
                guard !deleted.isEmpty || !notFound.isEmpty else { throw error }
                throw BulkDeletePartialFailure(
                    partial: BulkDeleteResponse(deleted: deleted, notFound: notFound),
                    underlying: error
                )
            }
        }
        return BulkDeleteResponse(deleted: deleted, notFound: notFound)
    }
}
