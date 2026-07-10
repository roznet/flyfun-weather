import Foundation
import OSLog
import UIKit
import UserNotifications

/// Pure, testable helpers for APNs handling — kept off the app delegate so the
/// token encoding and payload parsing can be unit-tested without a device.
enum PushSupport {
    /// APNs environment this *build* runs in — decided by the app build, not the
    /// server (a debug build's token is APNs-sandbox; TestFlight/App Store is
    /// production). The client reports it at register time; the server routes on
    /// it. See ios-app-briefing-notifications.md → "Sandbox vs production".
    static var environment: String {
        #if DEBUG
        "sandbox"
        #else
        "production"
        #endif
    }

    /// Lowercase-hex encoding of a raw APNs device token, as APNs expects it in
    /// the `/3/device/<token>` path.
    static func hexEncode(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }

    /// The deep-link target carried by a briefing push, if any. The server sets
    /// a top-level `flight_id`; a silent badge-sync push carries none.
    static func pendingNavigation(from userInfo: [AnyHashable: Any]) -> PendingNavigation? {
        guard let flightId = userInfo["flight_id"] as? String, !flightId.isEmpty else {
            return nil
        }
        return .briefing(flightId: flightId)
    }
}

/// App delegate bridging APNs into the SwiftUI app.
///
/// Registration, token upload, and badge run **in-process** in the main app via
/// the existing `AppState` / `APIClient` — no App Group or extra auth plumbing.
/// Deep-linking on tap reuses the same `PendingNavigation` seam `onOpenURL` and
/// App Intents rely on, so a tapped push and "open my next FlyFun briefing" land
/// on one code path.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Push")

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // MARK: - Remote-notification registration

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let hex = PushSupport.hexEncode(deviceToken)
        Self.logger.info("APNs token registered (\(PushSupport.environment, privacy: .public))")
        Task { @MainActor in await AppState.current?.uploadDeviceToken(hex) }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        Self.logger.warning("APNs registration failed: \(error.localizedDescription, privacy: .public)")
    }

    // MARK: - Silent badge-sync push (content-available)

    /// A silent `content-available` push fires when the unseen count changed for
    /// a reason other than a new alert (e.g. the flight was read on the web).
    /// APNs coalesces these, so we don't trust the pushed number blindly — we
    /// re-derive it authoritatively from the server (`reconcileBadge`).
    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        Task { @MainActor in
            await AppState.current?.reconcileBadge()
            // A refresh push means the server's data changed — nudge the visible
            // list / open briefing to re-sync to the newest online pack (a silent
            // badge-sync carries no flight_id, which is fine: the sync no-ops when
            // nothing changed).
            AppState.current?.signalExternalSync(flightId: PushSupport.pendingNavigation(from: userInfo)?.flightId)
            completionHandler(.newData)
        }
    }

    // MARK: - UNUserNotificationCenterDelegate

    /// Foreground presentation. In-app suppression (design): a user watching the
    /// app shouldn't also get a banner — just keep the badge accurate. So while
    /// foregrounded we present nothing visible and reconcile the badge instead.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        let userInfo = notification.request.content.userInfo
        Task { @MainActor in
            await AppState.current?.reconcileBadge()
            // Foregrounded when a refresh landed → re-sync the visible data so the
            // suppressed banner still results in fresh content on screen.
            AppState.current?.signalExternalSync(flightId: PushSupport.pendingNavigation(from: userInfo)?.flightId)
        }
        return []
    }

    /// Notification tapped → deep-link to the updated briefing via the shared
    /// `PendingNavigation` seam (cold-launch-safe: written to the store, consumed
    /// on the next `.active`).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        if let nav = PushSupport.pendingNavigation(from: userInfo) {
            PendingNavigationStore.set(nav)
            await MainActor.run { AppState.current?.consumePendingNavigation() }
        }
    }
}
