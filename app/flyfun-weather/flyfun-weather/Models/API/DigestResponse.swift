import Foundation

/// Structured LLM digest JSON response.
struct DigestResponse: Codable, Sendable {
    let assessment: String?
    let assessmentReason: String?
    let synoptic: String?
    let winds: String?
    let icing: String?
    let turbulence: String?
    let precipitation: String?
    let visibility: String?
    let trend: String?
    let watchItems: [String]?
    let recommendations: String?
    let modelAgreement: String?

    /// All non-nil sections for display.
    var sections: [(title: String, text: String)] {
        var result: [(String, String)] = []
        if let synoptic { result.append(("Synoptic Overview", synoptic)) }
        if let winds { result.append(("Winds", winds)) }
        if let icing { result.append(("Icing", icing)) }
        if let turbulence { result.append(("Turbulence", turbulence)) }
        if let precipitation { result.append(("Precipitation", precipitation)) }
        if let visibility { result.append(("Visibility", visibility)) }
        if let trend { result.append(("Trend", trend)) }
        if let modelAgreement { result.append(("Model Agreement", modelAgreement)) }
        if let recommendations { result.append(("Recommendations", recommendations)) }
        return result
    }
}
