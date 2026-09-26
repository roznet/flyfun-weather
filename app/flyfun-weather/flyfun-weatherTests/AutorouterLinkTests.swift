//
//  AutorouterLinkTests.swift
//  flyfun-weatherTests
//
//  In-app Autorouter linking (#625): callback parsing, and the picker's
//  not-linked → Connect → routes state machine on AddFlightViewModel.
//

import Testing
import Foundation
@testable import flyfun_weather

// MARK: - Callback parsing

struct AutorouterCallbackTests {
    @Test func linkedCallbackIsSuccess() throws {
        let url = URL(string: "flyfunweather://autorouter/callback?status=linked")!
        #expect(try AutorouterLinker.outcome(from: url))
    }

    @Test func errorCallbackCarriesReason() {
        let url = URL(string: "flyfunweather://autorouter/callback?status=error&reason=denied")!
        #expect(throws: AutorouterLinkError(reason: "denied")) {
            try AutorouterLinker.outcome(from: url)
        }
    }

    @Test func foreignCallbackIsRejected() {
        // The sign-in callback shares the scheme; it must never read as a link.
        let url = URL(string: "flyfunweather://auth/callback?code=x&state=y")!
        #expect(throws: AutorouterLinkError.self) {
            try AutorouterLinker.outcome(from: url)
        }
    }
}

// MARK: - AddFlightViewModel

private struct FakeLinker: AutorouterLinking {
    let result: Result<Bool, Error>
    func link(at url: URL) async throws -> Bool { try result.get() }
}

private let notLinked = APIError.serverError(409, "autorouter_not_linked")

private let sampleRoute = AutorouterRoute(
    routeid: "r1", departure: "EGTF", destination: "LFAT",
    departureName: nil, destinationName: nil, departureTime: nil,
    fplan: "(FPL-ZZABC-VG -SR22/L -S/C -EGTF0900 -N0170F090 DCT -LFAT0130)",
    routeDistanceNm: 180, aircraftDescription: nil, callsign: nil
)

@MainActor
struct AutorouterPickerStateTests {
    private func makeViewModel(
        routes: [Result<[AutorouterRoute], Error>]
    ) -> (AddFlightViewModel, MockBriefingRepository) {
        let repo = MockBriefingRepository()
        repo.autorouterRoutesResults = routes
        repo.autorouterLinkURLResult = .success(URL(string: "https://example.test/autorouter/link?ticket=t")!)
        return (AddFlightViewModel(repository: repo), repo)
    }

    @Test func notLinkedOffersConnectInsteadOfAnError() async {
        let (vm, _) = makeViewModel(routes: [.failure(notLinked)])
        await vm.loadAutorouterRoutes()
        #expect(vm.autorouterNeedsLink)
        #expect(vm.autorouterError == nil)
    }

    @Test func connectingLoadsTheRoutes() async {
        let (vm, repo) = makeViewModel(routes: [.failure(notLinked), .success([sampleRoute])])
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success(true)))

        #expect(linked)
        #expect(!vm.autorouterNeedsLink)
        #expect(vm.autorouterRoutes == [sampleRoute])
        #expect(repo.autorouterRoutesCallCount == 2)
    }

    @Test func cancellingStaysOnConnectWithoutAnError() async {
        let (vm, repo) = makeViewModel(routes: [.failure(notLinked)])
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success(false)))

        #expect(!linked)
        #expect(vm.autorouterNeedsLink)
        #expect(vm.autorouterLinkError == nil)
        #expect(repo.autorouterRoutesCallCount == 1)
    }

    @Test func declinedConsentExplainsAndStaysOnConnect() async {
        let (vm, _) = makeViewModel(routes: [.failure(notLinked)])
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(
            using: FakeLinker(result: .failure(AutorouterLinkError(reason: "denied")))
        )

        #expect(!linked)
        #expect(vm.autorouterNeedsLink)
        #expect(vm.autorouterLinkError?.contains("Allow") == true)
    }
}
