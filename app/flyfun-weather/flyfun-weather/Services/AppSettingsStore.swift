import Foundation

/// How the app auto-downloads briefing packs for offline use when a briefing is
/// displayed. The pack data is the same bundle the explicit download button
/// fetches, so an auto-downloaded pack renders fully (incl. Skew-Ts) offline.
enum AutoDownloadMode: String, CaseIterable, Identifiable, Sendable {
    /// Never auto-download; only explicit per-pack downloads are cached.
    case off
    /// Auto-download only on Wi-Fi / wired.
    case wifiOnly
    /// Auto-download on any connection, including cellular (the default), except
    /// in Low Data Mode. A pack bundle is ~0.1–0.7 MB gzipped, and without it
    /// every Skew-T point is a separate request: scrubbing the cross-section on
    /// cellular fired ~150 of them in a minute on 2026-11-26.
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
    /// `isConstrained` is iOS Low Data Mode, which every mode respects.
    func allows(isOnWiFi: Bool, isConnected: Bool, isConstrained: Bool) -> Bool {
        switch self {
        case .off: false
        case .wifiOnly: isOnWiFi && !isConstrained
        case .wifiAndCellular: isConnected && !isConstrained
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

    /// Whether/where to auto-download briefings for offline use. Defaults to any
    /// connection (bundles are small; see `AutoDownloadMode.wifiAndCellular`).
    /// The value is only persisted once the pilot changes it, so installs that
    /// never touched the old Wi-Fi-only default pick up the new one, while an
    /// explicit Wi-Fi-only choice is kept.
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
            self.autoDownloadMode = .wifiAndCellular
        }
    }
}
