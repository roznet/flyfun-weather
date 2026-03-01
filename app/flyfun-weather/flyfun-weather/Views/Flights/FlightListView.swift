import SwiftUI

/// Main screen showing the user's saved flights.
struct FlightListView: View {
    @Environment(AppState.self) private var appState
    @State private var viewModel: FlightListViewModel?

    var body: some View {
        NavigationStack {
            Group {
                if let viewModel {
                    LoadingStateView(state: viewModel.state, retryAction: viewModel.loadFlights) { flights in
                        if flights.isEmpty {
                            ContentUnavailableView(
                                "No Flights",
                                systemImage: "airplane",
                                description: Text("Create a flight on weather.flyfun.aero to see it here.")
                            )
                        } else {
                            List(flights) { flight in
                                NavigationLink(value: flight) {
                                    FlightCardView(flight: flight)
                                }
                            }
                            .refreshable {
                                await viewModel.loadFlights()
                            }
                        }
                    }
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Flights")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Sign Out", systemImage: "rectangle.portrait.and.arrow.right") {
                        appState.logout()
                    }
                }
            }
            .navigationDestination(for: FlightResponse.self) { flight in
                BriefingContainerView(flight: flight)
            }
        }
        .task {
            guard let repo = appState.repository else { return }
            let vm = FlightListViewModel(repository: repo)
            viewModel = vm
            await vm.loadFlights()
        }
    }
}

extension FlightResponse: Hashable {
    static func == (lhs: FlightResponse, rhs: FlightResponse) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}
