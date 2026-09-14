import SwiftUI

/// The trip screen (#607) — the chain, what decides it, and the trip-level
/// actions.
///
/// Low-traffic by design: the flight list answers the common question ("go
/// straight to the leg I care about"), so most pilots reach this screen rarely.
/// That is not a reason to thin it out — it remains the only home for the
/// refresh button, the AI paragraph, continuity warnings and the flown legs.
///
/// Two rules it must never break, both from `designs/flight-trips.md`:
///
/// 1. **No verdict for the trip.** The hero is a sentence about a *leg*. Nothing
///    here renders `chainStatus` as a badge.
/// 2. **No re-derivation.** `summary.headline` and `summary.bindingLegId` are
///    rendered verbatim.
struct TripDetailView: View {
    let tripId: String
    /// Called when a leg is tapped, so the container decides how to present the
    /// briefing (an iPad detail swap, or a push on iPhone).
    var onOpenLeg: ((String) -> Void)?
    /// Called when the trip no longer exists (deleted, or pruned with its last
    /// leg), so the container can close the screen the right way for its
    /// presentation — `dismiss()` does nothing in an iPad detail pane. Falls back
    /// to `dismiss()` when nil.
    var onClose: (() -> Void)?

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: TripDetailViewModel?
    @State private var showDeleteConfirm = false
    @State private var renameText = ""
    @State private var showRename = false
    @State private var legToRemove: TripLeg?
    @State private var showUnfollowConfirm = false

    // Split into `screen` + `alerts(on:)`: as one modifier chain the body hit the
    // type checker's time limit.
    var body: some View {
        alerts(on: screen)
    }

    private var screen: some View {
        Group {
            if let viewModel {
                LoadingStateView(state: viewModel.state, retryAction: viewModel.load) { trip in
                    content(trip: trip, viewModel: viewModel)
                }
            } else {
                ProgressView()
            }
        }
        .navigationTitle(viewModel?.trip?.displayName ?? "Trip")
        .navigationSubtitle(viewModel?.trip?.summary.chainLabel ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbarContent }
        .task {
            guard viewModel == nil else { return }
            guard let tripRepo = appState.tripRepository else { return }
            let model = TripDetailViewModel(
                tripId: tripId,
                repository: tripRepo,
                briefingRepository: appState.repository
            )
            viewModel = model
            await model.load()
            await model.pollActiveLegRefreshes()
        }
        .onDisappear { viewModel?.stopPolling() }
        .onChange(of: viewModel?.isGone ?? false) { _, gone in
            guard gone else { return }
            if let onClose { onClose() } else { dismiss() }
        }
        .refreshable { await viewModel?.load() }
    }

