import SwiftUI

/// One app-wide strip shown while the server can't answer (maintenance or an
/// outage), carrying the server's own sentence. Driven by
/// `ServiceStatusStore.shared`; disappears on the next successful request.
struct ServiceStatusBanner: View {
    let status: ServiceStatus

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: status.kind == .maintenance ? "wrench.and.screwdriver" : "exclamationmark.icloud")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(status.message)
                    .font(.footnote.weight(.medium))
                    .fixedSize(horizontal: false, vertical: true)
                if let until = status.until {
                    Text("Your time: \(until.formatted(date: .omitted, time: .shortened))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Text("Saved flights stay available.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity)
        .background(.orange.opacity(0.12))
        .background(.regularMaterial)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("serviceStatusBanner")
    }
}

/// Puts `ServiceStatusBanner` above the content whenever the store holds a status.
struct ServiceStatusInset: ViewModifier {
    @State private var store = ServiceStatusStore.shared

    func body(content: Content) -> some View {
        content.safeAreaInset(edge: .top, spacing: 0) {
            if let status = store.current {
                ServiceStatusBanner(status: status)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.default, value: store.current)
    }
}

extension View {
    func serviceStatusBanner() -> some View { modifier(ServiceStatusInset()) }
}
