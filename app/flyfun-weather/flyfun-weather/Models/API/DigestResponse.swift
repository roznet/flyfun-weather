import Foundation

/// Structured LLM digest JSON response.
/// Note: The server may return some fields as either strings or arrays depending on the LLM output.
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
    let watchItems: FlexibleStringArray?
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

    /// Watch items as an array of strings.
    var watchItemsList: [String] {
        watchItems?.values ?? []
    }
}

/// Decodes a JSON value that could be either a string or an array of strings.
struct FlexibleStringArray: Codable, Sendable {
    let values: [String]

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let array = try? container.decode([String].self) {
            values = array
        } else if let string = try? container.decode(String.self) {
            // Split by newlines or treat as single item
            values = string.components(separatedBy: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        } else {
            values = []
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(values)
    }
}
