import SwiftUI
import MapKit

/// Native map showing route polyline and waypoint annotations.
struct RouteMapView: View {
    let viewModel: BriefingViewModel
    var trackingService: FlightTrackingService
    @State private var mapVM = RouteMapViewModel()

    var body: some View {
        Group {
            if mapVM.routeCoordinates.isEmpty {
                switch viewModel.snapshotState {
                case .loading:
                    ProgressView("Loading map data...")
                case .error(let error):
                    ContentUnavailableView("Map Unavailable", systemImage: "map", description: Text(error.localizedDescription))
                default:
                    ProgressView()
                }
            } else {
                // Read observable values in view body — Map content builder is @escaping.
                // locationUpdateCount forces re-evaluation since CLLocation is a reference type.
                let _ = trackingService.locationUpdateCount
                let isTracking = trackingService.isTracking
                let aircraftLocation = trackingService.currentLocation
                let aircraftOpacity = trackingService.projectedPosition?.opacity ?? 0.3
                let aircraftHeading = trackingService.projectedPosition?.headingDeg ?? 0
                let activeRoutePoint = viewModel.routePoint(for: viewModel.activePoint)
                Map(initialPosition: .region(mapVM.mapRegion)) {
                    MapPolyline(coordinates: mapVM.routeCoordinates)
                        .stroke(.blue, lineWidth: 3)

                    ForEach(mapVM.waypoints) { wp in
                        Annotation(wp.id, coordinate: wp.coordinate) {
                            VStack(spacing: 2) {
                                Text(wp.id)
                                    .font(.caption2.bold())
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 2)
                                    .background(.ultraThinMaterial, in: Capsule())
                                Circle()
                                    .fill(.blue)
                                    .frame(width: 8, height: 8)
                            }
                        }
                    }

                    if let activeRoutePoint {
                        Annotation("", coordinate: .init(latitude: activeRoutePoint.lat, longitude: activeRoutePoint.lon), anchor: .center) {
                            ZStack {
                                Circle()
                                    .fill(.orange.opacity(0.20))
                                Circle()
                                    .stroke(.orange, lineWidth: 3)
                            }
                                .frame(width: 22, height: 22)
                        }
                        .annotationTitles(.hidden)
                    }

                    // Live aircraft position — drawn last so it renders on top
                    if isTracking, let location = aircraftLocation {
                        Annotation("", coordinate: location.coordinate, anchor: .center) {
                            Image(systemName: "airplane")
                                .font(.system(size: 28, weight: .bold))
                                .foregroundStyle(.orange)
                                // SF Symbol "airplane" points right (90°); rotate so 0° = north
                                .rotationEffect(.degrees(aircraftHeading - 90))
                                .opacity(aircraftOpacity)
                                .shadow(color: .black.opacity(0.5), radius: 3)
                        }
                        .annotationTitles(.hidden)
                    }
                }
                .mapStyle(.standard(elevation: .realistic))
            }
        }
        .onChange(of: viewModel.snapshotState.isLoaded) {
            if case .loaded(let snapshot) = viewModel.snapshotState {
                mapVM.update(from: snapshot)
            }
        }
        .onChange(of: viewModel.routeAnalysesState.isLoaded) {
            if case .loaded(let analyses) = viewModel.routeAnalysesState {
                mapVM.update(from: analyses)
            }
        }
        .task {
            // Immediate update if data already loaded
            if case .loaded(let analyses) = viewModel.routeAnalysesState {
                mapVM.update(from: analyses)
            } else if case .loaded(let snapshot) = viewModel.snapshotState {
                mapVM.update(from: snapshot)
            }
        }
    }
}

// Helper to detect state changes
extension LoadingState {
    var isLoaded: Bool {
        if case .loaded = self { return true }
        return false
    }
}
