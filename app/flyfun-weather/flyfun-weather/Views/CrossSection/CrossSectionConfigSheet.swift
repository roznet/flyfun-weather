import SwiftUI

/// Chart options (#605). Holds what you set once and forget — the emulation, the
/// theme, the observed corridor — plus, on iPhone, every family's pills for when
/// the bar's one-tap chips are not enough. Anything toggled in order to COMPARE
/// lives on the layer bar instead, next to a chart that stays visible: a sheet
/// hides one of the two states being compared. The model selector is not here
/// either — switching models is more frequent than any of this.
struct CrossSectionConfigSheet: View {
    @Bindable var csVM: CrossSectionViewModel
    /// Show every family's pills. iPhone only: iPad's bar carries them inline.
    var showsLayerPills: Bool = true
    /// The loaded snapshot, so the corridor picker can re-resolve the observed
    /// discs without a request (every sampled radius already shipped with the
    /// pack). nil → the observed section hides itself.
    var snapshot: SnapshotResponse? = nil
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                lensSection
                themeSection
                if showsLayerPills { layersSection }
                observedSection
            }
            .navigationTitle("Chart options")
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

    // MARK: Lens — Emulate × Focus

    private var lensSection: some View {
        Section {
            Picker("Emulate", selection: Binding(
                get: { csVM.activeEmulation ?? "" },
                set: { csVM.applyEmulation($0.isEmpty ? nil : $0) }
            )) {
                Text(CrossSectionPresets.ownConventionsLabel).tag("")
                ForEach(CrossSectionPresets.all) { Text($0.label).tag($0.id) }
            }
            .pickerStyle(.segmented)
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
        } header: {
            Text("Lens")
        } footer: {
            if let id = csVM.activeAdvisoryPreset, let lens = CrossSectionPresets.advisory[id] {
                Text(lens.caption)
            } else {
                Text("Emulate picks the methods and the look; Focus picks which layers are on. They combine — GRAMET focused on icing shows GRAMET's icing method and nothing else.")
            }
        }
    }

    // MARK: Theme — colours only

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
            Text("Colours only. Picking an emulation also sets its matching theme.")
        }
    }

    // MARK: Layers — every family's pills (iPhone)

    private var layersSection: some View {
        Section {
            ForEach(LayerFamily.visible(in: csVM)) { family in
                VStack(alignment: .leading, spacing: Theme.spacingS) {
                    HStack {
                        FamilyDot(family: family, hollow: !csVM.isFamilyOn(family))
                        Text(family.label).font(.subheadline.weight(.semibold))
                        Spacer()
                        FamilyAboutButton(family: family)
                    }
                    FamilyPills(csVM: csVM, family: family)
                    ForEach(FamilyPills.substitutionNotes(csVM, family: family), id: \.self) { note in
                        Label(note, systemImage: "info.circle")
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                    }
                }
                .padding(.vertical, 4)
            }
        } header: {
            Text("Layers")
        } footer: {
            Text("Every family is pick-any — two methods overlaid is a comparison, not an error. The chips under the chart switch a whole family in one tap.")
        }
    }

    // MARK: Observed conditions (#574)

    /// Hidden entirely when the pack carries no observed payload — a D-1+ pack, a
    /// deployment with the collector off, or a pack built before #574. The
    /// layers themselves are pills in the Observed family; this holds the
    /// corridor and each source's age.
    @ViewBuilder
    private var observedSection: some View {
        if let observed = csVM.vizData?.observed {
            Section {
                if observed.radiiNm.count > 1 {
                    Picker("Corridor", selection: Binding(
                        get: { observed.radiusNm },
                        set: { csVM.setObservedRadius($0, snapshot: snapshot) }
                    )) {
                        // Discs are cumulative, not rings: "within 10 NM" is the
                        // question a pilot asks.
                        ForEach(observed.radiiNm, id: \.self) { r in
                            Text("\(Int(r)) NM").tag(r)
                        }
                    }
                    .pickerStyle(.segmented)
                }
                // Per-source ages, never blended. Four streams that are minutes
                // apart share no instant, so each says so for itself — the same
                // rule the chart badges follow.
                ForEach(observedSources(observed), id: \.source) { source in
                    HStack {
                        Text(source.label)
                            .font(.caption)
                            .foregroundStyle(Theme.textMuted)
                        Spacer()
                        Text(ObservedBadge.ageText(source.validTime, source.ageMinutes))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(Theme.textMuted)
                    }
                }
            } header: {
                Text("Observed conditions")
            } footer: {
                Text("Measured, not forecast. Radar and satellite frames are minutes old and each carries its own time. Cloud-top bands under 5% of the sky aren't drawn, so a point with no band is not a point with no cloud.")
            }
        }
    }

    private func observedSources(_ observed: VizObserved) -> [VizObservedSource] {
        [observed.cloudTops, observed.reflectivity, observed.rainRate, observed.lightning]
            .compactMap { $0 }
    }

    private func swatch(_ color: Color) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(color)
            .frame(width: 16, height: 16)
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(Theme.border, lineWidth: 0.5))
    }
}
