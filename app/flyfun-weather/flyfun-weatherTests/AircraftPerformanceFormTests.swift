//
//  AircraftPerformanceFormTests.swift
//  flyfun-weatherTests
//
//  The aircraft form's climb / descent presets (#1005, #1008): the same cases as
//  web `aircraft-performance-form.test.ts`, plus the form model's behaviour.
//

import Testing
import Foundation
@testable import flyfun_weather

private let hp = AircraftPerformancePreset(
    id: "hp_piston", climbIasKt: 110, climbRateFpm: 1000,
    descentMode: "rate", descentRateFpm: 500, descentAngleDeg: nil, turbo: false)
private let trainer = AircraftPerformancePreset(
    id: "trainer_piston", climbIasKt: 75, climbRateFpm: 700,
    descentMode: "rate", descentRateFpm: 500, descentAngleDeg: nil, turbo: false)
private let turboprop = AircraftPerformancePreset(
    id: "single_turboprop", climbIasKt: 150, climbRateFpm: 2000,
    descentMode: "angle", descentRateFpm: nil, descentAngleDeg: 3, turbo: false)

@Suite struct AircraftPerformanceRulesTests {
    private func values(_ change: (inout AircraftPerformanceValues) -> Void = { _ in }) -> AircraftPerformanceValues {
        var v = AircraftPerformanceForm.values(of: hp)
        change(&v)
        return v
    }

    @Test func notCustomOnPresetValuesOrEmptyFields() {
        #expect(!AircraftPerformanceForm.isCustom(values(), base: hp))
        #expect(!AircraftPerformanceForm.isCustom(values { $0.climbIasKt = nil; $0.climbRateFpm = nil }, base: hp))
    }

    @Test func customWhenAValueOrTurboDiffers() {
        #expect(AircraftPerformanceForm.isCustom(values { $0.climbRateFpm = 900 }, base: hp))
        #expect(AircraftPerformanceForm.isCustom(values { $0.turbo = true }, base: hp))
        // An angle on a rate preset is a different descent.
        #expect(AircraftPerformanceForm.isCustom(values { $0.descentRateFpm = nil; $0.descentAngleDeg = 3 }, base: hp))
    }

    @Test func anglePresetWithEmptyRateIsThePreset() {
        #expect(!AircraftPerformanceForm.isCustom(AircraftPerformanceForm.values(of: turboprop), base: turboprop))
    }

    @Test func savesThePresetAndZerosWhenNothingDiffers() {
        #expect(AircraftPerformanceForm.overrides(for: values(), base: hp) == AircraftPerformanceOverrides(
            performancePreset: "hp_piston", isTurbo: false,
            climbIasKt: 0, climbRateFpm: 0, descentRateFpm: 0, descentAngleDeg: 0))
    }

    @Test func savesOnlyTheDifferingValuesAndTurboAsIs() {
        let o = AircraftPerformanceForm.overrides(for: values { $0.climbRateFpm = 850; $0.turbo = true }, base: hp)
        #expect(o == AircraftPerformanceOverrides(
            performancePreset: "hp_piston", isTurbo: true,
            climbIasKt: 0, climbRateFpm: 850, descentRateFpm: 0, descentAngleDeg: 0))
    }

    @Test func switchesAnAnglePresetToARateDescent() {
        var v = AircraftPerformanceForm.values(of: turboprop)
        v.descentAngleDeg = nil
        v.descentRateFpm = 1500
        let o = AircraftPerformanceForm.overrides(for: v, base: turboprop)
        #expect(o.descentRateFpm == 1500)
        #expect(o.descentAngleDeg == 0)
    }

    @Test func readsADecimalCommaAndIgnoresJunk() {
        #expect(AircraftPerformanceForm.number(from: "3,5") == 3.5)
        #expect(AircraftPerformanceForm.number(from: " 500 ") == 500)
        #expect(AircraftPerformanceForm.number(from: "") == nil)
        #expect(AircraftPerformanceForm.number(from: "0") == nil)
        #expect(AircraftPerformanceForm.number(from: "abc") == nil)
    }

    @Test func showsWholeNumbersWithoutADecimal() {
        #expect(AircraftPerformanceForm.text(from: 110) == "110")
        #expect(AircraftPerformanceForm.text(from: 3.5) == "3.5")
        #expect(AircraftPerformanceForm.text(from: nil) == "")
    }

    /// The shared fixture is the server's real table.
    @Test @MainActor func fixtureTableDecodes() {
        let table = FixtureBriefingData.aircraftPresets
        #expect(table.presets.count == 6)
        #expect(table.suggested == "hp_piston")
        #expect(table.presets.first { $0.id == "single_turboprop" }?.descentMode == "angle")
    }
}

@Suite @MainActor struct AircraftFormModelPerformanceTests {
    private func makeForm(suggested: String = "hp_piston") -> (AircraftFormModel, MockBriefingRepository) {
        let repo = MockBriefingRepository()
        repo.aircraftPresetsResult = .success(AircraftPresetsResponse(
            presets: [trainer, hp, turboprop], suggested: suggested))
        return (AircraftFormModel(repository: repo), repo)
    }

