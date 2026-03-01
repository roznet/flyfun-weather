import SwiftUI

/// Sign-in screen with Google OAuth.
struct LoginView: View {
    @Environment(AppState.self) private var appState

    @State private var isSigningIn = false
    @State private var errorMessage: String?

    private let authService = AuthService()

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            Image(systemName: "cloud.sun.fill")
                .font(.system(size: 80))
                .foregroundStyle(.blue)

            Text("WeatherBrief")
                .font(.largeTitle.bold())

            Text("Aviation weather briefings\nfor cross-country flights")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
            }

            Button {
                Task { await signIn() }
            } label: {
                HStack {
                    if isSigningIn {
                        ProgressView()
                            .tint(.white)
                    }
                    Text("Sign in with Google")
                        .fontWeight(.semibold)
                }
                .frame(maxWidth: 280)
                .padding()
                .background(.blue)
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(isSigningIn)

            Spacer()
                .frame(height: 60)
        }
        .padding()
    }

    private func signIn() async {
        isSigningIn = true
        errorMessage = nil
        do {
            let token = try await authService.signIn(baseURL: AppState.defaultBaseURL)
            // Build a callback URL and pass through the standard handler
            let callbackURL = URL(string: "weatherbrief://auth/callback?token=\(token)")!
            appState.handleAuthCallback(url: callbackURL)
        } catch {
            if (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin {
                // User cancelled — not an error
            } else {
                errorMessage = error.localizedDescription
            }
        }
        isSigningIn = false
    }
}

import AuthenticationServices
