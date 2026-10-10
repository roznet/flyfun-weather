//
//  RunwayWindRulesTests.swift
//  flyfun-weatherTests
//
//  The runway + wind widget's pure rules (#758).
//
//  SYNC — these cases mirror web/tests/unit/runway-wind-core.test.ts one for
//  one, with the same inputs and the same expected strings and coordinates.
//  The two files are the guarantee that `RunwayWindRules.swift` and
//  `runway-wind-core.ts` still agree.
//

import Testing
import Foundation
import CoreGraphics
@testable import flyfun_weather

private typealias R = RunwayWindRules

private func rwy(_ a: String, _ ha: Double, _ b: String, _ hb: Double, _ length: Int? = 6000) -> RunwayInfo {
    RunwayInfo(id: "\(a)/\(b)", lengthFt: length, surface: "ASPH", hard: true,
               ends: [RunwayEndInfo(ident: a, headingTrue: ha), RunwayEndInfo(ident: b, headingTrue: hb)])
}

private func sample(
    source: String = "metar", time: String? = "2026-10-02T09:20:00+00:00",
    dir: Int? = 270, speed: Int? = 12, gust: Int? = nil,
    variable: Bool = false, from: Int? = nil, to: Int? = nil, calm: Bool = false
) -> WindSample {
    WindSample(source: source, time: time, directionTrue: dir, speedKt: speed, gustKt: gust,
               variable: variable, variableFrom: from, variableTo: to, calm: calm)
}

private func end(
    _ ident: String = "27", head: Double = 12, cross: Double = 0, side: String = "",
    gustCross: Double? = nil, max: Double = 0
) -> EndComponents {
    EndComponents(ident: ident, headwindKt: head, crosswindKt: cross, side: side,
                  gustHeadwindKt: nil, gustCrosswindKt: gustCross, maxCrosswindKt: max)
}

private func wind(_ s: WindSample, _ ends: [EndComponents], best: String?? = .none, advisory: String? = "green") -> WindAtAirport {
    let bestEnd: String? = best ?? ends.first?.ident
    return WindAtAirport(wind: s, ends: ends, bestEnd: bestEnd, advisory: advisory)
}

private func close(_ p: CGPoint?, _ x: Double, _ y: Double) -> Bool {
    guard let p else { return false }
    return abs(p.x - x) < 1e-4 && abs(p.y - y) < 1e-4
}

private func obs(_ icao: String, _ picture: RunwayWindPicture?) throws -> AirportObservation {
    var json: [String: Any] = ["icao": icao]
    if let picture {
        let data = try JSONEncoder.weatherBrief.encode(picture)
        json["runway_wind"] = try JSONSerialization.jsonObject(with: data)
    }
    return try JSONDecoder.weatherBrief.decode(AirportObservation.self, from: JSONSerialization.data(withJSONObject: json))
}

@Suite struct RunwayWindRulesTests {

    // MARK: Runway bars

    @Test func draws0927EastWestThroughTheCentreIdentsOnTheApproachSide() {
        let bar = R.runwayBars([rwy("09", 90, "27", 270)])[0]
        #expect(close(bar.a, 0.2, 0.5)) // threshold of 09: you land 09 arriving from the west
        #expect(close(bar.b, 0.8, 0.5))
        #expect(bar.labels.map(\.ident) == ["09", "27"])
        #expect(close(bar.labels[0].at, 0.13, 0.5))
        #expect(close(bar.labels[1].at, 0.87, 0.5))
    }

    @Test func spreadsParallelsSideBySideLToTheLeftOf050() {
        let bars = R.runwayBars([rwy("05L", 50, "23R", 230), rwy("05R", 50, "23L", 230)])
        func mid(_ b: R.RunwayBar) -> CGPoint { CGPoint(x: (b.a.x + b.b.x) / 2, y: (b.a.y + b.b.y) / 2) }
        #expect(close(mid(bars[0]), 0.474288, 0.469358))
        #expect(close(mid(bars[1]), 0.525712, 0.530642))
        // Their labels spread 2.5× as far, so "05L" and "05R" don't overprint.
        #expect(close(bars[0].labels[0].at, 0.152285, 0.661227))
        #expect(close(bars[1].labels[0].at, 0.280842, 0.814436))
    }

