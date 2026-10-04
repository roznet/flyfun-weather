import Foundation
import MapKit
import UIKit

// =============================================================================
// SYNC — port of web/ts/visualization/route-map/observed-overlay-geometry.ts and
// the layer rules in observed-overlay.ts (#652 → iOS #654).
//
// The route map draws observed imagery as Web Mercator XYZ tiles served by
// `/api/observed/tiles/...`, keyed by frame stamp: radar (reflectivity or rain
// rate, one at a time) over an optional satellite-infrared underlay, plus the
// dashed corridor box the sampled numbers describe. Everything here is pure so
// the rules — what is drawn, what the badge says, which zooms are fetched — are
// unit-testable; `RouteMapKitView` only applies the result.
// =============================================================================

enum ObservedMapImagery {

    /// Radar sources the map draws as tiles, in menu order. Cloud tops stay on
    /// the web's corridor image and lightning is points — neither is on iOS yet.
    static let radarSources = ["opera_dbzh", "opera_rate"]
    /// The satellite infrared underlay (EUMETView, proxied by the server).
    static let satelliteSource = "satellite_ir"

    /// Reflectivity by default: highest cadence, and the one that answers "is
    /// that cell on my route" (web parity).
    static let defaultSelection = "opera_dbzh"
    static let defaultOpacity = 0.75
    /// Fixed, like the web: the muted basemap's coastlines read through it, and
    /// the opacity control stays about the radar.
    static let satelliteOpacity = 0.8
    /// Menu steps for the radar opacity (the web's slider is 10–100%).
    static let opacitySteps: [Double] = [0.4, 0.6, 0.75, 0.9, 1.0]
    /// A satellite cycle younger than this may have been advertised before it
    /// was complete upstream; its tiles are not pinned in the memory cache.
    static let youngSatelliteMinutes = 30.0

    /// Menu / fallback label per source, used before (or without) a listing.
    static func label(for source: String) -> String {
        switch source {
        case "opera_dbzh": "Radar reflectivity"
        case "opera_rate": "Radar rain rate"
        case satelliteSource: "Satellite infrared"
        default: source
        }
    }

    // MARK: Selection

    /// Radar sources this briefing collected — an option that would draw an
    /// empty map is worse than an absent one ("nothing there" vs "not collected").
    static func availableRadar(_ observed: ObservedConditions?) -> [String] {
        guard let observed else { return [] }
        return radarSources.filter { source in
            switch source {
            case "opera_dbzh": observed.reflectivity != nil
            case "opera_rate": observed.rainRate != nil
            default: false
            }
        }
    }

    /// The radar layer to draw for the pilot's pick. `""` ("None") is a choice
    /// and draws nothing; only a pick this briefing did not collect falls back —
    /// to reflectivity, else rain rate, else nothing (web `resolveObservedSelection`).
    static func resolveSelection(_ chosen: String, available: [String]) -> String {
        if chosen.isEmpty { return "" }
        if available.contains(chosen) { return chosen }
        return radarSources.first { available.contains($0) } ?? ""
    }

    // MARK: Tiles

    /// The frame to draw now, or nil when there is nothing current — a stale
    /// frame is not drawn as if it were the present sky.
    static func currentFrame(_ info: ObservedFramesResponse?) -> ObservedImageryFrame? {
        guard let info, !info.stale else { return nil }
        return info.frames.first
    }

    /// True for zooms the server renders. MapKit asks for every zoom; outside
    /// this range the tile endpoint answers 404, so those are never requested.
    nonisolated static func isZoomServed(_ z: Int, minZoom: Int, maxZoom: Int) -> Bool {
        z >= minZoom && z <= maxZoom
    }

    /// Request path for one tile of one frame. The stamp is in the path, so a
    /// different frame is a different URL, never a re-fetch of the same one.
    nonisolated static func tilePath(template: String, stamp: String, z: Int, x: Int, y: Int) -> String {
        var segment = CharacterSet.urlPathAllowed
        segment.remove("/")
        let encoded = stamp.addingPercentEncoding(withAllowedCharacters: segment) ?? stamp
        return template
            .replacingOccurrences(of: "{stamp}", with: encoded)
            .replacingOccurrences(of: "{z}", with: String(z))
            .replacingOccurrences(of: "{x}", with: String(x))
            .replacingOccurrences(of: "{y}", with: String(y))
    }

    /// One tile layer to put on the map.
    nonisolated struct TileLayerSpec: Equatable, Sendable {
        let source: String
        let stamp: String
        let template: String
        let minZoom: Int
        let maxZoom: Int
        let opacity: Double
        /// Satellite underneath, radar on top.
        let isUnderlay: Bool
        /// Whether tiles may be kept in the in-memory cache for the session.
        let cacheable: Bool
        /// Source + frame: an unchanged key keeps its overlay (no re-fetch).
        var key: String { "\(source)|\(stamp)" }
    }

