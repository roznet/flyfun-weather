package aero.flyfun.weather.viz

import aero.flyfun.weather.model.CATRiskLayer
import aero.flyfun.weather.model.ConvectiveAssessment
import aero.flyfun.weather.model.ElevationPoint
import aero.flyfun.weather.model.ElevationResponse
import aero.flyfun.weather.model.EnhancedCloudLayer
import aero.flyfun.weather.model.IcingZone
import aero.flyfun.weather.model.InversionLayer
import aero.flyfun.weather.model.ModelDivergence
import aero.flyfun.weather.model.NWPCloudDiagnostics
import aero.flyfun.weather.model.NWPCloudLayerDiag
import aero.flyfun.weather.model.RouteAnalysesResponse
import aero.flyfun.weather.model.RoutePointAnalysis
import aero.flyfun.weather.model.SfipZone
import aero.flyfun.weather.model.SoundingAnalysis
import aero.flyfun.weather.model.ThermodynamicIndices
import aero.flyfun.weather.model.VerticalMotionAssessment
import aero.flyfun.weather.model.WindComponent
import aero.flyfun.weather.model.viz.MonotoneCubicSpline
import aero.flyfun.weather.model.viz.VizDataExtractor
import kotlin.math.abs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VizDataExtractorTest {

    @Test
    fun `extractVizData extracts cloud layers icing zones altitude lines and terrain`() {
        val sounding0 = SoundingAnalysis(
            indices = ThermodynamicIndices(
                freezingLevelFt = 6500.0,
                minus10cLevelFt = 11500.0,
                minus20cLevelFt = 16500.0,
                lclAltitudeFt = 3200.0,
                lfcAltitudeFt = 5000.0,
                elAltitudeFt = 22000.0,
                capeSurfaceJkg = 450.0,
                cinSurfaceJkg = -25.0,
                soundingCeilingFt = 3500.0,
                nwpCeilingFt = 3800.0,
            ),
            cloudLayers = listOf(
                EnhancedCloudLayer(
                    baseFt = 3500.0,
                    topFt = 7500.0,
                    coverage = "bkn",
                    meanDewpointDepressionC = 1.2,
                )
            ),
            nwpCloudLayers = listOf(
                EnhancedCloudLayer(
                    baseFt = 3800.0,
                    topFt = 8000.0,
                    coverage = "ovc",
                    meanCloudCoverPct = 85.0,
                )
            ),
            icingZones = listOf(
                IcingZone(baseFt = 6500.0, topFt = 8000.0, risk = "moderate", icingType = "mixed")
            ),
            icingOgimetNwpZones = listOf(
                IcingZone(baseFt = 6500.0, topFt = 8000.0, risk = "severe", icingType = "rime")
            ),
            sfipZones = listOf(
                SfipZone(baseFt = 6600.0, topFt = 7800.0, risk = "moderate", variant = "full", meanSfip100 = 58.0)
            ),
            inversionLayers = listOf(
                InversionLayer(baseFt = 1500.0, topFt = 2200.0, strengthC = 2.8, surfaceBased = false)
            ),
            convective = ConvectiveAssessment(
                riskLevel = "moderate",
                baseFt = 5000.0,
                topFt = 22000.0,
                coverPct = 40.0,
            ),
            convectiveNwp = ConvectiveAssessment(
                riskLevel = "low",
                baseFt = 4800.0,
                topFt = 18000.0,
                coverPct = 90.0,
            ),
            verticalMotion = VerticalMotionAssessment(
                classification = "synoptic_ascent",
                maxOmegaPaS = -1.2,
                maxWFpm = 150.0,
                catRiskLayers = listOf(
                    CATRiskLayer(baseFt = 12000.0, topFt = 15000.0, risk = "light", richardsonNumber = 0.45)
                ),
            ),
            nwpCloudDiagnostics = NWPCloudDiagnostics(
                low = NWPCloudLayerDiag(coverPct = 80.0, baseFt = 3800.0, topFt = 7000.0),
                mid = NWPCloudLayerDiag(coverPct = 50.0, baseFt = 8000.0, topFt = 14000.0),
                ceilingFt = 3800.0,
            ),
            cloudCoverLowPct = 80.0,
            cloudCoverMidPct = 50.0,
            cloudCoverHighPct = 0.0,
        )

        val sounding1 = SoundingAnalysis(
            indices = ThermodynamicIndices(
                freezingLevelFt = 6000.0,
                minus10cLevelFt = 11000.0,
                minus20cLevelFt = 16000.0,
                lclAltitudeFt = 3000.0,
            ),
        )

        val manifest = RouteAnalysesResponse(
            routeName = "EGTK - LFMD",
            targetDate = "2026-04-19",
            departureTime = "2026-04-19T08:00:00Z",
            flightDurationHours = 3.5,
            totalDistanceNm = 100.0,
            cruiseAltitudeFt = 8000,
            flightCeilingFt = 18000,
            models = listOf("ecmwf", "gfs"),
            analyses = listOf(
                RoutePointAnalysis(
                    pointIndex = 0,
                    lat = 51.8,
                    lon = -1.3,
                    distanceFromOriginNm = 0.0,
                    waypointIcao = "EGTK",
                    waypointName = "Oxford",
                    interpolatedTime = "2026-04-19T08:00:00Z",
                    trackDeg = 150.0,
                    windComponents = mapOf(
                        "ecmwf" to WindComponent(
                            windSpeedKt = 15.0,
                            windDirectionDeg = 180.0,
                            headwindKt = 12.5,
                            crosswindKt = -8.0,
                        )
                    ),
                    sounding = mapOf("ecmwf" to sounding0),
                    modelDivergence = listOf(
                        ModelDivergence(
                            variable = "temperature_c",
                            modelValues = mapOf("ecmwf" to 16.0),
                        ),
                        ModelDivergence(
                            variable = "cruise_temperature_c",
                            modelValues = mapOf("ecmwf" to -2.0),
                        ),
                    ),
                ),
                RoutePointAnalysis(
                    pointIndex = 1,
                    lat = 50.5,
                    lon = 0.2,
                    distanceFromOriginNm = 100.0,
                    waypointIcao = "LFMD",
                    waypointName = "Cannes",
                    interpolatedTime = "2026-04-19T11:30:00Z",
                    trackDeg = 150.0,
                    windComponents = mapOf(
                        "ecmwf" to WindComponent(
                            windSpeedKt = 7.0,
                            windDirectionDeg = 320.0,
                            headwindKt = -5.0,
                            crosswindKt = 4.0,
                        )
                    ),
                    sounding = mapOf("ecmwf" to sounding1),
                ),
            ),
        )

        val elevation = ElevationResponse(
            routeName = "EGTK - LFMD",
            points = listOf(
                ElevationPoint(distanceNm = 0.0, elevationFt = 270.0, lat = 51.8, lon = -1.3),
                ElevationPoint(distanceNm = 50.0, elevationFt = 1400.0, lat = 51.1, lon = -0.5),
                ElevationPoint(distanceNm = 100.0, elevationFt = 15.0, lat = 50.5, lon = 0.2),
            ),
            maxElevationFt = 1400.0,
            totalDistanceNm = 100.0,
        )

        val viz = VizDataExtractor.extractVizData(
            routeAnalyses = manifest,
            elevation = elevation,
            selectedModel = "ecmwf",
        )
        assertNotNull(viz)
        requireNotNull(viz)

        assertEquals(2, viz.points.size)
        assertEquals(8000.0, viz.cruiseAltitudeFt, 0.01)
        assertEquals(18000.0, viz.flightCeilingFt, 0.01)
        assertEquals(100.0, viz.totalDistanceNm, 0.01)
        assertEquals(2, viz.waypoints.size)
        assertEquals("EGTK", viz.waypoints[0].icao)
        assertEquals(3, viz.terrainProfile.size)

        val p0 = viz.points[0]
        assertEquals(1, p0.cloudLayers.size)
        assertEquals(3500.0, p0.cloudLayers[0].baseFt, 0.01)
        assertEquals(1, p0.nwpCloudLayers.size)
        assertEquals(85.0, p0.nwpCloudLayers[0].coveragePct ?: 0.0, 0.01)
        assertEquals(1, p0.icingZones.size)
        assertEquals(1, p0.icingOgimetNwpZones.size)
        assertEquals("severe", p0.icingOgimetNwpZones[0].risk)
        assertEquals(1, p0.sfipZones.size)
        assertEquals(1, p0.catLayers.size)
        assertEquals(1, p0.inversionLayers.size)
        assertEquals("moderate", p0.thermoConvective.riskLevel)
        assertEquals(6500.0, p0.altitudeLines.freezingLevelFt ?: 0.0, 0.01)
        assertEquals(3800.0, p0.nwpCeilingFt ?: 0.0, 0.01)
        assertEquals(12.5, p0.headwindKt ?: 0.0, 0.01)
        assertEquals(-8.0, p0.crosswindKt ?: 0.0, 0.01)
        assertEquals(270.0, p0.terrainElevationFt, 0.01)
        assertNotNull(p0.isaDevC)

        // Random overlap of 80% and 50% = (1 - 0.2 * 0.5) * 100 = 90%
        assertEquals(90.0, p0.cloudCoverTotalPct ?: 0.0, 0.1)
    }

    @Test
    fun `MonotoneCubicSpline preserves monotonicity without overshoot`() {
        val knots = listOf(
            0.0 to 1000.0,
            25.0 to 2000.0,
            50.0 to 2000.0,
            75.0 to 4500.0,
            100.0 to 6000.0,
        )

        val sampled = MonotoneCubicSpline.sampleCurve(knots, stepCount = 40)
        assertTrue(sampled.size > 5)

        // Check monotonicity: every consecutive y is >= previous y (within floating point tolerance)
        for (i in 1 until sampled.size) {
            assertTrue(
                "Expected monotonic non-decreasing at index $i: ${sampled[i - 1].second} -> ${sampled[i].second}",
                sampled[i].second >= sampled[i - 1].second - 1e-6,
            )
        }
        // Flat segment [25, 50] should stay at 2000.0 without overshoot
        val flatPoints = sampled.filter { it.first in 25.0..50.0 }
        for (pt in flatPoints) {
            assertTrue(abs(pt.second - 2000.0) < 1e-4)
        }
    }
}
