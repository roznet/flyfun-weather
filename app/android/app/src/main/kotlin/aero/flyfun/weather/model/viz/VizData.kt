package aero.flyfun.weather.model.viz

import aero.flyfun.weather.model.AdvisoriesResponse
import aero.flyfun.weather.model.AdvisoryHighlights
import aero.flyfun.weather.model.ElevationResponse
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.ModelDivergence
import aero.flyfun.weather.model.ObservedConditions
import aero.flyfun.weather.model.ObservedStationRef
import aero.flyfun.weather.model.RouteAnalysesResponse
import aero.flyfun.weather.model.RoutePointAnalysis
import aero.flyfun.weather.model.SnapshotResponse
import aero.flyfun.weather.model.SoundingAnalysis
import aero.flyfun.weather.model.WindComponent
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

data class VizCloudLayer(
    val baseFt: Double,
    val topFt: Double,
    val coverage: String = "sct",
    val meanDewpointDepressionC: Double? = null,
    val coveragePct: Double? = null,
)

data class VizIcingZone(
    val baseFt: Double,
    val topFt: Double,
    val risk: String = "light",
    val type: String = "rime",
    val meanIndex: Double? = null,
)

data class VizSfipZone(
    val baseFt: Double,
    val topFt: Double,
    val risk: String = "light",
    val variant: String = "full",
    val meanSfip: Double? = null,
)

data class VizSldZone(
    val baseFt: Double,
    val topFt: Double,
    val risk: String = "light",
    val mechanism: String = "warm_nose",
)

data class VizCatLayer(
    val baseFt: Double,
    val topFt: Double,
    val risk: String = "light",
    val meanRi: Double? = null,
)

data class VizInversionLayer(
    val baseFt: Double,
    val topFt: Double,
    val strengthC: Double = 0.0,
    val surfaceBased: Boolean = false,
)

data class VizConvectiveZone(
    val riskLevel: String = "none",
    val baseFt: Double? = null,
    val topFt: Double? = null,
    val coverPct: Double? = null,
    val capeJkg: Double? = null,
    val cinJkg: Double? = null,
    val lclFt: Double? = null,
    val lfcFt: Double? = null,
    val elFt: Double? = null,
    val isDepthUnresolved: Boolean = false,
)

data class VizAltitudeLines(
    val freezingLevelFt: Double? = null,
    val minus10cLevelFt: Double? = null,
    val minus20cLevelFt: Double? = null,
    val lclFt: Double? = null,
    val lfcFt: Double? = null,
    val elFt: Double? = null,
)

data class VizPoint(
    val pointIndex: Int = 0,
    val distanceNm: Double = 0.0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    val waypointIcao: String? = null,
    val waypointName: String? = null,
    val flightTimeIso: String? = null,
    val terrainElevationFt: Double = 0.0,
    val altitudeLines: VizAltitudeLines = VizAltitudeLines(),
    val cloudLayers: List<VizCloudLayer> = emptyList(),
    val nwpCloudLayers: List<VizCloudLayer> = emptyList(),
    val hasNativeNwpClouds: Boolean = false,
    val icingZones: List<VizIcingZone> = emptyList(),
    val icingOgimetNwpZones: List<VizIcingZone> = emptyList(),
    val sfipZones: List<VizSfipZone> = emptyList(),
    val iengIcingZones: List<VizIcingZone> = emptyList(),
    val sldZones: List<VizSldZone> = emptyList(),
    val catLayers: List<VizCatLayer> = emptyList(),
    val eShearLayers: List<VizCatLayer> = emptyList(),
    val inversionLayers: List<VizInversionLayer> = emptyList(),
    val thermoConvective: VizConvectiveZone = VizConvectiveZone(),
    val nwpConvective: VizConvectiveZone? = null,
    val headwindKt: Double? = null,
    val crosswindKt: Double? = null,
    val windSpeedKt: Double? = null,
    val windDirectionDeg: Double? = null,
    val temperatureC: Double? = null,
    val temperatureCruiseC: Double? = null,
    val isaDevC: Double? = null,
    val precipitationMm: Double? = null,
    val cloudCoverTotalPct: Double? = null,
    val cloudCoverLowPct: Double? = null,
    val cloudCoverMidPct: Double? = null,
    val capeSurfaceJkg: Double? = null,
    val cinSurfaceJkg: Double? = null,
    val qnhHpa: Double? = null,
    val visibilityMeters: Double? = null,
    val soundingCeilingFt: Double? = null,
    val nwpCeilingFt: Double? = null,
    val observedRadarDbz: Double? = null,
    val observedCloudTopFt: Double? = null,
)

