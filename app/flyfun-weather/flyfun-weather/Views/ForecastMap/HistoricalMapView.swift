import CoreLocation
import MapKit
import SwiftUI

/// The historical map (#629), the iOS port of the web Historical tab: pick a
/// past date and a 30-min UTC time and see, per watchlist airport, the METAR,
/// the TAF read at that instant, or a model's forecast from the run fetched
/// before it (lead Latest / D-1…D-6), coloured by the forecast map's catalog on
/// the same marker layer.
///
/// Laid out like `ForecastMapView`: "when" (date, time, lead) on top, "what"
/// (source, metric, legend) bottom-right, the airport card in an iPad inspector
/// or an iPhone bottom sheet. Container-agnostic in the same way.
struct HistoricalMapView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: HistoricalMapViewModel
    @State private var locator = OneShotLocator()
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
            bottomControls
            if !viewModel.didLoadOnce {
                loadingOverlay
            }
        }
        .task { viewModel.start() }
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
            HStack(alignment: .top) {
                if isCompact, let onClose {
                    MapChrome.circleButton(systemImage: "xmark", action: onClose)
                        .accessibilityLabel("Close map")
                        .accessibilityIdentifier("mapCloseButton")
                } else if !isCompact, let onToggleSidebar {
                    MapChrome.circleButton(systemImage: "sidebar.leading", action: onToggleSidebar)
                        .accessibilityLabel("Toggle Sidebar")
                        .accessibilityIdentifier("mapSidebarToggle")
                }
                Spacer()
                VStack(spacing: 2) {
                    Text(navTitle).font(.headline).monospacedDigit()
                    Text(navSubtitle).font(.caption).foregroundStyle(Theme.textMuted)
                }
                Spacer()
                if let onShowForecast {
                    MapChrome.circleButton(systemImage: "map", action: onShowForecast)
                        .accessibilityLabel("Forecast map")
                        .accessibilityIdentifier("mapForecastButton")
                }
            }
            HStack(spacing: Theme.spacingS) {
                datePicker
                timeStepper
                leadMenu
                if viewModel.isLoading, viewModel.didLoadOnce {
                    ProgressView().controlSize(.small)
                }
                Spacer(minLength: 0)
            }
            if let status = viewModel.statusLine {
                Text(status)
                    .font(.caption2)
                    .foregroundStyle(Theme.text)
                    .lineLimit(2)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, Theme.spacingM)
        .padding(.top, isCompact ? Theme.spacingM : Theme.spacingS)
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

    private var timeStepper: some View {
        HStack(spacing: 6) {
            Button { viewModel.step(-1) } label: { Image(systemName: "chevron.left") }
                .disabled(!viewModel.canStepBack)
                .accessibilityLabel("Earlier")
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
                    .font(.subheadline.weight(.medium)).monospacedDigit()
            }
            Button { viewModel.step(1) } label: { Image(systemName: "chevron.right") }
                .disabled(!viewModel.canStepForward)
                .accessibilityLabel("Later")
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: Capsule())
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
            MapChrome.capsuleLabel(text: Self.leadShortLabel(viewModel.lead), systemImage: "clock.arrow.circlepath")
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

    // MARK: - Bottom-right controls ("what")

    private var bottomControls: some View {
        VStack {
            Spacer()
            HStack(alignment: .bottom) {
                if let cat = catalog, let legend = cat.legend(metric: viewModel.metric) {
                    MapChrome.legendCapsule(legend)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: Theme.spacingS) {
                    sourceMenu
                    metricMenu
                    locateButton
                }
            }
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
            MapChrome.capsuleLabel(text: viewModel.source.label, systemImage: "square.stack.3d.up")
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
            MapChrome.capsuleLabel(text: catalog?.label(metric: viewModel.metric) ?? "Metric", systemImage: "paintpalette")
        }
    }

    private var locateButton: some View {
        Button {
            locator.request()
        } label: {
            Image(systemName: "location")
                .font(.subheadline.weight(.medium))
                .padding(10)
                .background(.ultraThinMaterial, in: Circle())
        }
        .accessibilityLabel("Centre on my location")
    }

    private var loadingOverlay: some View {
        VStack(spacing: Theme.spacingM) {
            if let err = viewModel.loadError {
                ContentUnavailableView("Historical Map Unavailable", systemImage: "clock.arrow.circlepath",
                                       description: Text(err))
                Button("Retry") { viewModel.retry() }
                    .buttonStyle(.bordered)
            } else {
                ProgressView("Loading historical map…")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg.opacity(0.6))
    }

    // MARK: - Labels

    private var navTitle: String {
        guard let at = viewModel.selectedInstant else { return "Historical" }
        return "\(HistoricalTime.shortDateLabel(at)) · \(HistoricalTime.timeLabel(at))"
    }

    private var navSubtitle: String {
        var parts = ["Historical", viewModel.source.label]
        if !viewModel.source.isObserved {
            parts.append(viewModel.lead == 0 ? "latest runs" : "runs from D-\(viewModel.lead)")
        }
        if let at = viewModel.selectedInstant, viewModel.isObservationOnly(at) {
            parts.append("METAR/TAF only")
        }
        return parts.joined(separator: " · ")
    }
}
