import Foundation

struct PirepResponse: Codable, Identifiable, Sendable {
    let id: Int
    let clientUuid: String?
    let submittedAt: String
    let observedAt: String
    let latitude: Double
    let longitude: Double
    let gpsAltitudeFt: Int?
    let reportedAltitudeFt: Int?
    let inCloud: Bool?
    let icingIntensity: String?
    let icingType: String?
    let turbulenceIntensity: String?
    let ceilingMslFt: Int?
    let topsMslFt: Int?
    let topsBasis: String?
    let tempC: Double?
    let windDir: Int?
    let windSpeedKt: Int?
    let remarks: String?
    let aircraftType: String?
    let packId: Int?
    let source: String
    let isOwn: Bool

    /// Best altitude — prefer reported, fall back to GPS.
    var altitude: Int? { reportedAltitudeFt ?? gpsAltitudeFt }

    /// Parsed observation date.
    var observedDate: Date? {
        ISO8601DateFormatter().date(from: observedAt)
    }

    /// Highest severity across icing and turbulence.
    var maxSeverity: String {
        let order = ["none", "trace", "light", "moderate", "severe"]
        var maxIdx = 0
        if let ic = icingIntensity, let i = order.firstIndex(of: ic) { maxIdx = max(maxIdx, i) }
        if let tb = turbulenceIntensity, let i = order.firstIndex(of: tb) { maxIdx = max(maxIdx, i) }
        return order[maxIdx]
    }
}

struct PirepListResponse: Codable, Sendable {
    let items: [PirepResponse]
    let count: Int
}

struct SubmitPirepRequest: Codable, Sendable {
    var clientUuid: String?
    var observedAt: String
    var latitude: Double
    var longitude: Double
    var gpsAltitudeFt: Int?
    var reportedAltitudeFt: Int?
    var inCloud: Bool?
    var icingIntensity: String?
    var icingType: String?
    var turbulenceIntensity: String?
    var ceilingMslFt: Int?
    var topsMslFt: Int?
    var topsBasis: String?
    var tempC: Double?
    var windDir: Int?
    var windSpeedKt: Int?
    var remarks: String?
    var aircraftId: Int?
    var packId: Int?
    var source: String = "inflight"
}
