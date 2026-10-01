import Foundation
import Testing
import FMDB
import RZFlight
@testable import flyfun_weather

@MainActor
@Suite struct AirportDatabaseInstallTests {

    /// A minimal `airports` table — only the columns `Airport(res:)` needs.
    private func makeDBData(_ icaos: [String]) throws -> Data {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("airports-src-\(UUID().uuidString).db")
        defer { try? FileManager.default.removeItem(at: url) }
        let db = FMDatabase(path: url.path)
        #expect(db.open())
        try db.executeUpdate(
            "CREATE TABLE airports(icao_code TEXT, name TEXT, type TEXT, latitude_deg REAL, longitude_deg REAL, elevation_ft REAL)",
            values: nil)
        for icao in icaos {
            try db.executeUpdate(
                "INSERT INTO airports VALUES (?, ?, 'small_airport', 50.0, 7.0, 300)",
                values: [icao, "\(icao) Field"])
        }
        db.close()
        return try Data(contentsOf: url)
    }

    /// An update replaces the file the current handle has open; the old handle
    /// must be closed first, and the new DB must be the one served afterwards.
    @Test func installOverAnOpenDatabaseServesTheNewOne() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("airports-db-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let database = AirportDatabase(cacheURL: dir.appendingPathComponent("airports.db"))

        await database.install(data: try makeDBData(["ZZAA"]), etag: nil)
        #expect(database.isLoaded)
        #expect(database.airport(icao: "ZZAA") != nil)

        await database.install(data: try makeDBData(["ZZBB"]), etag: nil)
        #expect(database.isLoaded)
        #expect(database.airport(icao: "ZZBB") != nil)
        #expect(database.airport(icao: "ZZAA") == nil)
        #expect(database.search(needle: "ZZB").first?.icao == "ZZBB")
        // The staging file is consumed, not left beside the cache.
        #expect(!FileManager.default.fileExists(
            atPath: dir.appendingPathComponent("airports.db.download").path))
    }

    /// A cold-launch `loadCached()` racing the update is awaited, not cut off.
    @Test func installWaitsForAnInFlightCachedLoad() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("airports-db-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("airports.db")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try makeDBData(["ZZAA"]).write(to: url)

        let database = AirportDatabase(cacheURL: url)
        database.loadCached()
        await database.install(data: try makeDBData(["ZZBB"]), etag: nil)
        #expect(database.isLoaded)
        #expect(database.airport(icao: "ZZBB") != nil)
    }
}
