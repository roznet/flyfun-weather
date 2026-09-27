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
    @Test func authorizedCallbackYieldsTheCodeToRedeem() throws {
        let url = URL(string: "flyfunweather://autorouter/callback?status=authorized&code=abc.def")!
        #expect(try AutorouterLinker.outcome(from: url) == "abc.def")
    }

    @Test func authorizedWithoutACodeIsAFailure() {
        let url = URL(string: "flyfunweather://autorouter/callback?status=authorized")!
        #expect(throws: AutorouterLinkError.self) {
            try AutorouterLinker.outcome(from: url)
        }
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
    /// A code = approved; nil = cancelled.
    let result: Result<String?, Error>
    func link(at url: URL) async throws -> String? { try result.get() }
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

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success("code-1")))

        #expect(linked)
        #expect(repo.completedAutorouterLinkCodes == ["code-1"])
        #expect(!vm.autorouterNeedsLink)
        #expect(vm.autorouterRoutes == [sampleRoute])
        #expect(repo.autorouterRoutesCallCount == 2)
    }

    @Test func reloadFailingAfterLinkShowsTheErrorNotConnect() async {
        // Linked fine, but the follow-up route load hits a network error:
        // the pilot must see that error, not be asked to connect again.
        let (vm, _) = makeViewModel(routes: [.failure(notLinked), .failure(URLError(.timedOut))])
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success("code-1")))

        #expect(linked)
        #expect(!vm.autorouterNeedsLink)
        #expect(vm.autorouterError != nil)
    }

    @Test func refusedRedemptionStaysOnConnect() async {
        // Server says the code isn't this account's (403): nothing linked.
        let (vm, repo) = makeViewModel(routes: [.failure(notLinked)])
        repo.completeAutorouterLinkError = APIError.serverError(403, "Link code belongs to another account")
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success("someone-elses")))

        #expect(!linked)
        #expect(vm.autorouterNeedsLink)
        #expect(vm.autorouterLinkError != nil)
        #expect(repo.autorouterRoutesCallCount == 1)
    }

    @Test func cancellingStaysOnConnectWithoutAnError() async {
        let (vm, repo) = makeViewModel(routes: [.failure(notLinked)])
        await vm.loadAutorouterRoutes()

        let linked = await vm.connectAutorouter(using: FakeLinker(result: .success(nil)))

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