    @Test func crosses1836And0927ShorterOneShorterBothCentred() {
        let bars = R.runwayBars([rwy("18", 180, "36", 360, 3000), rwy("09", 90, "27", 270)])
        #expect(close(bars[0].a, 0.5, 0.35))
        #expect(close(bars[0].b, 0.5, 0.65))
        #expect(close(bars[0].labels[0].at, 0.5, 0.28))
        #expect(close(bars[1].a, 0.2, 0.5))
    }

    @Test func floorsShortStripsAndDrawsUnknownLengthsFull() {
        let bars = R.runwayBars([rwy("09", 90, "27", 270, 10000), rwy("18", 180, "36", 360, 1000), rwy("04", 40, "22", 220, nil)])
        #expect(close(bars[0].a, 0.2, 0.5))
        #expect(close(bars[1].a, 0.5, 0.38)) // 0.4 × 0.3 — the floor
        #expect(abs(hypot(bars[2].a.x - 0.5, bars[2].a.y - 0.5) - 0.3) < 1e-4)
    }

    @Test func readsTheDesignator() {
        #expect(R.designator("05L") == "L")
        #expect(R.designator("23C") == "C")
        #expect(R.designator("27") == nil)
    }

    @Test func labelsPerSize() {
        let bars = R.runwayBars([rwy("09", 90, "27", 270)])
        #expect(R.visibleLabels(bars, size: .regular, bestEnd: "27").map(\.ident) == ["09", "27"])
        #expect(R.visibleLabels(bars, size: .compact, bestEnd: "27").map(\.ident) == ["27"])
        #expect(R.visibleLabels(bars, size: .compact, bestEnd: nil).map(\.ident) == ["09", "27"])
        #expect(R.visibleLabels(bars, size: .inline, bestEnd: "27").isEmpty)
    }

    // MARK: Wind arrow

    @Test func draws270FromTheLeftEdgePointingRightWithItsGust() throws {
        let a = try #require(R.windArrow(sample(dir: 270, speed: 12, gust: 22)))
        #expect(close(a.from, 0.04, 0.5))
        #expect(close(a.to, 0.16, 0.5)) // 12 kt is under the floor
        #expect(close(a.gustTo, 0.228571, 0.5))
    }

    @Test func capsAStrong045WindFromTheNorthEastRim() throws {
        let a = try #require(R.windArrow(sample(dir: 45, speed: 40)))
        #expect(close(a.from, 0.825269, 0.174731))
        #expect(close(a.to, 0.613137, 0.386863))
        #expect(a.gustTo == nil)
    }

    @Test func keepsALight180WindVisibleFromTheBottomRim() throws {
        let a = try #require(R.windArrow(sample(dir: 180, speed: 2)))
        #expect(close(a.from, 0.5, 0.96))
        #expect(close(a.to, 0.5, 0.84))
        #expect(abs(R.arrowLength(2) - 0.12) < 1e-6)
    }

    @Test func dropsAGustWithinTheDisplayThreshold() {
        #expect(R.windArrow(sample(speed: 12, gust: 15))?.gustTo == nil)
    }

    @Test func hasNoArrowForCalmOrVrb() {
        #expect(R.windArrow(sample(dir: 0, speed: 0, calm: true)) == nil)
        #expect(R.windArrow(sample(dir: nil, speed: 4, variable: true)) == nil)
    }

