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
    /// In-app help guide (`/help.html`) — see `WebPageSheet` for why it's web.
    @State private var showHelp = false
    /// Native "Send Feedback" sheet (the web help page's modal, in-app).
    @State private var showFeedback = false
    /// Release stream ("What's New") — native, not the web page, so it reads
    /// offline from the cached stream (#550).
    @State private var showWhatsNew = false
    @State private var showSignOutWarning = false
    @State private var editingFlight: FlightResponse?
    /// A flight the user chose to file a PIREP for from the list (context menu),
    /// presented as the reporting sheet. nil when closed.
    @State private var pirepFlight: FlightResponse?
    /// One-shot-location tracking service backing the list-presented PIREP sheet
    /// — the list carries no live route track, so the form fetches its own fix.
    @State private var pirepTrackingService = FlightTrackingService()
    /// Compact-width forecast-map cover (the app's first `fullScreenCover`) and
    /// the deep-link state it opens with.
    @State private var showMapCover = false
    @State private var mapDeepLink: MapDeepLink?
    /// A shared flight resolved from a `/s/{code}` deep link, presented as a
    /// preview-before-subscribe cover (#446). nil when no shared link is open.
    @State private var sharedPreviewFlight: FlightResponse?
    /// Error surfaced when a `/s/{code}` link can't be resolved (unknown code,
    /// private flight, or the owner's own private link).
    @State private var shareResolveError: String?
    /// Error surfaced when a swipe/context-menu unsubscribe fails, so the row
    /// staying put reads as a failure rather than a silent no-op (#446).
    @State private var unsubscribeError: String?
    /// A flight the user swiped/long-pressed Delete on, held while the destructive
    /// confirmation is up. Deleting drops all briefing history and can't be undone,
    /// so — like the web's `confirm()` — it is never a one-tap action.
    @State private var deleteCandidate: FlightResponse?
    /// Error surfaced when a delete fails (same reasoning as `unsubscribeError`).
    @State private var deleteError: String?
    /// A flight whose share sheet is open, presented via `ShareActivitySheet` so
    /// the list's Share matches the briefing toolbar's (custom "Open in Safari").
    @State private var shareFlight: FlightResponse?
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
                                ForEach(
                                    Self.groupedFlights(
                                        flights,
                                        order: appState.userPreferences.preferences.flightOrderPreference
                                    ),
                                    id: \.title
                                ) { group in
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

                            Button {
                                showHelp = true
                            } label: {
                                Label("Help", systemImage: "questionmark.circle")
                            }

                            Button {
                                showWhatsNew = true
                            } label: {
                                // A menu row can't carry a dot, so the unseen
                                // count rides in the title (the ellipsis itself
                                // shows the dot — see the Menu label below).
                                Label(
                                    appState.whatsNew.unseenCount > 0
                                        ? "What's New (\(appState.whatsNew.unseenCount))"
                                        : "What's New",
                                    systemImage: "sparkles"
                                )
                            }
                            .accessibilityIdentifier("whatsNewButton")

                            Button {
                                showFeedback = true
                            } label: {
                                Label("Send Feedback", systemImage: "exclamationmark.bubble")
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
                            // Red dot for unseen highlighted release entries —
                            // same affordance as the flight card's unseen-briefing
                            // dot, and the web nav's Help badge.
                            Label("More", systemImage: "ellipsis.circle")
                                .overlay(alignment: .topTrailing) {
                                    if appState.whatsNew.unseenCount > 0 {
                                        Circle()
                                            .fill(.red)
                                            .frame(width: 8, height: 8)
                                            .offset(x: 3, y: -2)
                                            .accessibilityLabel("Unread updates")
                                    }
                                }
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
            .sheet(isPresented: $showHelp) {
                WebPageSheet(url: .flyfunWeb("help.html"))
                    .ignoresSafeArea()
            }
            .sheet(isPresented: $showFeedback) {
                FeedbackFormView()
            }
            .sheet(isPresented: $showWhatsNew) {
                WhatsNewView()
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
            .sheet(item: $shareFlight) { flight in
                if let code = flight.shareCode {
                    ShareActivitySheet(items: [AppState.shareURL(forShareCode: code)],
                                       activities: [OpenInSafariActivity()])
                        .presentationDetents([.medium, .large])
                        .ignoresSafeArea()
                }
            }
            .sheet(item: $pirepFlight) { flight in
                // File a PIREP straight from the list (context menu). The form
                // grabs a one-shot GPS fix to pre-fill lat/lon/altitude.
                if let repo = appState.repository {
                    PirepReportingView(
                        viewModel: PirepViewModel(flight: flight, repository: repo,
                                                  offlineStore: appState.pirepOfflineStore),
                        trackingService: pirepTrackingService
                    )
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
                //
                // The map owns its sidebar toggle. Every other detail (the briefing)
                // has a navigation bar, so the split view puts the system toggle
                // there for free; the map is deliberately chrome-less, so collapsing
                // the sidebar over it used to strand the user on the map with no way
                // back to the list.
                if let repo = appState.repository {
                    ForecastMapView(repository: repo, deepLink: mapDeepLink,
                                    onToggleSidebar: toggleSidebar)
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
        .fullScreenCover(item: $sharedPreviewFlight) { flight in
            // Shared-flight preview (#446): the flight isn't in /api/flights until
            // the viewer subscribes, so it's presented on its own rather than via
            // the sidebar selection. Reload the list on any subscribe change so a
            // now-subscribed flight appears (and an unsubscribe drops it).
            NavigationStack {
                SharedFlightPreviewView(flight: flight) {
                    Task { await viewModel?.loadFlights() }
                }
            }
        }
        .alert("Flight unavailable", isPresented: Binding(
            get: { shareResolveError != nil },
            set: { if !$0 { shareResolveError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(shareResolveError ?? "")
        }
        .alert("Couldn’t unsubscribe", isPresented: Binding(
            get: { unsubscribeError != nil },
            set: { if !$0 { unsubscribeError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(unsubscribeError ?? "")
        }
        // Destructive delete confirmation. Attached at the list level (not per row)
        // so the swipe action can complete and the row close before it appears;
        // `presenting:` carries the flight so the message names the route.
        //
        // An `alert`, NOT a `confirmationDialog`: on iPad the latter renders as a
        // popover, and a popover drops the cancel-role button (it dismisses by
        // tapping outside instead) — leaving Delete as the only visible choice on
        // an irreversible action. An alert draws both buttons on every idiom and
        // won't dismiss on an outside tap.
        .alert(
            "Delete Flight?",
            isPresented: Binding(
                get: { deleteCandidate != nil },
                set: { if !$0 { deleteCandidate = nil } }
            ),
            presenting: deleteCandidate
        ) { flight in
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                if let viewModel { delete(flight, viewModel: viewModel) }
            }
            .accessibilityIdentifier("confirmDeleteFlightButton")
        } message: { flight in
            Text("\(flight.shortTitle) and all of its briefing history will be deleted. This can’t be undone.")
        }
        .alert("Couldn’t delete flight", isPresented: Binding(
            get: { deleteError != nil },
            set: { if !$0 { deleteError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(deleteError ?? "")
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
        case .share(let code):
            // Resolve the share code to a flight and present the preview. Keep the
            // pending target when the repository isn't ready yet (e.g. the link
            // arrived while signed out) so it re-applies once the list appears
            // authenticated — that's the sign-in resumption path (#446).
            guard let repo = appState.repository else { return }
            appState.clearPendingNavigation()
            Task {
                do {
                    let flight = try await repo.flightByShareCode(code)
                    if flight.role == .subscriber {
                        sharedPreviewFlight = flight
                    } else {
                        // Opening your OWN share link (self-share, testing, a link
                        // that bounced back via Messages): the resolver returns
                        // role == .owner. Skip the subscriber "Subscribe" banner —
                        // subscribing to your own flight 409s — and just open the
                        // flight normally, preferring the list's instance so the
                        // sidebar selection highlights.
                        if case .loaded(let flights)? = viewModel?.state,
                           let match = flights.first(where: { $0.id == flight.id }) {
                            selection = .flight(match)
                        } else {
                            selection = .flight(flight)
                        }
                    }
                } catch let error as APIError {
                    shareResolveError = {
                        if case .notFound = error {
                            return "This shared flight isn’t available. The link may be wrong, or the owner made it private."
                        }
                        return error.errorDescription ?? "Couldn’t open the shared flight."
                    }()
                } catch {
                    shareResolveError = error.localizedDescription
                }
            }
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
                // Authoritative reload still lacks it — the link names a flight
                // outside the viewer's list (a feedback email about another
                // pilot's flight, a link from a different sign-in account). The
                // server still serves GET /flights/{id} to any authenticated
                // viewer for a non-private flight, so fetch it directly instead
                // of silently landing on the list. A non-owner comes back
                // role == .subscriber and gets the same preview-before-subscribe
                // screen as a /s/{code} share link; a 404 (private/unknown)
                // surfaces an alert so the tap is never a silent no-op.
                // Keep the pending target when the repository isn't ready yet, as
                // the `.share` case does — clearing first would drop the deep link
                // for good instead of resuming it after sign-in.
                guard let repo = appState.repository else { return }
                reloadRetryFlightId = nil
                appState.clearPendingNavigation()
                Task {
                    do {
                        let flight = try await repo.flight(id: flightId)
                        if flight.role == .subscriber {
                            sharedPreviewFlight = flight
                        } else {
                            selection = .flight(flight)
                        }
                    } catch let error as APIError {
                        // Only a 404 means private/deleted/other-account. A
                        // transient failure (offline tap) must say so instead of
                        // blaming permissions — same split as the `.share` case.
                        shareResolveError = {
                            if case .notFound = error {
                                return "This briefing isn’t available. The flight may be private, deleted, or on a different account."
                            }
                            return error.errorDescription ?? "Couldn’t open this briefing."
                        }()
                    } catch {
                        shareResolveError = error.localizedDescription
                    }
                }
            }
        }
    }

    /// Drop the viewer's subscription to a shared flight, then reload the list so
    /// the row disappears. If the just-unsubscribed flight is the current detail
    /// selection (iPad), clear it so the detail pane doesn't dangle on a flight no
    /// longer in the list.
    private func unsubscribe(_ flight: FlightResponse, viewModel: FlightListViewModel) {
        Task {
            do {
                try await appState.repository?.unsubscribeFlight(id: flight.id)
                if selection == .flight(flight) { selection = nil }
                await viewModel.loadFlights()
            } catch let error as APIError {
                unsubscribeError = error.errorDescription ?? "Couldn’t unsubscribe from this flight."
            } catch {
                unsubscribeError = error.localizedDescription
            }
        }
    }

    /// Delete one of the user's own flights (confirmed), then reload the list so
    /// the row disappears. Same detail-pane hygiene as `unsubscribe`: if the
    /// deleted flight is the current iPad selection, clear it so the detail pane
    /// doesn't dangle on a flight the server no longer has.
    private func delete(_ flight: FlightResponse, viewModel: FlightListViewModel) {
        Task {
            do {
                if selection == .flight(flight) { selection = nil }
                try await viewModel.deleteFlight(flight)
            } catch let error as APIError {
                deleteError = error.errorDescription ?? "Couldn’t delete this flight."
            } catch {
                deleteError = error.localizedDescription
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

    /// Show/hide the split view's sidebar, for details that draw no navigation bar
    /// of their own (the forecast map). `.automatic` means "system's choice", which
    /// on a wide iPad is a visible sidebar — so it toggles to `.detailOnly`; every
    /// other state comes back to `.all`. That keeps the button an escape hatch: from
    /// the collapsed state it always restores the list.
    private func toggleSidebar() {
        withAnimation {
            columnVisibility = columnVisibility == .detailOnly ? .all : .detailOnly
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
            // Unsubscribe from a shared flight — the first row-removal flow on iOS
            // (#446). Online-only; drops the viewer's subscription then reloads.
            if !viewModel.isOffline && flight.role == .subscriber {
                Button(role: .destructive) {
                    unsubscribe(flight, viewModel: viewModel)
                } label: {
                    Label("Unsubscribe", systemImage: "person.badge.minus")
                }
            }
            // Delete an owned flight, matching the web's Delete button. Owner-only
            // (the server 404s a subscriber's delete) and online-only. Never
            // immediate: it opens the destructive confirmation. `allowsFullSwipe`
            // is already false on this edge, so a long swipe can't trigger it.
            if !viewModel.isOffline && flight.isEditable {
                Button(role: .destructive) {
                    deleteCandidate = flight
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                .accessibilityIdentifier("deleteFlightSwipeButton")
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
            // File a pilot report for this flight. Gated so a list-filed report
            // actually links to the flight's tab: the server links a pack_id-less
            // PIREP only when observed_at is in the flight's window AND the report's
            // user_id equals the flight OWNER's (storage/pireps.py:130-140). So we
            // require both `isEditable` (owner — a subscriber's report never carries
            // the owner's id, so it'd orphan regardless of timing) and the tracking
            // window. Not gated on offline — `PirepViewModel.submit` queues
            // transient-network failures via `PirepOfflineStore`.
            if appState.userPreferences.preferences.pirepCanPublish,
               flight.isEditable,
               flight.isInTrackingWindow() {
                Button {
                    pirepFlight = flight
                } label: {
                    Label("Add PIREP", systemImage: "cloud.sun")
                }
                .accessibilityIdentifier("addPirepMenuItem")
            }
            // Owner sharing (#446): share the short /s/{code} link. Hidden for
            // private flights and subscribers (isShareable) — recipients of a
            // private link would 404, mirroring the web share gate.
            // Presents `ShareActivitySheet`, not `ShareLink`, so this entry point
            // carries the same "Open in Safari" activity as the briefing toolbar's
            // Share button — sharing the same flight from two places behaved
            // differently otherwise.
            if flight.isShareable, flight.shareCode != nil {
                Button {
                    shareFlight = flight
                } label: {
                    Label("Share Flight", systemImage: "square.and.arrow.up")
                }
            }
            if !viewModel.isOffline && flight.role == .subscriber {
                Button(role: .destructive) {
                    unsubscribe(flight, viewModel: viewModel)
                } label: {
                    Label("Unsubscribe", systemImage: "person.badge.minus")
                }
            }
            if !viewModel.isOffline && flight.isEditable {
                Button(role: .destructive) {
                    deleteCandidate = flight
                } label: {
                    Label("Delete Flight", systemImage: "trash")
                }
                .accessibilityIdentifier("deleteFlightMenuItem")
            }
        }
    }

    // MARK: - Logbook grouping (Future · Recent · Past, §4.4)

    struct FlightGroup { let title: String; let flights: [FlightResponse] }

    /// Group flights into Future · Recent · Past. Recent and Past are always
    /// most-recent-first; Future follows the user's `flight_order` preference
    /// (#536). Empty groups are dropped.
    ///
    /// Prefers the server's `section` (the authoritative debrief-nudge window:
    /// `recent` = recent past flights not yet debriefed, bounded server-side). A
    /// legacy server that omits `section` falls back to client-side bucketing
    /// (Future until the flight ends, Recent = departed within 7 days) so the
    /// list still groups.
    ///
    /// The sort is applied here rather than inherited from the server because
    /// this view has always owned its own ordering; passing `order` in keeps the
    /// function pure and testable.
    static func groupedFlights(
        _ flights: [FlightResponse],
        order: FlightOrder = .furthestFirst,
        now: Date = Date()
    ) -> [FlightGroup] {
        func date(_ f: FlightResponse) -> Date { f.departureDate ?? now }
        func bucketed(_ future: [FlightResponse], _ recent: [FlightResponse], _ past: [FlightResponse]) -> [FlightGroup] {
            let futureSorted = order == .soonestFirst
                ? future.sorted { date($0) < date($1) }
                : future.sorted { date($0) > date($1) }
            return [
                FlightGroup(title: "Future", flights: futureSorted),
                FlightGroup(title: "Recent", flights: recent.sorted { date($0) > date($1) }),
                FlightGroup(title: "Past", flights: past.sorted { date($0) > date($1) }),
            ].filter { !$0.flights.isEmpty }
        }

        // Server-driven grouping when any flight carries a section.
        if flights.contains(where: { $0.section != nil }) {
            return bucketed(
                flights.filter { $0.resolvedSection(now: now) == .future },
                flights.filter { $0.resolvedSection(now: now) == .recent },
                flights.filter { $0.resolvedSection(now: now) == .past }
            )
        }

        // Legacy fallback: client-side bucketing, duration-aware at the Future
        // boundary so it keeps mirroring the server exactly.
        let recentCutoff = now.addingTimeInterval(-7 * 24 * 3600)
        var future: [FlightResponse] = []
        var recent: [FlightResponse] = []
        var past: [FlightResponse] = []
        for f in flights {
            let dep = f.departureDate ?? now
            if !f.hasEnded(now: now) { future.append(f) }
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
