import SwiftUI

/// The cross-section layer bar (#605; port of the web bar from #591): one chip
/// per `LayerFamily`, each naming what it is currently drawing.
///
/// Two chip kinds, chosen by the layout mode:
///  - `.full` (iPad) reads `Icing · SFIP-NWP`; a tap opens that family's detail
///    row, where the methods are pick-any pills.
///  - `.compact` (iPhone) is a plain on/off with the method decision made for
///    you; a tap switches the family, press-and-hold opens its detail row.
///    Compact is the right default on a phone, not a degraded mode.
///
/// Every tap changes the chart in place — nothing covers it — because flipping a
/// layer on and off to compare is the point, and a modal hides one of the two
/// states being compared.
struct LayerBarView: View {
    enum Style { case full, compact }

    @Bindable var csVM: CrossSectionViewModel
    let style: Style
    /// Wrap onto several lines (portrait, iPad) or run along one scrolling line
    /// (iPhone landscape, where height is the scarce thing).
    var wraps: Bool = true
    @Binding var openFamily: LayerFamily?
    /// Whether press-and-hold may open a compact chip's detail row. Off in
    /// landscape, which has no room for one; methods live in the options sheet.
    var allowsDetail: Bool = true
    /// Adds the advisory-highlight chip — only while a highlight with geometry
    /// for the selected model is active, so it is never a dead control (#374).
    var highlightAvailable: Bool = false
    /// Any chip interaction (retires the layers tip).
    var onInteract: () -> Void = {}

    var body: some View {
        if wraps {
            FlowLayout(spacing: 6) { chips }
        } else {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) { chips }
            }
        }
    }

    @ViewBuilder private var chips: some View {
        ForEach(LayerFamily.visible(in: csVM)) { family in
            switch style {
            case .full: fullChip(family)
            case .compact: compactChip(family)
            }
        }
        if highlightAvailable { highlightChip }
    }

    // MARK: Full chip (iPad)

    private func fullChip(_ family: LayerFamily) -> some View {
        let summary = family.summary(enabledLayers: csVM.enabledLayers, cloudStyle: csVM.cloudStyle)
        let open = openFamily == family
        return Button {
            onInteract()
            withAnimation(.snappy(duration: 0.2)) { openFamily = open ? nil : family }
        } label: {
            HStack(spacing: 5) {
                FamilyDot(family: family, hollow: summary.off)
                Text(family.label).fontWeight(.semibold)
                Text(summary.text)
                    .foregroundStyle(summary.off ? Theme.textMuted : Theme.primary)
                    .lineLimit(1)
                Image(systemName: open ? "chevron.up" : "chevron.down")
                    .font(.caption2)
                    .foregroundStyle(Theme.textMuted)
            }
            .font(.caption)
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(open ? Theme.primary.opacity(0.12) : Theme.surface, in: Capsule())
            .overlay(Capsule().stroke(open ? Theme.primary.opacity(0.6) : Theme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(family.label), \(summary.text)")
        .accessibilityHint(open ? "Closes its methods" : "Opens its methods")
        .accessibilityIdentifier("layerFamily-\(family.rawValue)")
    }

    // MARK: Compact chip (iPhone)

    private func compactChip(_ family: LayerFamily) -> some View {
        let on = csVM.isFamilyOn(family)
        let open = openFamily == family
        return HStack(spacing: 5) {
            FamilyDot(family: family, hollow: !on)
            Text(family.label)
        }
        .font(.caption.weight(on ? .semibold : .regular))
        .foregroundStyle(on ? Theme.text : Theme.textMuted)
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(on ? family.dotColor.opacity(0.18) : Theme.surface, in: Capsule())
        .overlay(Capsule().stroke(
            open ? Theme.primary : (on ? family.dotColor.opacity(0.7) : Theme.border),
            lineWidth: open ? 1.5 : 1))
        .contentShape(Capsule())
        .onTapGesture {
            onInteract()
            csVM.setFamily(family, on: !on)
        }
        .modifier(HoldToOpen(enabled: allowsDetail) {
            onInteract()
            withAnimation(.snappy(duration: 0.2)) { openFamily = open ? nil : family }
        })
        .sensoryFeedback(.selection, trigger: on)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityValue(on ? "On" : "Off")
        .accessibilityHint("Shows or hides \(family.label.lowercased()) on the chart")
        .accessibilityAction(named: "Choose method") {
            if allowsDetail { openFamily = family }
        }
        .accessibilityIdentifier("layerFamily-\(family.rawValue)")
    }

    // MARK: Highlight chip

    /// Show/hide for the active advisory highlight. A visibility control, never a
    /// lens edit: it neither drops the Focus lens nor clears the highlight.
    private var highlightChip: some View {
        let on = csVM.highlightVisible
        return Button {
            csVM.setHighlightVisible(!on)
        } label: {
            HStack(spacing: 5) {
                Image(systemName: on ? "circle.lefthalf.filled" : "circle.dashed")
                Text("Highlight")
            }
            .font(.caption.weight(on ? .semibold : .regular))
            .foregroundStyle(on ? Theme.primary : Theme.textMuted)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(on ? Theme.primary.opacity(0.12) : Theme.surface, in: Capsule())
            .overlay(Capsule().stroke(on ? Theme.primary.opacity(0.6) : Theme.border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityValue(on ? "On" : "Off")
        .accessibilityHint("Dims the chart outside the advisory's flagged areas")
        .accessibilityIdentifier("layerHighlightToggle")
    }
}

/// Press-and-hold to open a compact chip's detail row. Attached only where a
/// detail row can open, so a long hold in landscape is not silently swallowed.
private struct HoldToOpen: ViewModifier {
    let enabled: Bool
    let action: () -> Void

    func body(content: Content) -> some View {
        if enabled {
            content.onLongPressGesture(minimumDuration: 0.35, perform: action)
        } else {
            content
        }
    }
}

/// The family's colour dot — hollow when nothing in the family is drawn.
struct FamilyDot: View {
    let family: LayerFamily
    var hollow = false

    var body: some View {
        Circle()
            .fill(hollow ? Color.clear : family.dotColor)
            .overlay(Circle().stroke(family.dotColor, lineWidth: hollow ? 1.5 : 0))
            .frame(width: 8, height: 8)
    }
}

extension LayerFamily {
    /// The families the loaded pack has something for. Observed disappears when
    /// the pack carries no observed payload (a D-1+ pack, the collector off),
    /// rather than offering a chip that opens an empty row.
    static func visible(in csVM: CrossSectionViewModel) -> [LayerFamily] {
        allCases.filter { $0 != .observed || csVM.vizData?.observed != nil }
    }
}
