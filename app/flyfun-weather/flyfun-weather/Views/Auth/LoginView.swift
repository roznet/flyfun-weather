import AuthenticationServices
import SwiftUI

/// Sign-in screen with Apple and Google OAuth.
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

            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.fullName, .email]
            } onCompletion: { result in
                Task { await handleAppleSignIn(result) }
            }
            .signInWithAppleButtonStyle(.black)
            .frame(maxWidth: 280, minHeight: 50)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .disabled(isSigningIn)

            Button {
                Task { await signIn(provider: "google") }
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

            #if targetEnvironment(simulator)
            Button {
                Task { await devLogin() }
            } label: {
                Text("Dev Login")
                    .fontWeight(.semibold)
                    .frame(maxWidth: 280)
                    .padding()
                    .background(.gray)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(isSigningIn)
            #endif

            Spacer()
                .frame(height: 60)
        }
        .padding()
    }

    private func handleAppleSignIn(_ result: Result<ASAuthorization, Error>) async {
        isSigningIn = true
        errorMessage = nil
        defer { isSigningIn = false }
        do {
            let authorization = try result.get()
            guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential else {
                errorMessage = String(localized: "Unexpected credential type.")
                return
            }
            let token = try await authService.exchangeAppleCredential(credential, baseURL: AppState.defaultBaseURL)
            guard let callbackURL = URL(string: "flyfunweather://auth/callback?token=\(token)") else {
                errorMessage = String(localized: "Failed to create authentication URL.")
                return
            }
            appState.handleAuthCallback(url: callbackURL)
        } catch {
            if (error as? ASAuthorizationError)?.code != .canceled {
                errorMessage = error.localizedDescription
            }
        }
    }

    #if targetEnvironment(simulator)
    private func devLogin() async {
        isSigningIn = true
        errorMessage = nil
        defer { isSigningIn = false }
        do {
            let url = AppState.defaultBaseURL.appendingPathComponent("auth/dev-token")
            let (data, _) = try await URLSession.shared.data(from: url)
            let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard let token = json?["token"] as? String else {
                errorMessage = "No token in dev-token response"
                return
            }
            let callbackURL = URL(string: "flyfunweather://auth/callback?token=\(token)")!
            appState.handleAuthCallback(url: callbackURL)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
    #endif

    private func signIn(provider: String) async {
        isSigningIn = true
        errorMessage = nil
        defer { isSigningIn = false }
        do {
            let token = try await authService.signIn(baseURL: AppState.defaultBaseURL, provider: provider)
            let callbackURL = URL(string: "flyfunweather://auth/callback?token=\(token)")!
            appState.handleAuthCallback(url: callbackURL)
        } catch {
            if (error as? ASWebAuthenticationSessionError)?.code != .canceledLogin {
                errorMessage = error.localizedDescription
            }
        }
    }
}
