//
//  NwpFallbackTests.swift
//  flyfun-weatherTests
//
//  Unit tests for `NwpFallback` — the iOS port of the web's
//  `data-extract.ts:getUnavailableLayers` + `cross-section/nwp-fallback.ts`.
//  Locks in web↔iOS parity for the "model has no native NWP data" case
//  (e.g. a far-out ECMWF flight): the NWP cloud toggle is marked unavailable and
//  the render substitutes same-style DD clouds / Ogimet-DD / thermo convective
//  instead of drawing a blank layer.
//

import Testing
import Foundation
@testable import flyfun_weather

@Suite @MainActor struct NwpFallbackTests {

    /// A VizPoint with benign defaults; the three NWP-availability inputs are the
    /// only knobs the tests vary.
    private func point(
        nwpCloudLayers: [VizCloudLayer]?,
        sfipZones: [VizSfipZone] = [],
        hasNwpConvective: Bool = true
    ) -> VizPoint {
        VizPoint(
            distanceNm: 0, lat: 0, lon: 0, time: "",
            altitudeLines: AltitudeLines(freezingLevelFt: nil, minus10cLevelFt: nil,
                                         minus20cLevelFt: nil, lclAltitudeFt: nil,
                                         lfcAltitudeFt: nil, elAltitudeFt: nil),
            cloudLayers: [], nwpCloudLayers: nwpCloudLayers,
            icingZones: [], icingOgimetNwpZones: [], sfipZones: sfipZones,
            catLayers: [], inversions: [],
            convectiveRisk: "none", convectiveBaseFt: nil, convectiveTopFt: nil,
            nwpConvectiveRisk: "none", nwpConvectiveBaseFt: nil, nwpConvectiveTopFt: nil,
            nwpConvectiveCoverPct: nil, nwpConvectiveMethod: nil,
            hasNwpConvective: hasNwpConvective,
            cloudCoverTotalPct: 0, cloudCoverLowPct: 0, cloudCoverMidPct: 0,
            headwindKt: 0, crosswindKt: 0, capeSurfaceJkg: 0,
            worstModelAgreement: "good", nwpCloudDiag: nil,
            temperatureC: nil, precipitationMm: nil)
    }

    private func route(_ points: [VizPoint]) -> VizRouteData {
        VizRouteData(points: points, cruiseAltitudeFt: 8000, ceilingAltitudeFt: 8000,
                     flightCeilingFt: 13000, totalDistanceNm: 100, waypointMarkers: [],
                     departureTime: "", flightDurationHours: 1, terrainProfile: nil)
    }

    private let cloud = VizCloudLayer(baseFt: 3000, topFt: 8000, coverage: "bkn",
                                      meanDewpointDepressionC: 1.0, meanCloudCoverPct: 72)

    // MARK: getUnavailableLayers

    /// No point carries native NWP cloud data (all nil) → the NWP cloud signal and
    /// the NWP-cloud-gated Ogimet-NWP icing are unavailable. This is the far-out
    /// ECMWF case the pilot hit.
    @Test func nwpCloudUnavailableWhenAllNil() {
        let data = route([point(nwpCloudLayers: nil), point(nwpCloudLayers: nil)])
        let unavailable = NwpFallback.unavailableLayers(in: data)
        #expect(unavailable.contains("nwp-cloud-bands"))
        #expect(unavailable.contains("icing-ogimet-nwp-bands"))
    }

    /// A single point with native NWP data (even an empty [] = "model clear")
    /// keeps the NWP cloud toggle available — [] is "clear sky", not "no data".
    @Test func nwpCloudAvailableWhenAnyPointHasData() {
        let data = route([point(nwpCloudLayers: nil), point(nwpCloudLayers: [])])
        let unavailable = NwpFallback.unavailableLayers(in: data)
        #expect(!unavailable.contains("nwp-cloud-bands"))
        #expect(!unavailable.contains("icing-ogimet-nwp-bands"))
    }

