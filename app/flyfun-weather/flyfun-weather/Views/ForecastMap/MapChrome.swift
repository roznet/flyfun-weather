import SwiftUI

/// Floating-control building blocks shared by the forecast map and the
/// historical map (#629), so both screens keep one look: material capsules,
/// checkmarked menu rows and the catalog legend.
enum MapChrome {
    /// Menu row with a leading checkmark only when selected (avoids a blank SF
    /// Symbol slot for the unselected rows).
    @ViewBuilder static func menuRow(_ title: String, selected: Bool) -> some View {
        if selected { Label(title, systemImage: "checkmark") } else { Text(title) }
    }

    /// A menu's capsule label: icon, text, disclosure chevron.
    static func capsuleLabel(text: String, systemImage: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage)
            Text(text)
            Image(systemName: "chevron.down").font(.caption2)
        }
        .font(.subheadline.weight(.medium))
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: Capsule())
    }

    /// A round material button for the top bar (close, sidebar, switch map).
    static func circleButton(systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.headline)
                .padding(8)
                .background(.ultraThinMaterial, in: Circle())
        }
    }

    /// The active metric's legend, wrapped so a long ramp fits narrow phones.
    static func legendCapsule(_ legend: ForecastMapCatalog.Legend) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(legend.title).font(.caption2.weight(.semibold)).foregroundStyle(Theme.textMuted)
            FlowLayout(spacing: 6) {
                ForEach(legend.items) { item in
                    HStack(spacing: 3) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(Color.catalog(item.color))
                            .frame(width: 12, height: 8)
                        Text(item.label).font(.system(size: 10)).foregroundStyle(Theme.text)
                    }
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: 220, alignment: .leading)
    }
}

/// Presents a tapped-airport card as a trailing `.inspector` column on iPad
/// (regular width) and a bottom sheet on iPhone. The compact sheet keeps the map
/// pannable and live-recolouring underneath via
/// `.presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))`, so
/// stepping time from the card updates the map and the card together.
struct MapCardPresenter<Card: View>: ViewModifier {
    let isPresented: Binding<Bool>
    let isCompact: Bool
    @ViewBuilder let card: () -> Card

    func body(content: Content) -> some View {
        if isCompact {
            content.sheet(isPresented: isPresented) {
                card()
                    .presentationDetents([.fraction(0.45), .large])
                    .presentationBackgroundInteraction(.enabled(upThrough: .fraction(0.45)))
                    .presentationDragIndicator(.visible)
            }
        } else {
            content.inspector(isPresented: isPresented) {
                card().inspectorColumnWidth(min: 320, ideal: 360, max: 440)
            }
        }
    }
}