data class VizTerrainPoint(
    val distanceNm: Double,
    val elevationFt: Double,
)

data class VizWaypointMarker(
    val distanceNm: Double,
    val icao: String,
    val name: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
)

data class VizNightInterval(
    val startDistanceNm: Double,
    val endDistanceNm: Double,
    val kind: String = "night",
)

data class VizFrontMarker(
    val distanceNm: Double,
    val frontType: String = "cold",
    val intensity: Double = 1.0,
    val isWet: Boolean = true,
    val persistence: Double = 1.0,
    val isConvective: Boolean = false,
)

data class VizCurrentConditionsAirport(
    val icao: String,
    val distanceNm: Double,
    val flightCategory: String,
    val ceilingFt: Int? = null,
    val visibilityMeters: Int? = null,
)

data class VizCurrentConditionsSigmet(
    val id: String,
    val hazard: String,
    val startDistanceNm: Double,
    val endDistanceNm: Double,
    val baseFt: Double,
    val topFt: Double,
)

data class VizCurrentConditions(
    val airports: List<VizCurrentConditionsAirport> = emptyList(),
    val sigmets: List<VizCurrentConditionsSigmet> = emptyList(),
)

data class VizRouteData(
    val model: String = "ecmwf",
    val availableModels: List<String> = emptyList(),
    val totalDistanceNm: Double = 0.0,
    val cruiseAltitudeFt: Double = 8000.0,
    val flightCeilingFt: Double = 18000.0,
    val maxAltitudeFt: Double = 23000.0,
    val points: List<VizPoint> = emptyList(),
    val terrainProfile: List<VizTerrainPoint> = emptyList(),
    val waypoints: List<VizWaypointMarker> = emptyList(),
    val nightIntervals: List<VizNightInterval> = emptyList(),
    val fronts: List<VizFrontMarker> = emptyList(),
    val currentConditions: VizCurrentConditions? = null,
    val advisoryHighlights: AdvisoryHighlights? = null,
)

object VizDataExtractor {

    /**
     * ISA standard-atmosphere temperature (°C) at a geopotential altitude (ft).
     * Troposphere lapse rate ≈ 1.9812 °C per 1000 ft to the 36,089 ft tropopause,
     * isothermal −56.5 °C above.
     */
    fun isaTemperatureC(altitudeFt: Double): Double =
        if (altitudeFt <= 36089.0) 15.0 - 1.9812 * (altitudeFt / 1000.0) else -56.5

    /**
     * Combine low/mid/high cloud-cover percentages into a total using the
     * random-overlap approximation: `1 - (1 - L)(1 - M)(1 - H)`.
     */
    fun randomOverlapPct(low: Double, mid: Double, high: Double): Double {
        fun clamp01(pct: Double): Double = min(100.0, max(0.0, pct)) / 100.0
        val l = clamp01(low)
        val m = clamp01(mid)
        val h = clamp01(high)
        return (1.0 - (1.0 - l) * (1.0 - m) * (1.0 - h)) * 100.0
    }

    /**
     * Linearly interpolate terrain elevation (ft MSL) at [distanceNm] along the route,
     * clamped to the profile endpoints.
     */
    fun interpolateTerrainElevation(profile: List<VizTerrainPoint>, distanceNm: Double): Double {
        if (profile.isEmpty()) return 0.0
        val first = profile.first()
        val last = profile.last()
        if (distanceNm <= first.distanceNm) return first.elevationFt
        if (distanceNm >= last.distanceNm) return last.elevationFt
        for (i in 0 until profile.size - 1) {
            val a = profile[i]
            val b = profile[i + 1]
            if (distanceNm >= a.distanceNm && distanceNm <= b.distanceNm) {
                val span = b.distanceNm - a.distanceNm
                if (span <= 0.0) return a.elevationFt
                val t = (distanceNm - a.distanceNm) / span
                return a.elevationFt + t * (b.elevationFt - a.elevationFt)
            }
        }
        return 0.0
    }

