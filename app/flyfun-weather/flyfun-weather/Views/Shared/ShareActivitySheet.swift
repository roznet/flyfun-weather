import SwiftUI
import UIKit

/// "Open in Safari" entry for the share sheet. iOS never offers Safari as a
/// share target on its own (only apps that ship a share extension, like Chrome,
/// appear), so sharing our briefing URL had "Open in Chrome" but no Safari row.
/// This custom activity fills the gap. Opening our own Universal Link via
/// `UIApplication.open` is safe: the system deliberately routes an app's own
/// universal link to the browser instead of bouncing it back into the app.
final class OpenInSafariActivity: UIActivity {
    private var url: URL?

    override class var activityCategory: UIActivity.Category { .action }
    override var activityType: UIActivity.ActivityType {
        UIActivity.ActivityType("net.ro-z.flyfun-weather.openInSafari")
    }
    override var activityTitle: String? { String(localized: "Open in Safari") }
    override var activityImage: UIImage? { UIImage(systemName: "safari") }

    override func canPerform(withActivityItems activityItems: [Any]) -> Bool {
        activityItems.contains { $0 is URL }
    }

    override func prepare(withActivityItems activityItems: [Any]) {
        url = activityItems.first { $0 is URL } as? URL
    }

    override func perform() {
        guard let url else {
            activityDidFinish(false)
            return
        }
        UIApplication.shared.open(url) { [weak self] success in
            self?.activityDidFinish(success)
        }
    }
}

/// `UIActivityViewController` wrapper so a share sheet can carry custom
/// activities — SwiftUI's `ShareLink` cannot. Present inside a `.sheet`; the
/// completion handler dismisses the hosting sheet when the user finishes or
/// cancels, mirroring `ShareLink`'s behaviour.
struct ShareActivitySheet: UIViewControllerRepresentable {
    let items: [Any]
    var activities: [UIActivity] = []
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(
            activityItems: items,
            applicationActivities: activities.isEmpty ? nil : activities
        )
        controller.completionWithItemsHandler = { _, _, _, _ in
            dismiss()
        }
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
