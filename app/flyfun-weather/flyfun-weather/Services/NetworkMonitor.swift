import Foundation
import Network
import OSLog

/// Observes network reachability so callers can gate auto-download on Wi-Fi.
///
/// `NWPathMonitor` delivers updates on a background queue; we hop to the main
/// actor to publish observable state so SwiftUI and the view models can read it
/// without data races.
@Observable
@MainActor
final class NetworkMonitor {
    /// Whether any network path is currently usable.
    private(set) var isConnected = false
    /// Whether the active path is "expensive" (cellular / personal hotspot).
    private(set) var isExpensive = false
    /// Whether the active path is constrained (e.g. Low Data Mode).
    private(set) var isConstrained = false

    /// On Wi-Fi (or wired): connected, not cellular, not Low-Data. This is the
    /// gate used for the default "Wi-Fi only" auto-download mode.
    var isOnWiFi: Bool { isConnected && !isExpensive && !isConstrained }

    @ObservationIgnored private let monitor = NWPathMonitor()
    @ObservationIgnored private let queue = DispatchQueue(label: "aero.flyfun.weather.network-monitor")
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Network")

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            // Snapshot the Sendable scalars off the path before hopping actors.
            let connected = path.status == .satisfied
            let expensive = path.isExpensive
            let constrained = path.isConstrained
            Task { @MainActor in
                self?.apply(connected: connected, expensive: expensive, constrained: constrained)
            }
        }
        monitor.start(queue: queue)
    }

    deinit { monitor.cancel() }

    private func apply(connected: Bool, expensive: Bool, constrained: Bool) {
        isConnected = connected
        isExpensive = expensive
        isConstrained = constrained
        Self.logger.debug(
            "Path update: connected=\(connected) expensive=\(expensive) constrained=\(constrained)"
        )
    }
}