    fun extractVizData(
        routeAnalyses: RouteAnalysesResponse?,
        elevation: ElevationResponse? = null,
        snapshot: SnapshotResponse? = null,
        advisories: AdvisoriesResponse? = null,
        selectedModel: String = "ecmwf",
        flight: FlightResponse? = null,
        activeAdvisoryHighlightId: String? = null,
    ): VizRouteData? {
        if (routeAnalyses == null) return null

        val availableModels = buildList {
            addAll(routeAnalyses.models)
            advisories?.models?.forEach { if (it !in this) add(it) }
            if (isEmpty() && selectedModel.isNotBlank()) {
                add(selectedModel)
            }
        }

        val hasSelectedInAnalyses = routeAnalyses.analyses.any {
            selectedModel in it.sounding || selectedModel in it.windComponents
        }
        val model = when {
            selectedModel.isNotBlank() && (selectedModel in availableModels || hasSelectedInAnalyses || availableModels.isEmpty()) ->
                selectedModel
            availableModels.isNotEmpty() -> availableModels.first()
            else -> selectedModel
        }

        val cruiseAltitudeFt = (
            flight?.cruiseAltitudeFt?.takeIf { it > 0 }
                ?: routeAnalyses.cruiseAltitudeFt.takeIf { it > 0 }
                ?: snapshot?.route?.cruiseAltitudeFt?.takeIf { it > 0 }
                ?: advisories?.cruiseAltitudeFt?.takeIf { it > 0 }
                ?: 8000
            ).toDouble()

        val flightCeilingFt = (
            flight?.flightCeilingFt?.takeIf { it > 0 }
                ?: routeAnalyses.flightCeilingFt?.takeIf { it > 0 }
                ?: snapshot?.route?.flightCeilingFt?.takeIf { it > 0 }
                ?: advisories?.flightCeilingFt?.takeIf { it > 0 }
                ?: cruiseAltitudeFt.toInt()
            ).toDouble()

        val maxAltitudeFt = max(flightCeilingFt, cruiseAltitudeFt) + 5000.0

        val terrainProfile = elevation?.points?.map {
            VizTerrainPoint(distanceNm = it.distanceNm, elevationFt = it.elevationFt)
        }.orEmpty()

        val waypoints = mutableListOf<VizWaypointMarker>()
        val waypointDistanceByIcao = mutableMapOf<String, Double>()
        val observedConditions = snapshot?.observedConditions

        val points = routeAnalyses.analyses.mapIndexed { idx, rpa ->
            val sounding = rpa.sounding[model]
            val wind = rpa.windComponents[model]
            val terrainFt = interpolateTerrainElevation(terrainProfile, rpa.distanceFromOriginNm)
            val obsStation = matchObservedStation(observedConditions, rpa, idx, routeAnalyses.analyses.size)

            if (!rpa.waypointIcao.isNullOrBlank()) {
                val icao = rpa.waypointIcao
                waypoints.add(
                    VizWaypointMarker(
                        distanceNm = rpa.distanceFromOriginNm,
                        icao = icao,
                        name = rpa.waypointName.orEmpty(),
                        lat = rpa.lat,
                        lon = rpa.lon,
                    )
                )
                waypointDistanceByIcao[icao.uppercase()] = rpa.distanceFromOriginNm
            }

            extractPoint(
                rpa = rpa,
                sounding = sounding,
                wind = wind,
                model = model,
                terrainElevationFt = terrainFt,
                cruiseAltitudeFt = cruiseAltitudeFt,
                observedStation = obsStation,
            )
        }

        val totalDistanceNm = when {
            routeAnalyses.totalDistanceNm > 0.0 -> routeAnalyses.totalDistanceNm
            elevation != null && elevation.totalDistanceNm > 0.0 -> elevation.totalDistanceNm
            points.isNotEmpty() -> points.maxOf { it.distanceNm }
            else -> 0.0
        }

        val currentConditions = buildCurrentConditions(
            snapshot = snapshot,
            waypointDistanceByIcao = waypointDistanceByIcao,
            maxAltitudeFt = maxAltitudeFt,
        )

        val advisoryHighlights = activeAdvisoryHighlightId?.let { highlightId ->
            advisories?.advisories?.firstOrNull { it.advisoryId == highlightId }?.highlights
        }

        val nightIntervals = routeAnalyses.sun?.nightIntervals.orEmpty().map { ni ->
            VizNightInterval(
                startDistanceNm = ni.startDistanceNm,
                endDistanceNm = ni.endDistanceNm,
                kind = ni.phase.lowercase(),
            )
        }

        return VizRouteData(
            model = model,
            availableModels = availableModels,
            totalDistanceNm = totalDistanceNm,
            cruiseAltitudeFt = cruiseAltitudeFt,
            flightCeilingFt = flightCeilingFt,
            maxAltitudeFt = maxAltitudeFt,
            points = points,
            terrainProfile = terrainProfile,
            waypoints = waypoints,
            nightIntervals = nightIntervals,
            fronts = emptyList(),
            currentConditions = currentConditions,
            advisoryHighlights = advisoryHighlights,
        )
    }

