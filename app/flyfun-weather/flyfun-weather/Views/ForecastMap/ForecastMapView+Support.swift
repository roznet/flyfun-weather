import CoreLocation
import SwiftUI

// MARK: - Metric picker: "a list of questions, not variable names" (#420)

extension ForecastMapView {
    /// One entry in the metric menu.
    struct MetricItem { let label: String; let metric: String }
    /// A question the map answers, grouping one or two metrics (design table).
    struct MetricSection { let title: String; let items: [MetricItem] }

    /// The picker frames each metric as a pilot's question — what makes this map
    /// different from Windy. Metric ids match the served catalog; entries whose
    /// metric the catalog doesn't define are filtered out at render time.
    static let metricSections: [MetricSection] = [
        MetricSection(title: "Can I get in?", items: [
            MetricItem(label: "Flight category", metric: "flight_category"),
        ]),
        MetricSection(title: "Can I land?", items: [
            MetricItem(label: "Crosswind (best runway)", metric: "crosswind_kt"),
        ]),
        MetricSection(title: "Will I see the ground?", items: [
            MetricItem(label: "Ceiling", metric: "ceiling_ft"),
            MetricItem(label: "Visibility", metric: "visibility_m"),
        ]),
        MetricSection(title: "Will it be rough?", items: [
            MetricItem(label: "Convective risk", metric: "convective_risk"),
            MetricItem(label: "CAPE", metric: "cape_jkg"),
        ]),
        MetricSection(title: "Will it take forever?", items: [
            MetricItem(label: "Headwind", metric: "headwind_kt"),
        ]),
        MetricSection(title: "Do I need an alternate?", items: [
            MetricItem(label: "FAA / EASA", metric: "alternate_needed"),
        ]),
        MetricSection(title: "More", items: [
            MetricItem(label: "Wind", metric: "wind_speed_kt"),
            MetricItem(label: "Cloud cover", metric: "cloud_cover_pct"),
        ]),
    ]

    // MARK: - Day labels (weekday + date, never D-N — #419 W1)

    private static func date(for day: ForecastDay?, dayOffset: Int) -> Date {
        if let iso = day?.date, !iso.isEmpty, let d = isoDayFormatter.date(from: iso) { return d }
        // Fallback (grid unavailable): today + offset, in UTC.
        return Calendar.utc.date(byAdding: .day, value: dayOffset, to: Date()) ?? Date()
    }

    /// Short capsule label: "Today" for D+0, else "Sat 18".
    static func shortDayLabel(day: Int, days: [ForecastDay]) -> String {
        if day == 0 { return "Today" }
        return shortFormatter.string(from: date(for: days.first { $0.day == day }, dayOffset: day))
    }

    /// Nav-title label: "Sat 18 Jul 2026" (D+0 prefixed "Today · ").
    static func fullDayLabel(day: Int, days: [ForecastDay]) -> String {
        let full = fullFormatter.string(from: date(for: days.first { $0.day == day }, dayOffset: day))
        return day == 0 ? "Today · \(full)" : full
    }

    /// Menu-row label: "Today · Sat 18" / "Sat 18 Jul".
    static func dayMenuLabel(_ day: ForecastDay) -> String {
        let d = date(for: day, dayOffset: day.day)
        let short = menuFormatter.string(from: d)
        return day.day == 0 ? "Today · \(short)" : short
    }

    // Formatters (UTC — the grid's dates are UTC forecast dates).
    private static let isoDayFormatter: DateFormatter = {
        let f = DateFormatter(); f.calendar = Calendar.utc; f.timeZone = .utc
        f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); return f
    }()
    private static let shortFormatter = makeFormatter("EEE d")
    private static let menuFormatter = makeFormatter("EEE d MMM")
    private static let fullFormatter = makeFormatter("EEE d MMM yyyy")

    private static func makeFormatter(_ format: String) -> DateFormatter {
        let f = DateFormatter(); f.calendar = Calendar.utc; f.timeZone = .utc
        f.setLocalizedDateFormatFromTemplate(format); f.dateFormat = format
        return f
    }
}

private extension Calendar {
    static var utc: Calendar {
        var c = Calendar(identifier: .gregorian); c.timeZone = .utc; return c
    }
}

private extension TimeZone {
    static let utc = TimeZone(identifier: "UTC")!
}

// MARK: - Airport-card presentation (iPad inspector · iPhone bottom sheet)

/// Presents the tapped-airport card as a trailing `.inspector` column on iPad
/// (regular width) and a bottom sheet on iPhone. The compact sheet keeps the map
/// pannable + live-recolouring underneath via
/// `.presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))` —
/// the whole "step the hour and watch both update" interaction depends on it.
struct AirportCardPresenter: ViewModifier {
    let viewModel: ForecastMapViewModel
    let catalog: ForecastMapCatalog?
    let isCompact: Bool

    func body(content: Content) -> some View {
        let isPresented = Binding(
            get: { viewModel.selectedIcao != nil },
            set: { if !$0 { viewModel.deselect() } }
        )
        if isCompact {
            content.sheet(isPresented: isPresented) {
                card
                    .presentationDetents([.fraction(0.45), .large])
                    .presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))
                    .presentationDragIndicator(.visible)
            }
        } else {
            content.inspector(isPresented: isPresented) {
                card.inspectorColumnWidth(min: 320, ideal: 360, max: 440)
            }
        }
    }

    @ViewBuilder private var card: some View {
        if let airport = viewModel.selectedAirport, let catalog {
            ForecastAirportCard(viewModel: viewModel, airport: airport, catalog: catalog)
        } else {
            Color.clear
        }
    }
}

// MARK: - One-shot user location (locate-me)

/// A single-fix location request for the "centre on me" button — lighter than
/// wiring `FlightTrackingService` (which is for continuous route tracking). The
/// app already declares `NSLocationWhenInUseUsageDescription`.
@Observable
@MainActor
final class OneShotLocator: NSObject {
    /// Equatable so SwiftUI `onChange` fires once per fresh fix.
    struct Fix: Equatable { let lat: Double; let lon: Double }

    private(set) var located: Fix?
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyKilometer
    }

    func request() {
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            manager.requestLocation()
        default:
            break  // denied/restricted — nothing to do
        }
    }
}

extension OneShotLocator: @preconcurrency CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        if status == .authorizedWhenInUse || status == .authorizedAlways {
            manager.requestLocation()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let c = locations.last?.coordinate else { return }
        Task { @MainActor in self.located = Fix(lat: c.latitude, lon: c.longitude) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // Best-effort; a locate failure is a silent no-op.
    }
}
