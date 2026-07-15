import Foundation
import MapKit
import OSLog

/// State for the pan-European forecast map (#420): the day/hour/metric/model
/// selection, the per-(day, hour) payload with an in-memory LRU + adjacent-hour
/// prefetch, and the tapped-airport selection. One instance is shared by the
/// iPhone `fullScreenCover` and iPad detail-pane containers, so the map view
/// itself stays container-agnostic.
///
/// Metric and model switches are a **pure recolour** — they mutate published
/// state and the map re-reads the already-fetched payload. Only a day/hour
/// change hits the network.
@Observable
@MainActor
final class ForecastMapViewModel {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "ForecastMap")

    /// Identity of one payload slot.
    struct SlotKey: Hashable { let day: Int; let hour: Int }

    /// A camera move the map view should apply once, then clear.
    struct FocusRequest: Equatable {
        let center: CLLocationCoordinate2D
        /// Zoom span (nil = keep current zoom).
        let span: MKCoordinateSpan?
        /// Bias the centre latitude down so the airport sits above a bottom sheet.
        let biasForSheet: Bool

        static func == (l: FocusRequest, r: FocusRequest) -> Bool {
            l.center.latitude == r.center.latitude && l.center.longitude == r.center.longitude
                && l.span?.latitudeDelta == r.span?.latitudeDelta && l.biasForSheet == r.biasForSheet
        }
    }

    // MARK: - Published state

    /// The ragged day/hour/model grid, drawn from `/maps/forecast/days`.
    private(set) var days: [ForecastDay] = []
    private(set) var maxDay = 6

    private(set) var selectedDay = 0
    private(set) var selectedHour = 12
    var metric = "flight_category"
    var mode: ForecastModelMode = .worst
    private(set) var selectedIcao: String?

    /// Current slot's payload (nil until first load).
    private(set) var payload: ForecastMapResponse?
    /// Bumped on every payload assignment. The map rebuilds its annotations when
    /// this changes (NOT on the day/hour string), so the nil→populated transition
    /// on cold open and every slot swap both repopulate markers; a metric/model
    /// switch leaves it unchanged and is a pure recolour.
    private(set) var payloadRevision = 0
    private(set) var isLoading = false
    private(set) var loadError: String?
    /// True once the grid + first payload have loaded (drives the placeholder).
    private(set) var didLoadOnce = false

    /// Initial camera region (Europe, or centred on the pilot's usual departure
    /// area once frequent airports resolve). Consumed by the map on first appear.
    private(set) var initialRegion = ForecastMapViewModel.europeRegion
    /// A one-shot camera move (hour step recolour never moves; selection nudges).
    var focusRequest: FocusRequest?

    // MARK: - Private

    private let repository: any BriefingRepository
    private var slots: [SlotKey: ForecastMapResponse] = [:]
    private var lruOrder: [SlotKey] = []
    private static let lruCapacity = 8
    /// Cold-open target from frequent airports; resolved to a region once a
    /// payload with coordinates arrives. Cleared after it's applied once.
    private var coldOpenIcao: String?
    /// A deep-link may pin an airport to open once the payload loads.
    private var pendingOpenIcao: String?
    /// Suppresses cold-open centring when a deep link already set the view.
    private var hasExplicitLocation = false
    /// Set once the user pans/zooms the map, so a late-resolving cold-open target
    /// never yanks the camera out from under them.
    private var userDidInteract = false
    /// Slots with a fetch in flight, so a `selectHour` and a prefetch racing on the
    /// same (day, hour) don't both hit the network.
    private var inFlight: Set<SlotKey> = []
    private var loadTask: Task<Void, Never>?

    /// `deepLink` is the parsed `/maps.html?fc.*` share state (`MapDeepLink`), or
    /// nil for a plain "open the map" (toolbar button). It's applied once at init;
    /// a *new* inbound deep link re-creates the view (`.id` on the container) so a
    /// re-entrant link isn't dropped.
    init(repository: any BriefingRepository, deepLink: MapDeepLink? = nil) {
        self.repository = repository
        if let deepLink {
            hasExplicitLocation = true
            if let d = deepLink.day { selectedDay = d }
            if let h = deepLink.hour { selectedHour = h }
            if let m = deepLink.model { mode = ForecastModelMode(token: m) }
            if let mt = deepLink.metric { metric = mt }
            pendingOpenIcao = deepLink.airport
        }
    }

    // MARK: - Loading

    /// Load the grid, resolve the initial slot, then fetch it. Idempotent — safe
    /// to call on appear.
    func start() {
        guard !didLoadOnce, loadTask == nil else { return }
        loadTask = Task { await initialLoad() }
    }

    private func initialLoad() async {
        isLoading = true
        loadError = nil
        // 1. The grid (best-effort; a failure falls back to a near-days default).
        do {
            let resp = try await repository.forecastDays()
            days = resp.days
            maxDay = resp.maxDay
        } catch {
            Self.logger.warning("forecast days failed: \(error.localizedDescription)")
            days = Self.fallbackDays()
        }
        // 2. Snap the selection into the real grid (deep link may be off-grid).
        snapSelectionIntoGrid(smartDefaultHour: !hasExplicitLocation)
        // 3. Cold-open centring target (non-blocking, non-fatal).
        if !hasExplicitLocation {
            Task { await resolveColdOpenTarget() }
        }
        // 4. First payload.
        await loadSlot(day: selectedDay, hour: selectedHour, moveCamera: false)
        didLoadOnce = true
        isLoading = false
        loadTask = nil
        // 5. Open (and centre on) a deep-linked airport once its data is present,
        //    so a shared link lands on that airport, not just its day/hour/metric.
        if let apt = pendingOpenIcao, payload?.airports.contains(where: { $0.icao == apt }) == true {
            select(icao: apt, biasForSheet: false)
            pendingOpenIcao = nil
        }
        prefetchAdjacentHours()
    }

    /// Change day: snap the hour into the new day's grid, snap the model if the
    /// active one can't reach it, then fetch.
    func selectDay(_ day: Int) {
        guard day != selectedDay else { return }
        selectedDay = day
        selectedHour = Self.snapHour(selectedHour, into: hours(forDay: day))
        snapModeIntoDay()
        Task { await loadSlot(day: selectedDay, hour: selectedHour, moveCamera: false) }
    }

    func selectHour(_ hour: Int) {
        guard hour != selectedHour else { return }
        selectedHour = Self.snapHour(hour, into: hours(forDay: selectedDay))
        Task { await loadSlot(day: selectedDay, hour: selectedHour, moveCamera: false) }
    }

    /// Step the hour by ±1 within the current day's sample hours (the `‹ ›` feel).
    func stepHour(_ delta: Int) {
        let hrs = hours(forDay: selectedDay)
        guard let idx = hrs.firstIndex(of: selectedHour) else {
            if let first = hrs.first { selectHour(first) }
            return
        }
        let next = idx + delta
        guard hrs.indices.contains(next) else { return }
        selectHour(hrs[next])
    }

    var canStepHourBack: Bool {
        let hrs = hours(forDay: selectedDay)
        guard let idx = hrs.firstIndex(of: selectedHour) else { return false }
        return idx > 0
    }

    var canStepHourForward: Bool {
        let hrs = hours(forDay: selectedDay)
        guard let idx = hrs.firstIndex(of: selectedHour) else { return false }
        return idx < hrs.count - 1
    }

    /// Fetch a slot from the LRU or the network and publish it. `moveCamera`
    /// stays false — a day/hour change recolours in place, it never re-centres.
    private func loadSlot(day: Int, hour: Int, moveCamera: Bool) async {
        let key = SlotKey(day: day, hour: hour)
        if let cached = slots[key] {
            touchLRU(key)
            setPayload(cached)
            prefetchAdjacentHours()
            return
        }
        guard !inFlight.contains(key) else { return }  // a prefetch already owns it
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        isLoading = true
        loadError = nil
        do {
            let resp = try await repository.forecastMap(day: day, hour: hour)
            // `store` publishes the payload iff this slot is still the selected one,
            // so a late fetch for a scrubbed-past slot doesn't clobber the view, and
            // an in-flight *prefetch* the user then selects still shows (loadSlot
            // may have bailed on the in-flight guard above — store is the one seam).
            store(resp, for: key)
        } catch let error as APIError where error.isCancellation {
            // benign
        } catch {
            Self.logger.warning("forecast map \(day)/\(hour) failed: \(error.localizedDescription)")
            if selectedDay == day, selectedHour == hour, payload == nil {
                loadError = error.localizedDescription
            }
        }
        isLoading = false
        prefetchAdjacentHours()
    }

    /// Prefetch the current day's neighbouring hours (server-cached, so cheap) so
    /// `‹ ›` stepping feels instant.
    private func prefetchAdjacentHours() {
        let hrs = hours(forDay: selectedDay)
        guard let idx = hrs.firstIndex(of: selectedHour) else { return }
        for neighbour in [idx - 1, idx + 1] where hrs.indices.contains(neighbour) {
            let key = SlotKey(day: selectedDay, hour: hrs[neighbour])
            guard slots[key] == nil, !inFlight.contains(key) else { continue }
            inFlight.insert(key)
            Task { [weak self] in
                guard let self else { return }
                defer { self.inFlight.remove(key) }
                if let resp = try? await self.repository.forecastMap(day: key.day, hour: key.hour) {
                    self.store(resp, for: key)
                }
            }
        }
    }

    // MARK: - Selection

    func select(icao: String, biasForSheet: Bool) {
        selectedIcao = icao
        if let apt = payload?.airports.first(where: { $0.icao == icao }) {
            focusRequest = FocusRequest(
                center: CLLocationCoordinate2D(latitude: apt.lat, longitude: apt.lon),
                span: nil, biasForSheet: biasForSheet
            )
        }
    }

    func deselect() { selectedIcao = nil }

    var selectedAirport: ForecastAirport? {
        guard let selectedIcao else { return nil }
        return payload?.airports.first { $0.icao == selectedIcao }
    }

    // MARK: - Grid helpers

    /// Sample hours available for a day, from the grid (falls back to the fine set).
    func hours(forDay day: Int) -> [Int] {
        if let entry = days.first(where: { $0.day == day }), !entry.hours.isEmpty {
            return entry.hours
        }
        return day >= 6 ? [6, 12, 18] : [6, 9, 12, 15, 18]
    }

    /// Models present for a day (empty → assume all three, pre-grid).
    func models(forDay day: Int) -> [String] {
        days.first(where: { $0.day == day })?.models ?? ["gfs", "icon", "ecmwf"]
    }

    /// Whether a model can reach a day — greyed-but-tappable models must never
    /// read as agreement, so the picker disables (not hides) absent ones.
    func modelAvailable(_ model: String, day: Int) -> Bool {
        models(forDay: day).contains(model)
    }

    private func snapSelectionIntoGrid(smartDefaultHour: Bool) {
        selectedDay = min(max(selectedDay, 0), maxDay)
        let hrs = hours(forDay: selectedDay)
        if smartDefaultHour {
            // Cold open: nearest sample hour to the current UTC hour, not always 12Z.
            selectedHour = Self.snapHour(Self.currentUTCHour(), into: hrs)
        } else {
            selectedHour = Self.snapHour(selectedHour, into: hrs)
        }
        snapModeIntoDay()
    }

    /// Fall back to worst consensus when the active individual model can't reach
    /// the selected day (web behaviour).
    private func snapModeIntoDay() {
        if case .model(let m) = mode, !modelAvailable(m, day: selectedDay) {
            mode = .worst
        }
    }

    private static func snapHour(_ hour: Int, into hours: [Int]) -> Int {
        guard !hours.isEmpty else { return hour }
        if hours.contains(hour) { return hour }
        return hours.min(by: { abs($0 - hour) < abs($1 - hour) }) ?? hour
    }

    private static func currentUTCHour() -> Int {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        return cal.component(.hour, from: Date())
    }

    // MARK: - Cold-open centring

    private func resolveColdOpenTarget() async {
        guard let freq = try? await repository.frequentAirports() else { return }
        coldOpenIcao = freq.departures.first?.icao ?? freq.destinations.first?.icao
        applyColdOpenIfPossible()
    }

    /// The user panned/zoomed — freeze cold-open so it can't recentre later.
    func markUserInteracted() { userDidInteract = true }

    private func applyColdOpenIfPossible() {
        guard !hasExplicitLocation, !userDidInteract, let icao = coldOpenIcao,
              let apt = payload?.airports.first(where: { $0.icao == icao }) else { return }
        initialRegion = MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: apt.lat, longitude: apt.lon),
            span: MKCoordinateSpan(latitudeDelta: 8, longitudeDelta: 10)
        )
        focusRequest = FocusRequest(
            center: initialRegion.center, span: initialRegion.span, biasForSheet: false)
        coldOpenIcao = nil
    }

    /// Publish a payload and bump the revision so the map rebuilds its markers.
    private func setPayload(_ resp: ForecastMapResponse) {
        payload = resp
        payloadRevision &+= 1
    }

    // MARK: - LRU

    private func store(_ resp: ForecastMapResponse, for key: SlotKey) {
        slots[key] = resp
        touchLRU(key)
        let current = SlotKey(day: selectedDay, hour: selectedHour)
        // Publish only when this is the on-screen slot (covers both the user-driven
        // loadSlot and a prefetch the user has since navigated onto).
        if key == current { setPayload(resp) }
        // A payload may arrive before the cold-open target resolved.
        applyColdOpenIfPossible()
        while lruOrder.count > Self.lruCapacity {
            let evict = lruOrder.removeFirst()
            if evict == current {
                // Never drop the on-screen slot — re-queue it as most-recent so it
                // stays tracked (dropping it from lruOrder while keeping it in
                // `slots` leaked the entry and pinned stale data).
                lruOrder.append(evict)
            } else {
                slots[evict] = nil
            }
        }
    }

    private func touchLRU(_ key: SlotKey) {
        lruOrder.removeAll { $0 == key }
        lruOrder.append(key)
    }

    // MARK: - Regions & defaults

    static let europeRegion = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: 48, longitude: 10),
        span: MKCoordinateSpan(latitudeDelta: 22, longitudeDelta: 26)
    )

    /// Near-days fallback grid when `/maps/forecast/days` is unreachable (mirrors
    /// the web `fallbackDayGrid`): D+0…D+3, five hours, all three models.
    private static func fallbackDays() -> [ForecastDay] {
        (0...3).map { d in
            ForecastDay(day: d, date: "", available: true,
                        hours: [6, 9, 12, 15, 18], models: ["ecmwf", "gfs", "icon"])
        }
    }
}
