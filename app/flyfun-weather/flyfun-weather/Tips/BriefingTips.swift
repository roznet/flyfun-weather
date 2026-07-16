import SwiftUI
import TipKit

/// Contextual coachmarks for the briefing detail views (#312).
///
/// TipKit (iOS 17+) surfaces each tip at its point of use, then persists the
/// dismissal so a tip never nags twice — no storage code on our side. This is
/// the native, non-blocking counterpart to the web app's driver.js tour: tips
/// appear next to the control they describe, the user dismisses them, and the
/// framework remembers. There is no forced linear walkthrough.
///
/// Scope (per #312): buttons/controls *inside* the detail views, not the tab
/// bar (`.popoverTip` cannot cleanly anchor to a SwiftUI `Tab`). Add more tips
/// incrementally — keep each one short and action-oriented.

// MARK: - Events

/// Donated when the user acts on the refresh tip, so the download tip can
/// sequence *after* it. The two controls sit side by side and the whole point
/// is the contrast — fetch-new vs. offline-save — so they should read as a pair.
enum BriefingTipEvents {
    static let refreshTipSeen = Tips.Event(id: "briefing.refreshTipSeen")
}

// MARK: - Toolbar tips

/// 1. Refresh button (`arrow.clockwise`). Shown first: the refresh button is
/// always in the toolbar, so it's a reliable anchor to open the pair on.
struct RefreshBriefingTip: Tip {
    var title: Text { Text("Get the latest forecast") }
    var message: Text? {
        Text("Re-fetches the newest model run from the server. The offline copy keeps what you have now; refresh pulls new data.")
    }
    var image: Image? { Image(systemName: "arrow.clockwise") }
}

/// 2. Download indicator (`arrow.down.circle` / `.fill`). Sequenced after the
/// refresh tip via the `refreshTipSeen` event so the fetch-new / offline-save
/// pair reads in order. If the user ignores the refresh tip, this one simply
/// waits — it is still non-forced.
///
/// Downloads now happen automatically, so this reads as a *status signal*, not
/// a "tap to download" action: the icon fills in green once the briefing is
/// saved and available to view offline.
struct DownloadBriefingTip: Tip {
    var title: Text { Text("Offline availability") }
    var message: Text? {
        Text("This icon shows whether the briefing is saved for offline viewing. Briefings download automatically — it fills in once this one is ready to view without a connection.")
    }
    var image: Image? { Image(systemName: "arrow.down.circle") }
    var rules: [Rule] {
        #Rule(BriefingTipEvents.refreshTipSeen) { $0.donations.count > 0 }
    }
}

// MARK: - Cross-section tips

/// 3. Cross-section "Layers" pill. Gated on the Cross-Section tab being visible
/// so it never fires while the Advisory (or any other) tab is on screen.
struct CrossSectionLayersTip: Tip {
    /// Set from `CrossSectionView`'s appear/disappear so the tip is eligible
    /// only while that tab is the visible one. TipKit requires each `@Parameter`
    /// to live on its own Tip type, so this is written in tandem with
    /// `CrossSectionScrubTip.crossSectionVisible` — update both together.
    @Parameter static var crossSectionVisible: Bool = false

    var title: Text { Text("Choose what to show") }
    var message: Text? {
        Text("Toggle cloud, icing, wind and other layers on the cross-section.")
    }
    var image: Image? { Image(systemName: "slider.horizontal.3") }
    var rules: [Rule] {
        #Rule(Self.$crossSectionVisible) { $0 }
    }
}

/// 4. Cross-section canvas — "tap any point". An inline `TipView` banner above
/// the canvas the first time the tab is opened; invalidated once the user
/// scrubs once. Shares the visibility gate with the Layers tip.
struct CrossSectionScrubTip: Tip {
    /// Written in tandem with `CrossSectionLayersTip.crossSectionVisible` from
    /// `CrossSectionView`'s appear/disappear — update both together.
    @Parameter static var crossSectionVisible: Bool = false

    var title: Text { Text("Tap or drag anywhere") }
    var message: Text? {
        Text("Read exact values at any point along the route and altitude. Double-tap to hide the controls.")
    }
    var image: Image? { Image(systemName: "hand.point.up.left") }
    var rules: [Rule] {
        #Rule(Self.$crossSectionVisible) { $0 }
    }
}
