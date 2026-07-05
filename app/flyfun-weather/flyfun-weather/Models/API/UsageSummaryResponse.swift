import Foundation

/// Slim decode of `GET /api/user/usage`. The server payload also carries
/// today/month service-usage breakdowns, but the iOS app only needs the durable
/// timing-scan flags that gate the first-time Flexibility explainer (#357), so
/// we decode just those two keys and let the decoder ignore the rest.
struct UsageSummaryResponse: Codable, Sendable {
    /// Durable, all-time "has this user ever run a timing scan?" flag. Backs the
    /// first-time Flexibility explainer gate — the modal shows only until the
    /// user has genuinely run ≥1 scan.
    let timeScanUsed: Bool
    /// All-time count of timing scans run by this user. Surfaced for future
    /// heavy-user triggers; not enforced anywhere today.
    let timeScanCount: Int

    private enum CodingKeys: String, CodingKey {
        case timeScanUsed
        case timeScanCount
    }

    /// Tolerate a partial/legacy payload: default both flags to "not used" when
    /// the server omits them (older servers, or a decode of just part of the
    /// response), so the explainer errs toward gently informing.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        timeScanUsed = (try? c.decode(Bool.self, forKey: .timeScanUsed)) ?? false
        timeScanCount = (try? c.decode(Int.self, forKey: .timeScanCount)) ?? 0
    }
}
