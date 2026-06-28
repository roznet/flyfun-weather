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

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            Group {
                if let viewModel {
                    LoadingStateView(state: viewModel.state, retryAction: viewModel.loadFlights) { flights in
                        if flights.isEmpty {
                            emptyStateView
                        } else {
                            // Utility logbook (§4.4): Future · Recent · Past.
                            List(selection: $selectedFlight) {
                                ForEach(Self.groupedFlights(flights), id: \.title) { group in
                                    Section(group.title) {
                                        ForEach(group.flights) { flight in
                                            let hasCached = viewModel.cachedFlightIds.contains(flight.id)
                                            NavigationLink(value: flight) {
                                                FlightCardView(flight: flight, hasCachedData: hasCached,
                                                               isOffline: viewModel.isOffline)
                                            }
                                            .disabled(viewModel.isOffline && !hasCached)
                                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                                // Subscribers can't edit shared flights, and editing
                                                // is online-only (it regenerates the briefing).
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
                                    }
                                }
                            }
                            .refreshable {
                                await viewModel.loadFlights()
                            }
                            .accessibilityIdentifier("flightList")
                        }
                    }
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Flights")
            .toolbar {
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
            let vm = FlightListViewModel(repository: repo)
            viewModel = vm
            await vm.loadFlights()
        }
        .onChange(of: scenePhase) {
            if scenePhase == .active {
                Task { await viewModel?.loadFlights() }
            }
        }
    }

    // MARK: - Logbook grouping (Future · Recent · Past, §4.4)

    struct FlightGroup { let title: String; let flights: [FlightResponse] }

    /// Group flights into Future (upcoming), Recent (flown in the last 7 days),
    /// and Past. Future sorted soonest-first; the rest most-recent-first. Empty
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
        future.sort { date($0) < date($1) }
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

extension FlightResponse: Hashable {
    static func == (lhs: FlightResponse, rhs: FlightResponse) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}
