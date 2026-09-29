import Foundation
import MapKit
import OSLog

/// State for the historical map (#629, port of the web `HistoricalTab`): a past
/// instant on the 30-min UTC grid, a model lead (Latest / D-1…D-6), a source
/// (METAR / TAF / Worst / Majority / GFS / ICON / ECMWF) and a metric.
///
/// Every selection rule lives on the server (`tasks/historical_map.py`); this VM
/// only fetches on an instant or lead change and recolours on a source or
/// metric change, presenting the chosen source to the forecast map's marker
/// layer (`ForecastMapKitView`) as a `ForecastMapResponse`.
@Observable
@MainActor
final class HistoricalMapViewModel {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "HistoricalMap")

    /// Identity of one payload.
    struct SlotKey: Hashable { let at: Date; let lead: Int }

    /// Leads the server stores (web `HISTORICAL_LEADS`); the range response
    /// narrows which models each one carries.
    static let leads = Array(0...6)

    // MARK: - Published state

    private(set) var range: HistoricalRangeResponse?
    /// The selected instant, always on the grid once the range has loaded.
    private(set) var selectedInstant: Date?
    private(set) var lead = 0
    private(set) var source: HistoricalSource = .metar
    var metric = "flight_category"
    private(set) var selectedIcao: String?

    private(set) var payload: HistoricalMapResponse?
    /// The payload as the marker layer reads it: only the airports that have
    /// something for `source`, each presenting that source.
    private(set) var mapPayload: ForecastMapResponse?
    /// Bumped whenever `mapPayload` is replaced, so the map rebuilds its markers
    /// (a new instant/lead, or a source switch that changes which airports show).
    private(set) var payloadRevision = 0
    private(set) var isLoading = false
    private(set) var loadError: String?
    private(set) var didLoadOnce = false
    /// Why a tapped source can't be shown — the view presents it as an alert, so
    /// an unavailable source explains itself instead of sitting inert.
    var notice: String?

    let initialRegion = ForecastMapViewModel.europeRegion
    var focusRequest: ForecastMapViewModel.FocusRequest?

    // MARK: - Private

    private let repository: any BriefingRepository
    private let now: () -> Date
    private var cache: [SlotKey: HistoricalMapResponse] = [:]
    private var lruOrder: [SlotKey] = []
    private static let lruCapacity = 8
    /// A past instant stops changing once every input has landed (server
    /// `FINAL_AFTER`); only those are kept in the LRU.
    private static let finalAfter: TimeInterval = 2 * 3600
    /// Deep-linked instant, applied (and clamped) once the range loads.
    private var requestedInstant: Date?
    private var pendingOpenIcao: String?
    private var loadTask: Task<Void, Never>?

    init(repository: any BriefingRepository,
         deepLink: HistoricalMapDeepLink? = nil,
         now: @escaping () -> Date = Date.init) {
        self.repository = repository
        self.now = now
        if let deepLink {
            requestedInstant = deepLink.instant
            if let l = deepLink.lead, Self.leads.contains(l) { lead = l }
            if let s = deepLink.source.flatMap(HistoricalSource.init(rawValue:)) { source = s }
            if let m = deepLink.metric { metric = m }
            pendingOpenIcao = deepLink.airport
        }
    }

    // MARK: - Loading

    /// Load the range, clamp the selection into it, then fetch. Idempotent.
    func start() {
        guard !didLoadOnce, loadTask == nil else { return }
        loadTask = Task { await initialLoad() }
    }

    private func initialLoad() async {
        isLoading = true
        loadError = nil
        do {
            range = try await repository.historicalRange()
        } catch {
            Self.logger.warning("historical range failed: \(error.localizedDescription)")
            loadError = error.localizedDescription
            isLoading = false
            loadTask = nil
            return
        }
        selectedInstant = clamp(requestedInstant ?? latest)
        await load()
        // A failed first payload keeps the full-screen error (with Retry) up.
        didLoadOnce = payload != nil
        loadTask = nil
        if let apt = pendingOpenIcao, payload?.airports.contains(where: { $0.icao == apt }) == true {
            select(icao: apt, biasForSheet: false)
        }
        pendingOpenIcao = nil
    }

    /// Retry after a failed first load.
    func retry() {
        guard loadTask == nil else { return }
        didLoadOnce = false
        start()
    }

    private func load() async {
        guard let at = selectedInstant else { return }
        let key = SlotKey(at: at, lead: lead)
        func isCurrent() -> Bool { selectedInstant == key.at && lead == key.lead }
        if let cached = cache[key] {
            touchLRU(key)
            setPayload(cached)
            isLoading = false
            return
        }
        isLoading = true
        loadError = nil
        do {
            let resp = try await repository.historicalMap(at: at, lead: lead)
            if at < now().addingTimeInterval(-Self.finalAfter) { store(resp, for: key) }
            // The user may have stepped on while this was in flight.
            if isCurrent() { setPayload(resp) }
        } catch let error as APIError where error.isCancellation {
            // benign
        } catch {
            Self.logger.warning("historical map failed: \(error.localizedDescription)")
            if isCurrent() {
                // Don't leave the previous instant on screen under the new title.
                loadError = error.localizedDescription
                payload = nil
                rebuildMapPayload()
            }
        }
        if isCurrent() { isLoading = false }
    }

    private func setPayload(_ resp: HistoricalMapResponse) {
        payload = resp
        loadError = nil
        rebuildMapPayload()
    }

    private func rebuildMapPayload() {
        mapPayload = payload.map { payload in
            ForecastMapResponse(
                forecastTime: payload.at,
                modelInitTimes: [:],
                airports: payload.airports.compactMap { $0.forecastAirport(for: source) }
            )
        }
        payloadRevision &+= 1
    }

    private func store(_ resp: HistoricalMapResponse, for key: SlotKey) {
        cache[key] = resp
        touchLRU(key)
        while lruOrder.count > Self.lruCapacity {
            cache[lruOrder.removeFirst()] = nil
        }
    }

    private func touchLRU(_ key: SlotKey) {
        lruOrder.removeAll { $0 == key }
        lruOrder.append(key)
    }

    // MARK: - Time grid

    private var stepSeconds: TimeInterval { TimeInterval((range?.stepMinutes ?? 30) * 60) }

    /// Latest selectable instant (server's floor of "now"); now before the range loads.
    var latest: Date {
        range.flatMap { HistoricalTime.parse($0.latest) } ?? floorToStep(now())
    }

    /// Earliest selectable instant: the oldest observation, else the oldest model.
    var earliest: Date {
        let iso = range?.earliestObservation ?? range?.earliestModel
        return iso.flatMap(HistoricalTime.parse) ?? latest
    }

    /// First day with model data; days before it are METAR/TAF only.
    var earliestModel: Date? { range?.earliestModel.flatMap(HistoricalTime.parse) }

    private func floorToStep(_ date: Date) -> Date {
        let t = date.timeIntervalSince1970
        return Date(timeIntervalSince1970: t - t.truncatingRemainder(dividingBy: stepSeconds))
    }

    /// Snap an instant into [earliest, latest] on the grid (web `clampState`).
    func clamp(_ date: Date) -> Date {
        var at = date
        if at > latest { at = latest }
        if at < earliest { at = earliest }
        return floorToStep(at)
    }

    /// Every slot of the selected UTC day, for the time menu.
    var timeSlots: [Date] {
        guard let at = selectedInstant else { return [] }
        let start = HistoricalTime.utcCalendar.startOfDay(for: at)
        let count = Int(86_400 / stepSeconds)
        return (0..<count).map { start.addingTimeInterval(Double($0) * stepSeconds) }
    }

    /// Whether a slot is inside the selectable range (future and pre-history
    /// slots stay listed but disabled, as on the web).
    func isSelectable(_ slot: Date) -> Bool {
        slot <= latest && slot >= floorToStep(earliest)
    }

    /// Whether a UTC day predates the model history (METAR/TAF only).
    func isObservationOnly(_ day: Date) -> Bool {
        guard let earliestModel else { return true }
        return HistoricalTime.utcCalendar.startOfDay(for: day)
            < HistoricalTime.utcCalendar.startOfDay(for: earliestModel)
    }

    func selectInstant(_ date: Date) {
        let at = clamp(date)
        guard at != selectedInstant else { return }
        selectedInstant = at
        Task { await load() }
    }

    /// Pick a UTC day, keeping the time of day (then clamping, so choosing today
    /// with an evening time lands on the latest slot).
    func selectDay(_ day: Date) {
        guard let at = selectedInstant else { return }
        let cal = HistoricalTime.utcCalendar
        let offset = at.timeIntervalSince(cal.startOfDay(for: at))
        selectInstant(cal.startOfDay(for: day).addingTimeInterval(offset))
    }

    /// Step by ±1 slot, crossing midnight into the adjacent day.
    func step(_ delta: Int) {
        guard let at = selectedInstant else { return }
        let next = at.addingTimeInterval(Double(delta) * stepSeconds)
        guard isSelectable(next) else { return }
        selectInstant(next)
    }

    var canStepBack: Bool {
        selectedInstant.map { isSelectable($0.addingTimeInterval(-stepSeconds)) } ?? false
    }

    var canStepForward: Bool {
        selectedInstant.map { isSelectable($0.addingTimeInterval(stepSeconds)) } ?? false
    }

    func selectLead(_ newLead: Int) {
        guard newLead != lead, Self.leads.contains(newLead) else { return }
        lead = newLead
        Task { await load() }
    }

    /// Models the server stores at a lead (a model past its map horizon isn't).
    func models(forLead lead: Int) -> [String] {
        range?.leads.first { $0.leadDays == lead }?.models ?? ["gfs", "icon", "ecmwf"]
    }

    // MARK: - Source

    /// Switch source (a pure recolour). An unavailable source doesn't switch; it
    /// sets `notice` so it can say why.
    func selectSource(_ s: HistoricalSource) {
        guard sourceAvailable(s) else {
            notice = unavailableText(s)
            return
        }
        guard s != source else { return }
        source = s
        rebuildMapPayload()
    }

    /// Unknown until loaded, so nothing is greyed out before the first payload.
    func sourceAvailable(_ s: HistoricalSource) -> Bool {
        guard let payload else { return true }
        if s.isConsensus {
            return HistoricalSource.nwpModels.contains { payload.sources[$0.rawValue]?.available == true }
        }
        return payload.sources[s.rawValue]?.available ?? false
    }

    /// Why a source has nothing at this instant and lead, in words.
    func unavailableText(_ s: HistoricalSource) -> String {
        if s.isObserved { return "No \(s.label) reports at this time." }
        if s.isConsensus { return "No model has data at this time and lead." }
        switch payload?.sources[s.rawValue]?.reason {
        case "beyond_horizon":
            return "\(s.label) isn't stored \(lead) day\(lead == 1 ? "" : "s") ahead. Its map horizon is shorter."
        case "no_valid_time":
            return "No model sample at this time. Models are sampled every 3 h from 06Z to 18Z."
        case "no_run":
            return "No \(s.label) run was stored for this time and lead."
        default:
            return "No \(s.label) data at this time."
        }
    }

    /// One line under the pickers: why the map is empty, or what it shows.
    var statusLine: String? {
        if let loadError { return "Couldn't load this time: \(loadError)" }
        guard let payload else { return nil }
        guard sourceAvailable(source) else { return unavailableText(source) }
        let shown = mapPayload?.airports.count ?? 0
        return "\(shown) airports · " + provenance(source, in: payload)
    }

    private func provenance(_ s: HistoricalSource, in payload: HistoricalMapResponse) -> String {
        switch s {
        case .metar:
            return "reports ≤\(payload.sources["metar"]?.maxAgeMin ?? 90) min old"
        case .taf:
            return "TAFs read at \(HistoricalTime.timeLabel(payload.at))"
        case .worst, .majority:
            let present = HistoricalSource.nwpModels.filter { payload.sources[$0.rawValue]?.available == true }
            return "\(s.label.lowercased()) of " + present.map(\.label).joined(separator: " · ")
        case .gfs, .icon, .ecmwf:
            guard let info = payload.sources[s.rawValue] else { return s.label }
            var text = "run \(HistoricalTime.runLabel(info.modelInitTime)) → \(HistoricalTime.timeLabel(info.validTime))"
            if let h = info.leadHours { text += " (+\(h)h)" }
            return text
        }
    }

    // MARK: - Selection

    func select(icao: String, biasForSheet: Bool) {
        selectedIcao = icao
        if let apt = payload?.airports.first(where: { $0.icao == icao }) {
            focusRequest = ForecastMapViewModel.FocusRequest(
                center: CLLocationCoordinate2D(latitude: apt.lat, longitude: apt.lon),
                span: nil, biasForSheet: biasForSheet
            )
        }
    }

    func deselect() { selectedIcao = nil }

    /// The tapped airport, from the full payload (it stays open even when the
    /// selected source has nothing for it).
    var selectedAirport: HistoricalAirport? {
        guard let selectedIcao else { return nil }
        return payload?.airports.first { $0.icao == selectedIcao }
    }
}

