#if DEBUG
import Foundation

/// One real briefing for App Store screenshots (`FLYFUN_MOCK_SHOWCASE=<path>`).
///
/// The file is `flyfun-weatherUITests/Showcase/showcase.json`, written by
/// `scripts/export_showcase_fixture_ios.py` from a prod flight: ECMWF only, no
/// live layer, no charts / GRAMET, fictional aircraft. The UI test passes its
/// absolute path — a simulator app reads host paths — so the fixture never
/// ships in the app bundle.
///
/// Every `YYYY-MM-DD` in the file is shifted by whole days so the flight
/// departs tomorrow (UTC) whenever the screenshots are taken: it lists under
/// Future, nothing reads as days old, and times of day — the departure, the
/// low-sun advisory — stay as briefed.
struct FixtureShowcase {
    let flight: FlightResponse
    let pack: PackMetaResponse
    let advisories: AdvisoriesResponse
    let advisoryDetails: [String: AdvisoryDetailResponse]
    let digest: DigestResponse
    let snapshot: SnapshotResponse
    let routeAnalyses: RouteAnalysesResponse
    let elevation: ElevationResponse
    /// Keyed by route point index; ECMWF at the named waypoints only.
    let soundingProfiles: [Int: SoundingProfileResponse]

    /// A null layer: what the server returns before any live data.
    let liveLayer: LiveLayerResponse

    /// The file's shape. Its dictionaries' keys are ignored: `convertFromSnakeCase`
    /// rewrites dictionary keys too (`vfr_feasibility` comes back
    /// `vfrFeasibility`), so they are re-keyed from each value's own id.
    private struct File: Decodable {
        let flight: FlightResponse
        let pack: PackMetaResponse
        let advisories: AdvisoriesResponse
        let advisoryDetails: [String: AdvisoryDetailResponse]
        let digest: DigestResponse
        let snapshot: SnapshotResponse
        let routeAnalyses: RouteAnalysesResponse
        let elevation: ElevationResponse
        let soundingProfiles: [String: SoundingProfileResponse]
    }

    enum LoadError: Error { case noTargetDate }

    static func load(path: String, now: Date = Date()) throws -> FixtureShowcase {
        let text = try String(contentsOfFile: path, encoding: .utf8)
        guard let shifted = shiftDates(in: text, toDepartTomorrowFrom: now) else { throw LoadError.noTargetDate }
        let file = try JSONDecoder.weatherBrief.decode(File.self, from: Data(shifted.utf8))
        let live = try JSONDecoder.weatherBrief.decode(LiveLayerResponse.self, from: Data("""
            {"flight_id": "\(file.flight.id)", "pack_timestamp": "\(file.pack.fetchTimestamp)", "live_updated_at": null,
             "route_observations": null, "route_sigmets": null, "observed_conditions": null,
             "changes": null, "last_refresh_delta": null}
            """.utf8))
        return FixtureShowcase(
            flight: file.flight,
            pack: file.pack,
            advisories: file.advisories,
            advisoryDetails: Dictionary(file.advisoryDetails.values.map { ($0.advisoryId, $0) },
                                        uniquingKeysWith: { a, _ in a }),
            digest: file.digest,
            snapshot: file.snapshot,
            routeAnalyses: file.routeAnalyses,
            elevation: file.elevation,
            soundingProfiles: Dictionary(file.soundingProfiles.values.map { ($0.pointIndex, $0) },
                                         uniquingKeysWith: { a, _ in a }),
            liveLayer: live
        )
    }

    private static let datePattern = try! NSRegularExpression(pattern: #"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"#)

    private static var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// Shift every date by the whole days between the flight's `target_date`
    /// and tomorrow — every `YYYY-MM-DD` in the text, free text and ids
    /// included (the flight id carries its date), which is what keeps them
    /// consistent. Nil when the text has no `target_date` to anchor on.
    static func shiftDates(in text: String, toDepartTomorrowFrom now: Date) -> String? {
        let calendar = utc
        let ns = text as NSString
        guard let target = ns.range(of: #""target_date":\s*"\d{4}-\d{2}-\d{2}""#, options: .regularExpression)
                .nonEmpty.flatMap({ date(in: ns.substring(with: $0), calendar: calendar) })
        else { return nil }
        guard let tomorrow = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: now)),
              let days = calendar.dateComponents([.day], from: target, to: tomorrow).day,
              days != 0
        else { return text }

        let result = NSMutableString(string: text)
        for match in datePattern.matches(in: text, range: NSRange(location: 0, length: ns.length)).reversed() {
            guard let original = date(in: ns.substring(with: match.range), calendar: calendar),
                  let moved = calendar.date(byAdding: .day, value: days, to: original) else { continue }
            let c = calendar.dateComponents([.year, .month, .day], from: moved)
            result.replaceCharacters(in: match.range,
                                     with: String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!))
        }
        return result as String
    }

    private static func date(in text: String, calendar: Calendar) -> Date? {
        let ns = text as NSString
        guard let m = datePattern.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)),
              let y = Int(ns.substring(with: m.range(at: 1))),
              let mo = Int(ns.substring(with: m.range(at: 2))),
              let d = Int(ns.substring(with: m.range(at: 3))) else { return nil }
        return calendar.date(from: DateComponents(year: y, month: mo, day: d))
    }
}

private extension NSRange {
    var nonEmpty: NSRange? { location == NSNotFound ? nil : self }
}
#endif