    /// What the map draws for the current picks, and the badge lines that label
    /// it. Radar badge first, then the satellite's (web order).
    ///
    /// - `frames`: listings received so far, per source.
    /// - `failed`: sources whose listing could not be fetched (and none is held).
    static func compose(
        selection: String,
        showSatellite: Bool,
        radarOpacity: Double,
        frames: [String: ObservedFramesResponse],
        failed: Set<String>,
        now: Date
    ) -> (layers: [TileLayerSpec], badges: [String]) {
        var layers: [TileLayerSpec] = []
        var badges: [String] = []

        if showSatellite {
            let (layer, badge) = layerAndBadge(
                source: satelliteSource, opacity: satelliteOpacity, isUnderlay: true,
                frames: frames, failed: failed, now: now)
            if let layer { layers.append(layer) }
            if let badge { badges.append(badge) }
        }
        if !selection.isEmpty {
            let (layer, badge) = layerAndBadge(
                source: selection, opacity: radarOpacity, isUnderlay: false,
                frames: frames, failed: failed, now: now)
            if let layer { layers.append(layer) }
            if let badge { badges.insert(badge, at: 0) }
        }
        return (layers, badges)
    }

    private static func layerAndBadge(
        source: String, opacity: Double, isUnderlay: Bool,
        frames: [String: ObservedFramesResponse], failed: Set<String>, now: Date
    ) -> (TileLayerSpec?, String?) {
        guard let info = frames[source] else {
            // No listing yet: say nothing while it loads; once it has failed,
            // say so, so an empty map never reads as "no echoes".
            return (nil, failed.contains(source) ? unavailableBadge(source) : nil)
        }
        guard let frame = currentFrame(info) else { return (nil, staleBadge(info, now: now)) }
        let age = frameAgeMinutes(frame, now: now)
        let spec = TileLayerSpec(
            source: source, stamp: frame.stamp, template: info.tileUrlTemplate,
            minZoom: info.minZoom, maxZoom: info.maxZoom, opacity: opacity,
            isUnderlay: isUnderlay,
            cacheable: source != satelliteSource || age >= youngSatelliteMinutes)
        return (spec, frameBadge(info, frame, now: now))
    }

    // MARK: Badges

    /// Age of a drawn frame from its own valid time (the listing's
    /// `ageMinutes` was true when it was listed, up to a couple of minutes ago).
    static func frameAgeMinutes(_ frame: ObservedImageryFrame, now: Date) -> Double {
        guard let valid = Date.parseISO8601(frame.validTime) else { return frame.ageMinutes }
        return max(0, now.timeIntervalSince(valid) / 60)
    }

    /// "Radar reflectivity 14:05Z · 12 min old · 10 min rolling max · <attribution>"
    ///
    /// Every clause is load-bearing: the frame's own time, the age that turns
    /// "there is a cell" into "there was a cell twelve minutes ago", and the
    /// window note that stops a rolling maximum being read as a snapshot.
    static func frameBadge(_ info: ObservedFramesResponse, _ frame: ObservedImageryFrame, now: Date) -> String {
        var text = ObservedBadge.ageText(frame.validTime, frameAgeMinutes(frame, now: now), info.label)
        if let window = info.windowMinutes, window > 0 {
            text += " · \(Int(window.rounded())) min rolling max"
        }
        if let attribution = info.attribution?.text, !attribution.isEmpty {
            text += " · \(attribution)"
        }
        return text
    }

    /// A layer whose newest frame is too old to draw, so the pilot can tell
    /// "hidden because stale" from "no echoes".
    static func staleBadge(_ info: ObservedFramesResponse, now: Date) -> String {
        guard let newest = info.frames.first,
              let valid = Date.parseISO8601(newest.validTime),
              let hhmm = ObservedBadge.utcHHMM(newest.validTime) else {
            return "\(info.label): no current frame"
        }
        let age = Int((now.timeIntervalSince(valid) / 60).rounded())
        return "\(info.label): not shown — newest frame \(hhmm)Z is \(age) min old"
    }

    static func unavailableBadge(_ source: String) -> String {
        "\(label(for: source)): imagery unavailable"
    }

    // MARK: Corridor box

    nonisolated struct LatLonBox: Equatable, Sendable {
        let south: Double
        let west: Double
        let north: Double
        let east: Double
    }

    /// Route bounding box padded by the corridor width (web `corridorBox`).
    static func corridorBox(_ points: [CLLocationCoordinate2D], radiusNm: Double) -> LatLonBox? {
        guard !points.isEmpty else { return nil }
        let lats = points.map(\.latitude), lons = points.map(\.longitude)
        let minLat = lats.min()!, maxLat = lats.max()!
        let minLon = lons.min()!, maxLon = lons.max()!
        let padLat = (radiusNm * 1.852) / 111.0
        // Longitude degrees shrink with latitude; pad with the widest latitude
        // on the route so the box never clips the corridor at its poleward end.
        let cosLat = max(0.2, cos(max(abs(minLat), abs(maxLat)) * .pi / 180))
        let padLon = padLat / cosLat
        return LatLonBox(south: minLat - padLat, west: minLon - padLon,
                         north: maxLat + padLat, east: maxLon + padLon)
    }

