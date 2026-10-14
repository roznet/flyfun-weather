//
//  AircraftListViewModelTests.swift
//  flyfun-weatherTests
//
//  Settings → My Aircraft (#778): list order, delete (incl. the server's
//  last-aircraft 409), make default, add over the placeholder, and the row
//  text a placeholder shows.
//

import Testing
import Foundation
@testable import flyfun_weather

@MainActor
@Suite struct AircraftListViewModelTests {

    @Test func loadPutsTheDefaultFirst() async {
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([
            makeAircraft(id: 1, typeName: "Alpha"),
            makeAircraft(id: 2, typeName: "Zulu", isDefault: true),
            makeAircraft(id: 3, typeName: "Bravo"),
        ])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        #expect(vm.aircraft.map(\.id) == [2, 1, 3])
        #expect(vm.hasLoaded)
        #expect(vm.errorMessage == nil)
    }

    @Test func loadFailureKeepsTheListAndSaysSo() async {
        let repo = MockBriefingRepository()
        repo.aircraftResult = .failure(MockError.injected("offline"))
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        #expect(vm.aircraft.isEmpty)
        #expect(vm.hasLoaded == false)
        #expect(vm.loadFailed)
        // The empty list says so inline; no alert on top of it.
        #expect(vm.errorMessage == nil)
    }

    @Test func refreshFailureOverRowsAlerts() async {
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([makeAircraft(id: 1, typeName: "Alpha", isDefault: true)])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        repo.aircraftResult = .failure(MockError.injected("offline"))
        await vm.load()
        #expect(vm.aircraft.map(\.id) == [1])
        #expect(vm.loadFailed)
        #expect(vm.errorMessage?.hasPrefix("Couldn't refresh your aircraft") == true)
    }

    /// A load requested while one is in flight (the re-read after a make
    /// default racing a pull-to-refresh) runs once the first finishes, so the
    /// list ends on the post-change state rather than the stale response.
    @Test func loadDuringLoadIsQueuedNotDropped() async {
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([makeAircraft(id: 1, typeName: "Alpha", isDefault: true)])
        let vm = AircraftListViewModel(repository: repo)
        repo.aircraftHook = {
            repo.aircraftHook = nil
            repo.aircraftResult = .success([
                makeAircraft(id: 1, typeName: "Alpha"),
                makeAircraft(id: 2, typeName: "Bravo", isDefault: true),
            ])
            await vm.load()  // in flight: queued, returns at once
        }
        await vm.load()
        #expect(repo.aircraftCallCount == 2)
        #expect(vm.aircraft.map(\.id) == [2, 1])
        #expect(vm.isLoading == false)
    }

    /// Deleting the default: the server promotes another, so the list is
    /// re-read and the survivor shows as the default.
    @Test func deletingTheDefaultRereadsThePromotedList() async {
        let zulu = makeAircraft(id: 2, typeName: "Zulu", isDefault: true)
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([makeAircraft(id: 1, typeName: "Alpha"), zulu])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()

        repo.aircraftResult = .success([makeAircraft(id: 1, typeName: "Alpha", isDefault: true)])
        #expect(await vm.delete(zulu))
        #expect(repo.deletedAircraftIds == [2])
        #expect(vm.aircraft.map(\.id) == [1])
        #expect(vm.aircraft.first?.isDefault == true)
        #expect(vm.errorMessage == nil)
        #expect(vm.busyIds.isEmpty)
    }

    /// The server refuses the last aircraft (409): the row stays and the
    /// pilot reads the server's own sentence.
    @Test func deletingTheLastAircraftKeepsTheRowAndShowsTheServerMessage() async {
        let only = makeAircraft(id: 5, tailNumber: "ZZ-ABC", isDefault: true)
        let detail = "You can't delete your only aircraft. Edit it instead, or add another aircraft first."
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([only])
        repo.deleteAircraftError = APIError.serverError(409, detail)
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()

        #expect(await vm.delete(only) == false)
        #expect(vm.aircraft.map(\.id) == [5])
        #expect(vm.errorMessage == detail)
        #expect(vm.busyIds.isEmpty)
    }