    private func aircraft(preset: String? = nil, performance: AircraftPerformanceResponse? = nil,
                          placeholder: Bool = false) -> AircraftResponse {
        AircraftResponse(
            id: 7, icaoType: "SR22", typeName: "Cirrus SR22", tailNumber: "ZZ-ABC", nickname: nil,
            isIfr: true, isFiki: false, cruiseSpeedKt: 170, ceilingFt: nil, isDefault: true,
            createdAt: "2099-01-01T00:00:00Z", isPlaceholder: placeholder,
            performancePreset: preset, performance: performance)
    }

    private func resolved(_ preset: String = "hp_piston", climbRate: Double = 1000) -> AircraftPerformanceResponse {
        AircraftPerformanceResponse(
            preset: preset, source: "aircraft", climbIasKt: 110, climbRateFpm: climbRate, ceilingFt: 17500,
            descentMode: "rate", descentRateFpm: 500, descentAngleDeg: nil, turbo: false)
    }

    @Test func newAircraftOpensOnTheSuggestedPreset() async {
        let (form, _) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        #expect(form.basePresetId == "hp_piston")
        #expect(form.climbIasText == "110")
        #expect(form.descentRateText == "500")
        #expect(form.descentAngleText == "")
        #expect(!form.isPerformanceCustom)
    }

    @Test func savedAircraftOpensOnItsResolvedValuesAsCustom() async {
        let (form, _) = makeForm()
        form.prepareEdit(aircraft(preset: "hp_piston", performance: resolved(climbRate: 850)))
        await form.refreshPresets()
        #expect(form.climbRateText == "850")
        #expect(form.isPerformanceCustom)
        #expect(form.presetSelection == AircraftFormModel.customPresetSelection)
        #expect(form.customPresetTitle == "Custom (based on High-performance piston)")
    }

    @Test func placeholderOpensOnThePresetNotItsResolvedDefault() async {
        let (form, _) = makeForm()
        form.prepareEdit(aircraft(performance: resolved(climbRate: 850), placeholder: true))
        await form.refreshPresets()
        #expect(form.climbRateText == "1000")
    }

    @Test func pickingAPresetWritesAllItsValues() async {
        let (form, _) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        form.isTurbo = true
        form.selectPreset("single_turboprop")
        #expect(form.climbIasText == "150")
        #expect(form.descentRateText == "")
        #expect(form.descentAngleText == "3")
        #expect(!form.isTurbo)
        #expect(!form.isPerformanceCustom)
    }

    @Test func typingADescentRateClearsTheAngleAndTheReverse() async {
        let (form, _) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        form.descentAngleText = "3"
        #expect(form.descentRateText == "")
        form.descentRateText = "600"
        #expect(form.descentAngleText == "")
    }

    @Test func followingPresetMovesToTheNewSuggestion() async {
        let (form, repo) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        repo.aircraftPresetsResult = .success(AircraftPresetsResponse(
            presets: [trainer, hp, turboprop], suggested: "single_turboprop"))
        await form.refreshPresets()
        #expect(form.basePresetId == "single_turboprop")
        #expect(form.climbIasText == "150")
    }

    @Test func pickedPresetStaysWhenTheSuggestionMoves() async {
        let (form, repo) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        form.selectPreset("trainer_piston")
        repo.aircraftPresetsResult = .success(AircraftPresetsResponse(
            presets: [trainer, hp, turboprop], suggested: "single_turboprop"))
        await form.refreshPresets()
        #expect(form.basePresetId == "trainer_piston")
    }

    @Test func customValuesStayWhenTheSuggestionMoves() async {
        let (form, repo) = makeForm()
        form.prepareNew(existing: [])
        await form.refreshPresets()
        form.climbRateText = "900"
        repo.aircraftPresetsResult = .success(AircraftPresetsResponse(
            presets: [trainer, hp, turboprop], suggested: "single_turboprop"))
        await form.refreshPresets()
        #expect(form.basePresetId == "hp_piston")
        #expect(form.climbRateText == "900")
    }

    @Test func halfTypedTypeAsksWithoutAType() async {
        let (form, repo) = makeForm()
        form.prepareNew(existing: [])
        form.icaoType = "CESSNA"
        form.cruiseSpeedKt = "9999"
        await form.refreshPresets()
        #expect(repo.lastAircraftPresetsQuery?.icaoType == "")
        #expect(repo.lastAircraftPresetsQuery?.cruiseSpeedKt == nil)
    }