    /// SFIP and NWP convective are gated on their own signals independently.
    @Test func sfipAndNwpConvectiveGatedIndependently() {
        let data = route([point(nwpCloudLayers: [cloud], sfipZones: [], hasNwpConvective: false)])
        let unavailable = NwpFallback.unavailableLayers(in: data)
        #expect(unavailable.contains("sfip-bands"))
        #expect(unavailable.contains("nwp-convective-bg"))
        #expect(!unavailable.contains("nwp-cloud-bands"))  // native cloud present
    }

    // MARK: ddSubstituteId

    @Test func ddSubstituteMapsNwpCloudsToSameStyleDD() {
        #expect(NwpFallback.ddSubstituteId(for: "soft-nwp-cloud-bands") == "soft-cloud-bands")
        #expect(NwpFallback.ddSubstituteId(for: "nwp-cloud-bands") == "cloud-bands")
        #expect(NwpFallback.ddSubstituteId(for: "square-nwp-cloud-bands") == "square-cloud-bands")
        // Icing / convective map to their always-available fallbacks.
        #expect(NwpFallback.ddSubstituteId(for: "icing-ogimet-nwp-bands") == "icing-bands")
        #expect(NwpFallback.ddSubstituteId(for: "sfip-bands") == "icing-bands")
        #expect(NwpFallback.ddSubstituteId(for: "nwp-convective-bg") == "thermo-convective-bg")
        // DD layers have no fallback.
        #expect(NwpFallback.ddSubstituteId(for: "cloud-bands") == nil)
        #expect(NwpFallback.ddSubstituteId(for: "cat-bands") == nil)
    }

    // MARK: applyFallback

    /// The GRAMET default (Natural NWP clouds + Ogimet-NWP + NWP convective) on a
    /// model with no native NWP data: NWP methods off, DD substitutes on, and the
    /// stored preference is never mutated.
    @Test func applyFallbackSubstitutesDdForGrametOnNwpLessModel() {
        let stored = CrossSectionPresets.gramet
        let unavailable: Set<String> = ["nwp-cloud-bands", "icing-ogimet-nwp-bands"]
        let effective = NwpFallback.applyFallback(enabledLayers: stored, unavailable: unavailable)

        // NWP layers disabled…
        #expect(effective["nwp-cloud-bands"] == false)
        #expect(effective["icing-ogimet-nwp-bands"] == false)
        // …and their DD siblings turned on as substitutes.
        #expect(effective["cloud-bands"] == true)         // Natural DD clouds
        #expect(effective["icing-bands"] == true)         // Ogimet-DD
        // Stored preference untouched (parity with web: no persisted downgrade).
        #expect(stored["nwp-cloud-bands"] == true)
        #expect(stored["cloud-bands"] != true)
    }

    /// substitutedLayers reports exactly the layers the fallback turned on that
    /// the user did not — the auto-substitutes, for the UI hint.
    @Test func substitutedLayersReportsAutoEnabled() {
        let stored = CrossSectionPresets.gramet
        let unavailable: Set<String> = ["nwp-cloud-bands", "icing-ogimet-nwp-bands"]
        let effective = NwpFallback.applyFallback(enabledLayers: stored, unavailable: unavailable)
        let substituted = NwpFallback.substitutedLayers(enabledLayers: stored, effective: effective)
        #expect(substituted.contains("cloud-bands"))
        #expect(substituted.contains("icing-bands"))
        #expect(!substituted.contains("nwp-cloud-bands"))  // disabled, not substituted
    }

    /// When the user already has the DD sibling enabled, the fallback doesn't
    /// double-toggle and reports no auto-substitute for it.
    @Test func applyFallbackNoOpWhenDdAlreadyEnabled() {
        var stored = CrossSectionPresets.foreflight  // Square DD clouds + Ogimet-DD
        stored["nwp-cloud-bands"] = false
        let unavailable: Set<String> = ["nwp-cloud-bands", "icing-ogimet-nwp-bands"]
        let effective = NwpFallback.applyFallback(enabledLayers: stored, unavailable: unavailable)
        let substituted = NwpFallback.substitutedLayers(enabledLayers: stored, effective: effective)
        // ForeFlight already uses DD, so nothing is auto-substituted.
        #expect(substituted.isEmpty)
        #expect(effective["icing-bands"] == true)
    }
}
