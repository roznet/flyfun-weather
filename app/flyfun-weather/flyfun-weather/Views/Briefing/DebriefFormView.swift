import SwiftUI

/// Post-flight debrief sheet (past owned flights). A decision segmented control,
/// then — per decision — cancel-reason chips or per-category outcome grading,
/// plus a note. Mirrors the web debrief form's choices; reuses the PIREP sheet's
/// Form + submit-state shape.
struct DebriefFormView: View {
    @Bindable var viewModel: DebriefViewModel
    /// Called with the saved debrief on save, or `nil` on delete, so the caller
    /// can refresh its card without a round-trip.
    var onFinished: (DebriefResponse?) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                decisionSection
                switch viewModel.decision {
                case "cancelled": cancelledSection
                case "monitoring": monitoringSection
                default: flownSection
                }
                noteSection
                submitSection
            }
            .navigationTitle(viewModel.isEditing ? "Edit Debrief" : "Add Debrief")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    // MARK: - Decision

    private var decisionSection: some View {
        Section("What happened?") {
            Picker("Decision", selection: $viewModel.decision) {
                ForEach(viewModel.taxonomy.decisions) { option in
                    Text(option.label).tag(option.id)
                }
            }
            .pickerStyle(.segmented)
        }
    }

    // MARK: - Cancelled → reason chips

    private var cancelledSection: some View {
        Section {
            FlowChips(
                tags: viewModel.reasonTags,
                isSelected: { viewModel.selectedReasons.contains($0) },
                toggle: { viewModel.toggleReason($0) }
            )
        } header: {
            Text("Why did you cancel?")
        } footer: {
            Text("Tap every condition that contributed. These feed forecast calibration.")
        }
    }

    // MARK: - Monitoring

    private var monitoringSection: some View {
        Section {
            Text("This flight was set up to watch the weather, not to be flown. It won't count toward flown/cancelled stats — add a note if you want to remember why.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Flown → per-category outcomes

    @ViewBuilder
    private var flownSection: some View {
        if viewModel.outcomeTags.isEmpty {
            Section {
                Text("No advisories were flagged on this briefing, so there's nothing to grade. Add a note if the weather differed from what you expected.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        } else {
            Section {
                ForEach(viewModel.outcomeTags) { tag in
                    outcomeRow(tag)
                }
            } header: {
                Text("How did the flagged conditions compare?")
            } footer: {
                Text("Everything defaults to “as forecast” — flip only what was different.")
            }
        }
    }

    private func outcomeRow(_ tag: DebriefTaxonomy.TagOption) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(tag.label)
                .font(.subheadline.weight(.medium))
            HStack(spacing: 6) {
                ForEach(viewModel.taxonomy.outcomeValues) { value in
                    let selected = (viewModel.outcomes[tag.id] ?? "consistent") == value.id
                    Button(value.label) {
                        viewModel.setOutcome(tag.id, value.id)
                    }
                    .buttonStyle(.bordered)
                    .tint(selected ? Self.outcomeColor(value.id) : .gray)
                    .font(.caption)
                }
            }
        }
        .padding(.vertical, 2)
    }

    /// Neutral-to-signal colouring: "as forecast" is neutral, "worse" reads red,
    /// "better" green — never a go/no-go verdict, just a delta.
    private static func outcomeColor(_ value: String) -> Color {
        switch value {
        case "worse": .red
        case "better": .green
        default: .blue
        }
    }

    // MARK: - Note

    private var noteSection: some View {
        Section {
            TextField("What happened? (optional)", text: $viewModel.note, axis: .vertical)
                .lineLimit(2...5)
        } header: {
            Text("Note")
        } footer: {
            HStack {
                Spacer()
                Text("\(viewModel.noteRemaining)")
                    .font(.caption2)
                    .foregroundStyle(viewModel.noteTooLong ? .red : .secondary)
            }
        }
    }

    // MARK: - Submit

    private var submitSection: some View {
        Section {
            switch viewModel.submitState {
            case .idle, .error:
                Button {
                    Task {
                        await viewModel.submit()
                        if case .loaded(let saved) = viewModel.submitState {
                            onFinished(saved)
                            dismiss()
                        }
                    }
                } label: {
                    Label(viewModel.isEditing ? "Save Changes" : "Save Debrief",
                          systemImage: "checkmark.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!viewModel.canSubmit)

                if let error = viewModel.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                }

                if viewModel.isEditing {
                    Button(role: .destructive) {
                        Task {
                            await viewModel.delete()
                            if case .idle = viewModel.submitState {
                                onFinished(nil)
                                dismiss()
                            }
                        }
                    } label: {
                        Label("Delete Debrief", systemImage: "trash")
                            .frame(maxWidth: .infinity)
                    }
                }

            case .loading:
                ProgressView("Saving…")
                    .frame(maxWidth: .infinity)

            case .loaded:
                Label("Debrief saved", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity)
            }
        } footer: {
            Text("Your debrief is stored to improve future forecasts for flights like this one.")
                .font(.caption2)
        }
    }
}

/// The debrief card shown below the hero on a past owned flight: an "Add
/// debrief" prompt when none exists, or a read-only summary + "Edit" when it
/// does. Tapping opens `DebriefFormView`.
struct DebriefCard: View {
    let debrief: DebriefResponse?
    let onEdit: () -> Void
    @Environment(AppState.self) private var appState

    private var taxonomy: DebriefTaxonomy { appState.helpCatalog.debriefTaxonomy }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack {
                Label("Debrief", systemImage: "text.badge.checkmark")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Spacer()
                Button(debrief == nil ? "Add" : "Edit", action: onEdit)
                    .font(.subheadline.weight(.medium))
            }
            if let debrief {
                summary(debrief)
            } else {
                Text("How did this flight go? A quick debrief helps calibrate future forecasts.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
    }

    @ViewBuilder
    private func summary(_ debrief: DebriefResponse) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            decisionPill(debrief.decision)
            if debrief.decision == "cancelled", !debrief.reasons.isEmpty {
                Text("Because: " + debrief.reasons.map(tagLabel).joined(separator: ", "))
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
            } else if debrief.decision == "flown" {
                // Non-consistent outcomes in taxonomy order (matches the chips /
                // outcome rows), not alphabetical by tag id.
                let diffs = taxonomy.tags.compactMap { tag -> (id: String, value: String)? in
                    guard let value = debrief.outcomes[tag.id], value != "consistent" else { return nil }
                    return (tag.id, value)
                }
                if diffs.isEmpty {
                    Text("Conditions were as forecast.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                } else {
                    ForEach(diffs, id: \.id) { tagId, value in
                        Text("\(tagLabel(tagId)): \(outcomeLabel(value))")
                            .font(.subheadline)
                            .foregroundStyle(value == "worse" ? Theme.red : Theme.text)
                    }
                }
            }
            if let note = debrief.note, !note.isEmpty {
                Text(note)
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func decisionPill(_ decision: String) -> some View {
        let (label, tint): (String, Color) = switch decision {
        case "cancelled": ("Cancelled", Theme.red)
        case "monitoring": ("Monitor only", Theme.textMuted)
        default: ("Flown", Theme.green)
        }
        return Text(label)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(tint.opacity(0.15), in: Capsule())
    }

    private func tagLabel(_ id: String) -> String { taxonomy.tag(id)?.label ?? id }
    private func outcomeLabel(_ id: String) -> String {
        taxonomy.outcomeValues.first { $0.id == id }?.label ?? id
    }
}

/// A simple wrapping row of selectable tag chips (cancel reasons). Uses a
/// `LazyVGrid` with an adaptive column so chips flow onto multiple lines.
private struct FlowChips: View {
    let tags: [DebriefTaxonomy.TagOption]
    let isSelected: (String) -> Bool
    let toggle: (String) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 84), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(tags) { tag in
                let selected = isSelected(tag.id)
                Button {
                    toggle(tag.id)
                } label: {
                    Text(tag.label)
                        .font(.caption.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .lineLimit(1)
                }
                .buttonStyle(.bordered)
                .tint(selected ? .accentColor : .gray)
                .accessibilityAddTraits(selected ? [.isSelected] : [])
            }
        }
        .padding(.vertical, 4)
    }
}
