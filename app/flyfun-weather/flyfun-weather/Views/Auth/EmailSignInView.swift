import FlyFunCommon
import SwiftUI

/// Email (magic-link) sign-in, presented as a sheet from ``LoginView``.
///
/// The server emails both a click-through link (used by the web app) and a
/// 6-digit code. The app uses the **code** so sign-in completes without ever
/// leaving the app — that is what `/auth/magic-link/consume-code` exists for.
///
/// Two stages: ask for the address, then ask for the code it was sent to.
struct EmailSignInView: View {
    let authService: FlyFunAuthService
    /// Called with the session JWT once the server accepts the code.
    let onSignedIn: (String) -> Void

    @Environment(\.dismiss) private var dismiss

    private enum Stage {
        case email
        case code
    }

    private enum Field {
        case email
        case code
    }

    /// Addresses on Apple's relay bounce, so the server rejects them outright.
    private static let applePrivateRelaySuffix = "@privaterelay.appleid.com"

    /// Seconds to wait before offering "Resend code" again. The server also
    /// rate-limits per address; this just keeps us from provoking a 429.
    private static let resendCooldownSeconds = 30

    @State private var stage: Stage = .email
    @State private var email = ""
    @State private var code = ""
    @State private var isBusy = false
    @State private var errorMessage: String?
    @State private var resendCooldown = 0
    @FocusState private var focusedField: Field?

    var body: some View {
        NavigationStack {
            Form {
                switch stage {
                case .email:
                    emailStageSections
                case .code:
                    codeStageSections
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Sign in with Email")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(isBusy)
                }
            }
            .onAppear { focusedField = .email }
        }
        .interactiveDismissDisabled(isBusy)
    }

    // MARK: - Stage 1: address

    @ViewBuilder
    private var emailStageSections: some View {
        Section {
            TextField("you@example.com", text: $email)
                .textContentType(.emailAddress)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($focusedField, equals: .email)
                .submitLabel(.send)
                .onSubmit { Task { await requestCode() } }
                .disabled(isBusy)
        } header: {
            Text("Email address")
        } footer: {
            Text("We'll email you a 6-digit code — no password needed.")
        }

        Section {
            Button {
                Task { await requestCode() }
            } label: {
                busyLabel("Email me a code")
            }
            .disabled(isBusy || !isPlausibleEmail(normalizedEmail))
        }
    }

    // MARK: - Stage 2: code

    @ViewBuilder
    private var codeStageSections: some View {
        Section {
            TextField("123456", text: $code)
                .textContentType(.oneTimeCode)
                .keyboardType(.numberPad)
                .font(.title2.monospacedDigit())
                .focused($focusedField, equals: .code)
                .disabled(isBusy)
                .onChange(of: code) { _, newValue in
                    // Keep it to at most 6 digits so the Sign In button's
                    // enablement rule stays honest.
                    let digits = String(newValue.filter(\.isNumber).prefix(6))
                    if digits != newValue { code = digits }
                }
        } header: {
            Text("6-digit code")
        } footer: {
            Text("Sent to \(normalizedEmail). The code expires in 15 minutes.")
        }

        Section {
            Button {
                Task { await submitCode() }
            } label: {
                busyLabel("Sign In")
            }
            .disabled(isBusy || code.count != 6)

            Button(resendCooldown > 0
                   ? "Resend code in \(resendCooldown)s"
                   : "Resend code") {
                Task { await requestCode() }
            }
            .disabled(isBusy || resendCooldown > 0)

            Button("Use a different email") {
                stage = .email
                code = ""
                errorMessage = nil
                focusedField = .email
            }
            .disabled(isBusy)
        }
    }

    @ViewBuilder
    private func busyLabel(_ title: LocalizedStringKey) -> some View {
        HStack {
            Spacer()
            if isBusy {
                ProgressView().controlSize(.small)
            } else {
                Text(title)
            }
            Spacer()
        }
    }

    // MARK: - Actions

    private var normalizedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    /// Mirrors the deliberately permissive server-side check in flyfun-common's
    /// `magic_link.py` — enough to catch obvious garbage before a round trip.
    private func isPlausibleEmail(_ value: String) -> Bool {
        value.range(of: #"^[^@\s]+@[^@\s]+\.[^@\s]+$"#, options: .regularExpression) != nil
    }

    private func requestCode() async {
        guard !isBusy else { return }
        let address = normalizedEmail

        guard isPlausibleEmail(address) else {
            errorMessage = String(localized: "Enter a valid email address.")
            return
        }
        // The server 400s on these with a helpful message, but FlyFunCommon
        // drops the response body (it logs `detail` and throws a bare
        // URLError), so check here to keep the advice reachable.
        guard !address.hasSuffix(Self.applePrivateRelaySuffix) else {
            errorMessage = String(localized: "Apple Private Relay addresses can't receive mail from us. Use Sign in with Apple instead.")
            return
        }

        isBusy = true
        errorMessage = nil
        defer { isBusy = false }

        do {
            try await authService.requestMagicLinkCode(email: address)
            email = address
            stage = .code
            focusedField = .code
            startResendCooldown()
        } catch {
            // A rate-limit 429 and a rejected address arrive here identically,
            // so the message has to cover both.
            errorMessage = String(localized: "Couldn't send the code. Check the address, or wait a few minutes if you've already asked for several codes.")
        }
    }

    private func submitCode() async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }

        do {
            let token = try await authService.consumeMagicLinkCode(
                email: normalizedEmail,
                code: code
            )
            dismiss()
            onSignedIn(token)
        } catch {
            code = ""
            focusedField = .code
            errorMessage = String(localized: "That code didn't work. It expires after 15 minutes, and is locked out after five wrong tries — request a new one if you're stuck.")
        }
    }

    private func startResendCooldown() {
        resendCooldown = Self.resendCooldownSeconds
        Task {
            while resendCooldown > 0 {
                try? await Task.sleep(for: .seconds(1))
                resendCooldown = max(0, resendCooldown - 1)
            }
        }
    }
}