    /// The corridor width the box outlines: the cross-section's corridor pick
    /// when it is a radius the server sampled, else the widest (as the resolver).
    static func corridorRadius(_ observed: ObservedConditions?, picked: Double?) -> Double? {
        let radii = observed?.radiiNm ?? []
        guard !radii.isEmpty else { return nil }
        if let picked, radii.contains(picked) { return picked }
        return radii.max()
    }

    // MARK: Legend

    /// `#rrggbb` → colour; nil for anything else.
    static func color(hex: String) -> UIColor? {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        return UIColor(red: CGFloat((value >> 16) & 0xFF) / 255,
                       green: CGFloat((value >> 8) & 0xFF) / 255,
                       blue: CGFloat(value & 0xFF) / 255, alpha: 1)
    }

    /// Legend end label: the value, with the units once on the high end.
    static func legendValue(_ value: Double, units: String?) -> String {
        let number = value.rounded() == value ? String(Int(value)) : String(format: "%g", value)
        guard let units, !units.isEmpty else { return number }
        return "\(number) \(units)"
    }
}

// MARK: - MapKit overlays

/// One observed tile layer. `MKTileOverlay` loads URLs itself and cannot add
/// headers, so `loadTile` is overridden to fetch through the app's API client
/// (bearer token, rolled forward like every other call).
nonisolated final class ObservedTileOverlay: MKTileOverlay {
    let spec: ObservedMapImagery.TileLayerSpec
    /// Renderer alpha. Touched on the main thread only (MapKit delegate + updates).
    var opacity: CGFloat
    private let fetch: @Sendable (String) async throws -> Data

    init(spec: ObservedMapImagery.TileLayerSpec, fetch: @escaping @Sendable (String) async throws -> Data) {
        self.spec = spec
        self.opacity = CGFloat(spec.opacity)
        self.fetch = fetch
        super.init(urlTemplate: nil)
        canReplaceMapContent = false
        tileSize = CGSize(width: 256, height: 256)
        // MapKit draws nothing below minimumZ and scales maximumZ tiles beyond it.
        minimumZ = spec.minZoom
        maximumZ = spec.maxZoom
    }

    override func loadTile(at path: MKTileOverlayPath, result: @escaping (Data?, Error?) -> Void) {
        let spec = self.spec
        guard ObservedMapImagery.isZoomServed(path.z, minZoom: spec.minZoom, maxZoom: spec.maxZoom) else {
            result(nil, nil)  // out of range: draw nothing, request nothing
            return
        }
        let tilePath = ObservedMapImagery.tilePath(
            template: spec.template, stamp: spec.stamp, z: path.z, x: path.x, y: path.y)
        if let cached = ObservedTileCache.data(for: tilePath) {
            result(cached, nil)
            return
        }
        let fetch = self.fetch
        // MapKit's `result` isn't `@Sendable`, but it's documented callable from
        // any thread and is invoked exactly once below, so the hop is safe.
        nonisolated(unsafe) let deliver = result
        Task.detached(priority: .utility) {
            do {
                let data = try await fetch(tilePath)
                if spec.cacheable { ObservedTileCache.store(data, for: tilePath) }
                deliver(data, nil)
            } catch {
                // 410 (frame purged) / 503 (being prepared): the tile stays
                // empty; the next frame is a new overlay anyway.
                deliver(nil, error)
            }
        }
    }
}

/// Tiles are immutable per stamp, so panning back over them needn't refetch.
/// Bounded; `NSCache` is thread-safe and evicts under memory pressure.
nonisolated enum ObservedTileCache {
    nonisolated(unsafe) private static let cache: NSCache<NSString, NSData> = {
        let c = NSCache<NSString, NSData>()
        c.totalCostLimit = 16 * 1024 * 1024
        return c
    }()

    static func data(for path: String) -> Data? { cache.object(forKey: path as NSString) as Data? }

    static func store(_ data: Data, for path: String) {
        cache.setObject(data as NSData, forKey: path as NSString, cost: data.count)
    }
}

/// The corridor the sampled numbers describe, outlined (dashed) on the map.
/// An `MKPolygon` edge is straight in map points, like the web's rectangle.
final class ObservedCorridorOverlay: MKPolygon {
    var box: ObservedMapImagery.LatLonBox?

    static func make(_ box: ObservedMapImagery.LatLonBox) -> ObservedCorridorOverlay {
        let corners = [
            CLLocationCoordinate2D(latitude: box.south, longitude: box.west),
            CLLocationCoordinate2D(latitude: box.north, longitude: box.west),
            CLLocationCoordinate2D(latitude: box.north, longitude: box.east),
            CLLocationCoordinate2D(latitude: box.south, longitude: box.east),
        ]
        let polygon = ObservedCorridorOverlay(coordinates: corners, count: corners.count)
        polygon.box = box
        return polygon
    }
}
