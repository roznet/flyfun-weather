import CoreLocation
import MapKit
import SwiftUI

/// The forecast-map screen (#420): a pan-European overview coloured by a
/// pilot-question metric, with the day/hour grid drawn from the server and a
/// tap-to-card interaction. Container-agnostic — the same view is the iPad detail
/// pane and the iPhone `fullScreenCover`; only the airport-card presentation
/// differs (inspector vs bottom sheet), keyed off size class.
///
/// "When" lives on top (the bottom belongs to the airport sheet, which would
/// otherwise cover the hour stepper); "what" (metric, models, legend) is one
/// panel at the bottom, above the sheet. Shared chrome: `MapChrome` (#996).
struct ForecastMapView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: ForecastMapViewModel
    @State private var locator = OneShotLocator()
    /// Set when the user taps a model that can't reach the selected day, to show
    /// the "why" instead of switching to an unreachable model.
    @State private var unavailableModel: String?
    /// Map-only view (#996): everything but close and the time pill hidden.
    @State private var controlsHidden = MapChrome.controlsHidden
    /// nil on the iPad detail pane; set on the iPhone cover so it can dismiss.
    private let onClose: (() -> Void)?
    /// Set on the iPad detail pane so the map can show/hide the split view's
    /// sidebar. The map draws no navigation bar — it's a full-bleed `ZStack` with
    /// its own floating chrome — so the system sidebar toggle, which lives in the
    /// detail column's nav bar, has nowhere to appear. Without this the user could
    /// collapse the sidebar over the map and be stranded there with no way back to
    /// the flight list.
    private let onToggleSidebar: (() -> Void)?
    /// Swap this screen for the past (historical, #629) map: the Past side of
    /// the Forecast | Past switch.
    private let onShowHistorical: (() -> Void)?
    /// The pilot's one flight in its live window, if any: the locate button's
    /// fix goes to it as a GPS position (#761).
    private let liveFlightId: String?

    init(repository: any BriefingRepository,
         deepLink: MapDeepLink? = nil,
         liveFlightId: String? = nil,
         onClose: (() -> Void)? = nil,
         onToggleSidebar: (() -> Void)? = nil,
         onShowHistorical: (() -> Void)? = nil) {
        _viewModel = State(initialValue: ForecastMapViewModel(repository: repository, deepLink: deepLink))
        self.liveFlightId = liveFlightId
        self.onClose = onClose
        self.onToggleSidebar = onToggleSidebar
        self.onShowHistorical = onShowHistorical
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
            if !viewModel.didLoadOnce, viewModel.payload == nil {
                loadingOverlay
            }
        }
        .task { viewModel.start() }
        .onChange(of: controlsHidden) { MapChrome.controlsHidden = controlsHidden }
        .onChange(of: locator.located) {
            guard let c = locator.located else { return }
            // Sent only once Start was pressed for the flight (#812): the
            // server reads the first fix on the ground at the departure as
            // Start, so a locate on the apron must not stand in for it. The
            // map still centres on the position either way.
            if let flightId = liveFlightId, appState.livePositions?.hasConsent(flightId: flightId) == true,
               let loc = locator.location,
               let fix = FlightTrackingService.fix(from: loc, alongNm: nil, crossTrackNm: nil) {
                appState.livePositions?.submit([fix], flightId: flightId)
            }
            viewModel.focusRequest = ForecastMapViewModel.FocusRequest(
                center: CLLocationCoordinate2D(latitude: c.lat, longitude: c.lon),
                span: MKCoordinateSpan(latitudeDelta: 4, longitudeDelta: 5), biasForSheet: false)
        }
        .modifier(AirportCardPresenter(viewModel: viewModel, catalog: catalog, isCompact: isCompact))
        .alert("No \(unavailableModel ?? "") data", isPresented: Binding(
            get: { unavailableModel != nil },
            set: { if !$0 { unavailableModel = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("This model doesn't reach \(shortDayLabel). Two- and three-model days differ by design — the consensus still shows what the models that do reach it agree on.")
        }
    }

    // MARK: - Map

    private var mapLayer: some View {
        ForecastMapKitView(
            payload: viewModel.payload,
            catalog: catalog,
            metric: viewModel.metric,
            mode: viewModel.mode,
            selectedIcao: viewModel.selectedIcao,
            payloadRevision: viewModel.payloadRevision,
            initialRegion: viewModel.initialRegion,
            focusRequest: viewModel.focusRequest,
            onSelect: { viewModel.select(icao: $0, biasForSheet: isCompact) },
            onFocusApplied: { viewModel.focusRequest = nil },
            onUserInteraction: { viewModel.markUserInteracted() }
        )
        .ignoresSafeArea(edges: isCompact ? .all : [])
    }

    // MARK: - Top controls ("when")

    private var topControls: some View {
        VStack(spacing: Theme.spacingS) {
            HStack(alignment: .center) {
                leadingControl
                Spacer(minLength: Theme.spacingS)
                if !controlsHidden, let onShowHistorical {
                    MapChrome.modeSwitch(current: .forecast) { _ in onShowHistorical() }
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
                // A linked date the grid can't show (#840): say which day is shown instead.
                if let notice = viewModel.linkedDateNotice {
                    Label(notice, systemImage: "calendar.badge.exclamationmark")
                        .font(.caption2)
                        .foregroundStyle(Theme.text)
                        .lineLimit(2)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(MapChrome.surface(RoundedRectangle(cornerRadius: 8)))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityIdentifier("mapLinkedDateNotice")
                }
            }
        }
        .frame(maxWidth: 560)
        .padding(.horizontal, Theme.spacingM)
        .padding(.top, isCompact ? Theme.spacingM : Theme.spacingS)
    }

    /// Dismiss on the iPhone cover, sidebar toggle on the iPad detail pane —
    /// the map's only route back to the flight list. Keyed on which callback
    /// the container passed, never on size class: the cover goes regular-width
    /// on a Plus/Pro Max iPhone in landscape (or an iPad window resized under
    /// it), and gating on `isCompact` hid the only exit there.
    @ViewBuilder private var leadingControl: some View {
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
    }

    /// ◀ day · hour ▶: the arrows step the hour, the middle opens the day and
    /// hour lists. Replaces the title, day capsule and hour stepper (#996),
    /// which showed the date twice.
    private var timeBar: some View {
        HStack(spacing: 4) {
            MapChrome.stepButton("chevron.left", label: "Earlier", enabled: viewModel.canStepHourBack) {
                viewModel.stepHour(-1)
            }
            Menu {
                Section("Day") {
                    ForEach(viewModel.days) { day in
                        Button {
                            viewModel.selectDay(day.day)
                        } label: {
                            MapChrome.menuRow(Self.dayMenuLabel(day), selected: day.day == viewModel.selectedDay)
                        }
                        .disabled(!day.available)
                    }
                }
                Section("Hour (UTC)") {
                    ForEach(viewModel.hours(forDay: viewModel.selectedDay), id: \.self) { hr in
                        Button {
                            viewModel.selectHour(hr)
                        } label: {
                            MapChrome.menuRow(String(format: "%02dZ", hr), selected: hr == viewModel.selectedHour)
                        }
                    }
                }
            } label: {
                MapChrome.timeLabel(title: timeTitle, hint: "Tap to pick day and hour")
            }
            .accessibilityIdentifier("mapTimeMenu")
            // A slot switch that misses the LRU shows a subtle in-flight spinner
            // (the full-screen overlay only covers the very first load).
            if viewModel.isLoading, viewModel.didLoadOnce {
                ProgressView().controlSize(.small)
            }
            MapChrome.stepButton("chevron.right", label: "Later", enabled: viewModel.canStepHourForward) {
                viewModel.stepHour(1)
            }
        }
        .padding(.horizontal, 6).padding(.vertical, 4)
        .background(MapChrome.surface(RoundedRectangle(cornerRadius: 12)))
    }

    // MARK: - Bottom panel ("what")

    private var bottomControls: some View {
        VStack(alignment: .trailing, spacing: Theme.spacingS) {
            Spacer()
            locateButton
            MapChrome.bottomPanel(legend: catalog?.legend(metric: viewModel.metric)) {
                metricMenu
                modelMenu
            }
            .frame(maxWidth: .infinity)
        }
        .padding(Theme.spacingM)
    }

    private var metricMenu: some View {
        Menu {
            ForEach(Self.metricSections, id: \.title) { section in
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

    private var modelMenu: some View {
        Menu {
            Button { viewModel.mode = .worst } label: {
                MapChrome.menuRow("Worst of models", selected: viewModel.mode == .worst)
            }
            Button { viewModel.mode = .majority } label: {
                MapChrome.menuRow("Majority of models", selected: viewModel.mode == .majority)
            }
            Section("Individual model") {
                ForEach(["gfs", "icon", "ecmwf"], id: \.self) { m in
                    let available = viewModel.modelAvailable(m, day: viewModel.selectedDay)
                    // Greyed-but-still-tappable: an unreachable model must explain
                    // itself on tap, not sit inert (design Gotchas). Only fully
                    // disable a day that has no data at all (the time bar's day list does that).
                    Button {
                        if available {
                            viewModel.mode = .model(m)
                        } else {
                            unavailableModel = m.uppercased()
                        }
                    } label: {
                        MapChrome.menuRow(m.uppercased() + (available ? "" : " (no data this day)"),
                                          selected: viewModel.mode == .model(m))
                    }
                }
            }
        } label: {
            MapChrome.pickLabel(caption: "Models", value: modeShortLabel)
        }
    }

    private var locateButton: some View {
        MapChrome.circleButton(systemImage: "location") { locator.request() }
            .accessibilityLabel("Centre on my location")
    }

    private var loadingOverlay: some View {
        VStack(spacing: Theme.spacingM) {
            if let err = viewModel.loadError {
                ContentUnavailableView("Map Unavailable", systemImage: "map", description: Text(err))
            } else {
                ProgressView("Loading forecast map…")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg.opacity(0.6))
    }

    // MARK: - Labels

    /// "Today · Sun 15 Nov · 12Z" for the time bar (no year: it fits a phone).
    private var timeTitle: String {
        let day = viewModel.days.first { $0.day == viewModel.selectedDay }
            .map(Self.dayMenuLabel) ?? shortDayLabel
        return String(format: "%@ · %02dZ", day, viewModel.selectedHour)
    }

    /// "Sun 15 · 12Z" for the hidden state's pill.
    private var compactTimeLabel: String {
        String(format: "%@ · %02dZ", shortDayLabel, viewModel.selectedHour)
    }

    private var shortDayLabel: String {
        Self.shortDayLabel(day: viewModel.selectedDay, days: viewModel.days)
    }

    /// "Worst of 3", "Majority of 2", "ECMWF": the count says worst of what.
    private var modeShortLabel: String {
        let n = viewModel.models(forDay: viewModel.selectedDay).count
        switch viewModel.mode {
        case .worst: return "Worst of \(n)"
        case .majority: return "Majority of \(n)"
        case .model(let m): return m.uppercased()
        }
    }
}
