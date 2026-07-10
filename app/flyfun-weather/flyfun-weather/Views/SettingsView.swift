import SwiftUI
#if DEBUG
import TipKit
#endif

/// App settings: account management, links, and legal.
struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    @State private var showSignOutWarning = false
    @State private var showDeleteConfirmation = false
    @State private var showFinalConfirmation = false
    @State private var isDeleting = false
    @State private var errorMessage: String?
    @State private var pushBusy = false
    @State private var notifyBusy = false
    @State private var showPushDeniedAlert = false
    #if DEBUG
    @State private var tipsResetConfirmation: String?
    #endif

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        openURL(AppState.defaultBaseURL)
                    } label: {
                        Label("Open on Website", systemImage: "safari")
                    }
                }

                Section {
                    Link(destination: URL(string: "https://weather.flyfun.aero/privacy")!) {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                }

                Section {
                    if notifyPrefs.decayNotice {
                        Label(
                            "Email was turned back on because you removed your last push device — otherwise you'd have no way to receive updates.",
                            systemImage: "envelope.badge"
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    }

                    Picker("Briefing updates", selection: Binding(
                        get: { notifyPrefs.briefingUpdates },
                        set: { setBriefingUpdates($0) }
                    )) {
                        Text("Off").tag(BriefingUpdates.off)
                        Text("Assessment changes").tag(BriefingUpdates.changes)
                        Text("Every update").tag(BriefingUpdates.every)
                    }
                    .disabled(notifyBusy || appState.apiClient == nil)

                    Toggle("Email", isOn: Binding(
                        get: { notifyPrefs.emailEnabled },
                        set: { setEmail($0) }
                    ))
                    .disabled(emailLocked || notifyBusy || appState.apiClient == nil)

                    Toggle("Push", isOn: Binding(
                        get: { notifyPrefs.pushEnabled },
                        set: { setPush($0) }
                    ))
                    .disabled(pushBusy || appState.apiClient == nil)
                } header: {
                    Text("Notifications")
                } footer: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("“Assessment changes” notifies when an update changes your flight's assessment — the GREEN/AMBER/RED headline or early outlook. Push is suppressed while you're looking at the app.")
                        if emailLocked {
                            Text("Email is your only channel — set Briefing updates to Off to stop, or enable Push.")
                        }
                    }
                }

                Section {
                    Picker("Auto-Download", selection: Binding(
                        get: { appState.settings.autoDownloadMode },
                        set: { appState.settings.autoDownloadMode = $0 }
                    )) {
                        ForEach(AutoDownloadMode.allCases) { mode in
                            Text(mode.label).tag(mode)
                        }
                    }
                } header: {
                    Text("Offline")
                } footer: {
                    Text("Automatically download upcoming briefings (today and later) for offline use, so Skew-Ts and the full briefing open instantly without a connection. Briefings for flights more than \(AppState.cacheRetentionDays) days past are cleared automatically.")
                }

                Section {
                    Label("Supplementary Tool", systemImage: "info.circle")
                        .foregroundStyle(.secondary)
                } footer: {
                    Text("FlyFun Weather is an exploratory briefing tool. It is not a substitute for official weather briefings (e.g. FSS, DATIS, or your country's AIS). Always verify conditions through approved sources before flight.")
                }

                Section {
                    Button {
                        Task {
                            if let caching = appState.cachingRepository,
                               !(await caching.cachedPacks()).isEmpty {
                                showSignOutWarning = true
                            } else {
                                appState.logout()
                            }
                        }
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }

                Section {
                    Button(role: .destructive) {
                        showDeleteConfirmation = true
                    } label: {
                        if isDeleting {
                            HStack {
                                Label("Deleting Account...", systemImage: "trash")
                                Spacer()
                                ProgressView()
                            }
                        } else {
                            Label("Delete Account", systemImage: "trash")
                        }
                    }
                    .disabled(isDeleting)
                } footer: {
                    Text("Permanently deletes your account and all associated data (flights, briefings, preferences). This cannot be undone.")
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                            .font(.caption)
                    }
                }

                #if DEBUG
                // Developer-only: TipKit persists which coachmarks have been
                // seen, so they never reappear once dismissed. `resetDatastore()`
                // can only run *before* `Tips.configure()` (it throws
                // `tipsDatastoreAlreadyConfigured` at runtime), so for in-app
                // testing we use the session overrides instead: "Show All"
                // force-displays every tip ignoring seen-state and eligibility
                // gates; "Reset" clears that override. Mirrors the debug server
                // picker — present only in DEBUG builds.
                Section {
                    Button {
                        Tips.showAllTipsForTesting()
                        tipsResetConfirmation = "Showing all tips — open the briefing / Cross-Section tab to see them."
                    } label: {
                        Label("Show All Tips", systemImage: "lightbulb")
                    }
                    Button {
                        Tips.hideAllTipsForTesting()
                        tipsResetConfirmation = "Override cleared — tips follow their normal seen-state and gates again."
                    } label: {
                        Label("Reset Tips Override", systemImage: "lightbulb.slash")
                    }
                } header: {
                    Text("Developer")
                } footer: {
                    Text(tipsResetConfirmation
                         ?? "Force-show the briefing coachmarks (#312) for testing. Session-only — overrides reset on relaunch.")
                }
                #endif
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .confirmationDialog(
                "Delete Account?",
                isPresented: $showDeleteConfirmation,
                titleVisibility: .visible
            ) {
                Button("Delete Account", role: .destructive) {
                    showFinalConfirmation = true
                }
            } message: {
                Text("All your flights, briefings, and settings will be permanently removed.")
            }
            .confirmationDialog(
                "Are you sure?",
                isPresented: $showFinalConfirmation,
                titleVisibility: .visible
            ) {
                Button("Permanently Delete", role: .destructive) {
                    Task { await performDeleteAccount() }
                }
            } message: {
                Text("This action cannot be undone. Your account and all data will be permanently deleted.")
            }
            .alert("Sign Out?", isPresented: $showSignOutWarning) {
                Button("Sign Out", role: .destructive) { appState.logout() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You have downloaded packs. They won't be accessible until you sign in again.")
            }
            .alert("Notifications Disabled", isPresented: $showPushDeniedAlert) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("Enable notifications for FlyFun Weather in the iOS Settings app to receive briefing alerts.")
            }
            .onDisappear { dismissDecayNoticeIfShown() }
        }
    }

    /// Current notification preferences (server-cached).
    private var notifyPrefs: PreferencesResponse { appState.userPreferences.preferences }

    /// Push is a working channel only with a registered device and the flag on.
    private var pushAvailableAndOn: Bool { notifyPrefs.hasPushDevice && notifyPrefs.pushEnabled }

    /// Email is locked on only when there's no registered device at all — email
    /// is then the sole *available* channel while updates are active, so it can't
    /// be turned off (the only way to silence is Briefing updates = Off). When a
    /// device exists, both toggles stay editable and turning off the last enabled
    /// one reroutes to Off — mirroring the web model exactly (channel invariant).
    private var emailLocked: Bool { notifyPrefs.briefingUpdates != .off && !notifyPrefs.hasPushDevice }

    /// Toggle the push channel: on turn-on request iOS authorization + register,
    /// and only persist `notify_push=true` once granted; on turn-off just persist
    /// the flag (the device stays registered so the badge keeps syncing).
    private func setPush(_ enabled: Bool) {
        guard let client = appState.apiClient else { return }
        pushBusy = true
        Task {
            if enabled {
                let granted = await appState.requestPushAuthorizationAndRegister()
                if !granted {
                    showPushDeniedAlert = true
                    pushBusy = false
                    return
                }
            } else if !notifyPrefs.emailEnabled && notifyPrefs.briefingUpdates != .off {
                // Turning off push while email is already off would strand the
                // user with no channel — "silence me" → reroute to Off (mirrors
                // setEmail, and the web pushToggle reroute). Without this the
                // channel invariant leaks into the silent dead-state.
                await appState.userPreferences.updateBriefingUpdates(.off, using: client)
            }
            await appState.userPreferences.updateNotifyPush(enabled, using: client)
            pushBusy = false
        }
    }

    /// Change the "Briefing updates" 3-stop. Leaving Off with no channel on
    /// re-enables email so scope≠off always keeps a working channel.
    private func setBriefingUpdates(_ updates: BriefingUpdates) {
        guard let client = appState.apiClient else { return }
        notifyBusy = true
        Task {
            if updates != .off && !notifyPrefs.emailEnabled && !pushAvailableAndOn {
                await appState.userPreferences.updateNotifyEmail(true, using: client)
            }
            await appState.userPreferences.updateBriefingUpdates(updates, using: client)
            notifyBusy = false
        }
    }

    /// Toggle the email channel. Turning off the only available channel means
    /// "silence me" → reroute to Briefing updates = Off (belt-and-suspenders;
    /// the toggle is disabled in that state, so this rarely runs).
    private func setEmail(_ enabled: Bool) {
        guard let client = appState.apiClient else { return }
        notifyBusy = true
        Task {
            if !enabled && !pushAvailableAndOn && notifyPrefs.briefingUpdates != .off {
                await appState.userPreferences.updateBriefingUpdates(.off, using: client)
            }
            await appState.userPreferences.updateNotifyEmail(enabled, using: client)
            notifyBusy = false
        }
    }

    /// Dismiss the one-time decay notice when the user leaves Settings.
    private func dismissDecayNoticeIfShown() {
        guard notifyPrefs.decayNotice, let client = appState.apiClient else { return }
        Task { await appState.userPreferences.dismissDecayNotice(using: client) }
    }

    private func performDeleteAccount() async {
        isDeleting = true
        errorMessage = nil
        do {
            try await appState.deleteAccount()
        } catch {
            errorMessage = "Failed to delete account: \(error.localizedDescription)"
            isDeleting = false
        }
    }
}
