import SwiftUI

/// Floating-control building blocks shared by the forecast map and the
/// past (historical, #629) map, so both screens keep one look.
///
/// #996: every control sits on an opaque surface with a soft shadow. The old
/// `.ultraThinMaterial` took on the map's beige and blue underneath, so
/// capsules and the title were low contrast over land. Layout on both maps:
/// close, the Forecast | Past switch and the hide toggle on top; the time bar
/// under them; one bottom panel with what is shown (metric, models or
/// source) and its legend; locate floating above the panel.
enum MapChrome {
    /// Which map is on screen, for the Forecast | Past switch.
    enum Kind: Hashable { case forecast, past }

    /// Whether the floating controls are hidden (#996). Read when a map
    /// appears and written on every toggle, so switching Forecast | Past or
    /// reopening the map keeps the pilot's choice for this run of the app.
    static var controlsHidden = false

    /// Menu row with a leading checkmark only when selected (avoids a blank SF
    /// Symbol slot for the unselected rows).
    @ViewBuilder static func menuRow(_ title: String, selected: Bool) -> some View {
        if selected { Label(title, systemImage: "checkmark") } else { Text(title) }
    }

    /// The opaque surface every floating control sits on.
    static func surface<S: Shape>(_ shape: S) -> some View {
        shape
            .fill(Theme.surface)
            .shadow(color: .black.opacity(0.18), radius: 6, y: 2)
            .overlay(shape.stroke(Theme.border, lineWidth: 0.5))
    }

    /// A round button for the top row (close, sidebar, hide) and locate.
    static func circleButton(systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            circleIcon(systemImage)
        }
    }

    static func circleIcon(_ systemImage: String) -> some View {
        Image(systemName: systemImage)
            .font(.headline)
            .foregroundStyle(Theme.text)
            .frame(width: 36, height: 36)
            .background(surface(Circle()))
    }

    /// Forecast | Past, labelled, the same place on both maps (Android has the
    /// same switch in its top bar). Replaces the unlabelled clock button.
    static func modeSwitch(current: Kind, onSwitch: @escaping (Kind) -> Void) -> some View {
        Picker("Map", selection: Binding(get: { current }, set: { if $0 != current { onSwitch($0) } })) {
            Text("Forecast").tag(Kind.forecast)
            Text("Past").tag(Kind.past)
        }
        .pickerStyle(.segmented)
        .fixedSize()
        .padding(3)
        .background(surface(RoundedRectangle(cornerRadius: 10)))
        .accessibilityIdentifier("mapModeSwitch")
    }

    /// The time bar's centre: what time is shown, with the hint that tapping
    /// it picks another.
    static func timeLabel(title: String, hint: String) -> some View {
        VStack(spacing: 1) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            Text(hint)
                .font(.caption2)
                .foregroundStyle(Theme.textMuted)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity)
    }

    /// One step arrow of the time bar.
    static func stepButton(_ systemImage: String, label: String, enabled: Bool,
                           action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.body.weight(.semibold))
                .frame(width: 32, height: 32)
        }
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    /// A labelled pick in the bottom panel: small caption, current value, chevron.
    static func pickLabel(caption: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(caption.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .kerning(0.4)
                .foregroundStyle(Theme.textMuted)
            HStack(spacing: 4) {
                Text(value)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                Spacer(minLength: 0)
                Image(systemName: "chevron.down")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bg, in: RoundedRectangle(cornerRadius: 8))
    }

    /// The hidden state's only control besides close: the time shown, which
    /// brings the controls back.
    static func showControlsPill(time: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: "slider.horizontal.3")
                Text(time).monospacedDigit()
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Theme.primary)
            .padding(.horizontal, 12).frame(height: 36)
            .background(surface(Capsule()))
        }
        .accessibilityLabel("Show map controls, \(time)")
        .accessibilityIdentifier("mapShowControlsButton")
    }

    /// The active metric's legend, wrapped so a long ramp fits narrow phones.
    /// Drawn inside the bottom panel, so it carries no background of its own.
    static func legend(_ legend: ForecastMapCatalog.Legend) -> some View {
        FlowLayout(spacing: 8) {
            ForEach(legend.items) { item in
                HStack(spacing: 3) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.catalog(item.color))
                        .frame(width: 12, height: 8)
                    Text(item.label).font(.caption2).foregroundStyle(Theme.text)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The bottom panel: picks on one row, the legend under them.
    static func bottomPanel<Picks: View>(legend: ForecastMapCatalog.Legend?,
                                         @ViewBuilder picks: () -> Picks) -> some View {
        bottomPanel(picks: picks) {
            if let legend { MapChrome.legend(legend) }
        }
    }

    /// The bottom panel with any legend area under the picks (the briefing
    /// route map's route scale plus airport line, #998).
    static func bottomPanel<Picks: View, Legend: View>(@ViewBuilder picks: () -> Picks,
                                                       @ViewBuilder legend: () -> Legend) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) { picks() }
            legend()
        }
        .padding(10)
        .frame(maxWidth: 520)
        .background(surface(RoundedRectangle(cornerRadius: 14)))
    }

    /// A labelled floating button (Open full forecast map, #998): an icon
    /// alone was not understood.
    static func labelledButton(_ title: String, systemImage: String,
                               action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: systemImage)
                Text(title)
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 12).frame(height: 36)
            .background(surface(Capsule()))
        }
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
