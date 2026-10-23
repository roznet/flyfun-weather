import Foundation

// =============================================================================
// Departure / arrival airport cards (#882, part of #737): the card's pure
// mapping from the server's fields to what is drawn. `AirportCardView.swift`
// owns the SwiftUI and holds no rules of its own.
//
// Every value is the server's (#880: `glance.departure` / `glance.destination`
// and their `airport` row): the meter positions, the sky and visibility words,
// the weather words and their icon kind, the short wind, the crosswind, the
// trend, the TAF hours and the fold. This file only picks and orders them; a
// missing field is left out, never derived here (Brice, 2026-10-23: nothing
// is computed on the phone).
//
// SYNC — web counterpart: web/ts/visualization/observed/airport-card-view.ts
// (#881). Same fields, same order:
//
//   boxes                → headHtml's boxes
//   shortType            → shortType
//   weatherSymbol        → WORD_ICON (SF Symbols here, the web's own SVGs there)
//   crosswindGlyph       → windRowHtml's glyph
//   crosswindTone        → windRowHtml's tone (METAR advisory, amber/red only)
//   trend                → provenanceHtml's trend
//   hourLabel            → hourLabel
//   meterPosition        → meterHtml's clamp
//   slots                → airportCardsHtml's order
//
// Deliberate divergence: the accessibility summary is iOS's (VoiceOver reads
// one element per card); the web exposes the rows themselves.
// =============================================================================

