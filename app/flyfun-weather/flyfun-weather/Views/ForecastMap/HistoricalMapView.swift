import CoreLocation
import MapKit
import SwiftUI

/// The historical map (#629), the iOS port of the web Historical tab: pick a
/// past date and a 30-min UTC time and see, per watchlist airport, the METAR,
/// the TAF read at that instant, or a model's forecast from the run fetched
/// before it (lead Latest / D-1…D-6), coloured by the forecast map's catalog on
/// the same marker layer.
///
/// Shown to the pilot as **Past**, the other side of the map's Forecast | Past
/// switch (#996). Laid out like `ForecastMapView` with the shared `MapChrome`:
/// "when" (date, time) in the time bar on top, "what" (metric, source, and for
/// model sources the run age) in one bottom panel with the legend, the airport
/// card in an iPad inspector or an iPhone bottom sheet.
struct HistoricalMapView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: HistoricalMapViewModel
    @State private var locator = OneShotLocator()
    /// Map-only view (#996), shared with the forecast map through `MapChrome`.
    @State private var controlsHidden = MapChrome.controlsHidden
    private let onClose: (() -> Void)?
    private let onToggleSidebar: (() -> Void)?
    /// Swap back to the forecast map.
    private let onShowForecast: (() -> Void)?

    init(repository: any BriefingRepository,
         deepLink: HistoricalMapDeepLink? = nil,
         onClose: (() -> Void)? = nil,
         onToggleSidebar: (() -> Void)? = nil,
         onShowForecast: (() -> Void)? = nil) {
        _viewModel = State(initialValue: HistoricalMapViewModel(repository: repository, deepLink: deepLink))
        self.onClose = onClose
        self.onToggleSidebar = onToggleSidebar
        self.onShowForecast = onShowForecast
    }

    private var isCompact: Bool { horizontalSizeClass == .compact }
    private var catalog: ForecastMapCatalog? { appState.helpCatalog.mapsCatalog }

    var body: some View {
        ZStack(alignment: .top) {
            mapLayer
            topControls
            if !controlsHidden {
                bottomControls
            }
            if !viewModel.didLoadOnce {
                loadingOverlay
            }
        }
        .task { viewModel.start() }
        .onChange(of: controlsHidden) { MapChrome.controlsHidden = controlsHidden }
        .onChange(of: locator.located) {
            guard let c = locator.located else { return }
            viewModel.focusRequest = ForecastMapViewModel.FocusRequest(
                center: CLLocationCoordinate2D(latitude: c.lat, longitude: c.lon),
                span: MKCoordinateSpan(latitudeDelta: 4, longitudeDelta: 5), biasForSheet: false)
        }
        .modifier(MapCardPresenter(
            isPresented: Binding(
                get: { viewModel.selectedIcao != nil },
                set: { if !$0 { viewModel.deselect() } }
            ),
            isCompact: isCompact
        ) {
            if let airport = viewModel.selectedAirport, let catalog {
                HistoricalAirportCard(viewModel: viewModel, airport: airport, catalog: catalog)
            } else {
                Color.clear
            }
        })
        .alert("Nothing to show", isPresented: Binding(
            get: { viewModel.notice != nil },
            set: { if !$0 { viewModel.notice = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.notice ?? "")
        }
    }

    // MARK: - Map

    private var mapLayer: some View {
        ForecastMapKitView(
            payload: viewModel.mapPayload,
            catalog: catalog,
            metric: viewModel.metric,
            mode: viewModel.source.mapMode,
            selectedIcao: viewModel.selectedIcao,
            payloadRevision: viewModel.payloadRevision,
            initialRegion: viewModel.initialRegion,
            focusRequest: viewModel.focusRequest,
            onSelect: { viewModel.select(icao: $0, biasForSheet: isCompact) },
            onFocusApplied: { viewModel.focusRequest = nil },
            onUserInteraction: {}
        )
        .ignoresSafeArea(edges: isCompact ? .all : [])
    }

    // MARK: - Top controls ("when")

    private var topControls: some View {
        VStack(spacing: Theme.spacingS) {
            HStack(alignment: .center) {
                // Keyed on the container's callback, not size class — see
                // ForecastMapView's leading slot.
                if let onClose {
                    MapChrome.circleButton(systemImage: "xmark", action: onClose)
                        .accessibilityLabel("Close map")
                        .accessibilityIdentifier("mapCloseButton")
                } else if let onToggleSidebar {
                    MapChrome.circleButton(systemImage: "sidebar.leading", action: onToggleSidebar)
                        .accessibilityLabel("Toggle Sidebar")
                        .accessibilityIdentifier("mapSidebarToggle")
                } else {
                    Color.clear.frame(width: 36, height: 36)
                }
                Spacer(minLength: Theme.spacingS)
                if !controlsHidden, let onShowForecast {
                    MapChrome.modeSwitch(current: .past) { _ in onShowForecast() }
                }
                Spacer(minLength: Theme.spacingS)
                if controlsHidden {
                    MapChrome.showControlsPill(time: compactTimeLabel) { controlsHidden = false }
                } else {
                    MapChrome.circleButton(systemImage: "arrow.up.left.and.arrow.down.right") { controlsHidden = true }
                        .accessibilityLabel("Hide map controls")
                        .accessibilityIdentifier("mapHideControlsButton")
                }
            }
            if !controlsHidden {
                timeBar
                if let status = viewModel.statusLine {
                    HStack(spacing: Theme.spacingS) {
                        Text(status)
                            .font(.caption2)
                            .foregroundStyle(Theme.text)
                            .lineLimit(2)
                        // A failed reload keeps the pickers; this re-fetches the same instant.
                        if viewModel.loadError != nil, viewModel.didLoadOnce, !viewModel.isLoading {
                            Spacer(minLength: 0)
                            Button("Retry") { viewModel.retryReload() }
                                .font(.caption2.weight(.semibold))
                                .accessibilityIdentifier("historicalRetryButton")
                        }
                    }
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(MapChrome.surface(RoundedRectangle(cornerRadius: 8)))
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(maxWidth: 560)
        .padding(.horizontal, Theme.spacingM)
        .padding(.top, isCompact ? Theme.spacingM : Theme.spacingS)
    }

    /// ◀ date · time ▶: the arrows step 30 minutes; the date opens the system
    /// calendar (a long history stays browsable) and the time the slot list.
    private var timeBar: some View {
        HStack(spacing: 4) {
            MapChrome.stepButton("chevron.left", label: "Earlier", enabled: viewModel.canStepBack) {
                viewModel.step(-1)
            }
            Spacer(minLength: 0)
            datePicker
            timeMenu
            if viewModel.isLoading, viewModel.didLoadOnce {
                ProgressView().controlSize(.small)
            }
            Spacer(minLength: 0)
            MapChrome.stepButton("chevron.right", label: "Later", enabled: viewModel.canStepForward) {
                viewModel.step(1)
            }
        }
        .padding(.horizontal, 6).padding(.vertical, 4)
        .background(MapChrome.surface(RoundedRectangle(cornerRadius: 12)))
    }

    /// UTC calendar date. The system compact picker keeps a long history
    /// browsable where a menu of every day would not.
    private var datePicker: some View {
        DatePicker(
            "Date",
            selection: Binding(
                get: { viewModel.selectedInstant ?? viewModel.latest },
                set: { viewModel.selectDay($0) }
            ),
            in: HistoricalTime.utcCalendar.startOfDay(for: viewModel.earliest)...viewModel.latest,
            displayedComponents: .date
        )
        .labelsHidden()
        .datePickerStyle(.compact)
        .environment(\.timeZone, HistoricalTime.utcCalendar.timeZone)
        .environment(\.calendar, HistoricalTime.utcCalendar)
        .disabled(viewModel.selectedInstant == nil)
        .accessibilityIdentifier("historicalDatePicker")
    }

    private var timeMenu: some View {
        Menu {
            ForEach(viewModel.timeSlots, id: \.self) { slot in
                Button {
                    viewModel.selectInstant(slot)
                } label: {
                    MapChrome.menuRow(HistoricalTime.timeLabel(slot), selected: slot == viewModel.selectedInstant)
                }
                .disabled(!viewModel.isSelectable(slot))
            }
        } label: {
            Text(viewModel.selectedInstant.map { HistoricalTime.timeLabel($0) } ?? "--:--Z")
                .font(.subheadline.weight(.semibold)).monospacedDigit()
                .foregroundStyle(Theme.text)
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Theme.bg, in: Capsule())
        }
        .accessibilityIdentifier("historicalTimeMenu")
    }

    private var leadMenu: some View {
        Menu {
            Section("Model runs") {
                ForEach(HistoricalMapViewModel.leads, id: \.self) { n in
                    Button {
                        viewModel.selectLead(n)
                    } label: {
                        MapChrome.menuRow(leadMenuLabel(n), selected: n == viewModel.lead)
                    }
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Runs", value: Self.leadShortLabel(viewModel.lead))
        }
    }

    private func leadMenuLabel(_ n: Int) -> String {
        let base = n == 0 ? "Latest run before this time"
            : "Run from \(n) day\(n == 1 ? "" : "s") before"
        let models = viewModel.models(forLead: n)
        // Say which models a long lead still carries (ICON stops early).
        return models.count < 3 ? base + " · " + models.map { $0.uppercased() }.joined(separator: ", ") : base
    }

    static func leadShortLabel(_ n: Int) -> String { n == 0 ? "Latest" : "D-\(n)" }

    // MARK: - Bottom panel ("what")

    private var bottomControls: some View {
        VStack(alignment: .trailing, spacing: Theme.spacingS) {
            Spacer()
            locateButton
            MapChrome.bottomPanel(legend: catalog?.legend(metric: viewModel.metric)) {
                metricMenu
                sourceMenu
                // Run age only means something for a model source (was a
                // clock-icon capsule on top, the same icon as the old switch).
                if !viewModel.source.isObserved {
                    leadMenu
                }
            }
            .frame(maxWidth: .infinity)
        }
        .padding(Theme.spacingM)
    }

    private var sourceMenu: some View {
        Menu {
            Section("Observed") {
                ForEach(HistoricalSource.observed, id: \.self) { sourceButton($0, title: $0.label) }
            }
            Section("Models") {
                sourceButton(.worst, title: "Worst of models")
                sourceButton(.majority, title: "Majority of models")
                ForEach(HistoricalSource.nwpModels, id: \.self) { sourceButton($0, title: $0.label) }
            }
        } label: {
            MapChrome.pickLabel(caption: "Source", value: viewModel.source.label)
        }
        .accessibilityIdentifier("historicalSourceMenu")
    }

    /// Unavailable sources stay tappable so they can explain themselves.
    private func sourceButton(_ s: HistoricalSource, title: String) -> some View {
        let available = viewModel.sourceAvailable(s)
        return Button {
            viewModel.selectSource(s)
        } label: {
            MapChrome.menuRow(title + (available ? "" : " (none at this time)"), selected: s == viewModel.source)
        }
    }

    private var metricMenu: some View {
        Menu {
            ForEach(ForecastMapView.metricSections, id: \.title) { section in
                Section(section.title) {
                    ForEach(section.items, id: \.metric) { item in
                        if catalog?.metrics[item.metric] != nil {
                            Button {
                                viewModel.metric = item.metric
                            } label: {
                                MapChrome.menuRow(item.label, selected: item.metric == viewModel.metric)
                            }
                        }
                    }
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Showing", value: catalog?.label(metric: viewModel.metric) ?? "Metric")
        }
    }

    private var locateButton: some View {
        MapChrome.circleButton(systemImage: "location") { locator.request() }
            .accessibilityLabel("Centre on my location")
    }

    private var loadingOverlay: some View {
        VStack(spacing: Theme.spacingM) {
            if let err = viewModel.loadError {
                ContentUnavailableView("Past Map Unavailable", systemImage: "clock.arrow.circlepath",
                                       description: Text(err))
                Button("Retry") { viewModel.retry() }
                    .buttonStyle(.bordered)
            } else {
                ProgressView("Loading past map…")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg.opacity(0.6))
    }

    // MARK: - Labels

    /// "28 Sep · 14:30Z" for the hidden state's pill.
    private var compactTimeLabel: String {
        guard let at = viewModel.selectedInstant else { return "Past" }
        return "\(HistoricalTime.shortDateLabel(at)) · \(HistoricalTime.timeLabel(at))"
    }
}
