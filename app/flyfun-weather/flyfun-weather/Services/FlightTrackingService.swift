import CoreLocation
import OSLog
import RZFlight

private let logger = Logger(subsystem: "aero.flyfun.weather", category: "FlightTracking")

/// Projected aircraft position onto the flight route.
struct ProjectedPosition {
    /// Distance along route from origin (nautical miles)
    let distanceNm: Double
    /// GPS altitude in feet MSL, nil when vertical accuracy is invalid
    let altitudeFt: Double?
    /// Perpendicular distance from route centerline (nautical miles)
    let crossTrackNm: Double
    /// True if within 10nm of route
    var isOnRoute: Bool { crossTrackNm < 10.0 }
    /// Aircraft heading in degrees true
    let headingDeg: Double
    /// Display opacity: 1.0 on-route, 0.3 off-route
    var opacity: Double { isOnRoute ? 1.0 : 0.3 }
}

/// Route point used for projection — lightweight coordinate + distance pair.
struct TrackingRoutePoint {
    let coordinate: CLLocationCoordinate2D
    let distanceFromOriginNm: Double
}

/// Wraps CLLocationManager to provide live aircraft position projected onto a flight route.
@Observable
@MainActor
final class FlightTrackingService: NSObject {
    private(set) var currentLocation: CLLocation?
    private(set) var isTracking = false
    private(set) var projectedPosition: ProjectedPosition?
    /// Incremented on every location update — forces SwiftUI view re-evaluation
    /// since CLLocation is a reference type that @Observable may not diff.
    private(set) var locationUpdateCount = 0

    private var locationManager: CLLocationManager?
    private var routePoints: [TrackingRoutePoint] = []
    private var flightEndTime: Date?
    private var destinationCoordinate: CLLocationCoordinate2D?
    private var lastProjectionTime: Date = .distantPast
    private let projectionThrottleInterval: TimeInterval = 5.0
    /// A one-shot `requestLocation()` is in flight (PIREP pre-fill outside an
    /// active route track). Cleared once a fix (or failure) lands.
    private var oneShotActive = false

    // MARK: - Start / Stop

    /// Start tracking with route analysis points for projection.
    ///
    /// - Parameters:
    ///   - routePoints: Fine-grained route points with coordinates and cumulative distance
    ///   - flightEndTime: Auto-stop after this time (departure + duration + 2h)
    func start(routePoints: [TrackingRoutePoint], flightEndTime: Date) {
        guard !isTracking else { return }
        self.routePoints = routePoints
        self.flightEndTime = flightEndTime
        self.destinationCoordinate = routePoints.last?.coordinate

        let manager = CLLocationManager()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 200 // ~0.1nm, throttles at GPS level
        self.locationManager = manager

        manager.requestWhenInUseAuthorization()

        // Start if already authorized
        let status = manager.authorizationStatus
        if status == .authorizedWhenInUse || status == .authorizedAlways {
            manager.startUpdatingLocation()
            isTracking = true
            logger.info("Flight tracking started")
        }
    }

    func stop() {
        locationManager?.stopUpdatingLocation()
        locationManager?.delegate = nil
        locationManager = nil
        isTracking = false
        oneShotActive = false
        currentLocation = nil
        projectedPosition = nil
        routePoints = []
        flightEndTime = nil
        destinationCoordinate = nil
        logger.info("Flight tracking stopped")
    }