nonisolated enum AirportCardRules {

    enum Role: String, Sendable {
        case departure = "DEP"
        case arrival = "ARR"
    }

    // MARK: Header

    /// One category box: filled = prevailing, dashed = a TEMPO/PROB group.
    struct CategoryBox: Equatable, Sendable {
        let category: String
        /// "now", "at ETA", "TEMPO", "PROB30".
        let caption: String
        let dashed: Bool
    }

    /// METAR now → TAF at ETD/ETA, plus a dashed box for a worse TEMPO/PROB
    /// group. Each box only when the server has its category.
    static func boxes(_ block: any LiveGlanceTerminal, role: Role) -> [CategoryBox] {
        let a = block.airport
        var boxes: [CategoryBox] = []
        if let now = a?.metarFlightCategory ?? block.metarCategory {
            boxes.append(CategoryBox(category: now, caption: String(localized: "now"), dashed: false))
        }
        if let at = a?.tafPrevailingCategoryAtEta {
            boxes.append(CategoryBox(
                category: at,
                caption: role == .departure ? String(localized: "at ETD") : String(localized: "at ETA"),
                dashed: false
            ))
            if let temp = a?.tafTemporaryCategoryAtEta {
                boxes.append(CategoryBox(category: temp, caption: shortType(a?.tafTemporaryType), dashed: true))
            }
        }
        return boxes
    }

    /// "PROB30 TEMPO" → "PROB30": the box has room for one word.
    static func shortType(_ type: String?) -> String {
        let t = (type ?? "").trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? "TEMPO" : String(t.prefix { !$0.isWhitespace })
    }

    // MARK: Rows

    /// The server's weather-word icon kind onto an SF Symbol.
    static func weatherSymbol(_ icon: String?) -> String {
        switch icon {
        case "thunder": "cloud.bolt"
        case "hail": "cloud.hail"
        case "snow", "freezing": "cloud.snow"
        case "showers", "rain": "cloud.rain"
        case "drizzle": "cloud.drizzle"
        case "fog", "mist": "cloud.fog"
        case "haze", "smoke", "dust": "sun.haze"
        case "squall", "funnel": "wind"
        default: "cloud"
        }
    }

    /// The side the crosswind comes from: ◀ left, ▶ right, nothing when it
    /// blows down the runway.
    static func crosswindGlyph(_ side: String?) -> String {
        switch side {
        case "left": "◀ "
        case "right": "▶ "
        default: ""
        }
    }

    /// "◀ 10G16 kt X": the crosswind chip's text.
    static func crosswindText(_ x: CrosswindReading) -> String {
        "\(crosswindGlyph(x.side))\(x.text) X"
    }

    /// The crosswind's tone: the server's own METAR wind advisory, amber or
    /// red only (green is quiet). The METAR's, not the block's `windAdvisory`
    /// (that one is the TAF at ETA's), so the number and its colour come from
    /// the same wind.
    static func crosswindTone(_ a: AirportObservation?) -> String? {
        switch a?.metarWindAdvisory {
        case "amber": "amber"
        case "red": "red"
        default: nil
        }
    }

    /// A 0…1 meter position, clamped; nil leaves the dot out.
    static func meterPosition(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        return min(1, max(0, value))
    }

    // MARK: Provenance

    struct Trend: Equatable, Sendable {
        /// "↘" (worse) or "↗" (better).
        let arrow: String
        /// "was VFR 09:00Z".
        let text: String
        let worse: Bool
    }

    /// "↘ was VFR 09:00Z": only when the server gave the trend and the
    /// category it compared against.
    static func trend(_ a: AirportObservation?) -> Trend? {
        guard let a, let trend = a.metarTrend, let was = a.metarPreviousFlightCategory else { return nil }
        let time = a.metarPreviousTime.flatMap(Date.parseISO8601).map(LiveTime.zulu)
        let text = time.map { String(localized: "was \(was) \($0)") } ?? String(localized: "was \(was)")
        return Trend(arrow: trend == "worse" ? "↘" : "↗", text: text, worse: trend == "worse")
    }

    /// "ETD 10:00Z" / "ETA 11:35Z"; nil without the time (position_sharing
    /// = none hides it).
    static func etaText(_ block: any LiveGlanceTerminal, role: Role) -> String? {
        let iso: String? = switch role {
        case .departure: (block as? LiveGlanceDeparture)?.etd
        case .arrival: (block as? LiveGlanceDestination)?.eta
        }
        guard let date = iso.flatMap(Date.parseISO8601) else { return nil }
        return role == .departure ? String(localized: "ETD \(LiveTime.zulu(date))")
                                  : String(localized: "ETA \(LiveTime.zulu(date))")
    }

    /// The server's reason when the TAF has no category at the ETA ("no TAF
    /// for ETA"); nil when the header boxes already say it.
    static func tafNote(_ block: any LiveGlanceTerminal) -> String? {
        guard block.airport?.tafPrevailingCategoryAtEta == nil,
              let text = block.tafText, !text.isEmpty else { return nil }
        return text
    }

    // MARK: TAF strip

    /// "10Z" for an hour's start.
    static func hourLabel(_ iso: String) -> String {
        guard let date = Date.parseISO8601(iso) else { return "" }
        return String(LiveTime.zulu(date).prefix(2)) + "Z"
    }

    // MARK: Layout

    enum Slot: Hashable, Sendable {
        case departure
        case foldedDeparture
        case arrival
    }

    /// Departure then arrival; once the server says the departure is behind
    /// (`folded`), the arrival first and the departure's one line after it.
    static func slots(departure: LiveGlanceDeparture?, destination: LiveGlanceDestination?) -> [Slot] {
        let folded = departure?.isFolded == true
        var slots: [Slot] = []
        if departure != nil, !folded { slots.append(.departure) }
        if destination != nil { slots.append(.arrival) }
        if departure != nil, folded { slots.append(.foldedDeparture) }
        return slots
    }

    /// UserDefaults key for one flight's folded departure being open.
    static let departureOpenKeyPrefix = "observedDepartureOpen:"
    static func departureOpenKey(flightId: String) -> String { departureOpenKeyPrefix + flightId }

    // MARK: Accessibility

    /// One spoken summary per card, from the server's fields in the card's
    /// order: "Arrival LFAT, MVFR now, MVFR at ETA, TEMPO IFR, wind 300/14,
    /// runway 31, crosswind 3 kt from the left".
    static func accessibilitySummary(_ block: any LiveGlanceTerminal, role: Role) -> String {
        var parts = [[role == .departure ? String(localized: "Departure") : String(localized: "Arrival"),
                      block.icao ?? ""].filter { !$0.isEmpty }.joined(separator: " ")]
        for box in boxes(block, role: role) {
            parts.append(box.dashed ? "\(box.caption) \(box.category)" : "\(box.category) \(box.caption)")
        }
        let a = block.airport
        if let sky = a?.metarSkyText { parts.append(sky) }
        if let vis = a?.metarVisibilityText { parts.append(String(localized: "visibility \(vis)")) }
        for word in a?.metarWeatherWords ?? [] { parts.append(word.text) }
        if let wind = a?.metarWindShort {
            parts.append(String(localized: "wind \(wind)"))
        } else if let wind = block.windText {
            parts.append(wind)
        }
        if let x = a?.metarCrosswind {
            parts.append(String(localized: "runway \(x.runway)"))
            switch x.side {
            case "left": parts.append(String(localized: "crosswind \(x.text) from the left"))
            case "right": parts.append(String(localized: "crosswind \(x.text) from the right"))
            default: parts.append(String(localized: "crosswind \(x.text)"))
            }
        }
        if let cells = block.cellsText { parts.append(cells) }
        if let lightning = block.lightningText { parts.append(lightning) }
        if let trend = trend(a) { parts.append(trend.text) }
        if let eta = etaText(block, role: role) { parts.append(eta) }
        return parts.joined(separator: ", ")
    }
}
