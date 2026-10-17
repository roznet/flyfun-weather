import CoreLocation
import Foundation
import MapKit
import UIKit

// =============================================================================
// SYNC — port of the lightning rules in
// web/ts/visualization/route-map/observed-overlay-geometry.ts
// (`FLASH_TRAIL_MINUTES`, `flashOpacity`, `drawableFlashes`, `FLASH_GLYPH`,
// `FLASH_GLYPH_*_PX`, `flashGlyphPoints`) and the glyph paint in
// web/ts/visualization/flash-glyph-layer.ts (#665).
//
// Lightning on the route map: every flash in the corridor box over the last
// hour, drawn as a small bolt faded by its own age (a ten-minute accumulation
// drawn flat would suggest every flash happened at once). One MapKit overlay
// paints them all — hundreds of `MKAnnotation`s jank (see `ForecastMapKitView`).
// Everything in `ObservedLightning` is pure so the rules — which flashes are
// drawn, how faded, what the badge says, when the feed is late — are
// unit-testable; `RouteMapKitView` only applies the result.
// =============================================================================

enum ObservedLightning {

    /// Lightning older than this is dropped rather than drawn nearly invisible.
    static let trailMinutes = 60.0
    /// MTG LI publishes every 10 min; past two missed cycles the feed is late
    /// and the badge says newer flashes may be missing (cells use the same 25).
    static let staleAfterMinutes = 25.0

    /// A small lightning bolt, polygon vertices in a unit box ((0,0) top-left,
    /// y down). A bolt rather than a dot so a flash reads as lightning without
    /// a legend and is not mistaken for a radar echo or an airport marker.
    nonisolated static let glyph: [(x: Double, y: Double)] = [
        (0.62, 0),
        (0.08, 0.58),
        (0.44, 0.58),
        (0.3, 1),
        (0.92, 0.4),
        (0.56, 0.4),
        (0.78, 0),
    ]
    /// On-screen size of the bolt, in points (a dot was 5–6 pt across).
    nonisolated static let glyphWidth: CGFloat = 8
    nonisolated static let glyphHeight: CGFloat = 12

