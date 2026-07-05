import SwiftUI

/// First-time / on-demand explainer for the Flexibility (timing-scenario)
/// feature — the iOS port of the web `flexibility-explainer.ts` (#357/#352).
///
/// Shown in two places, both rendering the *same* copy so the wording never
/// drifts:
///  1. A first-time sheet, presented the first time a pilot picks a scan mode in
///     the flight editor, gated so it stops once they have genuinely run a scan
///     (`UsageSummaryResponse.timeScanUsed`) — see `AddFlightViewModel`.
///  2. An always-reachable (i) button on the briefing's Timing Scenarios header.
///
/// Acknowledge-only: a single "Got it" button dismisses; the pilot's dropdown
/// selection stands (no revert).
struct FlexibilityExplainer: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.spacingM) {
                    beat(
                        "Flexibility looks for a **better departure window** across the period you pick — it grades the weather at other times and surfaces any that come out calmer than your planned time."
                    )
                    Text("Because checking many times across many models is data-heavy, it runs in **two steps**:")
                        .font(.body)
                    VStack(alignment: .leading, spacing: Theme.spacingS) {
                        step(1, "**First pass — ECMWF only.** A fast scan on the locally-held ECMWF model finds candidate windows.")
                        step(2, "**Confirm — all models.** Tap a promising candidate to verify it against the other models before relying on it.")
                    }
                    beat(
                        "It runs **in the background** — you don't have to wait. Leave the briefing and come back; the timing scenarios fill in automatically when the scan finishes."
                    )
                    beat(
                        "🙏 **Thanks for using it thoughtfully.** Flexibility is compute- and data-heavy, so it's best saved for flights where a different time is genuinely on the table. If your flight is on a fixed schedule, leave it off — our servers will thank you for it."
                    )
                }
                .padding()
            }
            .navigationTitle("About Flexibility scanning")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Got it") { dismiss() }
                }
            }
        }
    }

    private func beat(_ markdown: String) -> some View {
        Text(LocalizedStringKey(markdown))
            .font(.body)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func step(_ n: Int, _ markdown: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
            Text("\(n).")
                .font(.body.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(LocalizedStringKey(markdown))
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
