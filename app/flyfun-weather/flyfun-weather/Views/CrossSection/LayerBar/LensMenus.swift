import SwiftUI

/// Focus — what am I looking for (the advisory lenses). Two menus, because there
/// are two questions, and they compose rather than compete: see
/// `CrossSectionViewModel`.
struct FocusMenu: View {
    @Bindable var csVM: CrossSectionViewModel

    var body: some View {
        Menu {
            Picker("Focus", selection: Binding(
                get: { csVM.activeAdvisoryPreset ?? "" },
                set: { id in
                    if id.isEmpty {
                        csVM.clearAdvisoryPreset()
                    } else if let lens = CrossSectionPresets.advisory[id] {
                        csVM.applyAdvisoryPreset(lens)
                    }
                }
            )) {
                Text("Custom").tag("")
                ForEach(CrossSectionPresets.advisoryList) { Text($0.label).tag($0.id) }
            }
        } label: {
            LensMenuLabel(
                title: "Focus",
                value: csVM.activeAdvisoryPreset.flatMap { CrossSectionPresets.advisory[$0]?.label } ?? "Custom",
                systemImage: "scope")
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("crossSectionFocusMenu")
    }
}

/// Emulate — whose conventions: GRAMET / Windy / ForeFlight, or FlyFun for the
/// methods this briefing graded with. Picks the methods and the look.
struct EmulateMenu: View {
    @Bindable var csVM: CrossSectionViewModel

    var body: some View {
        Menu {
            Picker("Emulate", selection: Binding(
                get: { csVM.activeEmulation ?? "" },
                set: { csVM.applyEmulation($0.isEmpty ? nil : $0) }
            )) {
                Text(CrossSectionPresets.ownConventionsLabel).tag("")
                ForEach(CrossSectionPresets.all) { Text($0.label).tag($0.id) }
            }
        } label: {
            LensMenuLabel(
                title: "Emulate",
                value: CrossSectionPresets.emulation(csVM.activeEmulation)?.label
                    ?? CrossSectionPresets.ownConventionsLabel,
                systemImage: "paintpalette")
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("crossSectionEmulateMenu")
    }
}

/// Capsule label shared by the lens menus, matching `ModelSelectorView`.
struct LensMenuLabel: View {
    let title: String
    let value: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage).font(.caption)
            Text(title).font(.caption).foregroundStyle(Theme.textMuted)
            Text(value).font(.caption.bold()).lineLimit(1)
            Image(systemName: "chevron.down").font(.caption2)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.accentColor.opacity(0.1))
        .clipShape(Capsule())
    }
}
