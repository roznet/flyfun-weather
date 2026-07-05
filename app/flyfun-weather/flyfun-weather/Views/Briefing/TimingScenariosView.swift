import SwiftUI

/// The Timing Scenarios panel (#357) — a section inside the Advisory tab that
/// mirrors the web's inline "is there a better departure time?" surface. Posture
/// is inherited from the mitigation framework: an **attention-director, never a
/// verdict**. Candidates carry explicit model-coverage labels and the scan never
/// auto-switches the plan.
///
/// Renders the full state ladder off `BriefingViewModel.timeOptions`:
/// hidden (no data) · offline placeholder · running · failed · skipped · results.
struct TimingScenariosView: View {
    let viewModel: BriefingViewModel
    @State private var showExplainer = false

    var body: some View {
        if viewModel.timeOptionsOffline {
            panel { offlinePlaceholder }
        } else if let to = viewModel.timeOptions {
            panel { content(to) }
        }
        // else: nothing yet (first poll in flight, or 404 no-data) — the parent
        // keeps the section out of the scroll-spy until there's data to show.
    }

    // MARK: - Chrome

    @ViewBuilder
    private func panel<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Theme.spacingM) {
            HStack(spacing: Theme.spacingS) {
                Text("Timing Scenarios")
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                Button {
                    showExplainer = true
                } label: {
                    Image(systemName: "info.circle")
                        .foregroundStyle(Theme.primary)
                }
                .accessibilityLabel("How Flexibility scanning works")
                Spacer(minLength: 0)
            }
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .sheet(isPresented: $showExplainer) {
            FlexibilityExplainer()
        }
    }

    private var offlinePlaceholder: some View {
        Text("Timing scenarios need a connection — reconnect to check other departure times.")
            .font(.subheadline)
            .foregroundStyle(Theme.textMuted)
    }

    // MARK: - State ladder

    @ViewBuilder
    private func content(_ to: TimeOptionsResponse) -> some View {
        let status = to.status?.status
        if status == .pending || status == .running || (status == nil && to.scan == nil) {
            HStack(spacing: Theme.spacingS) {
                ProgressView()
                Text("Scenarios running — checking other departure times against this briefing…")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textMuted)
            }
        } else if status == .failed {
            muted("Scenario check failed — it will run again on the next refresh.")
        } else if status == .skipped || to.scan == nil {
            muted(skippedReason(to.status?.reason))
        } else if let scan = to.scan {
            results(scan)
        }
    }

    private func skippedReason(_ reason: String?) -> String {
        reason == "no_alternate_time"
            ? "Set an alternate departure time to grade it here."
            : "No scenario data for this briefing."
    }

    // MARK: - Results

    @ViewBuilder
    private func results(_ scan: TimeWindowScanDTO) -> some View {
        let betterCount = scan.candidates.filter { !$0.isBaseline && !$0.isAlternate }.count
        VStack(alignment: .leading, spacing: Theme.spacingM) {
            Text(headline(betterCount: betterCount))
                .font(.subheadline)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)

            ForEach(scan.candidates) { candidate in
                CandidateRow(
                    candidate: candidate,
                    scan: scan,
                    names: catalogNames,
                    anyConfirmPending: viewModel.anyConfirmPending,
                    viewModel: viewModel
                )
                Divider()
            }

            ForEach(footnotes(scan), id: \.self) { note in
                muted(note).font(.caption)
            }
        }
    }

    private func headline(betterCount: Int) -> String {
        if betterCount > 0 {
            let s = betterCount == 1 ? "" : "s"
            let looks = betterCount == 1 ? "s" : ""
            return "\(betterCount) departure window\(s) look\(looks) smoother than the planned time — worth a look, not a verdict."
        }
        return "No clearly better window found in the hours checkable from this briefing."
    }

    /// Edge-of-window footnotes, mirroring the web (refused hours, past-clipped,
    /// horizon-clipped) so a clipped scan doesn't read as a data gap.
    private func footnotes(_ scan: TimeWindowScanDTO) -> [String] {
        var notes: [String] = []
        if !scan.refusedTimes.isEmpty {
            let n = scan.refusedTimes.count
            notes.append("\(n) daylight hour\(n > 1 ? "s" : "") couldn’t be checked from this briefing’s data (outside the fetched forecast window).")
        }
        if scan.window?.pastClipped == true {
            notes.append("Hours that have already passed were left out — only upcoming departure times are shown.")
        }
        if scan.window?.horizonClipped == true {
            notes.append("The window stops where ECMWF forecast fidelity ends — later hours aren’t checkable yet.")
        }
        return notes
    }

    // MARK: - Helpers

    /// Advisory id → display name, from the loaded briefing catalog.
    private var catalogNames: [String: String] {
        guard case .loaded(let response) = viewModel.advisoriesState else { return [:] }
        return Dictionary(response.catalog.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
    }

    private func muted(_ text: String) -> some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Candidate row

/// One graded departure time: time + day suffix, assessment pill, shift label,
/// "★ your alternate" tag, improves/worsens diff, a confidence line, and an
/// expandable per-advisory dot-table.
private struct CandidateRow: View {
    let candidate: TimeCandidateDTO
    let scan: TimeWindowScanDTO
    let names: [String: String]
    /// True while any candidate is being confirmed — disables every action
    /// button (the server allows one confirm at a time per pack).
    let anyConfirmPending: Bool
    let viewModel: BriefingViewModel
    @State private var expanded = false
    @State private var actionInFlight = false

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.spacingS) {
                Text(timeLabel)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                AssessmentStringBadge(status: effectiveAssessment)
                Text(shiftLabel)
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
                if candidate.isAlternate {
                    Text("★ your alternate")
                        .font(.caption)
                        .foregroundStyle(Theme.primary)
                }
                Spacer(minLength: 0)
            }

            if !diffText.isEmpty {
                Text(diffText)
                    .font(.caption)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
            }

            confidenceLine
            actionButtons

            DisclosureGroup(isExpanded: $expanded) {
                TimingDetailTable(candidate: candidate, scan: scan, names: names)
                    .padding(.top, Theme.spacingXS)
            } label: {
                Text("details")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .padding(.vertical, Theme.spacingXS)
    }

    // MARK: Derived display

    /// Once confirmed, the multi-model verdict replaces the provisional grade.
    private var effectiveAssessment: String {
        candidate.confirmed?.assessment ?? candidate.assessment
    }

    private var timeLabel: String {
        TimingFormat.time(candidate.departureTime) + dayLabel
    }

    private var dayLabel: String {
        guard let baseline = scan.candidates.first(where: { $0.isBaseline }) else { return "" }
        return TimingFormat.daySuffix(candidate.departureTime, plannedISO: baseline.departureTime)
    }

    private var shiftLabel: String {
        if candidate.isBaseline { return "as planned" }
        let h = candidate.departureShiftHours
        let sign = h >= 0 ? "+" : ""
        let num = h == h.rounded() ? String(Int(h)) : String(format: "%.1f", h)
        return "\(sign)\(num)h"
    }

    /// improves/worsens — from the confirmed diff once a multi-model check ran,
    /// else the provisional one (never mix the two).
    private var diffText: String {
        let improves = candidate.confirmed?.improves ?? candidate.improves
        let worsens = candidate.confirmed?.worsens ?? candidate.worsens
        var parts: [String] = []
        if !improves.isEmpty { parts.append("improves: \(improves.map(nameOf).joined(separator: ", "))") }
        if !worsens.isEmpty { parts.append("worsens: \(worsens.map(nameOf).joined(separator: ", "))") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var confidenceLine: some View {
        if let confirmed = candidate.confirmed {
            let models = confirmed.modelsChecked.map { $0.uppercased() }.joined(separator: ", ")
            Text(confirmed.betterThanBaseline
                 ? "checked with \(models) — still looks smoother"
                 : "checked with \(models) — not clearly better after all")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        } else if candidate.confirmPending {
            HStack(spacing: Theme.spacingXS) {
                ProgressView().controlSize(.mini)
                Text("checking all models…")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
        } else if candidate.confidence == .ecmwfOnly {
            Text("ECMWF only")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        } else {
            Text("all models checked")
                .font(.caption)
                .foregroundStyle(Theme.textMuted)
        }
    }

    private func nameOf(_ id: String) -> String { names[id] ?? id }

    // MARK: Actions (slice 4)

    /// "Check all models" (on provisional ECMWF-only rows) and "Set as
    /// alternate" (same-day non-baseline rows). Owner-only — hidden for
    /// read-only subscribers. Both disable while any confirm runs.
    @ViewBuilder
    private var actionButtons: some View {
        if viewModel.flight.isEditable {
            HStack(spacing: Theme.spacingS) {
                if showsConfirmButton {
                    Button {
                        runAction { await viewModel.confirmTimeOption(departureTime: candidate.departureTime) }
                    } label: {
                        Text("Check all models")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(actionInFlight || anyConfirmPending)
                }
                if showsSetAlternateButton {
                    Button {
                        runAction { await viewModel.setScenarioAsAlternate(departureTime: candidate.departureTime) }
                    } label: {
                        Text("Set as alternate")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(actionInFlight || anyConfirmPending)
                }
            }
        }
    }

    /// The "Check all models" affordance shows on an unconfirmed, not-currently-
    /// checking, ECMWF-only candidate.
    private var showsConfirmButton: Bool {
        candidate.confirmed == nil && !candidate.confirmPending && candidate.confidence == .ecmwfOnly
    }

    /// "Set as alternate" is offered on same-day non-baseline, non-alternate
    /// rows (the alternate-time validation is same-day only, matching the web).
    private var showsSetAlternateButton: Bool {
        guard !candidate.isBaseline, !candidate.isAlternate else { return false }
        return TimingFormat.sameUTCDay(candidate.departureTime, scan.baseline.departureTime)
    }

    private func runAction(_ work: @escaping () async -> Void) {
        actionInFlight = true
        Task { @MainActor in
            await work()
            actionInFlight = false
        }
    }
}

// MARK: - Detail dot-table

/// Per-advisory comparison: Current (planned time) vs this candidate's grade
/// (ECMWF-only or all-models) vs the confirmed all-models check when present.
/// Each cell is a colored dot; rows are sorted worst-status first.
private struct TimingDetailTable: View {
    let candidate: TimeCandidateDTO
    let scan: TimeWindowScanDTO
    let names: [String: String]

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            if rowIds.isEmpty {
                Text("All advisories clear at every checked view.")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            } else {
                Grid(alignment: .leading, horizontalSpacing: Theme.spacingM, verticalSpacing: Theme.spacingXS) {
                    GridRow {
                        Text("").gridColumnAlignment(.leading)
                        ForEach(columns, id: \.label) { col in
                            Text(col.label)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(Theme.textMuted)
                        }
                    }
                    ForEach(rowIds, id: \.self) { id in
                        GridRow {
                            Text(nameOf(id))
                                .font(.caption)
                                .foregroundStyle(Theme.text)
                            ForEach(columns, id: \.label) { col in
                                dot(col.map[id] ?? "GREEN")
                            }
                        }
                    }
                }
            }
            Text(gradedFootnote)
                .font(.caption2)
                .foregroundStyle(Theme.textMuted)
                .padding(.top, Theme.spacingXS)
        }
    }

    // MARK: Columns

    private struct DetailColumn { let label: String; let map: [String: String] }

    private var columns: [DetailColumn] {
        var cols: [DetailColumn] = [
            DetailColumn(label: "Current", map: TimingFormat.parseReason(baselineReason))
        ]
        if !candidate.isBaseline {
            let provisionalLabel = (candidate.confirmed != nil || candidate.confidence == .ecmwfOnly)
                ? "ECMWF only" : "All models"
            cols.append(DetailColumn(label: provisionalLabel,
                                     map: TimingFormat.parseReason(candidate.assessmentReason)))
            if let confirmed = candidate.confirmed {
                cols.append(DetailColumn(label: "All models",
                                         map: TimingFormat.parseReason(confirmed.assessmentReason)))
            }
        }
        return cols
    }

    private var baselineReason: String {
        scan.candidates.first(where: { $0.isBaseline })?.assessmentReason ?? ""
    }

    private var rowIds: [String] {
        let ids = Set(columns.flatMap { $0.map.keys })
        return ids.sorted { a, b in
            let ra = worst(a), rb = worst(b)
            if ra != rb { return ra > rb }
            return nameOf(a).localizedCaseInsensitiveCompare(nameOf(b)) == .orderedAscending
        }
    }

    private func worst(_ id: String) -> Int {
        columns.map { rank($0.map[id]) }.max() ?? 0
    }

    private func rank(_ status: String?) -> Int {
        switch status {
        case "RED": 2
        case "AMBER": 1
        default: 0
        }
    }

    private var gradedFootnote: String {
        let models = candidate.modelsUsed.map { $0.uppercased() }.joined(separator: ", ")
        var line = "Graded with \(models)"
        if let first = candidate.validTimes.first, let last = candidate.validTimes.last {
            line += " · route ETAs \(TimingFormat.time(first)) → \(TimingFormat.time(last))"
        }
        return line
    }

    private func dot(_ status: String) -> some View {
        let color: Color = status == "RED" ? Theme.red : status == "AMBER" ? Theme.amber : Theme.green
        return Image(systemName: "circle.fill")
            .font(.caption2)
            .foregroundStyle(color)
            .accessibilityLabel("\(status)")
    }

    private func nameOf(_ id: String) -> String { names[id] ?? id }
}

// MARK: - Formatting helpers

/// Small ISO-string formatters shared by the timing panel. All times display in
/// UTC to match the web (`formatDepartureTime`).
enum TimingFormat {
    private static let utcTime: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    private static let utcDayKey: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    /// "HH:mm" in UTC, falling back to the raw string if unparseable.
    static func time(_ iso: String) -> String {
        guard let date = Date.parseISO8601(iso) else { return iso }
        return utcTime.string(from: date)
    }

    /// Day-difference suffix vs the planned day (" · next day" / " · previous
    /// day" / " · ±N days"), empty on the same day.
    static func daySuffix(_ iso: String, plannedISO: String) -> String {
        guard let cand = Date.parseISO8601(iso), let planned = Date.parseISO8601(plannedISO) else { return "" }
        let candDay = utcDayKey.string(from: cand)
        let planDay = utcDayKey.string(from: planned)
        guard candDay != planDay,
              let cd = utcDayKey.date(from: candDay), let pd = utcDayKey.date(from: planDay) else { return "" }
        let diff = Int((cd.timeIntervalSince(pd) / 86_400).rounded())
        switch diff {
        case 1: return " · next day"
        case -1: return " · previous day"
        default: return " · \(diff > 0 ? "+" : "")\(diff) days"
        }
    }

    /// Whether two ISO timestamps fall on the same UTC calendar day.
    static func sameUTCDay(_ a: String, _ b: String) -> Bool {
        guard let da = Date.parseISO8601(a), let db = Date.parseISO8601(b) else { return false }
        return utcDayKey.string(from: da) == utcDayKey.string(from: db)
    }

    /// Parse an `"id=STATUS, id2=STATUS"` reason string into an id→status map.
    static func parseReason(_ reason: String) -> [String: String] {
        var map: [String: String] = [:]
        for part in reason.split(separator: ",") {
            let kv = part.split(separator: "=", maxSplits: 1)
            guard kv.count == 2 else { continue }
            let id = kv[0].trimmingCharacters(in: .whitespaces)
            let status = kv[1].trimmingCharacters(in: .whitespaces)
            if !id.isEmpty, !status.isEmpty { map[id] = status }
        }
        return map
    }
}
