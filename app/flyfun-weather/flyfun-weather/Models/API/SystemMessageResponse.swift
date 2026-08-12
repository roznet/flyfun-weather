import Foundation

/// One entry in the release stream ("What's New"), from `GET /api/messages`.
///
/// The stream is unified across surfaces rather than split by platform: most
/// entries (a new advisory, a threshold change) reach app users the moment the
/// server deploys, with no app release involved. `app_release` marks the entries
/// that *are* an App Store version, whose body is the App Store "What's New" text.
struct SystemMessage: Codable, Identifiable, Equatable, Sendable {
    let id: Int
    /// Applicable date, `YYYY-MM-DD`. The stream is ordered by this rather than
    /// by insert order, so a backfilled entry interleaves chronologically.
    let date: String
    let title: String
    /// Markdown-lite: paragraphs, `- ` bullets, inline emphasis and links.
    let body: String
    /// `feature` | `change` | `fix` | `app_release`. Deliberately a plain String,
    /// not an enum: the server owns the valid set, and an entry in a category
    /// this build predates must still render rather than fail the whole decode.
    let category: String
    let highlight: Bool

    /// Chip label for the category. Hand-copied from the web locale strings
    /// (`messages.category.*` in `web/ts/i18n/locales/en.json`) — keep in sync.
    var categoryLabel: String {
        switch category {
        case "feature": "New"
        case "change": "Change"
        case "fix": "Fix"
        case "app_release": "iOS app"
        default: category.capitalized
        }
    }

    /// The entry's date rendered like the web card ("8 Aug 2026"), or the raw
    /// string if it isn't a parseable `YYYY-MM-DD`.
    var displayDate: String {
        guard let day = Self.isoDate.date(from: date) else { return date }
        return Self.display.string(from: day)
    }

    private static let isoDate: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let display: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "d MMM yyyy"
        return f
    }()
}

/// `GET /api/messages/status` — the unseen badge. The count includes **only**
/// highlighted entries, so a routine fix or a backfilled historical release
/// appears in the stream without lighting the dot.
struct MessagesStatus: Codable, Sendable {
    let unseenCount: Int
    let latestMessageDate: String?
}
