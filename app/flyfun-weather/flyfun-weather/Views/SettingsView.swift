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
        }
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
