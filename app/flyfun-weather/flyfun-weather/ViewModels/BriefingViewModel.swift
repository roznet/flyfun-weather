import Foundation
import OSLog

enum BriefingTab: String, Hashable {
    case advisories
    case crossSection
    case map
    case digest
}

/// View model for the full briefing viewer.
@Observable
@MainActor
final class BriefingViewModel {
    let flight: FlightResponse
    private let repository: any BriefingRepository

    // Pack metadata
    private(set) var pack: PackMetaResponse?

    // Section states
    private(set) var advisoriesState: LoadingState<AdvisoriesResponse> = .idle
    private(set) var digestState: LoadingState<DigestResponse> = .idle
    private(set) var snapshotState: LoadingState<SnapshotResponse> = .idle
    private(set) var routeAnalysesState: LoadingState<RouteAnalysesResponse> = .idle
    private(set) var elevationState: LoadingState<ElevationResponse> = .idle

    // UI state
    var selectedTab: BriefingTab = .advisories
    var selectedModel: String = "gfs"
    var availableModels: [String] = []

    private static let logger = Logger(subsystem: "aero.flyfun.weather", category: "Briefing")

    init(flight: FlightResponse, repository: any BriefingRepository) {
        self.flight = flight
        self.repository = repository
    }

    func loadBriefing() async {
        do {
            let pack = try await repository.latestPack(flightId: flight.id)
            self.pack = pack

            // Extract available models
            let models = Array(pack.modelInitTimes.keys).sorted()
            if !models.isEmpty {
                availableModels = models
                if !models.contains(selectedModel) {
                    selectedModel = models.first ?? "gfs"
                }
            }

            // Fire parallel requests — individual failures don't block others
            await withTaskGroup(of: Void.self) { group in
                let ts = pack.fetchTimestamp

                group.addTask { await self.loadAdvisories(timestamp: ts) }
                group.addTask { await self.loadDigest(timestamp: ts) }
                group.addTask { await self.loadSnapshot(timestamp: ts) }
                group.addTask { await self.loadRouteAnalyses(timestamp: ts) }
                group.addTask { await self.loadElevation(timestamp: ts) }
            }
        } catch {
            Self.logger.error("Failed to load pack meta: \(error)")
            // Set all states to error since we can't proceed without pack
            advisoriesState = .error(error)
            digestState = .error(error)
            snapshotState = .error(error)
        }
    }

    // MARK: - Section loaders

    private func loadAdvisories(timestamp: String) async {
        advisoriesState = .loading
        do {
            advisoriesState = .loaded(try await repository.advisories(flightId: flight.id, timestamp: timestamp))
        } catch {
            advisoriesState = .error(error)
            Self.logger.error("Failed to load advisories: \(error)")
        }
    }

    private func loadDigest(timestamp: String) async {
        digestState = .loading
        do {
            digestState = .loaded(try await repository.digest(flightId: flight.id, timestamp: timestamp))
        } catch {
            digestState = .error(error)
            Self.logger.error("Failed to load digest: \(error)")
        }
    }

    private func loadSnapshot(timestamp: String) async {
        snapshotState = .loading
        do {
            snapshotState = .loaded(try await repository.snapshot(flightId: flight.id, timestamp: timestamp))
        } catch {
            snapshotState = .error(error)
            Self.logger.error("Failed to load snapshot: \(error)")
        }
    }

    private func loadRouteAnalyses(timestamp: String) async {
        routeAnalysesState = .loading
        do {
            let response = try await repository.routeAnalyses(flightId: flight.id, timestamp: timestamp)
            routeAnalysesState = .loaded(response)
            // Update available models from route analyses (authoritative for cross-section)
            if !response.models.isEmpty {
                let raModels = response.models.sorted()
                availableModels = raModels
                if !raModels.contains(selectedModel) {
                    selectedModel = raModels.first ?? selectedModel
                    Self.logger.info("Switched model to \(self.selectedModel) (previous not in route analyses)")
                }
            }
        } catch {
            routeAnalysesState = .error(error)
            Self.logger.error("Failed to load route analyses: \(error)")
        }
    }

    private func loadElevation(timestamp: String) async {
        elevationState = .loading
        do {
            elevationState = .loaded(try await repository.elevation(flightId: flight.id, timestamp: timestamp))
        } catch {
            elevationState = .error(error)
            Self.logger.error("Failed to load elevation: \(error)")
        }
    }
}
