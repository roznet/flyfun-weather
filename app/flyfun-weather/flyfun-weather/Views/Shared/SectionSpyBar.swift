import SwiftUI

/// One registrable section in a scroll-spy tab (#310 item 5).
struct SpySection: Identifiable, Equatable {
    let id: String
    let title: String

    init(_ id: String, _ title: String) {
        self.id = id
        self.title = title
    }
}

extension View {
    /// Mark a scroll target for scroll-spy tracking. Pair with `ScrollSpyScroll`;
    /// the `id` must match a `SpySection.id`. It is a plain `.id(id)` — the native
    /// scroll APIs (`scrollPosition` for tap-to-scroll, `onScrollTargetVisibilityChange`
    /// for the active section) key off the target's id directly, so no offset
    /// plumbing is needed. `ScrollSpyScroll` marks the enclosing layout with
    /// `.scrollTargetLayout()`, which is what promotes these to scroll targets.
    func spyAnchor(_ id: String) -> some View {
        self.id(id)
    }
}

/// Sticky horizontally-scrollable pill bar that mirrors the web
/// `briefing-sidebar` scroll-spy: highlights the active section and jumps to a
/// section on tap. Compact by design so it works as a top band on iPhone too.
struct SectionSpyBar: View {
    let sections: [SpySection]
    let active: String
    let onTap: (String) -> Void

    /// Native scroll position to keep the active pill centered (replaces a
    /// `ScrollViewReader`). Ids are the plain `section.id` (no `"pill-"` prefix).
    @State private var pillPosition = ScrollPosition(idType: String.self)

    var body: some View {
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
                    .id(section.id)
                }
            }
            .padding(.horizontal, Theme.cardPadding)
            .padding(.vertical, Theme.spacingS)
            .scrollTargetLayout()
        }
        .scrollPosition($pillPosition)
        .onChange(of: active) { _, newValue in
            // Keep the active pill in view as the user scrolls content.
            withAnimation(.easeInOut(duration: 0.2)) {
                pillPosition.scrollTo(id: newValue, anchor: .center)
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
    let sections: [SpySection]
    @ViewBuilder var content: () -> Content

    /// Active section id (nil until the first visibility report), and the scroll
    /// position we drive on a pill tap. Native iOS 18+ scroll state — this
    /// replaced a `PreferenceKey` + `GeometryReader` offset reporter, a shared
    /// hardcoded coordinate-space name, a hand-rolled active-section reducer, and
    /// a `ScrollViewReader`.
    @State private var active: String?
    @State private var position = ScrollPosition(idType: String.self)

    var body: some View {
        // The bar is a PINNED SECTION HEADER inside the vertical ScrollView, not a
        // sibling pinned above it. On iPhone the briefing lives in a
        // NavigationSplitView detail column that collapses to a stack and reuses
        // its hosting container across pushes; a bar pinned OUTSIDE the scroll
        // content composited zero pixels on every briefing opened after the first
        // (correct frame, no draw). The scroll content itself always re-composites
        // correctly, so the bar rides inside it as a pinned header — same sticky
        // behaviour, immune to the reuse bug. (#436)
        ScrollView {
            LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                Section {
                    // `.scrollTargetLayout()` promotes the content's `.spyAnchor`'d
                    // children to scroll targets, so `onScrollTargetVisibilityChange`
                    // and `scrollPosition` can address them by id.
                    content()
                        .scrollTargetLayout()
                } header: {
                    if sections.count > 1 {
                        SectionSpyBar(sections: sections, active: active ?? sections.first?.id ?? "") { id in
                            withAnimation(.easeInOut(duration: 0.25)) {
                                position.scrollTo(id: id, anchor: .top)
                            }
                        }
                    }
                }
            }
        }
        .scrollPosition($position)
        .onScrollTargetVisibilityChange(idType: String.self) { visible in
            // Derive the topmost visible section from our own top→bottom `sections`
            // order rather than trusting the callback array to be position-ordered.
            if let top = sections.first(where: { visible.contains($0.id) })?.id {
                active = top
            }
        }
    }
}