    @Test func marksVrbCalmAndMissingNeverCalmForMissing() {
        #expect(R.windMark(wind(sample(dir: nil, speed: 4, variable: true), []), missing: "No METAR")
                == .vrb(r: 0.38, label: "VRB 04"))
        #expect(R.windMark(wind(sample(dir: 0, speed: 0, calm: true), []), missing: "No METAR") == .calm(label: "Calm"))
        #expect(R.windMark(nil, missing: "No METAR") == .missing(label: "No METAR"))
    }

    @Test func drawsAVariableRangeAsAClockwiseRimArcAcrossNorthToo() throws {
        let arc = try #require(R.variableArc(sample(from: 240, to: 300)))
        #expect(arc.sweepDeg == 60)
        #expect(arc.r == R.rimR)
        #expect(R.variableArc(sample(from: 350, to: 20))?.sweepDeg == 30)
        #expect(R.variableArc(sample()) == nil)
    }

    // MARK: Words

    @Test func labelsTheWindAsReported() {
        #expect(R.windLabel(sample(dir: 270, speed: 12, gust: 22)) == "270@12G22")
        #expect(R.windLabel(sample(dir: 5, speed: 8)) == "005@8")
        #expect(R.windLabel(sample(dir: 270, speed: 12, from: 240, to: 300)) == "270@12 240V300")
        #expect(R.windLabel(sample(dir: nil, speed: 4, gust: 15, variable: true)) == "VRB 04G15")
        #expect(R.windLabel(sample(dir: 0, speed: 0, calm: true)) == "Calm")
    }

    @Test func saysHeadCrosswindSideAndGustForTheBestEnd() {
        let e = end(head: 6.2, cross: -10.6, side: "left", gustCross: -17.3, max: 17.3)
        #expect(R.componentsLine(wind(sample(gust: 22), [e]), missing: "No METAR")
                == "27 · 6 kt head · 11 kt X-wind from left (G 17)")
    }

    @Test func saysTailForANegativeHeadwind() {
        let e = end("09", head: -3.4, cross: 2.0, side: "right", max: 2)
        #expect(R.endLine(e, sample()) == "09 · 3 kt tail · 2 kt X-wind from right")
    }

    @Test func namesNoSideForZeroCrosswindAndHidesACloseGust() {
        #expect(R.endLine(end(), sample()) == "27 · 12 kt head · 0 kt X-wind")
        let e = end(cross: 10, side: "right", gustCross: 12.4, max: 12.4)
        #expect(R.endLine(e, sample()) == "27 · 12 kt head · 10 kt X-wind from right")
    }

    @Test func addsTheVariableRangeWorstCaseWhenItSaysMore() {
        let e = end(cross: 10, side: "right", max: 17.3)
        #expect(R.endLine(e, sample(from: 270, to: 330)) == "27 · 12 kt head · 10 kt X-wind from right · up to 17 kt")
    }

    @Test func roundsTiesAwayFromZero() {
        let e = end("09", head: -2.5, cross: 0.5, side: "right", max: 0.5)
        #expect(R.endLine(e, sample()) == "09 · 3 kt tail · 1 kt X-wind from right")
    }

    @Test func givesVrbItsWorstCaseCalmAndMissingTheirWords() {
        let vrb = sample(dir: nil, speed: 4, variable: true)
        #expect(R.componentsLine(wind(vrb, [end(max: 4)], best: .some(nil), advisory: nil), missing: "No METAR")
                == "VRB 04 · up to 4 kt X-wind")
        #expect(R.componentsLine(wind(vrb, [], best: .some(nil), advisory: nil), missing: "No METAR")
                == "VRB 04 · up to 4 kt X-wind")
        #expect(R.componentsLine(wind(sample(dir: 0, speed: 0, calm: true), [end()]), missing: "No METAR") == "Calm")
        #expect(R.componentsLine(nil, missing: "No METAR") == "No METAR")
        #expect(R.componentsLine(wind(sample(), [], best: .some(nil), advisory: nil), missing: "No METAR") == "No runway data")
    }

    @Test func writesTheGhostLine() {
        let taf = sample(source: "taf", time: "2026-10-02T14:00:00+00:00", dir: 250, speed: 18, gust: 28)
        let e = end(head: 16.9, cross: -6.2, side: "left", max: 6.2)
        #expect(R.ghostLine(wind(taf, [e])) == "TAF 14Z 250@18G28 · 27 · 6 kt X-wind from left")
        #expect(R.ghostLine(wind(sample(source: "taf", time: nil, dir: 0, speed: 0, calm: true), [])) == "TAF Calm")
    }

    @Test func labelsSampleTimesInUtc() {
        #expect(R.sampleTimeLabel("2026-10-02T14:37:00Z") == "1437Z")
        #expect(R.sampleTimeLabel("2026-10-02T14:00:00") == "14Z")
        #expect(R.sampleTimeLabel(nil) == "")
    }

    @Test func tonesOnlyAmberAndRedAndOnlyTheCrosswindText() {
        #expect(R.crosswindTone("amber") == "amber")
        #expect(R.crosswindTone("red") == "red")
        #expect(R.crosswindTone("green") == nil)
        #expect(R.crosswindTone(nil) == nil)
        let e = end(head: 6.2, cross: -10.6, side: "left", max: 10.6)
        #expect(R.crosswindText(wind(sample(), [e])) == "11 kt X-wind from left")
        #expect(R.crosswindText(wind(sample(speed: 0, calm: true), [e])) == nil)
    }

    // MARK: The departure / destination pair

    private func picture(_ icao: String, _ winds: [WindAtAirport]) -> RunwayWindPicture {
        RunwayWindPicture(icao: icao, runways: [rwy("09", 90, "27", 270)], winds: winds)
    }

    @Test func givesTheDestinationItsTafGhostAndTheDepartureNone() throws {
        let metar = wind(sample(), [end()])
        let taf = wind(sample(source: "taf"), [end()])
        let dials = R.pair(airports: [try obs("ZZAA", picture("ZZAA", [metar, taf])), try obs("ZZBB", picture("ZZBB", [metar, taf]))],
                           departure: "ZZAA", destination: "ZZBB")
        #expect(dials.map(\.role) == ["DEP", "ARR"])
        #expect(dials[0].ghost == nil)
        #expect(dials[1].ghost == taf)
    }

    @Test func keepsASideWithoutAPictureSayingSo() throws {
        let dials = R.pair(airports: [try obs("ZZBB", picture("ZZBB", []))], departure: "ZZAA", destination: "ZZBB")
        #expect(dials.count == 2)
        #expect(R.missingWindLabel(dials[0]) == "No runway data")
        #expect(R.missingWindLabel(dials[1]) == "No METAR")
    }

    @Test func isEmptyWhenNoAirportHasAPictureOlderServer() throws {
        #expect(R.pair(airports: [try obs("ZZAA", nil)], departure: "ZZAA", destination: "ZZBB").isEmpty)
        #expect(R.pair(airports: nil, departure: "ZZAA", destination: "ZZBB").isEmpty)
    }

    // MARK: Decoding

    @Test func decodesTheServerPictureAndAPayloadWithoutIt() throws {
        let json = """
        {"icao": "ZZAA", "metar_wind_variable_from": 240, "metar_wind_variable_to": 300,
         "runway_wind": {"icao": "ZZAA",
           "runways": [{"id": "09/27", "length_ft": 6000, "surface": "ASPH", "hard": true,
                        "ends": [{"ident": "09", "heading_true": 91.5}, {"ident": "27", "heading_true": 271.5}]}],
           "winds": [{"wind": {"source": "metar", "time": "2026-10-02T09:20:00+00:00", "direction_true": 270,
                               "speed_kt": 12, "gust_kt": null, "variable": false, "variable_from": 240,
                               "variable_to": 300, "calm": false},
                      "ends": [{"ident": "27", "headwind_kt": 12.0, "crosswind_kt": -0.3, "side": "left",
                                "gust_headwind_kt": null, "gust_crosswind_kt": null, "max_crosswind_kt": 6.0}],
                      "best_end": "27", "advisory": "green"}]}}
        """
        let o = try JSONDecoder.weatherBrief.decode(AirportObservation.self, from: Data(json.utf8))
        #expect(o.metarWindVariableFrom == 240)
        #expect(o.runwayWind?.runways.first?.ends.last?.headingTrue == 271.5)
        #expect(o.runwayWind?.winds.first?.ends.first?.maxCrosswindKt == 6.0)
        #expect(o.runwayWind?.winds.first?.bestEnd == "27")
        let old = try JSONDecoder.weatherBrief.decode(AirportObservation.self, from: Data(#"{"icao": "ZZAA"}"#.utf8))
        #expect(old.runwayWind == nil)
    }
}
