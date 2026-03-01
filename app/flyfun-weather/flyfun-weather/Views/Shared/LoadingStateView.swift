import SwiftUI

/// Generic view wrapper that displays loading, error, or content based on state.
struct LoadingStateView<T, Content: View>: View {
    let state: LoadingState<T>
    let retryAction: () async -> Void
    @ViewBuilder let content: (T) -> Content

    var body: some View {
        switch state {
        case .idle:
            Color.clear
        case .loading:
            ProgressView("Loading...")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .loaded(let data):
            content(data)
        case .error(let error):
            ContentUnavailableView {
                Label("Error", systemImage: "exclamationmark.triangle")
            } description: {
                Text(error.localizedDescription)
            } actions: {
                Button("Retry") {
                    Task { await retryAction() }
                }
            }
        }
    }
}
