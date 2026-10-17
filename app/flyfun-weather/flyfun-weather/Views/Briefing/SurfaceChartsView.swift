import SwiftUI
import UIKit

/// Surface pressure & fronts charts on the Discussion tab (#799): DWD, Met
/// Office and Météo-France TEMSI, as listed by the pack's server-built
/// `charts` manifest — so which sources show, their tab labels and captions
/// are the server's call, never re-derived here.
///
/// Source picker (when there is more than one), a chip per chart, the image
/// with the route overlay (on by default, toggleable, as on the web), caption
/// and the source's credit. Tap the image for a full-screen zoom viewer where
/// the route zooms with the chart. A chart the server no longer has — or one
/// that can't be fetched offline — says so instead of disappearing.
struct SurfaceChartsView: View {
    let viewModel: BriefingViewModel
    let manifest: PackChartsManifest

    @State private var sourceId: String = ""
    /// Chosen chart per source, so switching source and back keeps the pick.
    @State private var chosenChart: [String: String] = [:]
    @State private var showRoute = true
    @State private var overlays: [String: ChartOverlay] = [:]
    @State private var imageState: ImageState = .loading
    @State private var zoomed: ZoomedChart?

    enum ImageState: Equatable {
        case loading
        case loaded(UIImage)
        /// The server evicted it (410) or never had it.
        case unavailable
        /// Couldn't be fetched (offline and not cached, or a server error).
        case failed
    }

    struct ZoomedChart: Identifiable {
        let id = UUID()
        let image: UIImage
        let overlay: ChartOverlayEntry?
        let title: String
        let caption: String
    }

    private var source: PackChartSource? {
        manifest.sources.first { $0.source == sourceId } ?? manifest.sources.first
    }

    private var chart: PackChart? {
        guard let source else { return nil }
        let id = chosenChart[source.source] ?? source.defaultChartId
        return source.charts.first { $0.chartId == id } ?? source.charts.first
    }

    private var overlayEntry: ChartOverlayEntry? {
        guard showRoute, let source, let chart else { return nil }
        return overlays[source.source]?[chart.overlayKey]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("Surface Charts")
                .font(.headline)
                .foregroundStyle(Theme.text)

            if manifest.sources.count > 1 {
                Picker("Chart source", selection: sourceBinding) {
                    ForEach(manifest.sources) { src in
                        Text(src.label).tag(src.source)
                    }
                }
                .pickerStyle(.segmented)
            }

            if let source {
                chips(for: source)
            }

            imageArea

            if let chart, let source {
                VStack(alignment: .leading, spacing: 2) {
                    if !chart.caption.isEmpty {
                        Text(chart.caption)
                    }
                    Text("Source: \(source.attribution)")
                }
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .task(id: chart?.cacheKey) { await loadImage() }
        .task(id: source?.source) { await loadOverlay() }
        .fullScreenCover(item: $zoomed) { z in
            ZoomViewerScreen(title: z.title, caption: z.caption, contentSize: z.image.size) {
                Image(uiImage: z.image)
                    .resizable()
                    .overlay {
                        if let entry = z.overlay { ChartRouteOverlay(entry: entry) }
                    }
            }
        }
    }

    private var sourceBinding: Binding<String> {
        Binding(get: { source?.source ?? "" }, set: { sourceId = $0 })
    }

    @ViewBuilder
    private func chips(for source: PackChartSource) -> some View {
        HStack(alignment: .center, spacing: Theme.spacingS) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Theme.spacingXS) {
                    ForEach(source.charts) { c in
                        let selected = c.chartId == chart?.chartId
                        Button {
                            chosenChart[source.source] = c.chartId
                        } label: {
                            Text(c.label)
                                .font(.caption.weight(selected ? .semibold : .regular))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(selected ? Theme.primary.opacity(0.15) : Theme.surface,
                                            in: Capsule())
                                .overlay(Capsule().stroke(selected ? Theme.primary : Theme.border))
                                .foregroundStyle(selected ? Theme.primary : Theme.text)
                        }
                        .buttonStyle(.plain)
                        // Still tappable: it opens on "no longer available (valid …)".
                        .opacity(c.available ? 1 : 0.5)
                        .accessibilityAddTraits(selected ? .isSelected : [])
                    }
                }
            }
            Toggle("Route", isOn: $showRoute)
                .toggleStyle(.button)
                .font(.caption)
                .accessibilityLabel("Show route on chart")
        }
    }

    @ViewBuilder
    private var imageArea: some View {
        switch imageState {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, minHeight: 180)
        case .loaded(let image):
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .overlay {
                    if let entry = overlayEntry { ChartRouteOverlay(entry: entry) }
                }
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
                .onTapGesture { openZoom(image) }
                .accessibilityLabel("\(source?.label ?? "") chart \(chart?.label ?? "")")
                .accessibilityHint("Opens the chart full screen")
                .accessibilityAddTraits(.isButton)
        case .unavailable:
            placeholder(Self.unavailableText(validTime: chart?.validTime), systemImage: "clock.badge.xmark")
        case .failed:
            placeholder("Chart couldn't be loaded — it isn't saved on this device. Connect to load it.",
                        systemImage: "wifi.slash")
        }
    }

    private func placeholder(_ text: String, systemImage: String) -> some View {
        Label(text, systemImage: systemImage)
            .font(.callout)
            .foregroundStyle(Theme.textMuted)
            .frame(maxWidth: .infinity, minHeight: 120)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 6))
    }

    private func openZoom(_ image: UIImage) {
        guard let source, let chart else { return }
        zoomed = ZoomedChart(
            image: image,
            overlay: overlayEntry,
            title: "\(source.label) \(chart.label)",
            caption: [chart.caption, "Source: \(source.attribution)"]
                .filter { !$0.isEmpty }.joined(separator: " · ")
        )
    }

    private func loadImage() async {
        guard let chart else { return }
        guard chart.available else {
            imageState = .unavailable
            return
        }
        imageState = .loading
        do {
            let data = try await viewModel.fetchChartImage(chart)
            guard !Task.isCancelled else { return }
            if let image = UIImage(data: data) {
                imageState = .loaded(image)
            } else {
                imageState = .failed
            }
        } catch APIError.serverError(410, _) {
            imageState = .unavailable
        } catch APIError.notFound {
            imageState = .unavailable
        } catch {
            guard !Task.isCancelled else { return }
            imageState = .failed
        }
    }

    private func loadOverlay() async {
        guard let source, overlays[source.source] == nil else { return }
        if let overlay = await viewModel.fetchChartOverlay(for: source) {
            overlays[source.source] = overlay
        }
    }

    /// "Chart no longer available (valid 2026-05-10 12Z)".
    static func unavailableText(validTime: String?) -> String {
        guard let validTime, let date = Date.parseISO8601(validTime) else {
            return "Chart no longer available."
        }
        return "Chart no longer available (valid \(utcFormatter.string(from: date)))."
    }

    private static let utcFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd HH'Z'"
        return f
    }()
}

/// Shown in place of the charts when the route is inside chart coverage but
/// the pack has none (beyond the forecast horizon, or the chart refresh failed).
struct SurfaceChartsUnavailableView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("Surface Charts")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Text("Surface charts unavailable for this briefing — the flight is beyond the chart horizon, or the chart refresh failed.")
                .font(.callout)
                .foregroundStyle(Theme.textMuted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
    }
}
