import SwiftUI
import TipKit

/// Contextual coachmarks for the flight-list (main) screen (#312).
///
/// Same model as `BriefingTips`: each tip anchors next to the control it
/// describes via `.popoverTip`, TipKit persists the dismissal, and the two
/// toolbar tips are sequenced so they read in order (add-flight first, then the
/// forecast map) rather than both competing for the popover at once.

// MARK: - Events

/// Donated when the user acts on (or dismisses) the add-flight tip, so the
/// forecast-map tip can sequence *after* it.
enum FlightListTipEvents {
    static let addFlightTipSeen = Tips.Event(id: "flightList.addFlightTipSeen")
}

// MARK: - Toolbar tips

/// 1. Add-flight button (`plus`). Shown first — the primary action of the
/// screen.
struct AddFlightTip: Tip {
    var title: Text { Text("Add a flight") }
    var message: Text? {
        Text("Create a new flight to get a weather briefing for your route.")
    }
    var image: Image? { Image(systemName: "plus") }
}

/// 2. Forecast-map button (`map`). Sequenced after the add-flight tip via the
/// `addFlightTipSeen` event so the two read in order.
struct ForecastMapTip: Tip {
    var title: Text { Text("Airport weather map") }
    var message: Text? {
        Text("Browse current and forecast conditions for airports across Europe on the map.")
    }
    var image: Image? { Image(systemName: "map") }
    var rules: [Rule] {
        #Rule(FlightListTipEvents.addFlightTipSeen) { $0.donations.count > 0 }
    }
}
