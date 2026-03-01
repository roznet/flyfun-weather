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
