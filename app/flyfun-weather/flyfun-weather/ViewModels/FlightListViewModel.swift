import Foundation
import OSLog

/// Loading state for async data.
enum LoadingState<T> {
    case idle
    case loading
    case loaded(T)
    case error(Error)
}

/// View model for the flight list screen.
@Observable
@MainActor
final class FlightListViewModel {
    private(set) var state: LoadingState<[FlightResponse]> = .idle
    private let repository: any BriefingRepository
    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "FlightList")

    init(repository: any BriefingRepository) {
        self.repository = repository
    }

    func loadFlights() async {
        state = .loading
        do {
            let flights = try await repository.flights()
            state = .loaded(flights)
            Self.logger.info("Loaded \(flights.count) flights")
        } catch {
            state = .error(error)
            Self.logger.error("Failed to load flights: \(error)")
        }
    }
}
