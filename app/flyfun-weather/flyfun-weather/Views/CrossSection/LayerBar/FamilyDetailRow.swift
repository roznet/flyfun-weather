import SwiftUI

/// The detail row for the open family (#605, web `familyDetailHtml`): its pills
/// plus the family's hint line and its About button.
///
/// One line when it fits — on iPad it then takes exactly the slot the caption
/// line held, so opening a family replaces a line rather than adding one and the
/// chart never moves — else stacked: header, wrapped pills, hint. Substitution
/// notes need the room, so they force the stacked form.
struct FamilyDetailRow: View {
    @Bindable var csVM: CrossSectionViewModel
    let family: LayerFamily
    var onClose: () -> Void

    var body: some View {
        let notes = FamilyPills.substitutionNotes(csVM, family: family)
        Group {
            if notes.isEmpty {
                ViewThatFits(in: .horizontal) {
                    inline
                    stacked(notes: notes)
                }
            } else {
                stacked(notes: notes)
            }
        }
        .accessibilityIdentifier("layerFamilyDetail-\(family.rawValue)")
    }

    private var title: some View {
        HStack(spacing: 5) {
            FamilyDot(family: family)
            Text(family.label).font(.subheadline.weight(.semibold))
        }
        .fixedSize()
    }

    private var closeButton: some View {
        Button(action: onClose) {
            Image(systemName: "xmark.circle.fill")
                .font(.body)
                .foregroundStyle(Theme.textMuted)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("Close \(family.label.lowercased()) methods")
    }

    private var inline: some View {
        HStack(spacing: Theme.spacingM) {
            title
            FamilyPills(csVM: csVM, family: family, wraps: false)
            // Ideal width 0, so the hint never decides whether the row fits — it
            // takes what is left and truncates.
            Text(family.hint)
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .lineLimit(1)
                .frame(minWidth: 0, idealWidth: 0, maxWidth: .infinity, alignment: .leading)
            FamilyAboutButton(family: family).fixedSize()
            closeButton
        }
    }

    private func stacked(notes: [String]) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack {
                title
                Spacer(minLength: Theme.spacingS)
                FamilyAboutButton(family: family)
                closeButton
            }
            FamilyPills(csVM: csVM, family: family)
            Text(family.hint)
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(notes, id: \.self) { note in
                Label(note, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .padding(Theme.spacingM)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.border, lineWidth: 0.5))
    }
}
