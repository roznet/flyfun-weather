import Foundation

/// One GPS fix the app sends the server (#761), `POST /api/flights/{id}/live/position`.
/// Keys go out snake_case (`JSONEncoder.weatherBrief`): `alt_ft`, `gs_kt`,
/// `track_deg`, `h_accuracy_m`, `along_nm`, `cross_track_nm`. Optionals are
/// omitted when nil (the device has no valid speed, course or altitude).
/// `alongNm` / `crossTrackNm` are the device's own projection on the route:
/// the server re-projects and keeps these as a cross-check only.
nonisolated struct LivePositionFix: Encodable, Equatable, Sendable {
    let at: Date
    let lat: Double
    let lon: Double
    let altFt: Double?
    let gsKt: Double?
    let trackDeg: Double?
    let hAccuracyM: Double
    let alongNm: Double?
    let crossTrackNm: Double?
}

/// The newest fix plus up to two recent ones, and `landed` when the app's
/// landing auto-stop fired.
nonisolated struct LivePositionRequest: Encodable, Sendable {
    let fixes: [LivePositionFix]
    let landed: Bool
}

/// What the server kept. The progress block it also returns is read from
/// `/live` (#760 client display), so only the counts are decoded here.
nonisolated struct LivePositionResult: Decodable, Sendable {
    let accepted: Int
    let landedRecorded: Bool
}
