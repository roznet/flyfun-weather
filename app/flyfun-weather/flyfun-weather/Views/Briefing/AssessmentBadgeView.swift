import SwiftUI

/// Colored badge showing a GREEN / AMBER / RED assessment.
struct AssessmentBadgeView: View {
    let assessment: Assessment

    var body: some View {
        Text(assessment.label)
            .font(.caption.bold())
            .foregroundStyle(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(assessment.color, in: Capsule())
    }
}

/// Badge from a raw string (e.g. API response).
struct AssessmentStringBadge: View {
    let status: String

    private var assessment: Assessment {
        Assessment(rawValue: status.lowercased()) ?? .unavailable
    }

    var body: some View {
        AssessmentBadgeView(assessment: assessment)
    }
}