    @Test func saveSendsThePresetAndOnlyTheDifferences() async {
        let (form, repo) = makeForm()
        repo.updateAircraftResult = .success(aircraft())
        form.prepareEdit(aircraft(preset: "hp_piston", performance: resolved()))
        await form.refreshPresets()
        form.climbRateText = "850"
        _ = await form.save()
        let request = repo.lastUpdateAircraftRequest
        #expect(request?.performancePreset == "hp_piston")
        #expect(request?.isTurbo == false)
        #expect(request?.climbRateFpm == 850)
        #expect(request?.climbIasKt == 0)
        #expect(request?.descentRateFpm == 0)
        #expect(request?.descentAngleDeg == 0)
    }

    @Test func presetsThatNeverLoadedLeaveTheAircraftUnchanged() async {
        let (form, repo) = makeForm()
        repo.aircraftPresetsResult = .failure(MockError.injected("offline"))
        repo.updateAircraftResult = .success(aircraft())
        form.prepareEdit(aircraft(preset: "hp_piston", performance: resolved(climbRate: 850)))
        await form.refreshPresets()
        #expect(form.presets.isEmpty)
        #expect(await form.save() != nil)
        let request = repo.lastUpdateAircraftRequest
        #expect(request?.performancePreset == nil)
        #expect(request?.isTurbo == nil)
        #expect(request?.climbRateFpm == nil)
    }

    @Test func refusesValuesTheServerWouldReject() async {
        let (form, repo) = makeForm()
        form.prepareEdit(aircraft(preset: "hp_piston", performance: resolved()))
        await form.refreshPresets()
        form.descentAngleText = "9"
        #expect(await form.save() == nil)
        #expect(form.errorMessage == "Descent angle must be 6\u{00b0} or less.")
        #expect(repo.lastUpdateAircraftRequest == nil)
    }

    @Test func aircraftFromAnOlderServerStillDecodes() throws {
        let json = #"{"id":1,"icao_type":"C172","type_name":"Cessna 172","is_ifr":false,"is_fiki":false,"is_default":true,"created_at":"2099-01-01T00:00:00Z"}"#
        let decoded = try JSONDecoder.weatherBrief.decode(AircraftResponse.self, from: Data(json.utf8))
        #expect(decoded.performance == nil)
        #expect(decoded.isTurbo == false)
    }
}

@Suite @MainActor struct AddFlightDurationEstimateTests {
    private func makeViewModel(hours: Double = 1.1) -> (AddFlightViewModel, MockBriefingRepository) {
        let repo = MockBriefingRepository()
        repo.routeDistanceResult = .success(RouteDistanceResponse(totalDistanceNm: 200, waypoints: []))
        repo.durationEstimateResult = .success(DurationEstimateResponse(
            durationHours: hours, preset: "hp_piston", tocNm: 20, todNm: 180, cruiseReached: true))
        let vm = AddFlightViewModel(repository: repo)
        vm.waypointsText = "LFMD LFML"
        return (vm, repo)
    }

    @Test func fillsTheDurationRoundedUpToTheQuarterHour() async {
        let (vm, repo) = makeViewModel(hours: 1.1)
        vm.cruiseAltitudeFt = 8000
        vm.selectedAircraftId = 7
        await vm.refreshDurationEstimate()
        #expect(vm.flightDurationHours == 1.25)
        #expect(repo.lastDurationEstimateRequest == DurationEstimateRequest(
            totalDistanceNm: 200, cruiseAltitudeFt: 8000, aircraftId: 7, origin: "LFMD", destination: "LFML"))
    }

    @Test func aPilotEditWins() async {
        let (vm, repo) = makeViewModel()
        vm.durationHours = 3
        let before = vm.flightDurationHours
        await vm.refreshDurationEstimate()
        #expect(vm.flightDurationHours == before)
        #expect(repo.lastDurationEstimateRequest == nil)
    }

    @Test func anEditWhileTheCallIsOutWins() async {
        let (vm, repo) = makeViewModel()
        repo.durationEstimateHook = { vm.durationMinutes = 45 }
        await vm.refreshDurationEstimate()
        #expect(vm.durationMinutes == 45)
        #expect(vm.flightDurationHours != 1.25)
    }

    @Test func aFailedCallLeavesTheDurationAlone() async {
        let (vm, repo) = makeViewModel()
        repo.durationEstimateResult = .failure(MockError.injected("offline"))
        await vm.refreshDurationEstimate()
        #expect(vm.flightDurationHours == 2.0)
    }

    @Test func anAltitudeChangeDoesNotMeasureTheRouteAgain() async {
        let (vm, repo) = makeViewModel()
        await vm.refreshDurationEstimate()
        vm.cruiseAltitudeFt = 9000
        await vm.refreshDurationEstimate()
        #expect(repo.routeDistanceCallCount == 1)
        #expect(repo.lastDurationEstimateRequest?.cruiseAltitudeFt == 9000)
    }

    @Test func oneWaypointAsksNothing() async {
        let (vm, repo) = makeViewModel()
        vm.waypointsText = "LFMD"
        await vm.refreshDurationEstimate()
        #expect(repo.routeDistanceCallCount == 0)
    }
}
