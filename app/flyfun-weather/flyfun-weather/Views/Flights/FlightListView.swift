import SwiftUI

/// Main screen showing the user's saved flights with sidebar/detail split on iPad.
struct FlightListView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @State private var viewModel: FlightListViewModel?
    @State private var selectedFlight: FlightResponse?
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic
    @State private var showAddFlight = false
    @State private var showSettings = false
    @State private var showSignOutWarning = false
    @State private var editingFlight: FlightResponse?
    /// "Past" flights start collapsed (like the web app) so the list opens on
    /// what's upcoming/recent.
    @State private var pastExpanded = false

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
                            List(selection: $selectedFlight) {
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
                        } label: {
                            Label("Add Flight", systemImage: "plus")
                        }
                        .accessibilityIdentifier("addFlightButton")
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
                            selectedFlight = flight
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
                            selectedFlight = updated
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
            if let selectedFlight {
                BriefingContainerView(flight: selectedFlight)
                    .id(selectedFlight.id)
            } else {
                ContentUnavailableView("Select a Flight", systemImage: "airplane",
                                       description: Text("Choose a flight from the list to view its briefing."))
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
            selectedFlight = nil
            appState.clearPendingNavigation()
        case .briefing(let flightId):
            guard let vm = viewModel, case .loaded(let flights) = vm.state else { return }
            if let match = flights.first(where: { $0.id == flightId }) {
                selectedFlight = match
                appState.clearPendingNavigation()
            } else if !vm.isOffline {
                // Authoritative (online) list loaded and the flight isn't in it —
                // it's gone; drop the target. When the list is offline/stale, keep
                // the target: a later successful fetch may include a just-created
                // flight the cache didn't have yet, so we retry after the next load.
                appState.clearPendingNavigation()
            }
        }
    }

    /// One flight row (navigation + edit swipe/context menu). Shared by every
    /// section so the collapsible Past section renders identical rows.
    @ViewBuilder
    private func flightRow(_ flight: FlightResponse, viewModel: FlightListViewModel) -> some View {
        let hasCached = viewModel.cachedFlightIds.contains(flight.id)
        NavigationLink(value: flight) {
            FlightCardView(flight: flight, hasCachedData: hasCached,
                           isOffline: viewModel.isOffline,
                           isRefreshing: viewModel.refreshingFlightIds.contains(flight.id))
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

    /// Group flights into Future (upcoming), Recent (flown in the last 7 days),
    /// and Past. All sorted most-recent-first (latest departure at top). Empty
    /// groups are dropped.
    static func groupedFlights(_ flights: [FlightResponse], now: Date = Date()) -> [FlightGroup] {
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
        func date(_ f: FlightResponse) -> Date { f.departureDate ?? now }
        future.sort { date($0) > date($1) }
        recent.sort { date($0) > date($1) }
        past.sort { date($0) > date($1) }
        return [
            FlightGroup(title: "Future", flights: future),
            FlightGroup(title: "Recent", flights: recent),
            FlightGroup(title: "Past", flights: past),
        ].filter { !$0.flights.isEmpty }
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
