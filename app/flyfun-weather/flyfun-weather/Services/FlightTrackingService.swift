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
    /// When the last fix was delivered. Drives the watchdog's staleness rule —
    /// the auto-stop conditions inside `projectLocation` can only fire when a fix
    /// arrives, which is exactly what stops happening once the aircraft parks.
    private var lastFixTime: Date = .distantPast
    private var watchdogTask: Task<Void, Never>?

    // MARK: - Energy budget

    /// Horizontal accuracy requested from Core Location.
    ///
    /// **Not `kCLLocationAccuracyBest`.** `Best` drives the GPS chip in
    /// continuous maximum-rate mode and is the single largest energy cost of a
    /// tracking session. Route projection resolves position to ~0.1nm (185m) and
    /// the cross-track threshold is 10nm, so ten metres is an order of magnitude
    /// more precision than any consumer of this data needs.
    ///
    /// Note that `distanceFilter` (below) is *not* an energy control: it gates
    /// **delegate delivery**, not the hardware duty cycle. `desiredAccuracy` is
    /// the only knob here that changes what the radio actually does.
    private static let trackingAccuracy = kCLLocationAccuracyNearestTenMeters

    /// Delegate-delivery threshold, in metres. Saves the projection work below
    /// (already throttled to `projectionThrottleInterval`) and SwiftUI ticks —
    /// but no radio energy. See `trackingAccuracy`.
    private static let trackingDistanceFilter: CLLocationDistance = 200 // ~0.1nm

    /// How often the watchdog re-evaluates the auto-stop conditions.
    private static let watchdogInterval: TimeInterval = 60

    /// Stop tracking after this long with no fix delivered.
    ///
    /// With a 200m delivery filter, a moving aircraft produces a fix every few
    /// seconds; fifteen minutes of silence means it has moved less than 200m in
    /// that time — parked, or the GPS has no usable signal. Either way the live
    /// position on screen has been frozen for a quarter of an hour, so stopping
    /// is both honest and the only thing that releases the GPS: the in-fix
    /// auto-stops cannot run when no fix arrives.
    private static let staleFixTimeout: TimeInterval = 15 * 60

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
        manager.desiredAccuracy = Self.trackingAccuracy
        manager.distanceFilter = Self.trackingDistanceFilter
        // Tells Core Location to expect fast, high-altitude motion so it filters
        // and duty-cycles for flight rather than the default `.other` profile.
        manager.activityType = .airborne
        self.locationManager = manager

        manager.requestWhenInUseAuthorization()

        // Start if already authorized
        let status = manager.authorizationStatus
        if status == .authorizedWhenInUse || status == .authorizedAlways {
            beginTracking(with: manager)
        }
    }

    func stop() {
        watchdogTask?.cancel()
        watchdogTask = nil
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
        lastFixTime = .distantPast
        logger.info("Flight tracking stopped")
    }

    /// Turn the GPS on and arm the watchdog. Shared by `start()` and the
    /// authorization callback, which both reach "tracking is now live" — keeping
    /// it in one place means the watchdog can never be armed on one path only.
    private func beginTracking(with manager: CLLocationManager) {
        manager.startUpdatingLocation()
        isTracking = true
        lastFixTime = Date()
        watchdogTask?.cancel()
        watchdogTask = Task { [weak self] in await self?.runWatchdog() }
        logger.info("Flight tracking started")
    }

    /// Wall-clock backstop for the auto-stop rules.
    ///
    /// Both conditions in `projectLocation` are evaluated only when a fix is
    /// delivered, so neither can fire once the aircraft stops moving: with a 200m
    /// delivery filter a parked aircraft produces no fixes at all, and the GPS
    /// would stay on until the pilot manually tapped Stop (or the process died).
    /// The same gap swallows a diversion, where the near-destination test never
    /// becomes true. This loop closes both by running on the clock instead.
    private func runWatchdog() async {
        var lastTick = Date()
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(Self.watchdogInterval))
            if Task.isCancelled { return }
            guard isTracking else { return }

            let now = Date()
            let sinceLastTick = now.timeIntervalSince(lastTick)
            lastTick = now

            if let endTime = flightEndTime, now > endTime {
                logger.info("Watchdog auto-stop: past flight window")
                stop()
                return
            }

            // A tick that arrives far later than scheduled means the process was
            // suspended in between (the app was backgrounded — location delivery
            // is suspended there too, since we hold when-in-use authorization and
            // declare no `location` background mode). The silence says nothing
            // about the aircraft, so roll the fix clock forward rather than
            // reading a backgrounded stretch as a landing.
            if sinceLastTick > Self.watchdogInterval * 2 {
                lastFixTime = now
                continue
            }

            let silence = now.timeIntervalSince(lastFixTime)
            if silence > Self.staleFixTimeout {
                logger.info("Watchdog auto-stop: no fix in \(Int(silence))s — parked or no GPS signal")
                stop()
                return
            }
        }
    }

    /// Request a single current-position fix WITHOUT starting full route
    /// tracking. Populates `currentLocation` (and its altitude) so the PIREP
    /// reporting form can pre-fill lat/lon/altitude even when the pilot hasn't
    /// tapped "Start" — the entry point is no longer gated on an active track.
    /// No-op while already tracking, since the live track already updates
    /// `currentLocation`.
    func requestOneShotLocation() {
        guard !isTracking else { return }
        // Drop any prior fix so a *stale* position can't be pre-filled before the
        // fresh one lands. The reporting form reads `currentLocation` synchronously
        // on appear (to seed altitude), and this service instance is shared across
        // repeated sheet presentations — without this reset, the second PIREP of a
        // flight would silently inherit the first one's altitude and the fresh fix
        // (delivered async) would be discarded by the "only fill when nil" guard.
        currentLocation = nil
        let manager = locationManager ?? CLLocationManager()
        manager.delegate = self
        // Same budget as a live track: a position report doesn't need the GPS
        // driven at `Best` for the seconds it takes to land one fix.
        manager.desiredAccuracy = Self.trackingAccuracy
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
        lastFixTime = now

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

        // `.debug`, not `.info`: this fires every `projectionThrottleInterval`
        // (~720×/hour in flight) and `.info` is persisted to the unified log.
        // The one-off "First location" above stays at `.info` — it's the line
        // that says tracking actually acquired.
        let altStr = altitudeFt.map { "\(Int($0))ft" } ?? "no-alt"
        logger.debug("Projected: \(bestAlongDistance, format: .fixed(precision: 1))nm along, \(altStr), \(bestPerpDistance, format: .fixed(precision: 1))nm off-track")
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
                    beginTracking(with: manager)
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
