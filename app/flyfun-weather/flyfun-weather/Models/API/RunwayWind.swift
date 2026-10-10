import Foundation

// Runway + wind picture (#758). Server: src/weatherbrief/models/runway_wind.py;
// web: `RunwayWindPicture` in web/ts/store/types.ts.
//
// Everything is TRUE north: runway headings are euro_aip heading_degT and
// METAR/TAF winds are true. Idents are as painted (magnetic). No magnetic
// variation is applied anywhere, on purpose — do not "fix" it.
//
// Every field the server may omit on older packs is optional, so a payload
// without `runway_wind` decodes exactly as before.

nonisolated struct RunwayEndInfo: Codable, Equatable, Sendable {
    let ident: String
    let headingTrue: Double
}

nonisolated struct RunwayInfo: Codable, Equatable, Sendable {
    let id: String
    let lengthFt: Int?
    let surface: String?
    let hard: Bool?
    let ends: [RunwayEndInfo]
}

nonisolated struct WindSample: Codable, Equatable, Sendable {
    /// "metar", "taf" or "model".
    let source: String
    let time: String?
    /// Nil when VRB. Calm keeps the reported 000 — read `calm` first.
    let directionTrue: Int?
    let speedKt: Int?
    let gustKt: Int?
    let variable: Bool?
    let variableFrom: Int?
    let variableTo: Int?
    let calm: Bool?

    var isVariable: Bool { variable ?? false }
    var isCalm: Bool { calm ?? false }
}

nonisolated struct EndComponents: Codable, Equatable, Sendable {
    let ident: String
    /// Negative = tailwind.
    let headwindKt: Double
    /// Signed, positive = from the right.
    let crosswindKt: Double
    /// "left", "right" or "".
    let side: String?
    let gustHeadwindKt: Double?
    let gustCrosswindKt: Double?
    /// Worst case over the variable range and the gust.
    let maxCrosswindKt: Double
}

nonisolated struct WindAtAirport: Codable, Equatable, Sendable {
    let wind: WindSample
    let ends: [EndComponents]
    /// The METAR/TAF table's best runway and its tier — the same picker.
    let bestEnd: String?
    let advisory: String?
}

nonisolated struct RunwayWindPicture: Codable, Equatable, Sendable {
    let icao: String
    let runways: [RunwayInfo]
    let winds: [WindAtAirport]
}
