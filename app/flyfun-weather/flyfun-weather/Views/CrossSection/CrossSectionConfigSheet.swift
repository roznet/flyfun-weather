import SwiftUI

/// The cross-section configuration panel (§4.5): "quick, complicated and
/// dense", served by progressive disclosure. 90% of users pick a preset and
/// never open "More". One row per concern (method dropdown), reference toggles
/// as chips, and the panel doubles as the LEGEND (each row carries a swatch
/// matching the chart). Model selector is NOT here — it lives in the chart
/// chrome (model-switching is more frequent than layer config).
struct CrossSectionConfigSheet: View {
    @Bindable var csVM: CrossSectionViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showMore = false

    var body: some View {
        NavigationStack {
            List {
                presetSection
                themeSection
                advisoryLensSection
                methodSection
                referenceSection
                if showMore { moreSection }
                moreToggleRow
            }
            .navigationTitle("Layers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    // MARK: Preset — collapses everything

    private var presetSection: some View {
        Section {
            Picker("Preset", selection: Binding(
                get: { csVM.currentPreset },
                set: { csVM.applyPreset($0) }
            )) {
                ForEach([CrossSectionViewModel.Preset.gramet, .windy, .foreFlight], id: \.self) {
                    Text($0.rawValue).tag($0)
                }
                if csVM.currentPreset == .custom {
                    Text("Custom").tag(CrossSectionViewModel.Preset.custom)
                }
            }
            .pickerStyle(.segmented)
        } header: {
            Text("Preset")
        } footer: {
            Text("A preset sets every layer. Changing any control below switches to Custom.")
        }
    }

    // MARK: Theme — colours only (orthogonal to the layer Preset, #320)

    private var themeSection: some View {
        Section {
            Picker("Theme", selection: Binding(
                get: { csVM.themeId },
                set: { csVM.setTheme($0) }
            )) {
                ForEach(CrossSectionThemeID.allCases) { id in
                    HStack {
                        swatch(id.theme.skyBackground)
                        Text(id.theme.label)
                    }
                    .tag(id)
                }
            }
            .pickerStyle(.menu)
        } header: {
            Text("Theme")
        } footer: {
            Text("Colours only — independent of the Preset. Selecting a Preset also sets its matching theme.")
        }
    }

    // MARK: Advisory lens — focus the chart on one hazard (ported from web)

    private var advisoryLensSection: some View {
        Section {
            Picker("Lens", selection: Binding(
                get: { csVM.activeAdvisoryPreset ?? "" },
                set: { id in
                    if id.isEmpty {
                        csVM.clearAdvisoryPreset()  // "None" — deselect the lens
                    } else if let p = CrossSectionPresets.advisory[id] {
                        csVM.applyAdvisoryPreset(p)
                    }
                }
            )) {
                Text("None").tag("")
                ForEach(CrossSectionPresets.advisoryList) { Text($0.label).tag($0.id) }
            }
        } header: {
            Text("Advisory view")
        } footer: {
            if let id = csVM.activeAdvisoryPreset, let p = CrossSectionPresets.advisory[id] {
                Text(p.caption)
            } else {
                Text("Focus the chart on one hazard (icing, clouds, convection…).")
            }
        }
    }

    // MARK: Method groups — one row per concern (pick-one method)

    private var methodSection: some View {
        Section("Conditions") {
            // Clouds get two independent axes (source + style); the other method
            // groups stay single dropdowns. (#7)
            cloudControl
            ForEach(LayerGroup.allCases.filter { $0.isMethodGroup && $0 != .clouds }, id: \.self) { group in
                methodRow(for: group)
            }
        }
    }

    // MARK: Cloud control — Source (DD/NWP) × Style (Soft/Natural/Square)

    @ViewBuilder
    private var cloudControl: some View {
        let axes = csVM.cloudAxes
        Toggle(isOn: Binding(
            get: { axes != nil },
            set: { csVM.setCloudEnabled($0) }
        )) {
            HStack {
                swatch(LegendColors.color(forGroup: .clouds))
                Text("Clouds")
            }
        }
        if let axes {
            Picker("Source", selection: Binding(
                get: { axes.source },
                set: { csVM.setCloud(source: $0) }
            )) {
                ForEach(CloudSource.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            Picker("Style", selection: Binding(
                get: { axes.style },
                set: { csVM.setCloud(style: $0) }
            )) {
                ForEach(CloudStyle.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            // NWP cloud has no native data for this model at this lead time (e.g.
            // a far-out ECMWF flight with no 3-D cloud-fraction enrichment). The
            // chart substitutes same-style DD clouds; tell the user why NWP looks
            // off rather than leaving a blank layer. Mirrors web's greyed NWP
            // toggle. (#nwp-cloud-layer-ios-web)
            if axes.source == .nwp, csVM.unavailableLayers.contains(NwpFallback.nwpCloudsSignal) {
                Label(
                    "No native NWP cloud data for this model at this range — showing DD clouds instead.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
            }
        }
    }

    @ViewBuilder
    private func methodRow(for group: LayerGroup) -> some View {
        let active = csVM.activeMethod(for: group)
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                swatch(LegendColors.color(forGroup: group))
                Text(group.label)
                Spacer()
                Menu {
                    Button {
                        csVM.setMethod(nil, for: group)
                    } label: {
                        Label("None", systemImage: active == nil ? "checkmark" : "")
                    }
                    ForEach(CrossSectionLayer.methodGroupOrder[group] ?? [], id: \.self) { layerId in
                        // A method with no data for this model (e.g. Ogimet-NWP
                        // when there's no native NWP cloud) is greyed and marked,
                        // mirroring web's disabled panel rows. (#nwp-cloud-layer-ios-web)
                        let unavailable = csVM.unavailableLayers.contains(layerId)
                        let base = CrossSectionLayer.methodLabels[layerId] ?? layerId
                        let label = unavailable ? "\(base) — no data" : base
                        Button {
                            csVM.setMethod(layerId, for: group)
                        } label: {
                            if active == layerId {
                                Label(label, systemImage: "checkmark")
                            } else {
                                Text(label)
                            }
                        }
                        .disabled(unavailable)
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(active.flatMap { CrossSectionLayer.methodLabels[$0] } ?? "None")
                            .foregroundStyle(active != nil ? Theme.primary : Theme.textMuted)
                        Image(systemName: "chevron.up.chevron.down").font(.caption2)
                    }
                }
            }
            // The active method has no data for this model, so a DD/thermo
            // fallback is drawn instead — say which, so the chart isn't a mystery.
            if let active, csVM.unavailableLayers.contains(active),
               let substitute = NwpFallback.ddSubstituteId(for: active) {
                Label(
                    "No data for this model — showing \(CrossSectionLayer.methodLabels[substitute] ?? substitute) instead.",
                    systemImage: "info.circle"
                )
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
            }
        }
    }

    // MARK: Reference toggles (independent on/off chips)

    private var referenceSection: some View {
        Section("Reference") {
            ForEach(referenceLayers, id: \.id) { layer in
                Toggle(isOn: Binding(
                    get: { csVM.enabledLayers[layer.id] ?? false },
                    set: { _ in csVM.toggleLayer(layer.id) }
                )) {
                    HStack {
                        swatch(LegendColors.color(forLayerId: layer.id))
                        Text(layer.name)
                    }
                }
            }
        }
    }

    /// Reference + temperature toggle layers (freezing / -10 / -20 / cruise),
    /// excluding the stability lines which live under "More". Terrain is
    /// intentionally omitted — it always renders (force-on in the renderer),
    /// mirroring the web panel which has no terrain toggle.
    private var referenceLayers: [any CrossSectionLayerProtocol] {
        CrossSectionLayer.allLayers.filter {
            ($0.group == .reference || $0.group == .temperature)
        }
    }

    // MARK: More (expert — rarely touched)

    private var moreSection: some View {
        Section("More") {
            ForEach(stabilityLayers, id: \.id) { layer in
                Toggle(isOn: Binding(
                    get: { csVM.enabledLayers[layer.id] ?? false },
                    set: { _ in csVM.toggleLayer(layer.id) }
                )) {
                    HStack {
                        swatch(LegendColors.color(forLayerId: layer.id))
                        Text(layer.name)
                    }
                }
            }
        }
    }

    private var stabilityLayers: [any CrossSectionLayerProtocol] {
        CrossSectionLayer.allLayers.filter { $0.group == .stability }
    }

    private var moreToggleRow: some View {
        Button {
            withAnimation { showMore.toggle() }
        } label: {
            Label(showMore ? "Less" : "More", systemImage: showMore ? "chevron.up" : "chevron.down")
                .font(.subheadline)
        }
    }

    private func swatch(_ color: Color) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(color)
            .frame(width: 16, height: 16)
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(Theme.border, lineWidth: 0.5))
    }
}

/// Legend swatch colours that match the chart (reusing ColorScales), so the
/// config panel doubles as the legend (§4.5).
private enum LegendColors {
    static func color(forGroup group: LayerGroup) -> Color {
        switch group {
        case .clouds: return Color(.sRGB, red: 0.55, green: 0.55, blue: 0.6, opacity: 0.8)
        case .icing: return ColorScales.icingRiskColor("moderate")
        case .turbulence: return ColorScales.catRiskColor("moderate")
        case .convection: return ColorScales.convectiveTowerFill("high")
        default: return Theme.textMuted
        }
    }

    static func color(forLayerId id: String) -> Color {
        switch id {
        case "terrain": return ColorScales.terrainFill
        case "freezing-level": return ColorScales.freezingLevelColor
        case "minus-10c": return ColorScales.minus10cColor
        case "minus-20c": return ColorScales.minus20cColor
        case "lcl": return ColorScales.lclColor
        case "lfc": return ColorScales.lfcColor
        case "el": return ColorScales.elColor
        case "reference-lines": return ColorScales.cruiseAltitudeColor
        default: return Theme.textMuted
        }
    }
}
