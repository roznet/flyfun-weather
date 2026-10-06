import Foundation

// =============================================================================
// SYNC — mirrors src/weatherbrief/models/live.py (#637)
//
// The per-flight **live observation layer**. The briefing pack is immutable;
// on flight day the newest METAR/TAF, route SIGMETs and observed radar /
// lightning / tops live in a separate per-flight store the server refreshes
// every ~10 min (departure −3 h … arrival +1 h). `GET /api/flights/{id}/live`
// serves it; the snapshot of the latest pack is also overlaid with it
// server-side, but a *downloaded* snapshot is frozen at download time, which is
// why the client fetches and caches this layer separately.
//
// Two mechanisms share the payload and must not be confused:
//  - Display: `routeObservations` / `routeSigmets` / `observedConditions` —
//    always the newest data, no thresholds.
//  - Significance: `changes` — what moved since the *briefing*, both
//    directions, server-side hysteresis applied. It never re-grades anything.
//
// Decoded tolerantly: every field is optional and the enums are kept as raw
// strings with computed helpers, so a new kind/source the server adds later
// renders generically instead of failing the whole decode.
// =============================================================================

/// `GET /api/flights/{id}/live`. Always 200 once the flight has a pack; when no
/// live data exists yet every block and `liveUpdatedAt` are nil (a "null
/// layer") and the client keeps showing the pack's own observations.
nonisolated struct LiveLayerResponse: Codable, Sendable {
    let flightId: String?
    /// The fetch timestamp of the pack this layer is relative to (the flight's
    /// latest pack). Compare as a `Date` — the format may differ from the pack
    /// meta's `fetchTimestamp` ("+00:00" vs "Z", fractional seconds).
    let packTimestamp: String?
    /// nil ⇒ no live data yet.
    let liveUpdatedAt: String?
    let routeObservations: RouteObservations?
    let observationsUpdatedAt: String?
    let routeSigmets: RouteSigmets?
    let sigmetsUpdatedAt: String?
    let observedConditions: ObservedConditions?
    let observedUpdatedAt: String?
    let changes: LiveChanges?
    let lastRefreshDelta: RefreshDelta?
    /// #688: radar storms against the route; #690: the Observed tab's nutshell,
    /// route ribbon (with tap-to-map focus). nil from an older server or when
    /// the tick could not build them (the tab then shows the details only).
    var storms: LiveStorms? = nil
    var glance: LiveGlance? = nil
    var ribbon: LiveRibbon? = nil

    /// Whether this layer carries any live data at all.
    var hasData: Bool { liveUpdatedAt != nil }

    /// Newest-wins ordering for the on-disk live cache: should `self` replace
    /// `other`? A layer for a newer pack always wins (a full refresh resets the
    /// layer); a layer for an older pack never does. For the same pack the newer
    /// `liveUpdatedAt` wins, with nil counting as oldest. Pure, for testing.
    func supersedes(_ other: LiveLayerResponse?) -> Bool {
        guard let other else { return true }
        let mine = packTimestamp.flatMap(Date.parseISO8601)
        let theirs = other.packTimestamp.flatMap(Date.parseISO8601)
        if let mine, let theirs, !LiveTime.sameInstant(mine, theirs) {
            return mine > theirs
        }
        switch (liveUpdatedAt.flatMap(Date.parseISO8601), other.liveUpdatedAt.flatMap(Date.parseISO8601)) {
        case (nil, nil): return true
        case (nil, _): return false
        case (_, nil): return true
        case let (m?, t?): return m >= t
        }
    }
}

