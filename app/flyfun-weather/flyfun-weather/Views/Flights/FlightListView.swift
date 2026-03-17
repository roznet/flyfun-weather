import SwiftUI

/// Main screen showing the user's saved flights with sidebar/detail split on iPad.
struct FlightListView: View {
    @Environment(AppState.self) private var appState
    @State private var viewModel: FlightListViewModel?
    @State private var selectedFlight: FlightResponse?
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
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
                            List(flights, selection: $selectedFlight) { flight in
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
        } detail: {
            if let selectedFlight {
                BriefingContainerView(flight: selectedFlight)
                    .id(selectedFlight.id)
            } else {
                ContentUnavailableView("Select a Flight", systemImage: "airplane",
                                       description: Text("Choose a flight from the list to view its briefing."))
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