    /// Legend tint: the rim purple, since a white bolt vanishes on the sheet.
    static let color = UIColor(cgColor: rimCGColor())
    /// Web `FLASH_COLOR` (white) and `FLASH_RIM` (#6d28d9): white like
    /// lightning, the purple rim keeps it readable over bright cloud tops and
    /// the light basemap. Built per call so the off-main renderer needn't
    /// touch a main-actor static.
    nonisolated static func fillCGColor() -> CGColor {
        CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)
    }
    nonisolated static func rimCGColor() -> CGColor {
        CGColor(srgbRed: 0x6D / 255, green: 0x28 / 255, blue: 0xD9 / 255, alpha: 1)
    }

    // MARK: Fade

    /// Opacity for a flash of a given age: linear fade to nothing at the trail end.
    static func opacity(ageMinutes: Double) -> Double {
        if ageMinutes <= 0 { return 0.9 }
        if ageMinutes >= trailMinutes { return 0 }
        return 0.9 * (1 - ageMinutes / trailMinutes)
    }

    /// A flash ready to draw: where it is and how faded.
    nonisolated struct DrawableFlash: Equatable, Sendable {
        let lat: Double
        let lon: Double
        let opacity: Double
    }

    /// Flashes still inside the trail with their faded opacity, oldest first
    /// so the most recent draw on top. Unparseable times are dropped.
    static func drawable(_ flashes: [ObservedFlash], now: Date) -> [DrawableFlash] {
        flashes
            .compactMap { flash -> (ObservedFlash, Double)? in
                guard let time = Date.parseISO8601(flash.time) else { return nil }
                let age = now.timeIntervalSince(time) / 60
                return age < trailMinutes ? (flash, age) : nil
            }
            .sorted { $0.1 > $1.1 }
            .map { DrawableFlash(lat: $0.0.lat, lon: $0.0.lon, opacity: opacity(ageMinutes: $0.1)) }
            .filter { $0.opacity > 0 }
    }

    /// The bolt's vertices centred on `center`, in the caller's units.
    nonisolated static func glyphPoints(center: CGPoint, width: CGFloat, height: CGFloat) -> [CGPoint] {
        glyph.map { CGPoint(x: center.x + (CGFloat($0.x) - 0.5) * width,
                            y: center.y + (CGFloat($0.y) - 0.5) * height) }
    }

    // MARK: Request

    /// `/api/observed/flashes` for the corridor box, last `trailMinutes` only
    /// (the web route map asks for every retained frame and drops the rest;
    /// asking for the trail keeps a phone's payload to what is drawn).
    static func flashesPath(box: ObservedMapImagery.LatLonBox) -> String {
        func f(_ v: Double) -> String { String(format: "%.4f", v) }
        return "/api/observed/flashes?south=\(f(box.south))&west=\(f(box.west))"
            + "&north=\(f(box.north))&east=\(f(box.east))&minutes=\(Int(trailMinutes))"
    }

    // MARK: Badge

    /// What the map knows about lightning right now.
    enum State: Equatable {
        /// Nothing asked yet / first request in flight: say nothing.
        case loading
        /// The request failed (no server, offline, observed disabled).
        case unavailable
        case loaded(ObservedFlashesResponse)
    }

    /// Age of the newest lightning frame, nil when none is held.
    static func newestAgeMinutes(_ response: ObservedFlashesResponse, now: Date) -> Double? {
        guard let iso = response.newestValidTime, let valid = Date.parseISO8601(iso) else { return nil }
        return max(0, now.timeIntervalSince(valid) / 60)
    }

    /// "Lightning 14:05Z · 6 min old · 12 flashes in the last 60 min · <attribution>"
    ///
    /// "Unavailable" and "no current frame" are said as such, never as "no
    /// lightning"; a late feed says newer flashes may be missing, so an empty
    /// patch of map is not read as a storm that has stopped.
    static func badge(_ state: State, now: Date) -> String? {
        switch state {
        case .loading:
            return nil
        case .unavailable:
            return "Lightning unavailable"
        case .loaded(let response):
            guard let iso = response.newestValidTime, let age = newestAgeMinutes(response, now: now) else {
                return "Lightning: no current frame"
            }
            var text = ObservedBadge.ageText(iso, age, "Lightning")
            if age > staleAfterMinutes { text += " · feed late, newer flashes may be missing" }
            text += " · \(flashCountText(drawable(response.flashes, now: now).count))"
            if let attribution = response.attribution?.text, !attribution.isEmpty {
                text += " · \(attribution)"
            }
            return text
        }
    }

    /// "1 flash" / "12 flashes" in the last hour. Flashes, not strikes: MTG LI
    /// sees in-cloud lightning too, not only cloud-to-ground.
    static func flashCountText(_ count: Int) -> String {
        let noun = count == 1 ? "flash" : "flashes"
        return "\(count) \(noun) in the last \(Int(trailMinutes)) min"
    }

    /// The layer's chip in the map's summary line — its own age, orange when
    /// unavailable or late.
    static func chip(_ state: State, now: Date) -> ObservedMapImagery.SummaryChip? {
        switch state {
        case .loading:
            return nil
        case .unavailable:
            return ObservedMapImagery.SummaryChip(name: "Lightning", status: "n/a", isWarning: true)
        case .loaded(let response):
            guard let age = newestAgeMinutes(response, now: now) else {
                return ObservedMapImagery.SummaryChip(name: "Lightning", status: "n/a", isWarning: true)
            }
            return ObservedMapImagery.SummaryChip(
                name: "Lightning", status: ObservedMapImagery.SummaryChip.ageText(age),
                isWarning: age > staleAfterMinutes)
        }
    }
}

// MARK: - MapKit overlay

/// Every drawn flash in one overlay, painted by `LightningRenderer`. Immutable:
/// a new set of flashes (or a new fade) is a new overlay.
nonisolated final class LightningOverlay: NSObject, MKOverlay {
    let points: [(point: MKMapPoint, opacity: Double)]
    let coordinate: CLLocationCoordinate2D
    // The whole world: a bolt is sized in screen points, so near the edge of
    // the flashes' own extent it spills over any tighter rect at low zoom.
    let boundingMapRect: MKMapRect = .world

    init(flashes: [ObservedLightning.DrawableFlash]) {
        points = flashes.map {
            (point: MKMapPoint(CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon)), opacity: $0.opacity)
        }
        coordinate = MKMapPoint(x: MKMapRect.world.midX, y: MKMapRect.world.midY).coordinate
    }
}

/// Paints each flash as a small bolt of fixed screen size.
nonisolated final class LightningRenderer: MKOverlayRenderer {
    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        guard let overlay = overlay as? LightningOverlay else { return }
        let width = ObservedLightning.glyphWidth / zoomScale
        let height = ObservedLightning.glyphHeight / zoomScale
        let reach = mapRect.insetBy(dx: -Double(width), dy: -Double(height))
        context.setLineWidth(1 / zoomScale)
        context.setLineJoin(.round)
        context.setStrokeColor(ObservedLightning.rimCGColor())
        context.setFillColor(ObservedLightning.fillCGColor())
        for flash in overlay.points where reach.contains(flash.point) {
            let vertices = ObservedLightning.glyphPoints(
                center: point(for: flash.point), width: width, height: height)
            context.setAlpha(CGFloat(flash.opacity))
            context.beginPath()
            context.addLines(between: vertices)
            context.closePath()
            context.drawPath(using: .fillStroke)
        }
        context.setAlpha(1)
    }
}