    /// Request a single current-position fix WITHOUT starting full route
    /// tracking. Populates `currentLocation` (and its altitude) so the PIREP
    /// reporting form can pre-fill lat/lon/altitude even when the pilot hasn't
    /// tapped "Start" — the entry point is no longer gated on an active track.
    /// No-op while already tracking, since the live track already updates
    /// `currentLocation`.
    func requestOneShotLocation() {
        guard !isTracking else { return }
        let manager = locationManager ?? CLLocationManager()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        self.locationManager = manager
        oneShotActive = true

        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            manager.requestLocation()
        case .notDetermined:
            // The fix fires from `locationManagerDidChangeAuthorization` once
            // the user answers the permission prompt.
            manager.requestWhenInUseAuthorization()
        default:
            // Denied/restricted — nothing to pre-fill; the form falls back to
            // manual entry.
            oneShotActive = false
            logger.warning("One-shot location unavailable: authorization denied/restricted")
        }
    }

    // MARK: - Projection

    private func projectLocation(_ location: CLLocation) {
        let now = Date()

        // Auto-stop: past flight window
        if let endTime = flightEndTime, now > endTime {
            logger.info("Auto-stopping: past flight window")
            stop()
            return
        }

        // Auto-stop: landing detection (speed < ~30kt AND near destination)
        if let dest = destinationCoordinate {
            let speedKt = location.speed * 1.94384 // m/s to knots
            let distToDest = RouteGeometry.directDistanceNm(
                from: location.coordinate,
                to: dest
            )
            if speedKt >= 0 && speedKt < 30 && distToDest < 5.0 {
                logger.info("Auto-stopping: landing detected (speed=\(speedKt)kt, dist=\(distToDest)nm)")
                stop()
                return
            }
        }

        let isFirstLocation = currentLocation == nil
        currentLocation = location
        locationUpdateCount += 1

        if isFirstLocation {
            logger.info("First location: \(location.coordinate.latitude, format: .fixed(precision: 4)), \(location.coordinate.longitude, format: .fixed(precision: 4)) alt=\(Int(location.altitude))m speed=\(Int(location.speed))m/s")
        }

        // Throttle projection computation
        guard now.timeIntervalSince(lastProjectionTime) >= projectionThrottleInterval else { return }
        lastProjectionTime = now

        guard routePoints.count >= 2 else { return }

        // Project onto route using RouteGeometry
        var bestPerpDistance = Double.infinity
        var bestAlongDistance = 0.0
        var bestSegmentIndex = 0
        let coord = location.coordinate

        for i in 0..<(routePoints.count - 1) {
            let segStart = routePoints[i]
            let segEnd = routePoints[i + 1]

            let (perpDist, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
                from: coord,
                toSegmentStart: segStart.coordinate,
                segmentEnd: segEnd.coordinate
            )

            if perpDist < bestPerpDistance {
                bestPerpDistance = perpDist
                bestSegmentIndex = i
                bestAlongDistance = segStart.distanceFromOriginNm +
                    ratio * (segEnd.distanceFromOriginNm - segStart.distanceFromOriginNm)
            }
        }

        let altitudeFt: Double? = location.verticalAccuracy >= 0
            ? location.altitude * 3.28084 : nil

        // Use GPS course if available, otherwise estimate from route segment bearing
        let heading: Double
        if location.course >= 0 {
            heading = location.course
        } else {
            let segStart = routePoints[bestSegmentIndex].coordinate
            let segEnd = routePoints[bestSegmentIndex + 1].coordinate
            heading = Self.bearing(from: segStart, to: segEnd)
        }

        projectedPosition = ProjectedPosition(
            distanceNm: bestAlongDistance,
            altitudeFt: altitudeFt,
            crossTrackNm: bestPerpDistance,
            headingDeg: heading
        )

        let altStr = altitudeFt.map { "\(Int($0))ft" } ?? "no-alt"
        logger.info("Projected: \(bestAlongDistance, format: .fixed(precision: 1))nm along, \(altStr), \(bestPerpDistance, format: .fixed(precision: 1))nm off-track")
    }

    /// Great-circle initial bearing from one coordinate to another, in degrees (0-360).
    private static func bearing(from start: CLLocationCoordinate2D, to end: CLLocationCoordinate2D) -> Double {
        let lat1 = start.latitude * .pi / 180
        let lat2 = end.latitude * .pi / 180
        let dLon = (end.longitude - start.longitude) * .pi / 180
        let y = sin(dLon) * cos(lat2)
        let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        let bearing = atan2(y, x) * 180 / .pi
        return (bearing + 360).truncatingRemainder(dividingBy: 360)
    }
}

// MARK: - CLLocationManagerDelegate

extension FlightTrackingService: @preconcurrency CLLocationManagerDelegate {

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            let status = manager.authorizationStatus
            if status == .authorizedWhenInUse || status == .authorizedAlways {
                if !routePoints.isEmpty && !isTracking {
                    manager.startUpdatingLocation()
                    isTracking = true
                    logger.info("Authorization granted, tracking started")
                } else if oneShotActive {
                    manager.requestLocation()
                }
            } else if status == .denied || status == .restricted {
                logger.warning("Location authorization denied")
                stop()
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        Task { @MainActor in
            projectLocation(location)
            // A one-shot fix has landed; the manager auto-stops after delivery.
            oneShotActive = false
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        logger.error("Location error: \(error.localizedDescription)")
        Task { @MainActor in oneShotActive = false }
    }
}
