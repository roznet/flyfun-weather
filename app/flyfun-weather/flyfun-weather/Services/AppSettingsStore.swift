import Foundation

/// How the app auto-downloads briefing packs for offline use when a briefing is
/// displayed. The pack data is the same bundle the explicit download button
/// fetches, so an auto-downloaded pack renders fully (incl. Skew-Ts) offline.
enum AutoDownloadMode: String, CaseIterable, Identifiable, Sendable {
    /// Never auto-download; only explicit per-pack downloads are cached.
    case off
    /// Auto-download only on Wi-Fi / wired (the default).
    case wifiOnly
    /// Auto-download on any connection, including cellular.
    case wifiAndCellular

    var id: String { rawValue }

    var label: String {
        switch self {
        case .off: "Off"
        case .wifiOnly: "Wi-Fi Only"
        case .wifiAndCellular: "Wi-Fi & Cellular"
        }
    }

    /// Whether auto-download should proceed under the given connectivity.
    func allows(isOnWiFi: Bool, isConnected: Bool) -> Bool {
        switch self {
        case .off: false
        case .wifiOnly: isOnWiFi
        case .wifiAndCellular: isConnected
        }
    }
}

/// Local, device-side app settings — distinct from the server-backed
/// `UserPreferencesStore`. Persisted in `UserDefaults` so the chosen value is
/// available immediately at launch and while offline.
@Observable
@MainActor
final class AppSettingsStore {
    private static let autoDownloadKey = "autoDownloadMode"

    /// Whether/where to auto-download briefings for offline use. Defaults to
    /// Wi-Fi only so the feature never silently spends a pilot's cellular data.
    var autoDownloadMode: AutoDownloadMode {
        didSet {
            guard oldValue != autoDownloadMode else { return }
            UserDefaults.standard.set(autoDownloadMode.rawValue, forKey: Self.autoDownloadKey)
        }
    }

    init() {
        if let raw = UserDefaults.standard.string(forKey: Self.autoDownloadKey),
           let mode = AutoDownloadMode(rawValue: raw) {
            self.autoDownloadMode = mode
        } else {
            self.autoDownloadMode = .wifiOnly
        }
    }
}
