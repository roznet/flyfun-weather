import SwiftUI

/// Connective chrome (§4.10): a persistent header above the 4 briefing tabs so
/// the briefing reads as ONE object the tabs are views onto. Carries identity
/// (route · date · FL), the freshness chip, the offline/cached status (§1D,
/// first-class), and the pack (D-N history) selector. Refresh / track / download
/// actions stay in the navigation toolbar.
struct BriefingHeaderView: View {
    @Bindable var viewModel: BriefingViewModel
    let flight: FlightResponse

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingXS) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(flight.shortTitle)
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    Text(identityDetail)
                        .font(.caption)
                        .foregroundStyle(Theme.textMuted)
                }
                Spacer()
                packSelector
            }

            HStack(spacing: Theme.spacingS) {
                freshnessChip
                if viewModel.packCacheStatus[viewModel.selectedPackTimestamp] == true {
                    Label("Cached", systemImage: "arrow.down.circle.fill")
                        .font(.caption2)
                        .foregroundStyle(Theme.green)
                }
                Spacer()
            }
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, Theme.spacingS)
        .background(Theme.surface)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 0.5)
        }
    }

    private static let dayTimeUTC: DateFormatter = {
        let fmt = DateFormatter()
        fmt.dateFormat = "d MMM HH:mm"
        fmt.timeZone = TimeZone(identifier: "UTC")
        return fmt
    }()

    private var identityDetail: String {
        var parts: [String] = []
        if let date = flight.departureDate {
            parts.append("\(Self.dayTimeUTC.string(from: date)) UTC")
        }
        parts.append("FL\(flight.cruiseAltitudeFt / 100)")
        return parts.joined(separator: " · ")
    }

    // MARK: Pack selector (D-N history)

    @ViewBuilder
    private var packSelector: some View {
        if viewModel.packHistory.count > 1 {
            Menu {
                ForEach(viewModel.packHistory, id: \.fetchTimestamp) { pack in
                    Button {
                        viewModel.selectedPackTimestamp = pack.fetchTimestamp
                    } label: {
                        HStack {
                            Text(viewModel.packLabel(for: pack))
                            if viewModel.packCacheStatus[pack.fetchTimestamp] == true {
                                Image(systemName: "arrow.down.circle.fill").foregroundStyle(Theme.green)
                            }
                            if pack.fetchTimestamp == viewModel.selectedPackTimestamp {
                                Image(systemName: "checkmark")
                            }
                        }
                    }
                }
            } label: {
                HStack(spacing: Theme.spacingXS) {
                    Image(systemName: "clock.arrow.circlepath")
                    Text(currentPackLabel)
                }
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.primary)
            }
        } else if viewModel.pack != nil {
            Text(currentPackLabel)
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.textMuted)
        }
    }

    private var currentPackLabel: String {
        if let pack = viewModel.pack { return viewModel.packLabel(for: pack) }
        return "History"
    }

    // MARK: Freshness chip

    @ViewBuilder
    private var freshnessChip: some View {
        if let status = viewModel.pack?.dataStatus {
            HStack(spacing: Theme.spacingXS) {
                Circle()
                    .fill(status.fresh ? Theme.green : Theme.amber)
                    .frame(width: 6, height: 6)
                Text(freshnessText(status))
                    .font(.tabularData(.caption2))
                    .foregroundStyle(Theme.textMuted)
            }
        } else if let initTimes = viewModel.pack?.modelInitTimes, !initTimes.isEmpty {
            Text(initTimesText(initTimes))
                .font(.tabularData(.caption2))
                .foregroundStyle(Theme.textMuted)
        }
    }

    private func freshnessText(_ status: DataStatus) -> String {
        if status.fresh {
            return initTimesText(status.modelInitTimes.isEmpty ? (viewModel.pack?.modelInitTimes ?? [:]) : status.modelInitTimes)
        }
        let stale = status.staleModels.map { $0.uppercased() }.joined(separator: ", ")
        return stale.isEmpty ? "Updating…" : "Updating: \(stale)"
    }

    private func initTimesText(_ times: [String: Int]) -> String {
        let parts = times
            .sorted { $0.key < $1.key }
            .prefix(3)
            .map { "\($0.key.uppercased()) \(String(format: "%02d", $0.value))Z" }
        return parts.joined(separator: " · ")
    }
}
