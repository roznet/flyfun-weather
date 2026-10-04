import CoreLocation
import Foundation
import MapKit
import UIKit

// =============================================================================
// SYNC — port of web/ts/visualization/cells-overlay-core.ts (#656 → iOS #661).
//
// The experimental radar-cell overlay: outlines per tier, a marker per cell
// coloured by **trend** (evolution, not safety — no red/green "go"), and a
// magenta arrow to where a core would be in 30 min, only for measured motion.
// Everything in `CellsOverlay` is pure so the rules — which overlay pairs with
// the radar frame, when it is too old to draw, what the badge and the cell
// sheet say — are unit-testable; `RouteMapKitView` only applies the result.
//
// Product voice: it directs attention, it never gives a verdict, and it is
// labelled experimental everywhere it appears.
// =============================================================================

enum CellsOverlay {

    /// Cells are fetched for the corridor box widened by this much: a cell just
    /// outside the sampled corridor is still one the pilot wants to see.
    static let marginNm = 50.0

    // MARK: Colours (web TREND_COLOURS / OUTLINE_STYLE / ARROW_COLOUR)

    static let trendHex: [String: String] = [
        "developing": "#d7263d",
        "decaying": "#1b6ca8",
        "steady": "#7a7a7a",
        "mixed": "#f18f01",
        "new": "#ffffff",
    ]
    static let unknownTrendHex = "#cccccc"
    static let arrowHex = "#b000b5"
    /// Legend order.
    static let trendOrder = ["developing", "steady", "decaying", "mixed", "new"]

    static func trendColor(_ state: String?) -> UIColor {
        let hex = state.flatMap { trendHex[$0] } ?? unknownTrendHex
        return ObservedMapImagery.color(hex: hex) ?? .lightGray
    }

    static var arrowColor: UIColor { ObservedMapImagery.color(hex: arrowHex) ?? .magenta }

    /// Outline stroke per tier; rain20 is never drawn on the route map (as on
    /// the web, which keeps rain areas for the "Now" tab).
    static func outlineStyle(_ tier: String) -> (color: UIColor, width: CGFloat) {
        switch tier {
        case "core41": (.black, 1.5)
        case "core35": (.white, 1.5)
        default: (UIColor(white: 0.54, alpha: 1), 1)
        }
    }

    static func tierLabel(_ tier: String) -> String {
        switch tier {
        case "rain20": "Rain area ≥20 dBZ"
        case "core35": "Core ≥35 dBZ"
        case "core41": "Core ≥41 dBZ"
        default: tier
        }
    }

    /// Marker radius in points (web circleMarker radius).
    static func markerRadius(_ tier: String) -> CGFloat {
        switch tier {
        case "core41": 7
        case "core35": 5
        default: 6
        }
    }

    /// Draw a motion arrow only for measured motion — never for withheld or
    /// unsupported motion, even if a stale arrow point were present — and only
    /// for cores: a frontal band's centroid motion is not a useful "where will
    /// it be".
    static func arrowEnd(_ cell: DisplayCell) -> CLLocationCoordinate2D? {
        guard cell.isCore, cell.motion?.status == "available",
              let arrow = cell.arrow, arrow.count == 2 else { return nil }
        return CLLocationCoordinate2D(latitude: arrow[0], longitude: arrow[1])
    }

    // MARK: Which overlay to draw

    enum Match: Equatable {
        case ok(CellFrame)
        case disabled
        case unavailable(since: String?)
    }

    /// Which overlay to draw beside a radar frame.
    ///
    /// The overlay for the **same stamp** as the drawn radar frame when there is
    /// one, else the newest at or before it (`radarStamp` nil — no reflectivity
    /// drawn — means the newest). Then the overlay's *own* age decides: past
    /// `staleAfterMinutes` nothing is drawn and the map says "unavailable since"
    /// that overlay's time. Never a stale picture.
    static func match(_ info: CellFramesResponse?, radarStamp: String?, now: Date) -> Match {
        guard let info else { return .unavailable(since: nil) }
        guard info.enabled else { return .disabled }
        let frames = info.frames
        let pick: CellFrame?
        if let radarStamp {
            pick = frames.first { $0.stamp == radarStamp } ?? frames.first { $0.stamp <= radarStamp }
        } else {
            pick = frames.first
        }
        guard let pick else {
            return .unavailable(since: frames.first?.validTime ?? info.unavailableSince)
        }
        guard let valid = Date.parseISO8601(pick.validTime),
              now.timeIntervalSince(valid) / 60 <= info.staleAfterMinutes else {
            return .unavailable(since: pick.validTime)
        }
        return .ok(pick)
    }

    /// Request path for one overlay, clipped to the route's box.
    static func displayPath(template: String, stamp: String, box: ObservedMapImagery.LatLonBox?) -> String {
        var segment = CharacterSet.urlPathAllowed
        segment.remove("/")
        let encoded = stamp.addingPercentEncoding(withAllowedCharacters: segment) ?? stamp
        let base = template.replacingOccurrences(of: "{stamp}", with: encoded)
        guard let box else { return base }
        let q = [("south", box.south), ("west", box.west), ("north", box.north), ("east", box.east)]
            .map { "\($0.0)=\(String(format: "%.2f", $0.1))" }
            .joined(separator: "&")
        return "\(base)?\(q)"
    }

