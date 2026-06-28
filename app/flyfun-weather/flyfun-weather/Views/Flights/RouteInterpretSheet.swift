import MapKit
import SwiftUI

/// Sheet that shows how the server interpreted a typed route: what was understood,
/// what was skipped or dropped as off-route, and the resolved route on a map
/// (#5 — parity with the web's "Interpret" popup).
struct RouteInterpretSheet: View {
    let interpretation: InterpretRouteResponse?
    let rawRoute: String
    let isResolving: Bool
    /// When non-nil, shows a confirmation button (e.g. "Accept & Create").
    let acceptTitle: String?
    let onAccept: () -> Void
    let onResolve: () async -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if let interpretation {
                    RouteInterpretContent(interpretation: interpretation, rawRoute: rawRoute)
                } else if isResolving {
                    ProgressView("Interpreting route\u{2026}")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ContentUnavailableView(
                        "Could not interpret route",
                        systemImage: "map",
                        description: Text("Check the waypoints and try again.")
                    )
                }
            }
            .navigationTitle("Route")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(acceptTitle == nil ? "Done" : "Cancel") { dismiss() }
                }
                if let acceptTitle {
                    ToolbarItem(placement: .confirmationAction) {
                        Button(acceptTitle) { onAccept() }
                    }
                }
            }
            .task {
                if interpretation == nil { await onResolve() }
            }
        }
    }
}

/// The understood/skipped/off-route summary plus the route map.
private struct RouteInterpretContent: View {
    let interpretation: InterpretRouteResponse
    let rawRoute: String

    private var coordinates: [CLLocationCoordinate2D] {
        interpretation.waypoints.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                routeMap
                    .frame(height: 240)
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                if !interpretation.interpreted.isEmpty {
                    summaryBlock(
                        title: "Understood",
                        systemImage: "checkmark.circle.fill",
                        tint: .green,
                        text: interpretation.interpreted.joined(separator: " \u{2192} ")
                    )
                }

                if !interpretation.skipped.isEmpty {
                    chipBlock(
                        title: "Skipped (not recognized)",
                        systemImage: "questionmark.circle.fill",
                        tint: .orange,
                        tokens: interpretation.skipped
                    )
                }

                if !interpretation.offRoute.isEmpty {
                    chipBlock(
                        title: "Off route (too far)",
                        systemImage: "arrow.uturn.left.circle.fill",
                        tint: .secondary,
                        tokens: interpretation.offRoute
                    )
                }
            }
            .padding()
        }
    }

    @ViewBuilder
    private var routeMap: some View {
        if coordinates.count >= 2 {
            Map(initialPosition: .region(Self.boundingRegion(coordinates))) {
                MapPolyline(coordinates: coordinates)
                    .stroke(.blue, lineWidth: 3)
                ForEach(interpretation.waypoints) { wp in
                    Marker(wp.icao, coordinate: CLLocationCoordinate2D(latitude: wp.lat, longitude: wp.lon))
                }
            }
        } else {
            ContentUnavailableView("No mappable waypoints", systemImage: "mappin.slash")
        }
    }

    private func summaryBlock(title: String, systemImage: String, tint: Color, text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: systemImage)
                .font(.subheadline.bold())
                .foregroundStyle(tint)
            Text(text)
                .font(.callout)
                .foregroundStyle(.primary)
        }
    }

    private func chipBlock(title: String, systemImage: String, tint: Color, tokens: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: systemImage)
                .font(.subheadline.bold())
                .foregroundStyle(tint)
            FlowChips(tokens: tokens, tint: tint)
        }
    }

    /// Region that fits all coordinates with a little padding.
    static func boundingRegion(_ coords: [CLLocationCoordinate2D]) -> MKCoordinateRegion {
        let lats = coords.map(\.latitude)
        let lons = coords.map(\.longitude)
        let minLat = lats.min() ?? 0, maxLat = lats.max() ?? 0
        let minLon = lons.min() ?? 0, maxLon = lons.max() ?? 0
        let center = CLLocationCoordinate2D(latitude: (minLat + maxLat) / 2, longitude: (minLon + maxLon) / 2)
        let span = MKCoordinateSpan(
            latitudeDelta: max((maxLat - minLat) * 1.4, 0.5),
            longitudeDelta: max((maxLon - minLon) * 1.4, 0.5)
        )
        return MKCoordinateRegion(center: center, span: span)
    }
}

/// Simple wrapping row of token chips.
private struct FlowChips: View {
    let tokens: [String]
    let tint: Color

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(tokens, id: \.self) { token in
                    Text(token)
                        .font(.caption.monospaced())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(tint.opacity(0.15), in: Capsule())
                }
            }
        }
    }
}