    private fun extractPoint(
        rpa: RoutePointAnalysis,
        sounding: SoundingAnalysis?,
        wind: WindComponent?,
        model: String,
        terrainElevationFt: Double,
        cruiseAltitudeFt: Double,
        observedStation: ObservedStationRef?,
    ): VizPoint {
        val indices = sounding?.indices
        val conv = sounding?.convective
        val convNwp = sounding?.convectiveNwp

        val altitudeLines = VizAltitudeLines(
            freezingLevelFt = indices?.freezingLevelFt,
            minus10cLevelFt = indices?.minus10cLevelFt,
            minus20cLevelFt = indices?.minus20cLevelFt,
            lclFt = indices?.lclAltitudeFt ?: conv?.lclFt,
            lfcFt = indices?.lfcAltitudeFt ?: conv?.lfcFt,
            elFt = indices?.elAltitudeFt ?: conv?.elFt,
        )

        val cloudLayers = sounding?.cloudLayers.orEmpty().map { cl ->
            VizCloudLayer(
                baseFt = cl.baseFt,
                topFt = cl.topFt,
                coverage = (cl.coverage ?: "sct").lowercase(),
                meanDewpointDepressionC = cl.meanDewpointDepressionC,
                coveragePct = cl.meanCloudCoverPct,
            )
        }

        val hasNativeNwpClouds = sounding?.nwpCloudLayers != null
        val nwpCloudLayers = sounding?.nwpCloudLayers.orEmpty().map { cl ->
            VizCloudLayer(
                baseFt = cl.baseFt,
                topFt = cl.topFt,
                coverage = (cl.coverage ?: "sct").lowercase(),
                meanDewpointDepressionC = cl.meanDewpointDepressionC,
                coveragePct = cl.meanCloudCoverPct,
            )
        }

        val icingZones = sounding?.icingZones.orEmpty().map { iz ->
            VizIcingZone(
                baseFt = iz.baseFt,
                topFt = iz.topFt,
                risk = iz.risk.lowercase(),
                type = iz.icingType.lowercase(),
                meanIndex = iz.meanIcingIndex,
            )
        }

        val icingOgimetNwpZones = sounding?.icingOgimetNwpZones.orEmpty().map { iz ->
            VizIcingZone(
                baseFt = iz.baseFt,
                topFt = iz.topFt,
                risk = iz.risk.lowercase(),
                type = iz.icingType.lowercase(),
                meanIndex = iz.meanIcingIndex,
            )
        }

        val sfipZones = sounding?.sfipZones.orEmpty().map { sz ->
            VizSfipZone(
                baseFt = sz.baseFt,
                topFt = sz.topFt,
                risk = sz.risk.lowercase(),
                variant = sz.variant ?: "full",
                meanSfip = sz.meanSfip100,
            )
        }

        val iengIcingZones = sounding?.iengIcingZones.orEmpty().map { iz ->
            VizIcingZone(
                baseFt = iz.baseFt,
                topFt = iz.topFt,
                risk = iz.risk.lowercase(),
                type = iz.icingType.lowercase(),
                meanIndex = iz.meanIcingIndex,
            )
        }

        val sldZones = sounding?.sldZones.orEmpty().map { sz ->
            VizSldZone(
                baseFt = sz.baseFt,
                topFt = sz.topFt,
                risk = sz.risk.lowercase(),
                mechanism = sz.mechanism ?: "warm_nose",
            )
        }

        val catLayers = sounding?.verticalMotion?.catRiskLayers.orEmpty().map { cl ->
            VizCatLayer(
                baseFt = cl.baseFt,
                topFt = cl.topFt,
                risk = cl.risk.lowercase(),
                meanRi = cl.richardsonNumber,
            )
        }

        val eShearLayers = sounding?.verticalMotion?.eShearLayers.orEmpty().map { cl ->
            VizCatLayer(
                baseFt = cl.baseFt,
                topFt = cl.topFt,
                risk = cl.risk.lowercase(),
                meanRi = cl.richardsonNumber,
            )
        }

        val inversionLayers = sounding?.inversionLayers.orEmpty().map { inv ->
            VizInversionLayer(
                baseFt = inv.baseFt,
                topFt = inv.topFt,
                strengthC = inv.strengthC,
                surfaceBased = inv.surfaceBased ?: false,
            )
        }

        val thermoRisk = conv?.riskLevel?.lowercase() ?: "none"
        val thermoConvective = VizConvectiveZone(
            riskLevel = thermoRisk,
            baseFt = conv?.baseFt ?: conv?.lfcFt ?: conv?.lclFt,
            topFt = conv?.topFt ?: conv?.elFt,
            coverPct = conv?.coverPct,
            capeJkg = conv?.capeJkg ?: indices?.capeSurfaceJkg,
            cinJkg = conv?.cinJkg ?: indices?.cinSurfaceJkg,
            lclFt = conv?.lclFt ?: indices?.lclAltitudeFt,
            lfcFt = conv?.lfcFt ?: indices?.lfcAltitudeFt,
            elFt = conv?.elFt ?: indices?.elAltitudeFt,
            isDepthUnresolved = false,
        )

        val nwpConvective = convNwp?.let { cn ->
            val risk = cn.riskLevel.lowercase()
            val unresolved = risk !in setOf("none", "marginal") && (cn.baseFt == null || cn.topFt == null)
            VizConvectiveZone(
                riskLevel = risk,
                baseFt = cn.baseFt,
                topFt = cn.topFt,
                coverPct = cn.coverPct,
                capeJkg = cn.capeJkg,
                cinJkg = cn.cinJkg,
                lclFt = cn.lclFt,
                lfcFt = cn.lfcFt,
                elFt = cn.elFt,
                isDepthUnresolved = unresolved,
            )
        }

        val lowPct = sounding?.cloudCoverLowPct
        val midPct = sounding?.cloudCoverMidPct
        val highPct = sounding?.cloudCoverHighPct
        val cloudCoverTotalPct = if (lowPct != null || midPct != null || highPct != null) {
            randomOverlapPct(lowPct ?: 0.0, midPct ?: 0.0, highPct ?: 0.0)
        } else {
            divergenceValue(rpa.modelDivergence, "cloud_cover_pct", model)
        }

        val temperatureC = divergenceValue(rpa.modelDivergence, "temperature_c", model)
        val temperatureCruiseC = rpa.cruiseTemperatureC?.get(model)
            ?: divergenceValue(rpa.modelDivergence, "cruise_temperature_c", model)
            ?: divergenceValue(rpa.modelDivergence, "temperature_cruise_c", model)
        val isaDevC = temperatureCruiseC?.let { it - isaTemperatureC(cruiseAltitudeFt) }
            ?: divergenceValue(rpa.modelDivergence, "isa_dev_c", model)
        val precipitationMm = divergenceValue(rpa.modelDivergence, "precipitation_mm", model)
        val qnhHpa = divergenceValue(rpa.modelDivergence, "pressure_msl_hpa", model)
        val visibilityMeters = divergenceValue(rpa.modelDivergence, "visibility_m", model)
            ?: divergenceValue(rpa.modelDivergence, "visibility_meters", model)

        val soundingCeilingFt = indices?.soundingCeilingFt
        val nwpCeilingFt = sounding?.nwpCloudDiagnostics?.ceilingFt ?: indices?.nwpCeilingFt

        val observedRadarDbz = observedStation?.radar?.let { radar ->
            radar.alongRouteMax ?: radar.annuli?.mapNotNull { it.max }?.maxOrNull()
        }
        val observedCloudTopFt = observedStation?.cloudTops?.let { tops ->
            tops.alongRouteMaxTopFt ?: tops.annuli?.mapNotNull { it.maxTopFt }?.maxOrNull()
        }

        return VizPoint(
            pointIndex = rpa.pointIndex,
            distanceNm = rpa.distanceFromOriginNm,
            lat = rpa.lat,
            lon = rpa.lon,
            waypointIcao = rpa.waypointIcao,
            waypointName = rpa.waypointName,
            flightTimeIso = rpa.interpolatedTime.takeIf { it.isNotBlank() },
            terrainElevationFt = terrainElevationFt,
            altitudeLines = altitudeLines,
            cloudLayers = cloudLayers,
            nwpCloudLayers = nwpCloudLayers,
            hasNativeNwpClouds = hasNativeNwpClouds,
            icingZones = icingZones,
            icingOgimetNwpZones = icingOgimetNwpZones,
            sfipZones = sfipZones,
            iengIcingZones = iengIcingZones,
            sldZones = sldZones,
            catLayers = catLayers,
            eShearLayers = eShearLayers,
            inversionLayers = inversionLayers,
            thermoConvective = thermoConvective,
            nwpConvective = nwpConvective,
            headwindKt = wind?.headwindKt,
            crosswindKt = wind?.crosswindKt,
            windSpeedKt = wind?.windSpeedKt,
            windDirectionDeg = wind?.windDirectionDeg,
            temperatureC = temperatureC,
            temperatureCruiseC = temperatureCruiseC,
            isaDevC = isaDevC,
            precipitationMm = precipitationMm,
            cloudCoverTotalPct = cloudCoverTotalPct,
            cloudCoverLowPct = lowPct,
            cloudCoverMidPct = midPct,
            capeSurfaceJkg = indices?.capeSurfaceJkg ?: conv?.capeJkg,
            cinSurfaceJkg = indices?.cinSurfaceJkg ?: conv?.cinJkg,
            qnhHpa = qnhHpa,
            visibilityMeters = visibilityMeters,
            soundingCeilingFt = soundingCeilingFt,
            nwpCeilingFt = nwpCeilingFt,
            observedRadarDbz = observedRadarDbz,
            observedCloudTopFt = observedCloudTopFt,
        )
    }

