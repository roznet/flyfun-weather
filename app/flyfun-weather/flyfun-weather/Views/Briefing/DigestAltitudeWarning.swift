import SwiftUI

/// Amber notice shown when the pilot changed the flight's planned cruise
/// altitude after this pack was built, so the AI summary still narrates the old
/// level.
///
/// Editing the altitude deliberately takes the cheap path: `PATCH
/// /api/flights/{id}` answers `invalidation = "advisories_only"` and
/// `AddFlightViewModel.regenerateBriefing` re-evaluates the advisories at the new
/// altitude via `advisories/recalculate`. That never re-runs the pipeline — by
/// design, a full refresh per altitude tweak would be wasteful — so the LLM
/// digest keeps describing the altitude the pack was generated for. The altitude
/// is baked all the way through it, not just in a header: `ALTITUDE: {ft}` in the
/// prompt (`digest/prompt_builder.py`) and the per-waypoint cruise wind/temp rows
/// are all taken at that level.
///
/// Web has carried this warning since #259 (`buildDigestAltitudeWarning`,
/// `web/ts/managers/briefing-ui.ts`), where it sits above the digest prose and
/// dims it. iOS had no equivalent, which is what a pilot reported as *"I have set
/// the altitude to 2000' but it keeps talking about FL55"*.
///
/// **Compare against the snapshot, not the advisories manifest.**
/// `AdvisoriesResponse.cruiseAltitudeFt` is rewritten to the new altitude by the
/// recalculate call, so it can never differ and the warning would never fire.
/// `SnapshotResponse.route` comes from `briefing.json` — the one artifact the
/// recalculate path leaves alone, and the same source web reads.
struct DigestAltitudeWarning: View {
    let viewModel: BriefingViewModel

    var body: some View {
        if let packAltitudeFt = stalePackAltitudeFt {
            Label(message(packAltitudeFt: packAltitudeFt),
                  systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(Theme.amber)
                .fixedSize(horizontal: false, vertical: true)
                .padding(Theme.spacingS)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.amber.opacity(Theme.tableRowHighlightOpacity),
                            in: RoundedRectangle(cornerRadius: 10))
                // Inset lives here, not at the call site, so that when there is
                // nothing to warn about the whole view collapses to a bare
                // `EmptyView` and the enclosing VStack adds no stray spacing.
                .padding(.horizontal, Theme.cardPadding)
        }
    }

    private var stalePackAltitudeFt: Int? {
        guard case .loaded(let snapshot) = viewModel.snapshotState else { return nil }
        return Self.stalePackAltitudeFt(snapshot: snapshot,
                                        flightCruiseAltitudeFt: viewModel.flight.cruiseAltitudeFt)
    }

    /// The altitude this pack (and therefore its digest) was built for, or nil
    /// when it still matches the flight and there is nothing to warn about.
    ///
    /// Takes the whole `SnapshotResponse` rather than an altitude so the "read it
    /// from the snapshot" half of the contract is what the tests pin — passing the
    /// advisories manifest's altitude instead is the one mistake that would make
    /// this warning silently dead.
    static func stalePackAltitudeFt(snapshot: SnapshotResponse,
                                    flightCruiseAltitudeFt: Int) -> Int? {
        let packAltitudeFt = snapshot.route.cruiseAltitudeFt
        return packAltitudeFt == flightCruiseAltitudeFt ? nil : packAltitudeFt
    }

    private func message(packAltitudeFt: Int) -> String {
        String(localized: """
            This AI summary was written for a cruise altitude of \
            \(Self.flightLevel(packAltitudeFt)); the flight is now planned at \
            \(Self.flightLevel(viewModel.flight.cruiseAltitudeFt)). Refresh the \
            briefing to regenerate the summary.
            """)
    }

    /// `FLnnn`, matching how the app writes altitudes everywhere else (the flight
    /// card, the briefing subtitle, the cross-section reference line).
    static func flightLevel(_ ft: Int) -> String { "FL\(ft / 100)" }
}
