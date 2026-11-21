import SwiftUI

/// The **Debrief** section (#1024), at the top of the Observed tab after the
/// live window: the server's headline, the flown / cancelled tap, then the
/// report's questions (or the cancel reasons), a note, and the detail lines
/// folded. Every word but the chrome is the server's (#1023); the web twin is
/// `web/ts/components/debrief-section.ts`.
struct DebriefSectionView: View {
    @Bindable var viewModel: DebriefSectionViewModel
    @AppStorage("debriefDetailsExpanded") private var detailsExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingM) {
            Label("Debrief", systemImage: "text.badge.checkmark")
                .font(.headline)
                .foregroundStyle(Theme.text)
            if let headline = viewModel.report?.headline {
                Text(headline)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("debriefHeadline")
            }
            decisionRow
            switch viewModel.decision {
            case .flown:
                questions
                noteField
            case .cancelled:
                reasons
                noteField
            case nil:
                EmptyView()
            }
            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(Theme.red)
                    .accessibilityIdentifier("debriefError")
            }
            if !viewModel.detailLines.isEmpty {
                DisclosureGroup(isExpanded: $detailsExpanded) {
                    VStack(alignment: .leading, spacing: Theme.spacingS) {
                        ForEach(Array(viewModel.detailLines.enumerated()), id: \.offset) { _, line in
                            Text(line.text)
                                .font(.footnote)
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.top, Theme.spacingXS)
                } label: {
                    Text("Details")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textMuted)
                }
                .accessibilityIdentifier("debriefDetails")
            }
        }
        .disabled(viewModel.isBusy)
        .opacity(viewModel.isBusy ? 0.6 : 1)
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .padding(.horizontal, Theme.cardPadding)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("debriefSection")
    }

    private var decisionRow: some View {
        HStack(spacing: Theme.spacingS) {
            decisionButton(.flown, "I flew it", id: "debriefFlown")
            decisionButton(.cancelled, "I cancelled", id: "debriefCancelled")
        }
    }

    private func decisionButton(_ decision: DebriefSectionViewModel.Decision, _ title: String, id: String) -> some View {
        let selected = viewModel.decision == decision
        return Button {
            Task { await viewModel.choose(decision) }
        } label: {
            Text(title)
                .font(.subheadline.weight(.medium))
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .tint(selected ? Theme.primary : .gray)
        .accessibilityAddTraits(selected ? [.isSelected] : [])
        .accessibilityIdentifier(id)
    }

    @ViewBuilder
    private var questions: some View {
        if viewModel.questions.isEmpty {
            Text("Nothing to ask about this flight: the instruments covered it. Thanks for flying it.")
                .font(.subheadline)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        ForEach(viewModel.questions) { question in
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                Text(question.text)
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                DebriefChips(items: question.options.map { DebriefChip(id: $0.id, label: $0.label) },
                             isSelected: { question.answer?.answer == $0 },
                             idPrefix: "debriefAnswer-\(question.id)") { option in
                    Task { await viewModel.answer(question, option: option) }
                }
                if question.avoidable {
                    Toggle(isOn: Binding(
                        get: { viewModel.isAvoided(question) },
                        set: { value in Task { await viewModel.setAvoided(question, value) } }
                    )) {
                        Text("I changed level or route to stay out of it")
                            .font(.footnote)
                            .foregroundStyle(Theme.textMuted)
                    }
                    .accessibilityIdentifier("debriefAvoided-\(question.id)")
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("debriefQuestion-\(question.id)")
        }
    }

    private var reasons: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("What made you cancel?")
                .font(.subheadline)
                .foregroundStyle(Theme.text)
            DebriefChips(items: viewModel.taxonomy.tags.map { DebriefChip(id: $0.id, label: $0.label) },
                         isSelected: { viewModel.selectedReasons.contains($0) },
                         idPrefix: "debriefReason") { tag in
                Task { await viewModel.toggleReason(tag) }
            }
        }
    }

    private var noteField: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            TextField("Anything else? (optional)", text: $viewModel.note, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("debriefNote")
            HStack {
                Text("\(viewModel.taxonomy.noteMaxLength - viewModel.note.count)")
                    .font(.caption2)
                    .foregroundStyle(viewModel.noteTooLong ? Theme.red : Theme.textMuted)
                Spacer()
                Button("Save note") { Task { await viewModel.saveNote() } }
                    .font(.footnote.weight(.medium))
                    .disabled(viewModel.noteTooLong)
                    .accessibilityIdentifier("debriefNoteSave")
            }
        }
    }
}

private struct DebriefChip: Identifiable {
    let id: String
    let label: String
}

/// A wrapping row of one-tap chips (question options, cancel reasons).
private struct DebriefChips: View {
    let items: [DebriefChip]
    let isSelected: (String) -> Bool
    let idPrefix: String
    let tap: (String) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(items) { item in
                let selected = isSelected(item.id)
                Button {
                    tap(item.id)
                } label: {
                    Text(item.label)
                        .font(.caption.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
                .buttonStyle(.bordered)
                .tint(selected ? Theme.primary : .gray)
                .accessibilityAddTraits(selected ? [.isSelected] : [])
                .accessibilityIdentifier("\(idPrefix)-\(item.id)")
            }
        }
    }
}

/// The briefing's debrief card on the Advisory tab (owner, past flight): the
/// stored decision and a link to the Debrief section on the Observed tab
/// (#1024), where the questions live.
struct DebriefCard: View {
    let debrief: DebriefResponse?
    let questionsOpen: Int
    let onOpen: () -> Void
    @Environment(AppState.self) private var appState

    private var taxonomy: DebriefTaxonomy { appState.helpCatalog.debriefTaxonomy }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            HStack {
                Label("Debrief", systemImage: "text.badge.checkmark")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Spacer()
                Button(questionsOpen > 0 ? "Answer" : "Open", action: onOpen)
                    .font(.subheadline.weight(.medium))
                    .accessibilityIdentifier("debriefCardOpen")
            }
            if questionsOpen > 0 {
                Text(questionsOpen == 1 ? "One quick question about this flight."
                                        : "\(questionsOpen) quick questions about this flight.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
            }
            if let debrief {
                summary(debrief)
            } else if questionsOpen == 0 {
                Text("Did you fly it? The debrief compares what was forecast with what was seen.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cornerRadius).stroke(Theme.border, lineWidth: 0.5))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("debriefCard")
    }

    @ViewBuilder
    private func summary(_ debrief: DebriefResponse) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            decisionPill(debrief.decision)
            if debrief.decision == "cancelled", !debrief.reasons.isEmpty {
                Text("Because: " + debrief.reasons.map { taxonomy.tag($0)?.label ?? $0 }.joined(separator: ", "))
                    .font(.subheadline)
                    .foregroundStyle(Theme.text)
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
}