    private fun matchObservedStation(
        observed: ObservedConditions?,
        rpa: RoutePointAnalysis,
        index: Int,
        totalPoints: Int,
    ): ObservedStationRef? {
        val stations = observed?.stations.orEmpty()
        if (stations.isEmpty()) return null

        if (!rpa.waypointIcao.isNullOrBlank()) {
            stations.firstOrNull { it.icao.equals(rpa.waypointIcao, ignoreCase = true) }?.let { return it }
        }

        val byCoords = stations
            .filter { it.lat != null && it.lon != null }
            .minByOrNull { hypot((it.lat ?: 0.0) - rpa.lat, (it.lon ?: 0.0) - rpa.lon) }
        if (byCoords != null && hypot((byCoords.lat ?: 0.0) - rpa.lat, (byCoords.lon ?: 0.0) - rpa.lon) <= 0.15) {
            return byCoords
        }

        if (stations.size == totalPoints && index in stations.indices) {
            return stations[index]
        }

        return null
    }

    private fun buildCurrentConditions(
        snapshot: SnapshotResponse?,
        waypointDistanceByIcao: Map<String, Double>,
        maxAltitudeFt: Double,
    ): VizCurrentConditions? {
        if (snapshot == null) return null

        val airports = snapshot.routeObservations?.airports.orEmpty().mapNotNull { obs ->
            val category = obs.metarFlightCategory?.takeIf { it.isNotBlank() }?.uppercase() ?: return@mapNotNull null
            val distNm = obs.enrouteDistanceNm ?: waypointDistanceByIcao[obs.icao.uppercase()] ?: return@mapNotNull null
            VizCurrentConditionsAirport(
                icao = obs.icao,
                distanceNm = distNm,
                flightCategory = category,
                ceilingFt = obs.metarCeilingFt,
                visibilityMeters = obs.metarVisibilityM,
            )
        }

        val sigmets = snapshot.routeSigmets?.sigmets.orEmpty().mapNotNull { sig ->
            val fromNm = sig.enrouteDistanceFromNm ?: return@mapNotNull null
            val toNm = sig.enrouteDistanceToNm ?: return@mapNotNull null
            VizCurrentConditionsSigmet(
                id = sig.id,
                hazard = sig.hazard.ifBlank { "SIGMET" },
                startDistanceNm = fromNm,
                endDistanceNm = toNm,
                baseFt = (sig.baseFt ?: 0).toDouble(),
                topFt = (sig.topFt ?: maxAltitudeFt.toInt()).toDouble(),
            )
        }

        if (airports.isEmpty() && sigmets.isEmpty()) return null
        return VizCurrentConditions(airports = airports, sigmets = sigmets)
    }