    private func alerts(on content: some View) -> some View {
        content
        .alert("Delete Trip?", isPresented: $showDeleteConfirm) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                // Closing is driven by `isGone`, the same path as a trip pruned
                // with its last leg.
                Task { await viewModel?.deleteTrip() }
            }
            .accessibilityIdentifier("confirmDeleteTripButton")
        } message: {
            // Spelled out because the flights are the expensive thing and this
            // does not touch them: it deletes a container, not the briefings.
            Text("The trip is removed. Its flights and all their briefings are kept.")
        }
        .alert("Remove Leg?", isPresented: Binding(
            get: { legToRemove != nil },
            set: { if !$0 { legToRemove = nil } }
        ), presenting: legToRemove) { leg in
            Button("Cancel", role: .cancel) {}
            Button("Remove", role: .destructive) {
                Task { await viewModel?.removeLeg(flightId: leg.flightId) }
            }
        } message: { leg in
            // "Remove" is an unlink and must never read as a delete.
            Text("\(leg.label) leaves this trip. The flight and its briefings are kept.")
        }
        .alert("Remove These Flights?", isPresented: $showUnfollowConfirm) {
            Button("Cancel", role: .cancel) {}
            Button("Remove", role: .destructive) {
                Task { await viewModel?.unfollow() }
            }
            .accessibilityIdentifier("confirmUnfollowTripButton")
        } message: {
            // Every leg goes at once, which is more than the one-tap button
            // suggests — and the web asks first, so iOS asking too keeps the two
            // from disagreeing about how destructive this is.
            Text("All legs of this shared trip leave your list. You can add them again from the trip link.")
        }
        .alert("Rename Trip", isPresented: $showRename) {
            TextField("Trip name", text: $renameText)
            Button("Cancel", role: .cancel) {}
            Button("Save") {
                Task { await viewModel?.rename(to: renameText) }
            }
        }
        .alert("Something went wrong", isPresented: Binding(
            get: { viewModel?.actionError != nil },
            set: { if !$0 { viewModel?.actionError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel?.actionError ?? "")
        }
    }

    @ViewBuilder
    private func content(trip: TripResponse, viewModel: TripDetailViewModel) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.sectionSpacing) {
                if !trip.isOwned {
                    TripSharedBanner(
                        trip: trip,
                        onFollow: { Task { await viewModel.follow() } },
                        onUnfollow: { showUnfollowConfirm = true }
                    )
                }

                TripBindingCallout(summary: trip.summary)

                if let progress = trip.refresh {
                    let message = progress.runMessage(legs: trip.summary.legs)
                    if progress.active || !message.isEmpty {
                        TripRefreshProgressView(status: progress, message: message)
                    }
                }

                TripTimelineView(
                    summary: trip.summary,
                    refreshingLegIds: viewModel.refreshingLegIds,
                    onOpenLeg: { onOpenLeg?($0) },
                    onRemoveLeg: removeLegHandler(for: trip)
                )

                if !trip.summary.continuityWarnings.isEmpty {
                    TripContinuityView(warnings: trip.summary.continuityWarnings)
                }

                if trip.isOwned {
                    TripAiSummaryView(
                        trip: trip,
                        unavailableReason: viewModel.aiUnavailableReason
                    )
                }
            }
            .padding(Theme.cardPadding)
        }
        .accessibilityIdentifier("tripDetail")
    }

    /// The unlink action, or nil on a trip the viewer does not own — the server
    /// refuses an unlink from anyone but the owner, so offering it could only
    /// ever produce an error.
    ///
    /// A named helper rather than a ternary at the call site: `cond ? { … } : nil`
    /// gives the type checker a closure literal and a nil to unify, which it
    /// cannot do without an explicit optional-closure type anyway.
    private func removeLegHandler(for trip: TripResponse) -> ((TripLeg) -> Void)? {
        guard trip.isOwned else { return nil }
        return { leg in legToRemove = leg }
    }

    /// The share link, or nil when the trip cannot be shared as it stands.
    ///
    /// Both conditions are the server's answer, not a guess: `isShareable` is
    /// false while any leg is private (the link would 404 for the recipient),
    /// and `shareCode` is minted server-side on the trip's first read.
    private func tripShareURL(for trip: TripResponse) -> URL? {
        guard trip.isShareable, let code = trip.shareCode, !code.isEmpty else {
            return nil
        }
        return AppState.tripShareURL(forShareCode: code)
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            // Every control here is an owner action the server refuses to anyone
            // else — refresh most of all, since a chain is admitted against the
            // *owner's* refresh slots and billed to them.
            if let viewModel, let trip = viewModel.trip, trip.isOwned {
                HStack(spacing: 12) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        if viewModel.isRefreshRunning {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("Refresh Trip", systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(viewModel.isRefreshRunning)
                    .accessibilityIdentifier("refreshTripButton")

                    Menu {
                        // Whether to auto-refresh is the *trip's* call — it
                        // refreshes the whole chain or none of it. The refresh
                        // *hour* stays each leg's, on its own briefing screen,
                        // because whichever leg comes due first pulls in the rest.
                        Toggle(isOn: Binding(
                            get: { trip.autoRefresh },
                            set: { value in Task { await viewModel.setAutoRefresh(value) } }
                        )) {
                            Label("Auto-refresh trip", systemImage: "clock.arrow.circlepath")
                        }

                        if let url = tripShareURL(for: trip) {
                            ShareLink(item: url) {
                                Label("Share Trip", systemImage: "square.and.arrow.up")
                            }
                            .accessibilityIdentifier("shareTripButton")
                        } else {
                            // All-or-nothing: one private leg and the link 404s
                            // for the recipient. Say why rather than hand out a
                            // dead link — only the owner can fix it, leg by leg.
                            Label(
                                "Make every leg shareable to share this trip",
                                systemImage: "lock"
                            )
                        }

                        Divider()

                        Button {
                            renameText = trip.name
                            showRename = true
                        } label: {
                            Label("Rename Trip", systemImage: "pencil")
                        }

                        Divider()

                        Button(role: .destructive) {
                            showDeleteConfirm = true
                        } label: {
                            Label("Delete Trip", systemImage: "trash")
                        }
                        .accessibilityIdentifier("deleteTripMenuItem")
                    } label: {
                        Label("More", systemImage: "ellipsis.circle")
                    }
                }
            }
        }
    }
}

/// The header on a trip someone else shared: whose it is, and the one action
/// that belongs to the recipient.
///
/// Deliberately a single action. Refresh, rename, delete and auto-refresh are
/// the owner's — refresh most sharply, since a chain is admitted against the
/// owner's per-user refresh slots and billed to them — so the recipient gets
/// the one decision that is genuinely theirs: keep these flights in my list, or
/// let them go.
struct TripSharedBanner: View {
    let trip: TripResponse
    var onFollow: () -> Void
    /// Raises the confirmation rather than unfollowing outright — it drops every
    /// leg at once, which one tap does not convey, and the web asks first too.
    var onUnfollow: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Label(sharedByText, systemImage: "person.crop.circle")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            // The web viewer says this in place of the controls it removes
            // (`trips.viewerNote`). iOS removes the whole toolbar instead, which
            // is *more* likely to read as a bug to a pilot who knows what their
            // own trip screen looks like — so say why rather than leave the
            // absence to be inferred.
            Text("You are viewing a shared trip. Only its owner can refresh or edit it.")
                .font(.caption)
                .foregroundStyle(.secondary)

