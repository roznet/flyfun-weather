import SwiftUI

/// Shared coordinate-space name for scroll-spy offset reporting. Kept on a
/// non-generic type so `spyAnchor` can reference it without `ScrollSpyScroll`'s
/// generic parameter.
private enum SpyConstants {
    static let coordSpace = "spyScroll"
}

/// One registrable section in a scroll-spy tab (#310 item 5).
struct SpySection: Identifiable, Equatable {
    let id: String
    let title: String

    init(_ id: String, _ title: String) {
        self.id = id
        self.title = title
    }
}

/// Reports each anchored section's top offset within the scroll coordinate
/// space so `ScrollSpyScroll` can decide which section is currently "active".
private struct SpyOffsetKey: PreferenceKey {
    static let defaultValue: [String: CGFloat] = [:]
    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue()) { _, new in new }
    }
}

extension View {
    /// Mark a scroll target and report its top offset for scroll-spy tracking.
    /// Pair with `ScrollSpyScroll`; the `id` must match a `SpySection.id`.
    ///
    /// `.id()` is applied LAST (outermost): `ScrollViewReader.scrollTo` only
    /// finds a target whose `.id` is on the outermost layout view, so applying it
    /// before `.background(GeometryReader…)` left tap-to-scroll a silent no-op
    /// (scroll-position tracking still worked, masking it). (#3)
    func spyAnchor(_ id: String) -> some View {
        self
            .background(
                GeometryReader { geo in
                    Color.clear.preference(
                        key: SpyOffsetKey.self,
                        value: [id: geo.frame(in: .named(SpyConstants.coordSpace)).minY]
                    )
                }
            )
            .id(id)
    }
}

/// Sticky horizontally-scrollable pill bar that mirrors the web
/// `briefing-sidebar` scroll-spy: highlights the active section and jumps to a
/// section on tap. Compact by design so it works as a top band on iPhone too.
struct SectionSpyBar: View {
    let sections: [SpySection]
    let active: String
    let onTap: (String) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Theme.spacingS) {
                    ForEach(sections) { section in
                        let isActive = section.id == active
                        Button { onTap(section.id) } label: {
                            Text(section.title)
                                .font(.caption.weight(isActive ? .semibold : .regular))
                                .foregroundStyle(isActive ? Theme.primary : Theme.textMuted)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(
                                    isActive ? Theme.primary.opacity(0.14) : Theme.surface,
                                    in: Capsule()
                                )
                                .overlay(
                                    Capsule().stroke(Theme.border, lineWidth: isActive ? 0 : 0.5)
                                )
                        }
                        .buttonStyle(.plain)
                        .id("pill-\(section.id)")
                    }
                }
                .padding(.horizontal, Theme.cardPadding)
                .padding(.vertical, Theme.spacingS)
            }
            .onChange(of: active) { _, newValue in
                // Keep the active pill in view as the user scrolls content.
                withAnimation(.easeInOut(duration: 0.2)) {
                    proxy.scrollTo("pill-\(newValue)", anchor: .center)
                }
            }
        }
        .background(.regularMaterial)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 0.5)
        }
    }
}

/// A scroll view with a sticky scroll-spy pill bar pinned above it. Content
/// marks its sections with `.spyAnchor(id)`; the bar highlights whichever is
/// nearest the top and taps scroll to it. The bar is suppressed when there is
/// only one section, so single-concept tabs pay nothing.
struct ScrollSpyScroll<Content: View>: View {
    static var coordSpace: String { SpyConstants.coordSpace }

    let sections: [SpySection]
    @ViewBuilder var content: () -> Content

    @State private var active: String = ""

    var body: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                if sections.count > 1 {
                    SectionSpyBar(sections: sections, active: active.isEmpty ? (sections.first?.id ?? "") : active) { id in
                        withAnimation(.easeInOut(duration: 0.25)) {
                            proxy.scrollTo(id, anchor: .top)
                        }
                    }
                }
                ScrollView {
                    content()
                }
                .coordinateSpace(name: Self.coordSpace)
                .onPreferenceChange(SpyOffsetKey.self) { offsets in
                    active = Self.activeSection(sections: sections, offsets: offsets)
                }
            }
        }
    }

    /// Active = the last section whose top has scrolled at/above the bar
    /// (small threshold below the top edge). Falls back to the first known
    /// section before any offsets arrive.
    private static func activeSection(sections: [SpySection], offsets: [String: CGFloat]) -> String {
        let threshold: CGFloat = 12
        var current = sections.first(where: { offsets[$0.id] != nil })?.id ?? sections.first?.id ?? ""
        for section in sections {
            if let y = offsets[section.id], y <= threshold { current = section.id }
        }
        return current
    }
}
