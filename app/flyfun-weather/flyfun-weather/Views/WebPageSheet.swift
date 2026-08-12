import SafariServices
import SwiftUI

/// In-app browser sheet backed by `SFSafariViewController`.
///
/// Used for long-form web content the app deliberately does not duplicate
/// natively — today the help guide (`/help.html`). Chosen over `WKWebView`
/// because it is free (no chrome, no navigation stack, no cookie jar to manage),
/// it shares Safari's cookie store — so a pilot already signed in on this device's
/// Safari sees the signed-in page — and it gives Reader, share and "open in
/// Safari" for nothing.
///
/// It does NOT share the app's Keychain JWT: the app authenticates with a bearer
/// token, the web with the `flyfun_auth` cookie. A pilot who has never signed in
/// via Safari on this device sees the signed-out help page. That is fine for the
/// guide (its content is public) and is exactly why in-app feedback is a native
/// sheet (`FeedbackFormView`) rather than a link into the page's modal.
struct WebPageSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let config = SFSafariViewController.Configuration()
        config.entersReaderIfAvailable = false
        let controller = SFSafariViewController(url: url, configuration: config)
        controller.dismissButtonStyle = .done
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

extension URL {
    /// Web page on the server this build talks to (dev HTTPS instance in DEBUG,
    /// production otherwise) — keeps every in-app link pointed at one origin
    /// instead of hardcoding `weather.flyfun.aero`.
    static func flyfunWeb(_ path: String) -> URL {
        AppState.defaultBaseURL.appendingPathComponent(path)
    }
}