            if trip.isSubscribed {
                HStack(spacing: Theme.spacingS) {
                    Text(followedText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Remove from My List", role: .destructive, action: onUnfollow)
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            } else {
                Button(action: onFollow) {
                    Label("Add These Flights to My List", systemImage: "plus.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("tripSharedBanner")
    }

    /// Never falls back to the owner's email — they shared a trip, not an
    /// address, and the server does not send one.
    private var sharedByText: String {
        if let owner = trip.ownerDisplayName, !owner.isEmpty {
            return "Shared by \(owner)"
        }
        return "Shared trip"
    }

    private var followedText: String {
        let count = trip.summary.totalLegs
        return count == 1
            ? "This leg is in your list"
            : "All \(count) legs are in your list"
    }
}

/// The deterministic binding-constraint callout — the thing the eye should land
/// on. `headline` is rendered **verbatim**: it is composed server-side so web,
/// iOS, the notification and the AI guardrail cannot disagree about which leg
/// decides the trip.
struct TripBindingCallout: View {
    let summary: TripSummary

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("DECIDES THIS TRIP")
                .font(.caption2.weight(.bold))
                .foregroundStyle(.secondary)
                .kerning(0.6)

            Text(summary.headline)
                .font(Theme.heroLabel)
                .fixedSize(horizontal: false, vertical: true)

            if let decidable = summary.decidableFromDate {
                Label(
                    "Decidable from \(DateFormatter.shortDate.string(from: decidable))",
                    systemImage: "calendar.badge.clock"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.cardPadding)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .overlay(alignment: .leading) {
            // Accent rail in the *binding leg's* colour, never a colour for the
            // trip. With no gradeable leg it stays neutral rather than inventing
            // a grade.
            RoundedRectangle(cornerRadius: 2)
                .fill(accent)
                .frame(width: 4)
                .padding(.vertical, 8)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("tripBindingCallout")
    }

    private var accent: Color {
        guard let leg = summary.bindingLeg else { return Color.secondary.opacity(0.4) }
        if leg.gradeKind == .outlook {
            return OutlookBadge.tint(for: leg.outlook ?? "")
        }
        return Assessment(rawValue: (leg.assessment ?? "").lowercased())?.color
            ?? Color.secondary.opacity(0.4)
    }
}

/// "Leg 2 of 3 · 2 of 3 legs had new data". `message` is shown verbatim: without
/// it a trip refresh that legitimately did almost nothing — every leg skipped
/// for want of a new model run — reads as one that failed. The caller passes
/// `TripRefreshStatus.runMessage(legs:)` rather than the raw field, so a
/// finished run's line disappears once a leg has been refreshed since.
struct TripRefreshProgressView: View {
    let status: TripRefreshStatus
    let message: String

    var body: some View {
        HStack(spacing: Theme.spacingS) {
            if status.active {
                ProgressView().controlSize(.small)
            }
            VStack(alignment: .leading, spacing: 2) {
                if status.active, status.total > 0 {
                    Text("Refreshing leg \(min(status.completed + 1, status.total)) of \(status.total)")
                        .font(.caption.weight(.medium))
                }
                if !message.isEmpty {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(Theme.spacingM)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        .accessibilityIdentifier("tripRefreshProgress")
    }
}

/// Soft continuity warnings — never a block. Pilots reposition, and a move is
/// allowed to break the chain; a broken chain is just usually a mistake worth
/// seeing.
struct TripContinuityView: View {
    let warnings: [ContinuityWarning]

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            ForEach(warnings) { warning in
                Label(
                    "Arrives \(warning.arrives) but next leg departs \(warning.departs)",
                    systemImage: "exclamationmark.triangle"
                )
                .font(.caption)
                .foregroundStyle(Theme.amber)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Theme.spacingM)
        .background(Theme.amber.opacity(0.1), in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
    }
}

/// The Haiku paragraph — **visually secondary by design**. It runs over
/// already-analysed conclusions and can only make the deterministic sentence
/// nicer to read, never different.
struct TripAiSummaryView: View {
    let trip: TripResponse
    /// From `POST /ai-summary` when there is no paragraph. Only `ai_disabled` is
    /// shown: a pilot who switched the AI digest off on one leg should see that
    /// the trip inherited it rather than an unexplained gap. The other reasons
    /// (generation failed, guardrail rejected) are ours, not theirs, and the
    /// deterministic headline already stands on its own — so they stay silent.
    var unavailableReason: String?

    var body: some View {
        if let text = trip.aiSummary, !text.isEmpty {
            VStack(alignment: .leading, spacing: Theme.spacingS) {
                Text("Trip summary")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(text)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityIdentifier("tripAiSummary")
        } else if unavailableReason == "ai_disabled" {
            Text("AI summaries are off for at least one leg of this trip, so this trip shows the deterministic summary only.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("tripAiDisabledNote")
        }
    }
}
