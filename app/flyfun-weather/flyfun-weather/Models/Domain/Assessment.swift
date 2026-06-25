import SwiftUI

/// Route-level assessment severity.
enum Assessment: String, Codable, CaseIterable {
    case green
    case amber
    case red
    case unavailable

    var color: Color {
        switch self {
        case .green: .green
        case .amber: .orange
        case .red: .red
        case .unavailable: .gray
        }
    }

    var label: String {
        rawValue.uppercased()
    }
}

/// Sort rank for an assessment status string (red > amber > green > other).
/// Shared so advisory lists order severity identically everywhere.
func severityRank(_ status: String) -> Int {
    switch status {
    case "red": 3
    case "amber": 2
    case "green": 1
    default: 0
    }
}
