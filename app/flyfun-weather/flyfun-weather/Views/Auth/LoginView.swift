import AuthenticationServices
import FlyFunCommon
import SwiftUI

/// Sign-in screen with Apple and Google OAuth.
struct LoginView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.colorScheme) private var colorScheme

    @State private var isSigningIn = false
    @State private var errorMessage: String?

    private var authService: FlyFunAuthService {
        FlyFunAuthService(config: .init(
            baseURL: AppState.defaultBaseURL,
            callbackScheme: "flyfunweather"
        ))
    }

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

            VStack(spacing: 12) {
                SignInWithAppleButton(.signIn) { request in
                    request.requestedScopes = [.fullName, .email]
                } onCompletion: { result in
                    Task { await handleAppleSignIn(result) }
                }
                .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                .frame(width: 175, height: 40)
                .disabled(isSigningIn)

                Button {
                    Task { await signIn(provider: "google") }
                } label: {
                    Image("SignInWithGoogle")
                        .resizable()
                        .frame(width: 175, height: 40)
                        .overlay {
                            if isSigningIn {
                                ProgressView().controlSize(.small)
                            }
                        }
                }
                .buttonStyle(.plain)
                .disabled(isSigningIn)

                #if DEBUG
                Button {
                    Task { await devLogin() }
                } label: {
                    Text("Dev Login")
                        .font(.footnote.weight(.semibold))
                        .frame(width: 175, height: 32)
                        .background(.gray)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(isSigningIn)
                .padding(.top, 4)
                #endif
            }

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
            let token = try await authService.exchangeAppleCredential(credential)
            appState.signIn(token: token)
        } catch {
            if (error as? ASAuthorizationError)?.code != .canceled {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func signIn(provider: String) async {
        isSigningIn = true
        errorMessage = nil
        defer { isSigningIn = false }
        do {
            let token = try await authService.signIn(provider: provider)
            appState.signIn(token: token)
        } catch {
            if (error as? ASWebAuthenticationSessionError)?.code != .canceledLogin {
                errorMessage = error.localizedDescription
            }
        }
    }

    #if DEBUG
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
            appState.signIn(token: token)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
    #endif
}
