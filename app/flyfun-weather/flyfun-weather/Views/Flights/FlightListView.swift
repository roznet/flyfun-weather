import SwiftUI
import TipKit

/// What the sidebar has selected: the pan-European forecast map, or a flight's
/// briefing (#420). The map is an iPad detail pane / iPhone `fullScreenCover`;
/// the flight case drives the briefing detail as before.
enum SidebarSelection: Hashable {
    case forecastMap
    case flight(FlightResponse)
}

/// Main screen showing the user's saved flights with sidebar/detail split on iPad.
struct FlightListView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: FlightListViewModel?
    @State private var selection: SidebarSelection?
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic
    @State private var showAddFlight = false
    @State private var showSettings = false
    @State private var showSignOutWarning = false
    @State private var editingFlight: FlightResponse?
    /// Compact-width forecast-map cover (the app's first `fullScreenCover`) and
    /// the deep-link state it opens with.
    @State private var showMapCover = false
    @State private var mapDeepLink: MapDeepLink?
    /// Bumped on every `openForecastMap`, used as the map view's `.id` so a *new*
    /// inbound deep link while the map is already open re-creates the view (the
    /// deep link is applied only at `ForecastMapViewModel.init`).
    @State private var mapOpenToken = 0

    private var isCompact: Bool { horizontalSizeClass == .compact }
    // Guards the one authoritative-reload retry in `applyPendingNavigation` so a
    // deep-link to a just-created flight (push tap / Universal Link) isn't dropped
    // against a stale cache-first list. Holds the flight id we've already retried.
    @State private var reloadRetryFlightId: String?
    /// "Past" flights start collapsed (like the web app) so the list opens on
    /// what's upcoming/recent.
    @State private var pastExpanded = false

    // Contextual tips (#312): the add-flight / forecast-map pair reads side by
    // side, so the forecast-map tip is sequenced after the add-flight tip
    // donates its event.
    private let addFlightTip = AddFlightTip()
    private let forecastMapTip = ForecastMapTip()

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            Group {
                if let viewModel {
                    LoadingStateView(state: viewModel.state, retryAction: viewModel.loadFlights) { flights in
                        if flights.isEmpty {
                            emptyStateView
                        } else {
                            VStack(spacing: 0) {
                            // Offline banner (#304/#318): the list is being served
                            // from the on-disk cache. Rows without a downloaded
                            // pack are dimmed + non-tappable (read-only), so the
                            // banner explains why. Only shown when actually offline.
                            if viewModel.isOffline {
                                OfflineListBanner()
                            }
                            // Utility logbook (§4.4): Future · Recent · Past.
                            // Past is collapsible (collapsed by default).
                            List(selection: $selection) {
                                ForEach(Self.groupedFlights(flights), id: \.title) { group in
                                    if group.title == "Past" {
                                        Section {
                                            if pastExpanded {
                                                ForEach(group.flights) { flight in
                                                    flightRow(flight, viewModel: viewModel)
                                                }
                                            }
                                        } header: {
                                            Button {
                                                withAnimation { pastExpanded.toggle() }
                                            } label: {
                                                HStack {
                                                    Text("Past")
                                                    Spacer()
                                                    Text("\(group.flights.count)")
                                                        .foregroundStyle(.secondary)
                                                    Image(systemName: pastExpanded ? "chevron.down" : "chevron.right")
                                                        .foregroundStyle(.secondary)
                                                }
                                                .contentShape(Rectangle())
                                            }
                                            .buttonStyle(.plain)
                                        }
                                    } else {
                                        Section(group.title) {
                                            ForEach(group.flights) { flight in
                                                flightRow(flight, viewModel: viewModel)
                                            }
                                        }
                                    }
                                }
                            }
                            .refreshable {
                                await viewModel.loadFlights()
                            }
                            .accessibilityIdentifier("flightList")
                            }
                        }
                    }
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Flights")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    // Subtle in-place refresh indicator — the list stays visible
                    // underneath instead of being replaced by a spinner (#1).
                    if viewModel?.isRefreshing == true {
                        ProgressView().controlSize(.small)
                    }
                }
                #if DEBUG
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        ForEach(ServerEnvironment.allCases, id: \.self) { env in
                            Button {
                                appState.setServerEnvironment(env)
                            } label: {
                                if env == AppState.serverEnvironment {
                                    Label(env.label, systemImage: "checkmark")
                                } else {
                                    Text(env.label)
                                }
                            }
                        }
                    } label: {
                        Label("Server", systemImage: "server.rack")
                    }
                }
                #endif
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        Button {
                            showAddFlight = true
                            addFlightTip.invalidate(reason: .actionPerformed)
                        } label: {
                            Label("Add Flight", systemImage: "plus")
                        }
                        .accessibilityIdentifier("addFlightButton")
                        .popoverTip(addFlightTip)
                        Button {
                            openForecastMap(deepLink: nil)
                            forecastMapTip.invalidate(reason: .actionPerformed)
                        } label: {
                            Label("Forecast Map", systemImage: "map")
                        }
                        .accessibilityIdentifier("forecastMapButton")
                        .popoverTip(forecastMapTip)
                        Menu {
                            Button {
                                showSettings = true
                            } label: {
                                Label("Settings", systemImage: "gearshape")
                            }

                            Button {
                                openURL(AppState.defaultBaseURL)
                            } label: {
                                Label("Open Website", systemImage: "safari")
                            }

                            Divider()

                            Button(role: .destructive) {
                                Task {
                                    if let caching = appState.cachingRepository,
                                       !(await caching.cachedPacks()).isEmpty {
                                        showSignOutWarning = true
                                    } else {
                                        appState.logout()
                                    }
                                }
                            } label: {
                                Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                            }
                        } label: {
                            Label("More", systemImage: "ellipsis.circle")
                        }
                    }
                }
            }
            .sheet(isPresented: $showAddFlight) {
                if let repo = appState.repository {
                    AddFlightView(repository: repo) { flight in
                        Task {
                            await viewModel?.loadFlights()
                            selection = .flight(flight)
                        }
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .sheet(item: $editingFlight) { flight in
                if let repo = appState.repository {
                    AddFlightView(repository: repo, flight: flight) { updated in
                        Task {
                            await viewModel?.loadFlights()
                            // Re-open the (regenerated) briefing.
                            selection = .flight(updated)
                        }
                    }
                }
            }
            .alert("Sign Out?", isPresented: $showSignOutWarning) {
                Button("Sign Out", role: .destructive) { appState.logout() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You have downloaded packs. They won't be accessible until you sign in again.")
            }
        } detail: {
            switch selection {
            case .flight(let flight):
                BriefingContainerView(flight: flight)
                    .id(flight.id)
            case .forecastMap:
                // iPad detail pane (regular width). On compact the map opens as a
                // fullScreenCover instead — see `openForecastMap`.
                if let repo = appState.repository {
                    ForecastMapView(repository: repo, deepLink: mapDeepLink)
                        .id(mapOpenToken)
                }
            case nil:
                ContentUnavailableView("Select a Flight", systemImage: "airplane",
                                       description: Text("Choose a flight from the list, or open the forecast map."))
            }
        }
        .fullScreenCover(isPresented: $showMapCover) {
            if let repo = appState.repository {
                ForecastMapView(repository: repo, deepLink: mapDeepLink) {
                    showMapCover = false
                }
                .id(mapOpenToken)
            }
        }
        .task {
            guard let repo = appState.repository else { return }
            let vm = FlightListViewModel(repository: repo, networkMonitor: appState.networkMonitor)
            viewModel = vm
            await vm.loadFlights()
            // A cold-launch intent may have set a target before the list existed;
            // resolve it now that flights are loaded.
            applyPendingNavigation()
            // Begin the live "Updating…" row poll now the list is on screen.
            vm.startActiveRefreshPolling()
        }
        .onChange(of: scenePhase) {
            if scenePhase == .active {
                Task {
                    await viewModel?.loadFlights()
                    applyPendingNavigation()
                }
                viewModel?.startActiveRefreshPolling()
            } else {
                // Don't poll in the background — resumes on the next `.active`.
                viewModel?.stopActiveRefreshPolling()
            }
        }
        .onDisappear {
            viewModel?.stopActiveRefreshPolling()
        }
        .onChange(of: appState.pendingNavigation) {
            applyPendingNavigation()
        }
        .onChange(of: appState.externalSync) {
            // A push (or other external "data changed" nudge) re-syncs the list so
            // the per-flight summaries reflect the newest online packs. Warm
            // refresh — the current list stays on screen (see loadFlights).
            Task { await viewModel?.loadFlights() }
        }
        .onChange(of: selection) { _, newValue in
            // Returned from a briefing (or the map) to the list — re-pull so any
            // per-flight field edited elsewhere (flexibility, departure times,
            // name, alternates config) is current, not just what was cached when
            // the list last loaded. Warm refresh: `loadFlights` self-dedupes
            // (guard on `isLoading`) and keeps the current list on screen. Fires
            // only on a real transition, never the initial `nil`, so it can't
            // double-load against the `.task` first load.
            if newValue == nil {
                Task { await viewModel?.loadFlights() }
            }
        }
        // Sequence the forecast-map tip after the add-flight tip: when the
        // add-flight tip is dismissed — closed via its × OR acted on — donate the
        // event that makes the forecast-map tip eligible, so the pair reads in
        // order (#312).
        .task {
            for await status in addFlightTip.statusUpdates {
                if case .invalidated = status {
                    await FlightListTipEvents.addFlightTipSeen.donate()
                    break
                }
            }
        }
        .onAppear {
            // Recovery for the narrow window where the app is killed between the
            // add-flight tip invalidating and the donate() above completing:
            // `statusUpdates` is a change stream, so it won't replay
            // `.invalidated` next launch. If the add-flight tip is already
            // dismissed but the event never landed, donate proactively so the
            // forecast-map tip can't get stuck permanently ineligible.
            if case .invalidated = addFlightTip.status,
               FlightListTipEvents.addFlightTipSeen.donations.isEmpty {
                Task { await FlightListTipEvents.addFlightTipSeen.donate() }
            }
        }
    }

    /// Consume an App Intent's navigation target (set on `AppState`) and route to
    /// it. Reuses the same `selectedFlight` seam a tap would drive. When the
    /// target flight isn't in the loaded list yet (cold launch, list still
    /// fetching), the pending value is left in place and re-tried after the next
    /// load completes.
    private func applyPendingNavigation() {
        guard let nav = appState.pendingNavigation else { return }
        switch nav {
        case .flightList:
            selection = nil
            appState.clearPendingNavigation()
        case .forecastMap(let deepLink):
            openForecastMap(deepLink: deepLink.isEmpty ? nil : deepLink)
            appState.clearPendingNavigation()
        case .briefing(let flightId):
            guard let vm = viewModel, case .loaded(let flights) = vm.state else { return }
            if let match = flights.first(where: { $0.id == flightId }) {
                selection = .flight(match)
                appState.clearPendingNavigation()
                reloadRetryFlightId = nil
            } else if vm.isOffline || vm.isRefreshing {
                // Offline/stale list, or a fresh fetch is still in flight — the
                // target flight (e.g. one just created) may still arrive. Keep the
                // target; the next load completion re-applies.
                return
            } else if reloadRetryFlightId != flightId {
                // Settled online list without the flight. It may be a cache-first
                // paint that predates a just-created flight, so force ONE
                // authoritative reload and re-check before giving up — otherwise a
                // push tap / Universal Link on a brand-new flight lands on the list
                // instead of opening the briefing.
                reloadRetryFlightId = flightId
                Task {
                    await vm.loadFlights()
                    applyPendingNavigation()
                }
            } else {
                // Authoritative reload still lacks it — it's genuinely gone. Drop.
                reloadRetryFlightId = nil
                appState.clearPendingNavigation()
            }
        }
    }

    /// Open the forecast map: an iPad detail pane (regular width) or an iPhone
    /// `fullScreenCover` (compact) — retrofitting the sidebar later is expensive,
    /// so iPad is done from the start (#420).
    private func openForecastMap(deepLink: MapDeepLink?) {
        // Only force a fresh view (new `.id`) when a *new* deep link arrives while
        // the map is ALREADY open — that's the only case where the view is reused
        // and wouldn't otherwise apply the new state. A plain re-open (toolbar
        // button, or opening from closed) must NOT bump the token: doing so would
        // tear down the VM and its (day,hour) LRU cache — the thing that makes
        // `‹ ›` stepping instant — and re-fetch on a no-op tap.
        let alreadyOpen = selection == .forecastMap || showMapCover
        if deepLink != nil, alreadyOpen {
            mapOpenToken &+= 1
        }
        mapDeepLink = deepLink
        // Present in exactly one container. If the size class flipped since a prior
        // open (iPad Split View / Stage Manager resize), the other container could
        // still describe "the map is open"; clear only the map's own state (never a
        // flight `selection`) so the two presentations can't both be live.
        if isCompact {
            if selection == .forecastMap { selection = nil }
            showMapCover = true
        } else {
            showMapCover = false
            selection = .forecastMap
        }
    }

    /// One flight row (navigation + edit swipe/context menu). Shared by every
    /// section so the collapsible Past section renders identical rows.
    @ViewBuilder
    private func flightRow(_ flight: FlightResponse, viewModel: FlightListViewModel) -> some View {
        let hasCached = viewModel.cachedFlightIds.contains(flight.id)
        NavigationLink(value: SidebarSelection.flight(flight)) {
            // `.equatable()` so the row diffs on the briefing content it draws,
            // not `FlightResponse`'s id-only `==` — otherwise a same-id briefing
            // update wouldn't repaint the card until app relaunch (#426).
            FlightCardView(flight: flight, hasCachedData: hasCached,
                           isOffline: viewModel.isOffline,
                           isRefreshing: viewModel.refreshingFlightIds.contains(flight.id))
                .equatable()
        }
        .disabled(viewModel.isOffline && !hasCached)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            // Subscribers can't edit shared flights, and editing is online-only
            // (it regenerates the briefing).
            if !viewModel.isOffline && flight.isEditable {
                Button {
                    editingFlight = flight
                } label: {
                    Label("Edit", systemImage: "pencil")
                }
                .tint(.blue)
            }
        }
        .contextMenu {
            if !viewModel.isOffline && flight.isEditable {
                Button {
                    editingFlight = flight
                } label: {
                    Label("Edit Flight", systemImage: "pencil")
                }
            }
        }
    }

    // MARK: - Logbook grouping (Future · Recent · Past, §4.4)

    struct FlightGroup { let title: String; let flights: [FlightResponse] }

    /// Group flights into Future · Recent · Past, sorted most-recent-first. Empty
    /// groups are dropped.
    ///
    /// Prefers the server's `section` (the authoritative debrief-nudge window:
    /// `recent` = recent past flights not yet debriefed, bounded server-side). A
    /// legacy server that omits `section` falls back to client-side date
    /// bucketing (Recent = departed within 7 days) so the list still groups.
    static func groupedFlights(_ flights: [FlightResponse], now: Date = Date()) -> [FlightGroup] {
        func date(_ f: FlightResponse) -> Date { f.departureDate ?? now }
        func bucketed(_ future: [FlightResponse], _ recent: [FlightResponse], _ past: [FlightResponse]) -> [FlightGroup] {
            [
                FlightGroup(title: "Future", flights: future.sorted { date($0) > date($1) }),
                FlightGroup(title: "Recent", flights: recent.sorted { date($0) > date($1) }),
                FlightGroup(title: "Past", flights: past.sorted { date($0) > date($1) }),
            ].filter { !$0.flights.isEmpty }
        }

        // Server-driven grouping when any flight carries a section.
        if flights.contains(where: { $0.section != nil }) {
            return bucketed(
                flights.filter { $0.flightSection == .future },
                flights.filter { $0.flightSection == .recent },
                flights.filter { $0.flightSection == .past }
            )
        }

        // Legacy fallback: client-side date bucketing.
        let recentCutoff = now.addingTimeInterval(-7 * 24 * 3600)
        var future: [FlightResponse] = []
        var recent: [FlightResponse] = []
        var past: [FlightResponse] = []
        for f in flights {
            let dep = f.departureDate ?? now
            if dep >= now { future.append(f) }
            else if dep >= recentCutoff { recent.append(f) }
            else { past.append(f) }
        }
        return bucketed(future, recent, past)
    }

    private var emptyStateView: some View {
        ContentUnavailableView {
            Label("No Flights", systemImage: "airplane")
        } description: {
            Text("Create a flight here or on the website to get started.")
        } actions: {
            VStack(spacing: 12) {
                Button {
                    showAddFlight = true
                } label: {
                    Label("New Flight", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)

                Button {
                    Task { await viewModel?.loadFlights() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)

                Button {
                    openURL(AppState.defaultBaseURL)
                } label: {
                    Label("Open Website", systemImage: "safari")
                }
                .buttonStyle(.bordered)
            }
        }
    }
}

/// Thin banner shown atop the flight list when it's served from cache (offline).
/// Communicates the read-only state that would otherwise only show as dimmed
/// rows (#304 "showing cached"). Carries a stable identifier for the XCUITest
/// offline journey (#318).
private struct OfflineListBanner: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.slash")
                .foregroundStyle(.secondary)
            Text("Offline — showing saved flights")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(.regularMaterial)
        .accessibilityIdentifier("offlineBanner")
    }
}

extension FlightResponse: Hashable {
    static func == (lhs: FlightResponse, rhs: FlightResponse) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}