    private fun divergenceValue(
        divergence: List<ModelDivergence>,
        variable: String,
        model: String,
    ): Double? {
        for (d in divergence) {
            if (d.variable == variable) {
                return d.modelValues[model]
            }
        }
        return null
    }
}

/**
 * Fritsch-Carlson monotone cubic spline interpolation so line and band renderers
 * can evaluate smooth curves between route points without overshoot.
 */
object MonotoneCubicSpline {

    /**
     * Compute Fritsch-Carlson monotone cubic tangents `m[i]` at each knot `(xs[i], ys[i])`.
     */
    fun computeTangents(xs: List<Double>, ys: List<Double>): List<Double> {
        val n = min(xs.size, ys.size)
        if (n < 2) return List(n) { 0.0 }

        val deltas = DoubleArray(n - 1)
        for (i in 0 until n - 1) {
            val dx = xs[i + 1] - xs[i]
            deltas[i] = if (dx == 0.0) 0.0 else (ys[i + 1] - ys[i]) / dx
        }

        val tangents = DoubleArray(n)
        tangents[0] = deltas[0]
        tangents[n - 1] = deltas[n - 2]

        for (i in 1 until n - 1) {
            if (deltas[i - 1] * deltas[i] <= 0.0) {
                tangents[i] = 0.0
            } else {
                tangents[i] = (deltas[i - 1] + deltas[i]) / 2.0
            }
        }

        for (i in 0 until n - 1) {
            val delta = deltas[i]
            if (delta == 0.0) {
                tangents[i] = 0.0
                tangents[i + 1] = 0.0
                continue
            }
            val alpha = tangents[i] / delta
            val beta = tangents[i + 1] / delta
            val tau = alpha * alpha + beta * beta
            if (tau > 9.0) {
                val s = 3.0 / sqrt(tau)
                tangents[i] = s * alpha * delta
                tangents[i + 1] = s * beta * delta
            }
        }

        return tangents.toList()
    }

