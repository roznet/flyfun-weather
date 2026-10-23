//
//  LivePositionTests.swift
//  flyfun-weatherTests
//
//  #761 — the GPS fixes the app sends: the wire keys the server reads
//  (`LivePositionFix` in src/weatherbrief/api/packs.py), the recent-fix
//  buffer, and the CLLocation → fix conversion.
//

import Testing
import Foundation
import CoreLocation
@testable import flyfun_weather

@Suite struct LivePositionTests {

    private let t0 = Date(timeIntervalSince1970: 1_791_000_000)

    private func fix(_ seconds: TimeInterval, lon: Double = 1.0) -> LivePositionFix {
        LivePositionFix(at: t0.addingTimeInterval(seconds), lat: 50.0, lon: lon, altFt: 4500,
                        gsKt: 120, trackDeg: 90, hAccuracyM: 8, alongNm: 12.5, crossTrackNm: 0.3)
    }

    @Test func requestUsesTheServersKeys() throws {
        let data = try JSONEncoder.weatherBrief.encode(LivePositionRequest(fixes: [fix(0)], landed: true))
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["landed"] as? Bool == true)
        let first = try #require((json["fixes"] as? [[String: Any]])?.first)
        #expect(Set(first.keys) == ["at", "lat", "lon", "alt_ft", "gs_kt", "track_deg",
                                    "h_accuracy_m", "along_nm", "cross_track_nm"])
        #expect(first["h_accuracy_m"] as? Double == 8)
    }

    @Test func invalidValuesAreOmitted() throws {
        let bare = LivePositionFix(at: t0, lat: 50, lon: 1, altFt: nil, gsKt: nil, trackDeg: nil,
                                   hAccuracyM: 30, alongNm: nil, crossTrackNm: nil)
        let data = try JSONEncoder.weatherBrief.encode(bare)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(Set(json.keys) == ["at", "lat", "lon", "h_accuracy_m"])
    }

    @Test func resultDecodes() throws {
        let json = #"{"flight_id": "f", "accepted": 2, "ignored": [], "landed_recorded": false, "progress": null}"#
        let result = try JSONDecoder.weatherBrief.decode(LivePositionResult.self, from: Data(json.utf8))
        #expect(result.accepted == 2 && result.landedRecorded == false)
    }

    @Test func bufferKeepsTheNewestPlusTwoSpacedFixes() {
        var buffer: [LivePositionFix] = []
        // A fix every 5 s, as the tracking service projects them.
        for s in stride(from: 0.0, through: 130, by: 5) {
            buffer = LivePositionRules.appending(fix(s), to: buffer)
        }
        // The newest, plus the recent ones ~60 s apart.
        #expect(buffer.map { $0.at.timeIntervalSince(t0) } == [60, 120, 130])
        // An older fix arriving late changes nothing; nil is no fix.
        #expect(LivePositionRules.appending(fix(10), to: buffer) == buffer)
        #expect(LivePositionRules.appending(nil, to: buffer) == buffer)
    }

    @Test func unsentQueueSpansTheFlight() {
        // A fix a minute for an hour with no data: the queue keeps the newest
        // plus older ones 10 min apart, at most 5 (what one post may carry).
        var queue: [LivePositionFix] = []
        for s in stride(from: 0.0, through: 3600, by: 60) {
            queue = LivePositionRules.queued([fix(s)], into: queue, since: nil)
        }
        let times = queue.map { $0.at.timeIntervalSince(t0) }
        #expect(times.count == 5 && times.last == 3600)
        #expect(zip(times, times.dropFirst()).dropLast().allSatisfy { $1 - $0 >= 600 })
        // Nothing the server already has; nothing older than the queue's last.
        #expect(LivePositionRules.queued([fix(10)], into: [], since: t0.addingTimeInterval(60)).isEmpty)
        #expect(LivePositionRules.queued([fix(10)], into: queue, since: nil) == queue)
    }

    private func result(_ accepted: Int) -> LivePositionResult {
        LivePositionResult(accepted: accepted, landedRecorded: false)
    }

    private func settle(_ repo: MockBriefingRepository, posts: Int) async {
        for _ in 0..<200 where repo.postedLivePositions.count < posts { await Task.yield() }
        for _ in 0..<20 { await Task.yield() }
    }

    @Test @MainActor func aFailedPostKeepsItsFixesForTheNext() async {
        let repo = MockBriefingRepository()
        repo.postLivePositionResults = [.failure(MockError.notStubbed("offline")), .success(result(2))]
        let service = LivePositionService(repository: repo, scope: "test", defaults: scratchDefaults())
        service.submit([fix(0)], flightId: "f")
        await settle(repo, posts: 1)
        // No data: the next fix goes out with the one that failed.
        service.submit([fix(900)], flightId: "f")
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.map { $0.fixes.map { $0.at.timeIntervalSince(t0) } } == [[0], [0, 900]])
        // Sent: nothing left, so a flush posts nothing.
        service.flush(flightId: "f")
        await settle(repo, posts: 3)
        #expect(repo.postedLivePositions.count == 2)
    }

    @Test @MainActor func anUnchangedTrackingBufferIsNotResent() async {
        let repo = MockBriefingRepository()
        repo.postLivePositionResults = [.success(result(2)), .success(result(1))]
        let service = LivePositionService(repository: repo, scope: "test", defaults: scratchDefaults())
        let buffer = [fix(0), fix(60)]
        service.submit(buffer, flightId: "f")
        await settle(repo, posts: 1)
        service.submit(buffer, flightId: "f")  // the next live poll, no new fix
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.count == 1)
        service.submit(buffer + [fix(120)], flightId: "f")
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.last?.fixes.map { $0.at.timeIntervalSince(t0) } == [120])
    }

    @Test @MainActor func theLandingIsKeptUntilSent() async {
        let repo = MockBriefingRepository()
        repo.postLivePositionResults = [.failure(MockError.notStubbed("offline")), .success(result(0))]
        let service = LivePositionService(repository: repo, scope: "test", defaults: scratchDefaults())
        service.submit([], flightId: "f", landed: true)
        await settle(repo, posts: 1)
        service.flushAll()  // back online
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.map(\.landed) == [true, true])
    }

    @Test func onlyATransientFailureIsRetried() {
        typealias R = LivePositionRules
        #expect(R.failureAction(for: APIError.networkError(URLError(.notConnectedToInternet))) == .retry)
        #expect(R.failureAction(for: APIError.serverError(503, nil)) == .retry)
        #expect(R.failureAction(for: APIError.serverError(429, nil)) == .retry)
        #expect(R.failureAction(for: APIError.serverError(409, "briefing unreadable")) == .hold)
        #expect(R.failureAction(for: APIError.serverError(422, nil)) == .drop)
        #expect(R.failureAction(for: APIError.notFound) == .drop)
        #expect(R.failureAction(for: APIError.forbidden("x")) == .drop)
        #expect(R.failureAction(for: APIError.decodingError(URLError(.badServerResponse))) == .sent)
    }

    @Test @MainActor func aRefusedPostIsNotRetried() async {
        let repo = MockBriefingRepository()
        repo.postLivePositionResults = [.failure(APIError.notFound), .success(result(1))]
        let service = LivePositionService(repository: repo, scope: "test", defaults: scratchDefaults())
        service.submit([fix(0)], flightId: "f", landed: true)
        await settle(repo, posts: 1)
        // Dropped: the next poll's flush has nothing to send.
        service.flush(flightId: "f")
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.count == 1)
        // A later fix goes on its own.
        service.submit([fix(900)], flightId: "f")
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.last?.fixes.map { $0.at.timeIntervalSince(t0) } == [900])
        #expect(repo.postedLivePositions.last?.landed == false)
    }

    @Test @MainActor func aConflictHoldsTheQueueUntilSomethingNew() async {
        // 409: the briefing cannot be read until refreshed. Kept, not resent
        // every poll; the next fix tries again, with the held ones.
        let repo = MockBriefingRepository()
        repo.postLivePositionResults = [.failure(APIError.serverError(409, nil)), .success(result(2))]
        let service = LivePositionService(repository: repo, scope: "test", defaults: scratchDefaults())
        service.submit([fix(0)], flightId: "f", landed: true)
        await settle(repo, posts: 1)
        service.flush(flightId: "f")  // a live poll: held, nothing sent
        service.flushAll()            // a reconnect: the same
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.count == 1)
        service.submit([fix(900)], flightId: "f")
        await settle(repo, posts: 2)
        #expect(repo.postedLivePositions.last?.fixes.map { $0.at.timeIntervalSince(t0) } == [0, 900])
        #expect(repo.postedLivePositions.last?.landed == true)
    }

    private func scratchDefaults() -> UserDefaults {
        UserDefaults(suiteName: "LivePositionTests.\(UUID().uuidString)")!
    }

    @Test @MainActor func consentIsPerFlightAndPerUser() {
        let defaults = scratchDefaults()
        let mine = LivePositionService(repository: MockBriefingRepository(), scope: "me", defaults: defaults)
        let theirs = LivePositionService(repository: MockBriefingRepository(), scope: "them", defaults: defaults)
        #expect(!mine.hasConsent(flightId: "a"))
        mine.grantConsent(flightId: "a")
        #expect(mine.hasConsent(flightId: "a"))
        #expect(!mine.hasConsent(flightId: "b"))
        #expect(!theirs.hasConsent(flightId: "a"))
    }

    // #874: Start makes a flight started until an app landing; Start again
    // undoes a landing; both survive a new service (the app relaunched).
    @Test @MainActor func startedUntilLanded() {
        let defaults = scratchDefaults()
        let service = LivePositionService(repository: MockBriefingRepository(), scope: "me", defaults: defaults)
        #expect(!service.isStartedNotLanded(flightId: "a"))
        service.markStarted(flightId: "a")
        #expect(service.isStartedNotLanded(flightId: "a"))
        #expect(service.hasConsent(flightId: "a"))
        service.submit([], flightId: "a", landed: true)
        #expect(!service.isStartedNotLanded(flightId: "a"))
        let relaunched = LivePositionService(repository: MockBriefingRepository(), scope: "me", defaults: defaults)
        #expect(!relaunched.isStartedNotLanded(flightId: "a"))
        #expect(relaunched.hasConsent(flightId: "a"))
        relaunched.markStarted(flightId: "a")
        #expect(relaunched.isStartedNotLanded(flightId: "a"))
        #expect(!relaunched.isStartedNotLanded(flightId: "b"))
    }

    @Test @MainActor func locationToFix() throws {
        let loc = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 50.1, longitude: 1.2), altitude: 1000,
            horizontalAccuracy: 12, verticalAccuracy: 5, course: 92, speed: 61.73, timestamp: t0
        )
        let f = try #require(FlightTrackingService.fix(from: loc, alongNm: 30, crossTrackNm: 1))
        #expect(f.at == t0 && f.hAccuracyM == 12 && f.trackDeg == 92 && f.alongNm == 30)
        #expect(abs((f.gsKt ?? 0) - 120) < 0.1)
        #expect(abs((f.altFt ?? 0) - 3280.84) < 0.1)
        // Invalid speed / course / altitude go as nil; no horizontal fix, no fix.
        let noSpeed = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 50.1, longitude: 1.2), altitude: 0,
            horizontalAccuracy: 12, verticalAccuracy: -1, course: -1, speed: -1, timestamp: t0
        )
        let g = try #require(FlightTrackingService.fix(from: noSpeed, alongNm: nil, crossTrackNm: nil))
        #expect(g.gsKt == nil && g.trackDeg == nil && g.altFt == nil)
        let invalid = CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: 0, longitude: 0), altitude: 0,
            horizontalAccuracy: -1, verticalAccuracy: -1, timestamp: t0
        )
        #expect(FlightTrackingService.fix(from: invalid, alongNm: nil, crossTrackNm: nil) == nil)
    }
}
