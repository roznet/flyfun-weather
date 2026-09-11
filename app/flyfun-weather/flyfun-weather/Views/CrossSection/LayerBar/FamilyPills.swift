import SwiftUI

/// The pick-any pills for one family (#605, web `familyDetailHtml`): per group
/// a `None` pill (groups of more than one layer) and a pill per layer; clouds get
/// per-source pills plus one shared style menu. Unavailable layers stay visible
/// and struck through — knowing a method exists but needs another model beats a
/// clean row — and a DD layer standing in for missing NWP reads on-but-dimmed,
/// so the pills always match what is drawn.
struct FamilyPills: View {
    @Bindable var csVM: CrossSectionViewModel
    let family: LayerFamily
    /// Wrap onto several lines, or run along one (the one-line iPad detail row).
    var wraps: Bool = true

    var body: some View {
        if wraps {
            FlowLayout(spacing: 6) { pills }
        } else {
            HStack(spacing: 6) { pills }.fixedSize()
        }
    }

    @ViewBuilder private var pills: some View {
        ForEach(family.groups, id: \.self) { group in
            if family.groups.count > 1 {
                Text(group.label.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.textMuted)
                    .padding(.vertical, 7)
            }
            if group == .clouds {
                cloudPills
            } else {
                let ids = CrossSectionLayer.layerIds(in: group)
                if ids.count > 1 {
                    PillButton(label: "None", isOn: !ids.contains { csVM.isLayerOn($0) }) {
                        csVM.clearGroup(group)
                    }
                }
                ForEach(ids, id: \.self) { layerPill($0) }
            }
        }
    }

    private func layerPill(_ id: String) -> some View {
        let substituted = csVM.substitutedLayers.contains(id)
        let unavailable = csVM.unavailableLayers.contains(id) && !substituted
        return PillButton(
            label: CrossSectionLayer.label(id),
            isOn: substituted || (!unavailable && csVM.isLayerOn(id)),
            unavailable: unavailable,
            substituted: substituted
        ) {
            csVM.toggleLayer(id)
        }
    }

    @ViewBuilder private var cloudPills: some View {
        let style = csVM.cloudStyle
        ForEach(CloudSource.allCases) { source in
            let id = CrossSectionPresets.cloudLayerId(source: source, style: style)
            let substituted = csVM.substitutedLayers.contains(id)
            let unavailable = source == .nwp && csVM.unavailableLayers.contains(NwpFallback.nwpCloudsSignal)
            PillButton(
                label: source.label,
                isOn: substituted || (!unavailable && csVM.isCloudSourceOn(source)),
                unavailable: unavailable,
                substituted: substituted
            ) {
                csVM.toggleCloudSource(source)
            }
            .accessibilityHint(source.hint)
        }
        Menu {
            Picker("Style", selection: Binding(
                get: { csVM.cloudStyle },
                set: { csVM.setCloudStyle($0) }
            )) {
                ForEach(CloudStyle.allCases) { Text($0.label).tag($0) }
            }
        } label: {
            HStack(spacing: 3) {
                Text(style.label)
                Image(systemName: "chevron.up.chevron.down").font(.caption2)
            }
            .font(.caption)
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Theme.bg, in: Capsule())
            .overlay(Capsule().stroke(Theme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Cloud style, \(style.label)")
    }
}

extension FamilyPills {
    /// "No NWP data for this model — showing DD instead", for each layer the user
    /// wants that this model cannot draw, so a substituted chart is never a
    /// mystery. Mirrors web's substitution tooltip.
    static func substitutionNotes(_ csVM: CrossSectionViewModel, family: LayerFamily) -> [String] {
        var notes: [String] = []
        if family == .clouds, csVM.isCloudSourceOn(.nwp),
           csVM.unavailableLayers.contains(NwpFallback.nwpCloudsSignal) {
            notes.append("No native NWP cloud for this model at this range — showing DD clouds instead.")
        }
        for id in family.layerIds where CrossSectionPresets.parseCloudLayerId(id) == nil {
            guard csVM.isLayerOn(id), csVM.unavailableLayers.contains(id),
                  let substitute = NwpFallback.ddSubstituteId(for: id) else { continue }
            notes.append("No \(CrossSectionLayer.label(id)) data for this model — showing \(CrossSectionLayer.label(substitute)) instead.")
        }
        return notes
    }
}

/// One rounded pill. Gapped and rounded because every family is pick-ANY: a
/// joined segmented control would say "pick one", which is now only true of
/// the lens selectors and the cloud style.
struct PillButton: View {
    let label: String
    let isOn: Bool
    var unavailable = false
    var substituted = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label)
                .strikethrough(unavailable)
                .font(.caption.weight(isOn ? .semibold : .regular))
                .foregroundStyle(isOn ? Theme.primary : Theme.text)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(isOn ? Theme.primary.opacity(0.14) : Theme.bg, in: Capsule())
                .overlay(Capsule().stroke(isOn ? Theme.primary.opacity(0.55) : Theme.border, lineWidth: 1))
                .opacity(unavailable ? 0.45 : (substituted ? 0.6 : 1))
        }
        .buttonStyle(.borderless)
        .disabled(unavailable)
        .accessibilityAddTraits(isOn ? .isSelected : [])
        .accessibilityValue(unavailable
            ? "Not available for this model"
            : (substituted ? "Showing DD — NWP not available for this model" : ""))
    }
}