/// Everything significant that moved since the briefing was built.
/// Mirrors `models/live.py::LiveChanges`.
nonisolated struct LiveChanges: Codable, Sendable {
    /// What the changes are measured against: the pack's fetch timestamp, or —
    /// when the pack had no observations (briefed before flight day) — when the
    /// live layer recorded its own starting point (`baselineSource == "live_start"`).
    let baselineAt: String?
    /// "briefing" (default) or "live_start".
    let baselineSource: String?
    let computedAt: String?
    /// Already sorted server-side: alert tier first, then dep/dest/alternate/
    /// route, worse before better. Rendered in this order.
    let changes: [LiveChange]?
    let worsenedCount: Int?
    let improvedCount: Int?
    let alertCount: Int?
    /// #669: changes that cleared on the weather within the hour, newest first,
    /// each with `clearedAt`. Display only: never counted, never an alert. nil on
    /// the snapshot overlay (it never carries trails); the server owns the hour
    /// window and re-applies it on every `/live` read.
    var recentlyCleared: [LiveChange]? = nil
    /// #689: keys ("sigmet:LECM|6") of the listed SIGMETs new to the flight —
    /// their reissue chain did not start in the briefing. nil from an older
    /// server (see `issuedSigmetKeys`).
    var newSigmets: [String]? = nil

    var items: [LiveChange] { changes ?? [] }
    var clearedItems: [LiveChange] { recentlyCleared ?? [] }
    var isEmpty: Bool { items.isEmpty }
    /// Changes are measured from the live layer's own starting point, not the
    /// briefing's observations (a pack built before flight day has none).
    var isFromLiveStart: Bool { baselineSource == "live_start" }

    /// ICAOs with any change at the airport (category, convective weather,
    /// significant weather, wind, TAF) — the observations table marks these rows.
    var changedAirportIcaos: Set<String> {
        Set(items.compactMap { change -> String? in
            guard change.isAirportChange, let icao = change.icao, !icao.isEmpty else { return nil }
            return icao.uppercased()
        })
    }

    /// The `sigmet:` keys of the SIGMETs new to the flight — the hazards
    /// table badges rows whose `SigmetAlongRoute.liveChangeKey` is in this set
    /// NEW. The server's `newSigmets` (#689: the chain did not start in the
    /// briefing, so a reissue of a briefed SIGMET is not new); an older server
    /// without it falls back to the SIGMETs named by "issued" change rows.
    /// One phenomenon issued by two FIRs is a single change keyed on both
    /// ("sigmet:LECB|3+sigmet:LECM|3"), so the key is split back per SIGMET.
    var issuedSigmetKeys: Set<String> {
        if let newSigmets { return Set(newSigmets) }
        return Set(items.filter { $0.kindValue == .sigmetIssued }.flatMap { change in
            change.key.split(separator: "+").map(String.init)
        })
    }

    /// Direction of the change for one airport, worst first (a worse change
    /// wins over a better one), for the row marker. nil when unchanged.
    func airportDirection(icao: String) -> LiveChange.Direction? {
        let matching = items.filter { $0.isAirportChange && $0.icao?.uppercased() == icao.uppercased() }
        if matching.contains(where: { $0.directionValue == .worse }) { return .worse }
        if matching.contains(where: { $0.directionValue == .better }) { return .better }
        return nil
    }
}

/// One significant change since the briefing. Mirrors `models/live.py::LiveChange`.
nonisolated struct LiveChange: Codable, Sendable, Identifiable {
    /// Stable identity across ticks: "metar:EGLL", "taf:EGLL", "sigmet:LFFF|3",
    /// "lightning:route", "radar:route".
    let key: String
    let kind: String?
    let source: String?
    let direction: String?
    let tier: String?
    let role: String?
    let icao: String?
    let stationId: String?
    let fromValue: String?
    let toValue: String?
    /// When the evidence was observed / issued (METAR time, TAF issue, SIGMET
    /// valid_from, radar/lightning frame).
    let observedAt: String?
    let enrouteDistanceNm: Double?
    /// Deterministic, language-neutral shorthand, e.g. "EGLL METAR: VFR → IFR".
    let message: String?
    /// For push delivery (#638); the client ignores it.
    let newAlert: Bool?
    /// #669: recent history, on `/live` and refresh responses only. Display only.
    var trail: LiveChangeTrail? = nil
    /// Only on `LiveChanges.recentlyCleared` rows: when it left the screen.
    var clearedAt: String? = nil
    /// #682: on a SIGMET reissue row, the SIGMET it replaces ("LFMM T01").
    /// The key is then "<chain's first SIGMET>+<this SIGMET>". Display only.
    var replaces: String? = nil

    /// `key` alone is not unique if the server ever emits the same key twice
    /// (e.g. a worse and a better reading), so fold the direction in.
    var id: String { "\(key)|\(direction ?? "")" }

    enum Kind: String, Sendable {
        case metarCategory = "metar_category"
        case metarConvective = "metar_convective"
        case metarWeather = "metar_weather"
        case metarWind = "metar_wind"
        case tafCategory = "taf_category"
        case sigmetIssued = "sigmet_issued"
        case sigmetCancelled = "sigmet_cancelled"
        case lightning
        case radar
    }

    enum Direction: String, Sendable {
        case worse
        case better
        /// Neither (#689): e.g. a plain reissue of a SIGMET the briefing had.
        /// Not counted as worse; rendered neutral.
        case updated
    }

    /// nil for a kind this client does not know yet.
    var kindValue: Kind? { kind.flatMap(Kind.init(rawValue:)) }
    /// nil for an unknown direction (rendered neutral).
    var directionValue: Direction? { direction.flatMap { Direction(rawValue: $0.lowercased()) } }
    /// Alert tier = departure / destination / alternate airport.
    var isAlert: Bool { tier?.lowercased() == "alert" }
    /// A change at an airport (any METAR/TAF trigger), as opposed to an area
    /// change (SIGMET, lightning, radar). Keyed on the airport so a kind the
    /// server adds later still marks its row.
    var isAirportChange: Bool {
        guard let icao, !icao.isEmpty else { return false }
        switch kindValue {
        case .sigmetIssued, .sigmetCancelled, .lightning, .radar: return false
        default: return true
        }
    }

    /// Badge text: the server's source, upper-cased; falls back to the kind.
    var sourceLabel: String {
        if let source, !source.isEmpty { return source.uppercased() }
        switch kindValue {
        case .metarCategory, .metarConvective, .metarWeather, .metarWind: return "METAR"
        case .tafCategory: return "TAF"
        case .sigmetIssued, .sigmetCancelled: return "SIGMET"
        case .lightning: return "LIGHTNING"
        case .radar: return "RADAR"
        case nil: return "LIVE"
        }
    }

    /// The row text: the server message, else a minimal fallback from the
    /// structured fields so a row is never blank.
    var displayMessage: String {
        if let message, !message.isEmpty { return message }
        let subject = icao ?? stationId ?? key
        if let from = fromValue, let to = toValue { return "\(subject): \(from) → \(to)" }
        return subject
    }
}

