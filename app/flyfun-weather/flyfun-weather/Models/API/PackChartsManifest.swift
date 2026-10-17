import Foundation

/// Surface charts for one briefing pack (#799), built server-side
/// (`api/_chart_manifest.py`) so the app renders a list instead of re-deriving
/// the web's coverage / horizon / Met Office / TEMSI gating rules.
///
/// Decoded with `JSONDecoder.weatherBrief` (snake_case → camelCase).
nonisolated struct PackChartsManifest: Codable, Sendable, Equatable {
    /// The route is inside at least one source's coverage. With no `sources`
    /// this means "charts expected but unavailable" (beyond the horizon or the
    /// chart refresh failed) rather than a route the charts don't cover.
    let inCoverage: Bool
    let sources: [PackChartSource]

    /// Every chart's cache path (`<cacheKey>.<fileExt>`), for the chart-cache sweep.
    var cachePaths: Set<String> {
        Set(sources.flatMap { $0.charts.map(\.cachePath) })
    }
}

nonisolated struct PackChartSource: Codable, Sendable, Equatable, Identifiable {
    /// `dwd` | `metoffice` | `meteofrance`.
    let source: String
    let label: String
    /// Plain-text credit to show under the chart (licence requirement).
    let attribution: String
    let defaultChartId: String
    /// Route-overlay JSON for this source (percent-encoded API path).
    let overlayUrl: String
    let charts: [PackChart]

    var id: String { source }
}

nonisolated struct PackChart: Codable, Sendable, Equatable, Identifiable {
    /// Unique within its source (`ana`, `036`, … or `<zone>|<run_cycle>` for TEMSI).
    let chartId: String
    let label: String
    let caption: String
    let validTime: String?
    let initTime: String?
    /// API path (query included), already percent-encoded — fetch it as is.
    let url: String
    /// Key into the source's overlay JSON (`analysis`, `icon`, `colour`, zone).
    let overlayKey: String
    /// `<source>/<run_cycle>/<stem>`: the bytes under one key never change, so
    /// a cached file is never revalidated.
    let cacheKey: String
    let fileExt: String
    /// The server still holds the bytes. False → the URL answers 410.
    let available: Bool
    /// Fetched by an offline download (charts around the ETD).
    let prefetch: Bool

    var id: String { chartId }
    var cachePath: String { "\(cacheKey).\(fileExt)" }
}

/// Route overlay for one chart type: waypoints pre-projected into the chart's
/// native pixel space (`native_size`). Drawn scaled to whatever size the image
/// is shown at — including the downscaled DWD analysis, which keeps the aspect.
///
/// Decoded with a plain `JSONDecoder` (explicit keys): the top level is a
/// dictionary keyed by chart type, which a key strategy must not touch.
nonisolated struct ChartOverlayEntry: Codable, Sendable, Equatable {
    let nativeSize: [Double]
    let waypoints: [ChartOverlayWaypoint]

    enum CodingKeys: String, CodingKey {
        case nativeSize = "native_size"
        case waypoints
    }

    var nativeWidth: Double { nativeSize.first ?? 1 }
    var nativeHeight: Double { nativeSize.count > 1 ? nativeSize[1] : 1 }
}

nonisolated struct ChartOverlayWaypoint: Codable, Sendable, Equatable {
    let icao: String
    let x: Double
    let y: Double
}

/// `{ "<chart type>": ChartOverlayEntry }` as served by `…/<source>-chart-overlay`.
typealias ChartOverlay = [String: ChartOverlayEntry]
