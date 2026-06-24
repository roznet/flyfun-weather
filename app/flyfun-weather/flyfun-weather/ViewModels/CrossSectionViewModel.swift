import Foundation

/// Extracts VizRouteData from API responses for a selected model.
/// Port of web's data-extract.ts extractVizData().
@Observable
@MainActor
final class CrossSectionViewModel {
    private(set) var vizData: VizRouteData?
    private(set) var enabledLayers: [String: Bool] = CrossSectionLayer.defaultEnabled

    func update(routeAnalyses: RouteAnalysesResponse, elevation: ElevationResponse?, model: String) {
        vizData = Self.extractVizData(from: routeAnalyses, model: model, elevation: elevation)
    }

    func toggleLayer(_ id: String) {
        enabledLayers[id] = !(enabledLayers[id] ?? false)
    }

    func setLayer(_ id: String, enabled: Bool) {
        enabledLayers[id] = enabled
    }

    func applyLayerPreset(_ preset: [String: Bool]) {
        for layer in CrossSectionLayer.allLayers {
            enabledLayers[layer.id] = preset[layer.id] ?? false
        }
    }

    /// Currently-active method layer ID for a method group (clouds/icing/etc),
    /// or nil if all methods in the group are off.
    func activeMethod(for group: LayerGroup) -> String? {
        guard let order = CrossSectionLayer.methodGroupOrder[group] else { return nil }
        return order.first(where: { enabledLayers[$0] == true })
    }

    /// Set the active method for a group. Passing nil disables all methods in the group.
    /// Other methods in the same group are turned off — only one is rendered at a time.
    func setMethod(_ layerId: String?, for group: LayerGroup) {
        guard let order = CrossSectionLayer.methodGroupOrder[group] else { return }
        for id in order {
            enabledLayers[id] = (id == layerId)
        }
    }

    // MARK: - Data extraction (port of data-extract.ts)

    static func extractVizData(
        from manifest: RouteAnalysesResponse,
        model: String,
        elevation: ElevationResponse?
    ) -> VizRouteData {
        var points: [VizPoint] = []
        var waypointMarkers: [WaypointMarker] = []

        for rpa in manifest.analyses {
            let sounding = rpa.sounding[model]
            let wind = rpa.windComponents[model]
            points.append(extractPoint(rpa: rpa, sounding: sounding, wind: wind, model: model))

            if let icao = rpa.waypointIcao {
                waypointMarkers.append(WaypointMarker(
                    distanceNm: rpa.distanceFromOriginNm,
                    icao: icao,
                    lat: rpa.lat,
                    lon: rpa.lon
                ))
            }
        }

        let actualCeiling = Double(manifest.cruiseAltitudeFt)
        let terrainProfile = elevation?.points.map {
            TerrainPoint(distanceNm: $0.distanceNm, elevationFt: $0.elevationFt)
        }

        return VizRouteData(
            points: points,
            cruiseAltitudeFt: Double(manifest.cruiseAltitudeFt),
            ceilingAltitudeFt: actualCeiling,
            flightCeilingFt: max(actualCeiling, Double(manifest.cruiseAltitudeFt)) + 5000,
            totalDistanceNm: manifest.totalDistanceNm,
            waypointMarkers: waypointMarkers,
            departureTime: manifest.departureTime,
            flightDurationHours: manifest.flightDurationHours,
            terrainProfile: terrainProfile
        )
    }

    private static func extractPoint(
        rpa: RoutePointAnalysis,
        sounding: SoundingAnalysis?,
        wind: WindComponent?,
        model: String
    ) -> VizPoint {
        let indices = sounding?.indices

        let altitudeLines = AltitudeLines(
            freezingLevelFt: indices?.freezingLevelFt,
            minus10cLevelFt: indices?.minus10cLevelFt,
            minus20cLevelFt: indices?.minus20cLevelFt,
            lclAltitudeFt: indices?.lclAltitudeFt,
            lfcAltitudeFt: indices?.lfcAltitudeFt,
            elAltitudeFt: indices?.elAltitudeFt
        )

        let cloudLayers = (sounding?.cloudLayers ?? []).map {
            VizCloudLayer(
                baseFt: $0.baseFt,
                topFt: $0.topFt,
                coverage: $0.coverage,
                meanDewpointDepressionC: $0.meanDewpointDepressionC,
                meanCloudCoverPct: $0.meanCloudCoverPct
            )
        }

        // nwp_cloud_layers: nil = no NWP source for this model; [] = clear sky.
        // Mirrors web's data-extract semantics so layer toggles can distinguish
        // "no data" (disable) from "clear sky" (render nothing).
        let nwpCloudLayers: [VizCloudLayer]? = sounding?.nwpCloudLayers.map { layers in
            layers.map {
                VizCloudLayer(
                    baseFt: $0.baseFt,
                    topFt: $0.topFt,
                    coverage: $0.coverage,
                    meanDewpointDepressionC: $0.meanDewpointDepressionC,
                    meanCloudCoverPct: $0.meanCloudCoverPct
                )
            }
        }

        let icingZones = (sounding?.icingZones ?? []).map {
            VizIcingZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType)
        }