/// A change's recent history (#669). Mirrors `models/live.py::LiveChangeTrail`;
/// computed server-side from the flight's live history (`tasks/live_trail.py`),
/// grouped by change key + direction.
nonisolated struct LiveChangeTrail: Codable, Sendable {
    let spans: [LiveTrailSpan]?
    /// Times on screen over the flight day, this one included.
    let timesToday: Int?
    /// `metar_category` only: the airport's category per report, oldest first.
    let reports: [LiveTrailReport]?
    /// What `fromValue` is measured against: "briefing" or "live_start".
    let baselineSource: String?
}

/// One period a change was on screen; `end` nil while it still is.
nonisolated struct LiveTrailSpan: Codable, Sendable {
    let start: String?
    let end: String?
}

/// One METAR/SPECI for a category row's strip.
nonisolated struct LiveTrailReport: Codable, Sendable {
    let at: String?
    let category: String?
    let reportType: String?
}

/// The trail line under a change row (#669). Pure; the same text as the web
/// (`web/ts/helpers/live-layer.ts::trailText`) — keep the two in step.
nonisolated enum LiveTrailText {
    /// Spans shown on one line (the latest).
    static let maxSpans = 4

    /// "12:42–13:02Z" for a closed span, "since 13:33Z" for an open one; nil
    /// when the start does not parse.
    static func span(_ span: LiveTrailSpan) -> String? {
        guard let start = span.start.flatMap(Date.parseISO8601) else { return nil }
        if let end = span.end.flatMap(Date.parseISO8601) {
            return "\(hhmm(start))–\(LiveTime.zulu(end))"
        }
        return String(localized: "since \(LiveTime.zulu(start))")
    }

    /// "1st", "2nd", "3rd", "11th".
    static func ordinal(_ n: Int) -> String {
        let mod100 = n % 100
        if (11...13).contains(mod100) { return "\(n)th" }
        switch n % 10 {
        case 1: return "\(n)st"
        case 2: return "\(n)nd"
        case 3: return "\(n)rd"
        default: return "\(n)th"
        }
    }

    /// "2nd time today" from 2; nil below.
    static func timesToday(_ n: Int?) -> String? {
        guard let n, n >= 2 else { return nil }
        return String(localized: "\(ordinal(n)) time today")
    }

    /// "briefed VFR · 11:00Z MVFR · 11:30Z VFR"; nil without reports.
    static func categoryStrip(_ change: LiveChange) -> String? {
        let reports = change.trail?.reports ?? []
        guard !reports.isEmpty else { return nil }
        var parts: [String] = []
        if let from = change.fromValue, !from.isEmpty {
            parts.append(change.trail?.baselineSource == "live_start"
                         ? String(localized: "at start \(from)")
                         : String(localized: "briefed \(from)"))
        }
        for report in reports {
            let time = report.at.flatMap(Date.parseISO8601).map(LiveTime.zulu) ?? "?"
            parts.append("\(time) \(report.category ?? "?")")
        }
        return parts.joined(separator: " · ")
    }

    /// The line under a row, or nil when it would only repeat the row (first
    /// time on screen, one span, one report). A cleared row always gets one.
    /// Category rows show the report strip; other kinds the on/off periods;
    /// "Nth time today" is appended from 2.
    static func line(_ change: LiveChange, cleared: Bool = false) -> String? {
        guard let trail = change.trail else { return nil }
        let spans = trail.spans ?? []
        let strip = change.kindValue == .metarCategory ? categoryStrip(change) : nil
        let spanText = spans.suffix(maxSpans).compactMap(span).joined(separator: ", ")
        let worth = cleared
            || (trail.timesToday ?? 0) >= 2
            || spans.count >= 2
            || (trail.reports?.count ?? 0) >= 2
        guard worth else { return nil }
        let parts = [strip ?? (spanText.isEmpty ? nil : spanText), timesToday(trail.timesToday)].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// "cleared 06:30Z".
    static func cleared(_ change: LiveChange) -> String? {
        guard let at = change.clearedAt.flatMap(Date.parseISO8601) else { return nil }
        return String(localized: "cleared \(LiveTime.zulu(at))")
    }

    /// "12:42" (no Z: the range's end carries it).
    private static func hhmm(_ date: Date) -> String {
        let zulu = LiveTime.zulu(date)
        return zulu.hasSuffix("Z") ? String(zulu.dropLast()) : zulu
    }
}

/// What got *worse* in the last realtime refresh — the pre-#637 banner shape,
/// kept server-side for older clients. Mirrors `models/observations.py::RefreshDelta`.
nonisolated struct RefreshDelta: Codable, Sendable {
    let worsened: Bool?
    let messages: [String]?
    let computedAt: String?
}

/// Pure time helpers for the live layer (age labels, instant comparison).
nonisolated enum LiveTime {
    /// Two timestamps denote the same instant. Tolerates sub-second drift
    /// between formats (microseconds vs whole seconds) — packs are minutes
    /// apart, so a one-second window can't confuse two of them.
    static func sameInstant(_ a: Date, _ b: Date) -> Bool {
        abs(a.timeIntervalSince(b)) < 1.0
    }

    /// Whether two ISO strings denote the same instant (false if either fails
    /// to parse).
    static func sameInstant(_ a: String?, _ b: String?) -> Bool {
        guard let a = a.flatMap(Date.parseISO8601), let b = b.flatMap(Date.parseISO8601) else { return false }
        return sameInstant(a, b)
    }

    /// Whether `candidate` is strictly newer than `current` (nil = oldest). An
    /// unparseable candidate is never newer.
    static func isNewer(_ candidate: String?, than current: String?) -> Bool {
        guard let c = candidate.flatMap(Date.parseISO8601) else { return false }
        guard let cur = current.flatMap(Date.parseISO8601) else { return true }
        return c > cur
    }

    /// The newest parseable instant among `timestamps`.
    static func newest(_ timestamps: [String?]) -> Date? {
        timestamps.compactMap { $0.flatMap(Date.parseISO8601) }.max()
    }

    /// "just now" / "12 min ago" / "2 h 5 min ago". A future instant (clock
    /// skew) reads as "just now".
    static func ageLabel(from date: Date, now: Date = Date()) -> String {
        let minutes = Int(now.timeIntervalSince(date) / 60)
        if minutes < 1 { return String(localized: "just now") }
        if minutes < 60 { return String(localized: "\(minutes) min ago") }
        let hours = minutes / 60
        let rest = minutes % 60
        if rest == 0 { return String(localized: "\(hours) h ago") }
        return String(localized: "\(hours) h \(rest) min ago")
    }

    /// "05:50Z" for an instant.
    static func zulu(_ date: Date) -> String {
        DateFormatter.utcTime.string(from: date)
    }

    /// Age beyond which observations are flagged stale in the UI.
    static let staleAfter: TimeInterval = 30 * 60
}

// MARK: - SIGMET identity (matches the server's change key)

nonisolated extension SigmetAlongRoute {
    /// Sequence id ("13" from "LTBB SIGMET 13 VALID …"), parsed from the raw
    /// bulletin exactly like the server's `_SEQ_RE = r"SIGMET\s+(\w+)"`
    /// (case-insensitive). nil when the bulletin has no sequence.
    var sequenceId: String? {
        guard let raw = rawText,
              let range = raw.range(of: #"SIGMET\s+\w+"#, options: [.regularExpression, .caseInsensitive])
        else { return nil }
        let token = raw[range].split(whereSeparator: { $0.isWhitespace }).last
        return token.map(String.init)
    }

    /// The server's `sigmet:` change key for this bulletin: `sigmet:{fir}|{seq}`.
    /// nil when there is no sequence id — the server then keys on
    /// FIR + hazard + validity using Python's `str(datetime)`, which this
    /// client does not try to reproduce; `matchesLiveChange` handles that case.
    var liveChangeKey: String? {
        sequenceId.map { "sigmet:\(firId)|\($0)" }
    }

    /// Whether this row is the subject of one of `keys` (the issued-SIGMET
    /// change keys). Exact match on `sigmet:{fir}|{seq}`; for a bulletin with no
    /// sequence id, fall back to the FIR + hazard prefix of the server's
    /// fallback key.
    func matchesLiveChange(keys: Set<String>) -> Bool {
        if let key = liveChangeKey { return keys.contains(key) }
        let prefix = "sigmet:\(firId)|\(hazard ?? "")|"
        return keys.contains { $0.hasPrefix(prefix) }
    }
}
