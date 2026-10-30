import Foundation
import RZFlight

/// Turns briefing / live text written for reading into text Siri can say
/// (CarPlay, AirPods, screen off).
///
/// Deterministic and conservative: it renames, expands and drops source
/// notes, but never summarises, so it cannot lose or invent a hazard. A
/// parenthetical goes only when it carries no hazard word ("(EGLL 8 NM NE
/// used for METAR and TAF)"); one naming a thunderstorm, SIGMET, icing, etc.
/// is kept. A server-written spoken variant is the planned upgrade; this stays
/// as its fallback.
///
/// Pure (`nonisolated`, names injected as a dictionary) so it is unit-tested
/// without the `@MainActor` airport database; `airportNames(in:)` builds that
/// dictionary on device.
nonisolated enum SpokenText {
    /// `text` as spoken: ICAO codes → airport names (when known), source-note
    /// parentheticals dropped, LIFR / MVFR / NM / ft / FL / zulu times
    /// expanded, arrows read as "to".
    static func speakable(_ text: String, names: [String: String] = [:]) -> String {
        var s = dropSourceNotes(text)
        s = replace(#"\b[A-Z]{4}\b"#, in: s) { match in names[match[0]] ?? match[0] }
        s = s.replacingOccurrences(of: " → ", with: " to ")
            .replacingOccurrences(of: "→", with: " to ")
            .replacingOccurrences(of: " · ", with: ", ")
        s = replace(#"\bLIFR\b"#, in: s) { _ in "low IFR" }
        s = replace(#"\bMVFR\b"#, in: s) { _ in "marginal VFR" }
        s = replace(#"\b(\d[\d,]*)\s?[-–]\s?(\d[\d,]*)\s?ft\b"#, in: s) { "\($0[1]) to \($0[2]) feet" }
        s = replace(#"\b(\d[\d,]*)\s?ft\b"#, in: s) { "\($0[1]) feet" }
        s = replace(#"\bFL\s?(\d{2,3})\b"#, in: s) { "flight level \(Int($0[1]) ?? 0)" }
        s = replace(#"\b(\d+(?:\.\d+)?)\s?NM\b"#, in: s) { "\($0[1]) nautical mile\($0[1] == "1" ? "" : "s")" }
        s = replace(#"\b(\d{2}):?(\d{2})Z\b"#, in: s) { "\($0[1]):\($0[2]) zulu" }
        return tidy(s)
    }

    /// "Fairoaks Airport" → "Fairoaks", "Le Touquet-Côte d'Opale Airport" →
    /// "Le Touquet", "Charles de Gaulle International Airport" → "Charles de
    /// Gaulle". A hyphen cuts only when at least two words precede it, so
    /// "Midden-Zeeland Airfield" stays "Midden-Zeeland".
    static func speakableName(_ name: String) -> String {
        let dropped: Set<String> = ["airport", "airfield", "aerodrome", "international", "intl", "airpark"]
        var words = name.split(separator: " ").map(String.init)
            .filter { !dropped.contains($0.lowercased()) }
        if let hyphenWord = words.firstIndex(where: { $0.contains("-") }) {
            let head = words[hyphenWord].split(separator: "-", maxSplits: 1).first.map(String.init) ?? ""
            if hyphenWord >= 1, !head.isEmpty {
                words = Array(words[..<hyphenWord]) + [head]
            }
        }
        let result = words.joined(separator: " ")
        return result.isEmpty ? name : result
    }

    /// Every 4-letter uppercase token in `texts` (candidate ICAO codes).
    static func icaoTokens(in texts: [String]) -> Set<String> {
        var found = Set<String>()
        for text in texts {
            for match in matches(#"\b[A-Z]{4}\b"#, in: text) { found.insert(match[0]) }
        }
        return found
    }

    // MARK: - Rules

    /// Words that keep a parenthetical: anything a pilot must still hear.
    private static let hazardPattern =
        #"(?i)\b(TS|CB|TCU|thunder\w*|SIGMET|ic(e|ing)|freezing|FZ\w*|convect\w*|cells?|hail|severe|SEV|turb\w*|EMBD|embedded|squall\w*|volcanic|ash|wind ?shear|gust\w*)\b"#

    private static func dropSourceNotes(_ text: String) -> String {
        replace(#"\s*\(([^()]*)\)"#, in: text) { match in
            matches(hazardPattern, in: match[1]).isEmpty ? "" : match[0]
        }
    }

    private static func tidy(_ text: String) -> String {
        var s = replace(#"\s+"#, in: text) { _ in " " }
        s = replace(#"\s+([,.;:])"#, in: s) { $0[1] }
        return s.trimmingCharacters(in: .whitespaces)
    }

    // MARK: - Regex helpers

    /// Capture groups of every match (index 0 = whole match; a group that did
    /// not participate is "").
    private static func matches(_ pattern: String, in text: String) -> [[String]] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let ns = text as NSString
        return regex.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { result in
            (0..<result.numberOfRanges).map { i in
                let range = result.range(at: i)
                return range.location == NSNotFound ? "" : ns.substring(with: range)
            }
        }
    }

    private static func replace(_ pattern: String, in text: String, with transform: ([String]) -> String) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return text }
        let ns = text as NSString
        var output = ""
        var cursor = 0
        for result in regex.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            output += ns.substring(with: NSRange(location: cursor, length: result.range.location - cursor))
            let groups = (0..<result.numberOfRanges).map { i -> String in
                let range = result.range(at: i)
                return range.location == NSNotFound ? "" : ns.substring(with: range)
            }
            output += transform(groups)
            cursor = result.range.location + result.range.length
        }
        output += ns.substring(from: cursor)
        return output
    }
}

extension SpokenText {
    /// Speakable names for the ICAO codes in `texts`, from the local airport
    /// database. Codes the database doesn't know are left out, so they stay
    /// as letters (and non-airport tokens like "LIFR" never match).
    @MainActor
    static func airportNames(in texts: [String]) -> [String: String] {
        var names: [String: String] = [:]
        for icao in icaoTokens(in: texts) {
            if let name = AirportDatabase.shared.airport(icao: icao)?.name, !name.isEmpty {
                names[icao] = speakableName(name)
            }
        }
        return names
    }
}