        let icingOgimetNwpZones = (sounding?.icingOgimetNwpZones ?? []).map {
            VizIcingZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType)
        }

        let sfipZones = (sounding?.sfipZones ?? []).map {
            VizSfipZone(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk, type: $0.icingType, meanSfip100: $0.meanSfip100, variant: $0.variant)
        }

        let catLayers = (sounding?.verticalMotion?.catRiskLayers ?? []).map {
            VizCATLayer(baseFt: $0.baseFt, topFt: $0.topFt, risk: $0.risk)
        }

        let inversions = (sounding?.inversionLayers ?? []).map {
            VizInversionLayer(baseFt: $0.baseFt, topFt: $0.topFt, strengthC: $0.strengthC)
        }

        let low = sounding?.cloudCoverLowPct ?? 0
        let mid = sounding?.cloudCoverMidPct ?? 0
        let high = sounding?.cloudCoverHighPct ?? 0
        let cloudCoverTotalPct = min(100, low + mid + high)

        var worstModelAgreement = "good"
        for d in rpa.modelDivergence {
            if d.agreement == "poor" { worstModelAgreement = "poor"; break }
            if d.agreement == "moderate" { worstModelAgreement = "moderate" }
        }

        let diag = sounding?.nwpCloudDiagnostics
        let nwpCloudDiag: VizCloudDiag? = diag.map {
            VizCloudDiag(
                low: VizCloudDiagBand(coverPct: $0.low.coverPct, baseFt: $0.low.baseFt, topFt: $0.low.topFt),
                mid: VizCloudDiagBand(coverPct: $0.mid.coverPct, baseFt: $0.mid.baseFt, topFt: $0.mid.topFt),
                high: VizCloudDiagBand(coverPct: $0.high.coverPct, baseFt: $0.high.baseFt, topFt: $0.high.topFt),
                ceilingFt: $0.ceilingFt
            )
        }

        let temperatureC = divergenceValue(rpa.modelDivergence, variable: "temperature_c", model: model)
        let precipitationMm = divergenceValue(rpa.modelDivergence, variable: "precipitation_mm", model: model)

        let convNwp = sounding?.convectiveNwp

        return VizPoint(
            distanceNm: rpa.distanceFromOriginNm,
            lat: rpa.lat,
            lon: rpa.lon,
            time: rpa.interpolatedTime,
            altitudeLines: altitudeLines,
            cloudLayers: cloudLayers,
            nwpCloudLayers: nwpCloudLayers,
            icingZones: icingZones,
            icingOgimetNwpZones: icingOgimetNwpZones,
            sfipZones: sfipZones,
            catLayers: catLayers,
            inversions: inversions,
            convectiveRisk: sounding?.convective?.riskLevel ?? "none",
            convectiveBaseFt: sounding?.convective?.baseFt,
            convectiveTopFt: sounding?.convective?.topFt,
            nwpConvectiveRisk: convNwp?.riskLevel ?? "none",
            nwpConvectiveBaseFt: convNwp?.baseFt,
            nwpConvectiveTopFt: convNwp?.topFt,
            nwpConvectiveCoverPct: convNwp?.coverPct,
            nwpConvectiveMethod: convNwp?.method,
            hasNwpConvective: convNwp != nil,
            cloudCoverTotalPct: cloudCoverTotalPct,
            cloudCoverLowPct: sounding?.cloudCoverLowPct ?? 0,
            cloudCoverMidPct: sounding?.cloudCoverMidPct ?? 0,
            headwindKt: wind?.headwindKt ?? 0,
            crosswindKt: wind?.crosswindKt ?? 0,
            capeSurfaceJkg: indices?.capeSurfaceJkg ?? 0,
            worstModelAgreement: worstModelAgreement,
            nwpCloudDiag: nwpCloudDiag,
            temperatureC: temperatureC,
            precipitationMm: precipitationMm
        )
    }

    private static func divergenceValue(_ divergence: [ModelDivergence], variable: String, model: String) -> Double? {
        for d in divergence {
            if d.variable == variable {
                return d.modelValues[model]
            }
        }
        return nil
    }
}
