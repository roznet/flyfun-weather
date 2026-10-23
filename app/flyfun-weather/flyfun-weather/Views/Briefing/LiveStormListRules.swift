import Foundation

/// Words for the Observed tab's Cells list and chip (#816), read from the
/// server's `/live` storms (`LiveLayer.storms`, #688) — the same list the
/// ribbon, the nutshell, the alerts, the web and Android read. Nothing is
/// measured here: off-track distance, side, place, closing speed and the abeam
/// time are the server's; this only picks the words and the order to show.
/// The map keeps drawing the raw cell feed (`CellsOverlay`).
enum LiveStormListRules {

    /// The chip: storms still ahead within the server's corridor, or nil when
    /// the feed is not up (the list says why; a chip would read as "none").
    static func chipText(_ storms: LiveStorms?) -> String? {
        guard let storms, storms.isAvailable else { return nil }
        let n = ahead(storms).count
        let corridor = corridorText(storms)
        switch n {
        case 0: return "No cells ahead within \(corridor) of track"
        case 1: return "1 cell ahead within \(corridor) of track"
        default: return "\(n) cells ahead within \(corridor) of track"
        }
    }

    /// "30 NM" — the server's listing distance (to the storm's core, either
    /// side, behind included).
    static func corridorText(_ storms: LiveStorms) -> String {
        "\(Int((storms.corridorNm ?? 30).rounded())) NM"
    }

    /// Storms still ahead, in the server's along-track order.
    static func ahead(_ storms: LiveStorms) -> [LiveStorm] {
        storms.items.filter { $0.ahead != false }
    }

    /// Storms the flight has passed, in the server's order.
    static func behind(_ storms: LiveStorms) -> [LiveStorm] {
        storms.items.filter { $0.ahead == false }
    }

    /// Why there is no list, when the feed is not up; nil when it is.
    static func statusText(_ storms: LiveStorms) -> String? {
        switch storms.status {
        case "available": return nil
        case "stale":
            let since = RouteRibbonInspectorRules.hhmmZ(storms.unavailableSince)
            return since.isEmpty ? "Radar cells unavailable" : "Radar cells unavailable since \(since)"
        case "disabled": return "Radar cells are not enabled on this server"
        default: return "Radar cells unavailable"
        }
    }

    /// The list's caption: what it holds and the frame it was read from.
    static func caption(_ storms: LiveStorms, now: Date = Date()) -> String {
        var parts = ["Cells within \(corridorText(storms)) of track, in route order"]
        if let frame = storms.frameTime.flatMap(Date.parseISO8601) {
            parts.append("radar \(LiveTime.zulu(frame)) (\(LiveTime.ageLabel(from: frame, now: now)))")
        }
        if storms.lightningPending == true { parts.append("lightning pending") }
        return parts.joined(separator: " · ")
    }

    /// "Heavy cell" / "Cell" — the ribbon card's title.
    static func title(_ storm: LiveStorm) -> String {
        storm.intensity.map { "\($0.prefix(1).uppercased())\($0.dropFirst()) cell" } ?? "Cell"
    }

    /// "abeam in ~25 min (~10:20Z)": the server's minutes to abeam (at its
    /// tick, to the nearest 5 like its `when_text`) and its clock time. Nil
    /// past the route's ends, behind, or without a time.
    static func abeamText(_ storm: LiveStorm) -> String? {
        guard storm.end == nil, storm.ahead != false else { return nil }
        let clock = RouteRibbonInspectorRules.hhmmZ(storm.abeamEta)
        guard let minutes = storm.minutesToAbeam else {
            return clock.isEmpty ? nil : "abeam ~\(clock)"
        }
        let inText: String
        if minutes <= 2 {
            inText = "abeam now"
        } else if minutes < 10 {
            inText = "abeam in ~\(Int(minutes.rounded())) min"
        } else {
            inText = "abeam in ~\(5 * Int((minutes / 5).rounded())) min"
        }
        return clock.isEmpty ? inText : "\(inText) (~\(clock))"
    }

    /// The row's second line: motion against the track, abeam, then what the
    /// storm is (trend, lightning, top, coverage).
    static func detailLine(_ storm: LiveStorm) -> String {
        var parts = [RouteRibbonRules.stormMotionText(storm)]
        if let abeam = abeamText(storm) { parts.append(abeam) }
        if let trend = storm.trend, !trend.isEmpty { parts.append(trend) }
        if let flashes = storm.flashes, flashes > 0 {
            parts.append(flashes == 1 ? "1 flash" : "\(flashes) flashes")
        } else if storm.flashes == nil, storm.flashesPending == true {
            parts.append("lightning pending")
        }
        if let top = storm.topFl { parts.append("top FL\(top)") }
        if storm.truncated == true { parts.append("partly outside radar coverage") }
        return parts.joined(separator: " · ")
    }
}
