import Foundation

/// The Past flights screen (#1032): search + chips over
/// `GET /api/flights/archive`. Filtering, paging and every count are the
/// server's; this only holds the filter, the loaded rows and the page cursor.
@Observable
@MainActor
final class PastFlightsViewModel {
    static let pageSize = 30

    private(set) var rows: [PastArchiveRow] = []
    private(set) var total = 0
    private(set) var facets: PastArchiveFacets?
    private(set) var isLoading = false
    private(set) var error: String?
    private(set) var filter: PastFilter

    private let repository: any BriefingRepository
    /// Newest request wins: a page or filter change that lands late is dropped.
    private var loadToken = 0
    private var searchTask: Task<Void, Never>?
    /// The filter reload in flight; a newer filter cancels it.
    private var reloadTask: Task<Void, Never>?

    init(repository: any BriefingRepository, filter: PastFilter = .all) {
        self.repository = repository
        self.filter = filter
    }

    var hasMore: Bool { rows.count < total }

    struct MonthSection: Identifiable {
        let title: String
        var rows: [PastArchiveRow]
        /// First row's id: unique even if two runs share a title.
        var id: String { rows.first?.id ?? title }
    }

    private static let monthFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "LLLL yyyy"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    /// Rows grouped by month (newest first) for the filtered view.
    var monthSections: [MonthSection] {
        var out: [MonthSection] = []
        for row in rows {
            let title = row.departureDate.map { Self.monthFormatter.string(from: $0) } ?? ""
            if out.last?.title == title {
                out[out.count - 1].rows.append(row)
            } else {
                out.append(MonthSection(title: title, rows: [row]))
            }
        }
        return out
    }

    func reload() async {
        loadToken += 1
        let token = loadToken
        isLoading = true
        do {
            let resp = try await repository.pastArchive(filter.queryItems(limit: Self.pageSize, offset: 0))
            guard token == loadToken else { return }
            rows = resp.rows
            total = resp.total
            facets = resp.facets
            error = nil
        } catch {
            // Superseded (cancelled by a newer filter): that request owns the state.
            guard token == loadToken, !Task.isCancelled else { return }
            // Drop the previous filter's rows and counts: beside the new chips
            // they would read as this filter's results. The view then shows the error.
            rows = []
            total = 0
            facets = nil
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    func loadMoreIfNeeded(after row: PastArchiveRow) async {
        guard row.id == rows.last?.id, hasMore, !isLoading else { return }
        loadToken += 1
        let token = loadToken
        isLoading = true
        do {
            let resp = try await repository.pastArchive(filter.queryItems(limit: Self.pageSize, offset: rows.count))
            guard token == loadToken else { return }
            let seen = Set(rows.map(\.id))
            rows += resp.rows.filter { !seen.contains($0.id) }
            total = resp.total
            facets = resp.facets
            error = nil
        } catch {
            guard token == loadToken else { return }
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    func update(_ change: (inout PastFilter) -> Void) {
        var next = filter
        change(&next)
        guard next != filter else { return }
        filter = next
        reloadTask?.cancel()
        reloadTask = Task { await reload() }
    }

    func toggleTag(_ tag: String) { update { $0.tags = Self.toggled($0.tags, tag) } }
    func toggleOutcome(_ key: String) { update { $0.outcomes = Self.toggled($0.outcomes, key) } }

    /// Debounced: each keystroke would otherwise be a request.
    func setQuery(_ query: String) {
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            self?.update { $0.query = query }
        }
    }

    static func toggled(_ list: [String], _ value: String) -> [String] {
        list.contains(value) ? list.filter { $0 != value } : list + [value]
    }
}