    // MARK: Words

    static func hhmmZ(_ iso: String?) -> String {
        guard let iso, let hhmm = ObservedBadge.utcHHMM(iso) else { return "--:--Z" }
        return "\(hhmm)Z"
    }

    /// The overlay's badge line — its own time, never the radar's.
    static func badge(_ match: Match, display: CellDisplay?, now: Date) -> String {
        switch match {
        case .disabled:
            return "Cell analysis: not available on this server"
        case .unavailable(let since):
            if let since { return "Cell analysis unavailable since \(hhmmZ(since))" }
            return "Cell analysis unavailable (nothing received yet)"
        case .ok(let frame):
            let age = Date.parseISO8601(frame.validTime)
                .map { max(0, Int((now.timeIntervalSince($0) / 60).rounded())) } ?? 0
            var text = "Cells \(hhmmZ(frame.validTime)) · \(age) min old"
            if let display { text += " · \(display.cells.count) cells" }
            // Never "no lightning" (reads as "none in these cells"): pending
            // (on its way, #666) and unavailable are said as such — web `cellsBadge`.
            if let pending = display?.pending, !pending.isEmpty {
                text += " · " + pending.joined(separator: ", ") + " pending"
            }
            if let missing = display?.unavailable, !missing.isEmpty {
                text += " · " + missing.map(\.what).joined(separator: ", ") + " unavailable"
            }
            return text + " · experimental"
        }
    }

    /// The overlay's chip in the map's summary line — its own age, never the radar's.
    static func chip(_ match: Match, now: Date) -> ObservedMapImagery.SummaryChip {
        switch match {
        case .disabled, .unavailable:
            return .init(name: "Cells", status: "n/a", isWarning: true)
        case .ok(let frame):
            let age = Date.parseISO8601(frame.validTime).map { max(0, now.timeIntervalSince($0) / 60) } ?? 0
            return .init(name: "Cells", status: ObservedMapImagery.SummaryChip.ageText(age), isWarning: false)
        }
    }

    private static let compassPoints = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                                        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