    @Test func deleteFailureMessages() {
        #expect(AircraftListViewModel.deleteFailureMessage(for: APIError.serverError(409, nil))
                == "You can't delete your only aircraft.")
        #expect(AircraftListViewModel.deleteFailureMessage(for: APIError.serverError(409, "Nope."))
                == "Nope.")
        #expect(AircraftListViewModel.deleteFailureMessage(for: APIError.notFound)
                == "Failed to delete: Resource not found.")
    }

    @Test func deleteConfirmationNamesTheAircraftLikeTheWeb() {
        #expect(AircraftListViewModel.deleteConfirmationTitle(
            for: makeAircraft(tailNumber: "ZZ-ABC", nickname: "Archie")) == "Delete ZZ-ABC?")
        #expect(AircraftListViewModel.deleteConfirmationTitle(
            for: makeAircraft(nickname: "Archie")) == "Delete Archie?")
        #expect(AircraftListViewModel.deleteConfirmationTitle(
            for: makeAircraft(typeName: "Cessna 172")) == "Delete Cessna 172?")
    }

    /// Make default sends only `is_default: true`, then re-reads: the new
    /// default moves to the top.
    @Test func makeDefaultPutsOnlyTheFlagAndReorders() async throws {
        let alpha = makeAircraft(id: 1, typeName: "Alpha")
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([alpha, makeAircraft(id: 2, typeName: "Zulu", isDefault: true)])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        #expect(vm.aircraft.map(\.id) == [2, 1])

        repo.updateAircraftResult = .success(makeAircraft(id: 1, typeName: "Alpha", isDefault: true))
        repo.aircraftResult = .success([
            makeAircraft(id: 1, typeName: "Alpha", isDefault: true),
            makeAircraft(id: 2, typeName: "Zulu"),
        ])
        await vm.makeDefault(alpha)

        #expect(repo.lastUpdateAircraftId == 1)
        let req = try #require(repo.lastUpdateAircraftRequest)
        #expect(req.isDefault == true)
        #expect(req.icaoType == nil)
        #expect(req.tailNumber == nil)     // nothing else touched
        #expect(req.nickname == nil)
        #expect(req.cruiseSpeedKt == nil)
        #expect(vm.aircraft.map(\.id) == [1, 2])
    }

    @Test func makeDefaultOnTheDefaultDoesNothing() async {
        let zulu = makeAircraft(id: 2, isDefault: true)
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([zulu])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        await vm.makeDefault(zulu)
        #expect(repo.lastUpdateAircraftId == nil)
    }

    @Test func makeDefaultFailureSaysSo() async {
        let alpha = makeAircraft(id: 1)
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([alpha, makeAircraft(id: 2, isDefault: true)])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()
        repo.updateAircraftResult = .failure(MockError.injected("boom"))
        await vm.makeDefault(alpha)
        #expect(vm.errorMessage?.hasPrefix("Couldn't make it the default") == true)
        #expect(vm.aircraft.first?.id == 2)
    }

    /// Adding over the server's placeholder: "Default aircraft" starts on, the
    /// add is a POST, and the re-read shows the server's replacement.
    @Test func addOverThePlaceholderPostsAndRereads() async throws {
        let placeholder = makeAircraft(id: 8, icaoType: "ZZZZ", typeName: "Unknown",
                                       nickname: "My aircraft", isDefault: true, isPlaceholder: true)
        let created = makeAircraft(id: 9, icaoType: "PA28", typeName: "Piper PA-28",
                                   tailNumber: "ZZ-XYZ", isDefault: true)
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([placeholder])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()

        vm.prepareNew()
        #expect(vm.form.isEditing == false)
        #expect(vm.form.isDefault)
        vm.form.icaoType = "PA28"
        vm.form.tailNumber = "zz-xyz"
        repo.createAircraftResult = .success(created)
        repo.aircraftResult = .success([created])
        #expect(await vm.save())

        let req = try #require(repo.lastCreateAircraftRequest)
        #expect(req.icaoType == "PA28")
        #expect(req.tailNumber == "ZZ-XYZ")
        #expect(req.isDefault)
        #expect(vm.aircraft.map(\.id) == [9])
    }

    @Test func editFromTheListPutsAndRereads() async throws {
        let alpha = makeAircraft(id: 1, typeName: "Alpha", cruiseSpeedKt: 100)
        let repo = MockBriefingRepository()
        repo.aircraftResult = .success([alpha])
        let vm = AircraftListViewModel(repository: repo)
        await vm.load()

        vm.prepareEdit(alpha)
        #expect(vm.form.isEditing)
        #expect(vm.form.title == "Edit Aircraft")
        vm.form.cruiseSpeedKt = "120"
        let updated = makeAircraft(id: 1, typeName: "Alpha", cruiseSpeedKt: 120)
        repo.updateAircraftResult = .success(updated)
        repo.aircraftResult = .success([updated])
        #expect(await vm.save())

        #expect(repo.lastUpdateAircraftId == 1)
        #expect(try #require(repo.lastUpdateAircraftRequest).cruiseSpeedKt == 120)
        #expect(vm.aircraft.first?.cruiseSpeedKt == 120)
    }

    @Test func invalidFormDoesNotSaveOrReread() async {
        let repo = MockBriefingRepository()
        let vm = AircraftListViewModel(repository: repo)
        vm.prepareNew()
        vm.form.icaoType = "C172"
        vm.form.cruiseSpeedKt = "fast"
        #expect(await vm.save() == false)
        #expect(vm.form.errorMessage == "Cruise speed must be a positive number.")
        #expect(repo.lastCreateAircraftRequest == nil)
    }

    // MARK: Row text

    /// A placeholder's ZZZZ type means nothing: the row shows its name, no
    /// type line, and the detail without ZZZZ (plus the set-up hint, keyed off
    /// `isPlaceholder` in the view).
    @Test func placeholderRowHidesItsType() {
        let placeholder = makeAircraft(icaoType: "ZZZZ", typeName: "Unknown", nickname: "My aircraft",
                                       cruiseSpeedKt: 120, ceilingFt: nil, isDefault: true,
                                       isPlaceholder: true)
        #expect(placeholder.displayName == "My aircraft")
        #expect(placeholder.listSubtitle == nil)
        #expect(placeholder.listDetail == "120 kt")

        let bare = makeAircraft(icaoType: "ZZZZ", typeName: "Unknown", nickname: "My aircraft",
                                cruiseSpeedKt: nil, ceilingFt: nil, isPlaceholder: true)
        #expect(bare.listDetail == nil)
    }

    @Test func realAircraftRowShowsNameTypeAndDetail() {
        let dot = "\u{00b7}"
        let full = makeAircraft(icaoType: "PA28", typeName: "Piper PA-28", tailNumber: "ZZ-ABC",
                                nickname: "Archie", cruiseSpeedKt: 105, ceilingFt: 12000)
        #expect(full.displayName == "Archie")
        #expect(full.listSubtitle == "Piper PA-28 \(dot) ZZ-ABC")
        #expect(full.listDetail == "PA28 \(dot) 105 kt \(dot) FL120")

        let tailOnly = makeAircraft(typeName: "Cessna 172", tailNumber: "ZZ-ABC")
        #expect(tailOnly.displayName == "ZZ-ABC")
        #expect(tailOnly.listSubtitle == "Cessna 172")

        let typeOnly = makeAircraft(typeName: "Cessna 172")
        #expect(typeOnly.displayName == "Cessna 172")
        #expect(typeOnly.listSubtitle == nil)
    }
}
