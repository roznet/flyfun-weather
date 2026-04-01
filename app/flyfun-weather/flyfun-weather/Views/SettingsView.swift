import SwiftUI

/// App settings: account management, links, and legal.
struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    @State private var showDeleteConfirmation = false
    @State private var showFinalConfirmation = false
    @State private var isDeleting = false
    @State private var errorMessage: String?

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
                    Label("Supplementary Tool", systemImage: "info.circle")
                        .foregroundStyle(.secondary)
                } footer: {
                    Text("FlyFun Weather is an exploratory briefing tool. It is not a substitute for official weather briefings (e.g. FSS, DATIS, or your country's AIS). Always verify conditions through approved sources before flight.")
                }

                Section {
                    Button {
                        appState.logout()
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