    static func compass(_ deg: Double?) -> String {
        guard let deg, deg.isFinite else { return "–" }
        let normalised = (deg.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
        return compassPoints[Int((normalised / 22.5).rounded()) % 16]
    }

    /// "moving NE at 9 kt", or why there is no motion — in words, not codes.
    static func motionText(_ m: CellMotion?) -> String {
        guard let m, let status = m.status else { return "motion unknown" }
        switch status {
        case "available":
            guard let toward = m.towardDeg, (m.speedKt ?? 0) >= 1 else { return "nearly stationary" }
            return "moving \(compass(toward)) at \(Int((m.speedKt ?? 0).rounded())) kt"
        case "withheld": return "motion withheld: split/merge this frame"
        case "unsupported": return "motion withheld: too little of the cell in matched tiles"
        case "no_pair": return "no motion yet: no earlier radar frame to compare"
        default:
            if let reason = m.reason, !reason.isEmpty { return "motion \(status): \(reason)" }
            return "motion \(status)"
        }
    }

    static func trendText(_ t: CellTrend?) -> String {
        guard let t else { return "trend unknown" }
        guard t.state != "new", let window = t.windowMin else { return "new (less than 15 min of history)" }
        var parts: [String] = []
        if let d = t.dPeakDb { parts.append("peak \(signed(d)) dB") }
        if let a = t.areaRatio { parts.append("area ×\(number(a))") }
        if let f = t.dFlashes { parts.append("flashes \(signed(f))") }
        let head = "\(t.state ?? "unknown") over \(Int(window.rounded())) min"
        return parts.isEmpty ? head : "\(head) (\(parts.joined(separator: ", ")))"
    }

    /// The cell's measurements, one clause per line — descriptive only (web
    /// `cellPopupHtml`, minus the markup).
    static func detailLines(_ c: DisplayCell) -> [String] {
        var lines = [
            "peak \(value(c.peakDbz, " dBZ")) · area \(value(c.areaKm2, " km²"))",
            "rain rate peak \(value(c.ratePeakMmH, " mm/h"))\(rateAsOfText(c)) · lightning \(lightningText(c))"
                + (c.topFl.map { " · cloud top FL\(Int($0.rounded()))" } ?? ""),
            "age \(value(c.ageMin, " min")) (\(c.event ?? "")) · \(trendText(c.trend))",
            motionText(c.motion),
        ]
        if c.truncated == true { lines.append("partly outside radar coverage") }
        return lines
    }

    /// The caveat every cell surface carries.
    static let caveat = "Provisional thresholds: cells often flicker in as \"new\", rain rate can show "
        + "artefact peaks and lag the radar by ~10 min. Describes evolution, not safety."

    /// "pending" while the frame's lightning is on its way (#666), else the count or "–".
    static func lightningText(_ c: DisplayCell) -> String {
        c.flashes == nil && c.flashesPending == true ? "pending" : value(c.flashes)
    }

    /// " (as of HH:MMZ)" when the rain rate is older than the radar (#666), else "".
    static func rateAsOfText(_ c: DisplayCell) -> String {
        guard c.ratePeakMmH != nil, let asOf = c.rateAsOf else { return "" }
        return " (as of \(hhmmZ(asOf)))"
    }

    static func value(_ v: Double?, _ unit: String = "") -> String {
        guard let v else { return "–" }
        return number(v) + unit
    }

    static func number(_ v: Double) -> String {
        v.rounded() == v ? String(Int(v)) : String(format: "%.1f", v)
    }

    private static func signed(_ v: Double) -> String { (v > 0 ? "+" : "") + number(v) }

    // MARK: Route list (Observed tab)

    /// Cores to list under the route, strongest first. Rain areas stay on the
    /// map's legend side: a list of frontal bands is not a list of cells.
    static func listedCells(_ display: CellDisplay) -> [DisplayCell] {
        display.cells
            .filter(\.isCore)
            .sorted { ($0.peakDbz ?? -.infinity) > ($1.peakDbz ?? -.infinity) }
    }

    /// "18 NM NE of LFPN" — where the cell is, named by the nearest route
    /// waypoint. A location label only: no off-track distance or abeam time
    /// (route geometry is planned server-side, observed-motion §10).
    static func locationLabel(_ cell: DisplayCell, waypoints: [(icao: String, coordinate: CLLocationCoordinate2D)]) -> String? {
        let here = CLLocation(latitude: cell.lat, longitude: cell.lon)
        let nearest = waypoints
            .map { ($0.icao, $0.coordinate, here.distance(from: CLLocation(latitude: $0.coordinate.latitude,
                                                                            longitude: $0.coordinate.longitude))) }
            .min { $0.2 < $1.2 }
        guard let (icao, coord, meters) = nearest else { return nil }
        let nm = meters / 1852
        if nm < 3 { return "over \(icao)" }
        return "\(Int(nm.rounded())) NM \(compass(bearing(from: coord, to: here.coordinate))) of \(icao)"
    }

    /// Initial great-circle bearing, degrees true.
    static func bearing(from a: CLLocationCoordinate2D, to b: CLLocationCoordinate2D) -> Double {
        let φ1 = a.latitude * .pi / 180, φ2 = b.latitude * .pi / 180
        let Δλ = (b.longitude - a.longitude) * .pi / 180
        let y = sin(Δλ) * cos(φ2)
        let x = cos(φ1) * sin(φ2) - sin(φ1) * cos(φ2) * cos(Δλ)
        return (atan2(y, x) * 180 / .pi + 360).truncatingRemainder(dividingBy: 360)
    }
}

// MARK: - MapKit overlays

/// One tier outline (a traced detection boundary).
final class CellOutlineOverlay: MKPolyline {
    var tier = "core35"
}

/// A core's 30-minute motion arrow, from the cell to where it would be.
final class CellArrowOverlay: MKPolyline {}

/// Strokes the arrow and dots its head, sized in screen points.
nonisolated final class CellArrowRenderer: MKPolylineRenderer {
    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        super.draw(mapRect, zoomScale: zoomScale, in: context)
        guard polyline.pointCount >= 2 else { return }
        let end = point(for: polyline.points()[polyline.pointCount - 1])
        let r = 3.0 / zoomScale
        context.setFillColor((strokeColor ?? .magenta).cgColor)
        context.fillEllipse(in: CGRect(x: end.x - r, y: end.y - r, width: 2 * r, height: 2 * r))
    }
}

/// One cell's marker; tapping it shows the measurements in a callout.
final class CellAnnotation: NSObject, MKAnnotation {
    let cell: DisplayCell
    @objc dynamic var coordinate: CLLocationCoordinate2D
    var title: String? { CellsOverlay.tierLabel(cell.tier) + " · experimental" }

    init(cell: DisplayCell) {
        self.cell = cell
        self.coordinate = CLLocationCoordinate2D(latitude: cell.lat, longitude: cell.lon)
    }
}

/// A trend-coloured disc with a dark rim (web `circleMarker`), with the
/// measurements as a multi-line callout.
final class CellMarkerView: MKAnnotationView {
    static let reuseID = "observedCell"
    private let detail = UILabel()

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        canShowCallout = true
        collisionMode = .circle
        displayPriority = .defaultHigh
        layer.borderColor = UIColor(white: 0.13, alpha: 1).cgColor
        layer.borderWidth = 1
        detail.numberOfLines = 0
        detail.font = .preferredFont(forTextStyle: .caption1)
        detailCalloutAccessoryView = detail
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func configure(_ cell: DisplayCell) {
        let r = CellsOverlay.markerRadius(cell.tier)
        bounds = CGRect(x: 0, y: 0, width: 2 * r, height: 2 * r)
        layer.cornerRadius = r
        backgroundColor = CellsOverlay.trendColor(cell.trend?.state).withAlphaComponent(0.9)
        detail.text = CellsOverlay.detailLines(cell).joined(separator: "\n")
        accessibilityLabel = CellsOverlay.tierLabel(cell.tier)
    }
}