// MARK: - UTC time helpers

/// Parsing and labels for the historical map's UTC instants, shared by the VM,
/// the map screen and the airport card.
enum HistoricalTime {
    static let utcCalendar: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()

    /// Server timestamps are Python `isoformat()` (`+00:00`, sometimes with
    /// microseconds); both shapes parse.
    static func parse(_ iso: String) -> Date? { Date.parseISO8601(iso) }

    /// "14:30Z"; empty when absent or unparseable.
    static func timeLabel(_ iso: String?) -> String {
        guard let iso, let d = parse(iso) else { return "" }
        return timeLabel(d)
    }

    static func timeLabel(_ date: Date) -> String { hhmm.string(from: date) + "Z" }

    /// "28 00Z": day-of-month + hour, the compact model-run notation.
    static func runLabel(_ iso: String?) -> String {
        guard let iso, let d = parse(iso) else { return "" }
        return run.string(from: d) + "Z"
    }

    /// "Mon 28 Sep 2026".
    static func dateLabel(_ date: Date) -> String { longDate.string(from: date) }

    /// "Mon 28 Sep".
    static func shortDateLabel(_ date: Date) -> String { shortDate.string(from: date) }

    /// "2026-09-28" (the `hist.date` token).
    static func isoDay(_ date: Date) -> String { isoDayFormatter.string(from: date) }

    /// "14:30" (the `hist.time` token).
    static func isoTime(_ date: Date) -> String { hhmm.string(from: date) }

    /// Rebuild an instant from `hist.date` + `hist.time` tokens.
    static func instant(date: String, time: String) -> Date? {
        dateTime.date(from: "\(date) \(time)")
    }

    private static let hhmm = formatter("HH:mm")
    private static let run = formatter("dd HH")
    private static let longDate = formatter("EEE d MMM yyyy")
    private static let shortDate = formatter("EEE d MMM")
    private static let isoDayFormatter = formatter("yyyy-MM-dd")
    private static let dateTime = formatter("yyyy-MM-dd HH:mm")

    private static func formatter(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.calendar = utcCalendar
        f.timeZone = utcCalendar.timeZone
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = format
        return f
    }
}
