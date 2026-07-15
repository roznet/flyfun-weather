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
/// otherwise cover the hour stepper); "what" (metric/legend/locate) sits
/// bottom-right, above the sheet.
struct ForecastMapView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var viewModel: ForecastMapViewModel
    @State private var locator = OneShotLocator()
    /// Set when the user taps a model that can't reach the selected day, to show
    /// the "why" instead of switching to an unreachable model.
    @State private var unavailableModel: String?
    /// nil on the iPad detail pane; set on the iPhone cover so it can dismiss.
    private let onClose: (() -> Void)?

    init(repository: any BriefingRepository, deepLink: MapDeepLink? = nil, onClose: (() -> Void)? = nil) {
        _viewModel = State(initialValue: ForecastMapViewModel(repository: repository, deepLink: deepLink))
        self.onClose = onClose
    }

    private var isCompact: Bool { horizontalSizeClass == .compact }
    private var catalog: ForecastMapCatalog? { appState.helpCatalog.mapsCatalog }

    var body: some View {
        ZStack(alignment: .top) {
            mapLayer
            topControls
            bottomControls
            if !viewModel.didLoadOnce, viewModel.payload == nil {
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
            HStack(alignment: .top) {
                if isCompact, let onClose {
                    Button { onClose() } label: {
                        Image(systemName: "xmark")
                            .font(.headline)
                            .padding(8)
                            .background(.ultraThinMaterial, in: Circle())
                    }
                    .accessibilityLabel("Close map")
                }
                Spacer()
                VStack(spacing: 2) {
                    Text(navTitle).font(.headline)
                    Text(navSubtitle).font(.caption).foregroundStyle(Theme.textMuted)
                }
                Spacer()
                // Balance the leading close button so the title stays centred.
                if isCompact, onClose != nil { Color.clear.frame(width: 36, height: 36) }
            }
            HStack(spacing: Theme.spacingS) {
                dayCapsule
                hourStepper
                // A slot switch that misses the LRU shows a subtle in-flight spinner
                // (the full-screen overlay only covers the very first load).
                if viewModel.isLoading, viewModel.didLoadOnce {
                    ProgressView().controlSize(.small)
                }
                Spacer()
            }
        }
        .padding(.horizontal, Theme.spacingM)
        .padding(.top, isCompact ? Theme.spacingM : Theme.spacingS)
    }

    /// Menu row with a leading checkmark only when selected (avoids a blank SF
    /// Symbol slot for the unselected rows).
    @ViewBuilder private func menuRow(_ title: String, selected: Bool) -> some View {
        if selected { Label(title, systemImage: "checkmark") } else { Text(title) }
    }

    private var dayCapsule: some View {
        Menu {
            ForEach(viewModel.days) { day in
                Button {
                    viewModel.selectDay(day.day)
                } label: {
                    menuRow(Self.dayMenuLabel(day), selected: day.day == viewModel.selectedDay)
                }
                .disabled(!day.available)
            }
        } label: {
            capsuleLabel(text: shortDayLabel, systemImage: "calendar")
        }
    }

    private var hourStepper: some View {
        HStack(spacing: 6) {
            Button { viewModel.stepHour(-1) } label: { Image(systemName: "chevron.left") }
                .disabled(!viewModel.canStepHourBack)
            Menu {
                ForEach(viewModel.hours(forDay: viewModel.selectedDay), id: \.self) { hr in
                    Button {
                        viewModel.selectHour(hr)
                    } label: {
                        menuRow(String(format: "%02dZ", hr), selected: hr == viewModel.selectedHour)
                    }
                }
            } label: {
                Text(String(format: "%02dZ", viewModel.selectedHour))
                    .font(.subheadline.weight(.medium)).monospacedDigit()
            }
            Button { viewModel.stepHour(1) } label: { Image(systemName: "chevron.right") }
                .disabled(!viewModel.canStepHourForward)
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: Capsule())
    }

    // MARK: - Bottom-right controls ("what")

    private var bottomControls: some View {
        VStack {
            Spacer()
            HStack(alignment: .bottom) {
                if let cat = catalog, let legend = cat.legend(metric: viewModel.metric) {
                    legendCapsule(legend)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: Theme.spacingS) {
                    modelMenu
                    metricMenu
                    locateButton
                }
            }
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
                                menuRow(item.label, selected: item.metric == viewModel.metric)
                            }
                        }
                    }
                }
            }
        } label: {
            capsuleLabel(text: catalog?.label(metric: viewModel.metric) ?? "Metric", systemImage: "paintpalette")
        }
    }

    private var modelMenu: some View {
        Menu {
            Button { viewModel.mode = .worst } label: {
                menuRow("Worst of models", selected: viewModel.mode == .worst)
            }
            Button { viewModel.mode = .majority } label: {
                menuRow("Majority of models", selected: viewModel.mode == .majority)
            }
            Section("Individual model") {
                ForEach(["gfs", "icon", "ecmwf"], id: \.self) { m in
                    let available = viewModel.modelAvailable(m, day: viewModel.selectedDay)
                    // Greyed-but-still-tappable: an unreachable model must explain
                    // itself on tap, not sit inert (design Gotchas). Only fully
                    // disable a day that has no data at all (dayCapsule does that).
                    Button {
                        if available {
                            viewModel.mode = .model(m)
                        } else {
                            unavailableModel = m.uppercased()
                        }
                    } label: {
                        menuRow(m.uppercased() + (available ? "" : " (no data this day)"),
                                selected: viewModel.mode == .model(m))
                    }
                }
            }
        } label: {
            capsuleLabel(text: modeShortLabel, systemImage: "square.stack.3d.up")
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

    private func legendCapsule(_ legend: ForecastMapCatalog.Legend) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(legend.title).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
            // Wrap the swatches so a long ramp doesn't overflow narrow phones.
            FlowLayout(spacing: 6) {
                ForEach(legend.items) { item in
                    HStack(spacing: 3) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Color.catalog(item.color))
                            .frame(width: 12, height: 8)
                        Text(item.label).font(.system(size: 10)).foregroundStyle(Theme.text)
                    }
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: 220, alignment: .leading)
    }

    private func capsuleLabel(text: String, systemImage: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage)
            Text(text)
            Image(systemName: "chevron.down").font(.caption2)
        }
        .font(.subheadline.weight(.medium))
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: Capsule())
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

    private var navTitle: String {
        Self.fullDayLabel(day: viewModel.selectedDay, days: viewModel.days)
    }

    private var navSubtitle: String {
        let n = viewModel.models(forDay: viewModel.selectedDay).count
        return String(format: "%02dZ · %@", viewModel.selectedHour, modeLongLabel(modelCount: n))
    }

    private var shortDayLabel: String {
        Self.shortDayLabel(day: viewModel.selectedDay, days: viewModel.days)
    }

    private var modeShortLabel: String {
        switch viewModel.mode {
        case .worst: return "Worst"
        case .majority: return "Majority"
        case .model(let m): return m.uppercased()
        }
    }

    private func modeLongLabel(modelCount: Int) -> String {
        switch viewModel.mode {
        case .worst: return "Worst of \(modelCount) model\(modelCount == 1 ? "" : "s")"
        case .majority: return "Majority of \(modelCount) model\(modelCount == 1 ? "" : "s")"
        case .model(let m): return m.uppercased()
        }
    }
}