    /**
     * Sample a smooth monotone cubic curve across [points] using [stepCount] uniform X steps.
     * Preserves monotonicity on every interval (no overshoot beyond neighboring knot Y values).
     */
    fun sampleCurve(
        points: List<Pair<Double, Double>>,
        stepCount: Int = 60,
    ): List<Pair<Double, Double>> {
        if (points.size < 2 || stepCount <= 0) return points
        val sorted = points.sortedBy { it.first }
        val xs = sorted.map { it.first }
        val ys = sorted.map { it.second }
        val xStart = xs.first()
        val xEnd = xs.last()
        if (xEnd <= xStart) return sorted

        val tangents = computeTangents(xs, ys)
        val n = sorted.size
        val result = ArrayList<Pair<Double, Double>>(stepCount + 1)
        var segIdx = 0

        for (step in 0..stepCount) {
            val x = if (step == stepCount) {
                xEnd
            } else {
                xStart + (xEnd - xStart) * (step.toDouble() / stepCount.toDouble())
            }

            while (segIdx < n - 2 && x > xs[segIdx + 1]) {
                segIdx++
            }

            val x0 = xs[segIdx]
            val x1 = xs[segIdx + 1]
            val y0 = ys[segIdx]
            val y1 = ys[segIdx + 1]
            val h = x1 - x0

            val y = if (h <= 0.0) {
                y0
            } else {
                val t = ((x - x0) / h).coerceIn(0.0, 1.0)
                val t2 = t * t
                val t3 = t2 * t
                val h00 = 2.0 * t3 - 3.0 * t2 + 1.0
                val h10 = t3 - 2.0 * t2 + t
                val h01 = -2.0 * t3 + 3.0 * t2
                val h11 = t3 - t2
                val rawY = h00 * y0 + h10 * h * tangents[segIdx] + h01 * y1 + h11 * h * tangents[segIdx + 1]
                val lo = min(y0, y1)
                val hi = max(y0, y1)
                rawY.coerceIn(lo, hi)
            }
            result.add(x to y)
        }

        return result
    }
}
