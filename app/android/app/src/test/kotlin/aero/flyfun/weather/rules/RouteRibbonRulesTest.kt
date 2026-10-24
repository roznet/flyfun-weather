package aero.flyfun.weather.rules

import aero.flyfun.weather.model.LiveRibbonSegment
import aero.flyfun.weather.model.ObsProxy
import aero.flyfun.weather.model.RibbonSigmet
import aero.flyfun.weather.model.RibbonStation
import aero.flyfun.weather.model.RibbonWeather
import aero.flyfun.weather.model.RoutePlace
import aero.flyfun.weather.model.rules.RouteRibbonRules
import aero.flyfun.weather.model.rules.VipIntensity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RouteRibbonRulesTest {

    @Test
    fun `VIP radar intensity classes map to expected hex colors and ranks`() {
        assertEquals("#22c55e", RouteRibbonRules.vipColorHex("light"))
        assertEquals("#eab308", RouteRibbonRules.vipColorHex("moderate"))
        assertEquals("#f97316", RouteRibbonRules.vipColorHex("heavy"))
        assertEquals("#ef4444", RouteRibbonRules.vipColorHex("very_heavy"))
        assertEquals("#7f1d1d", RouteRibbonRules.vipColorHex("extreme"))
        assertEquals("#a855f7", RouteRibbonRules.vipColorHex("purple"))

        assertEquals(0, RouteRibbonRules.vipRank("none"))
        assertEquals(1, RouteRibbonRules.vipRank("light"))
        assertEquals(2, RouteRibbonRules.vipRank("moderate"))
        assertEquals(3, RouteRibbonRules.vipRank("heavy"))
        assertEquals(4, RouteRibbonRules.vipRank("very_heavy"))
        assertEquals(5, RouteRibbonRules.vipRank("extreme"))

        assertEquals(VipIntensity.NONE, VipIntensity.fromDbz(10.0))
        assertEquals(VipIntensity.LIGHT, VipIntensity.fromDbz(22.0))
        assertEquals(VipIntensity.MODERATE, VipIntensity.fromDbz(35.0))
        assertEquals(VipIntensity.HEAVY, VipIntensity.fromDbz(45.0))
        assertEquals(VipIntensity.VERY_HEAVY, VipIntensity.fromDbz(52.0))
        assertEquals(VipIntensity.EXTREME, VipIntensity.fromDbz(58.0))
    }

    @Test
    fun `normalizeBins clamps fractions to 0_0 through 1_0 and resolves VIP intensity`() {
        val segments = listOf(
            LiveRibbonSegment(
                index = 0,
                startNm = -10.0,
                endNm = 50.0,
                radarStatus = "light",
                radarMaxDbz = 24.0,
                radarCoverage = 0.25,
            ),
            LiveRibbonSegment(
                index = 1,
                startNm = 150.0, // swapped order
                endNm = 50.0,
                radarStatus = "none",
                radarMaxDbz = 43.0, // infers heavy from dBZ
                radarCoverage = 0.6,
                flashCount = 4,
            ),
            LiveRibbonSegment(
                index = 2,
                startNm = 150.0,
                endNm = 240.0, // exceeds 200 nm route
                radarStatus = "extreme",
                radarMaxDbz = 59.0,
            ),
        )

        val bins = RouteRibbonRules.normalizeBins(segments, totalRouteNm = 200.0)
        assertEquals(3, bins.size)

        // Bin 0 clamped [-10, 50] -> [0, 50] / 200 = [0.0, 0.25]
        assertEquals(0.0, bins[0].startNm, 1e-6)
        assertEquals(50.0, bins[0].endNm, 1e-6)
        assertEquals(0.0, bins[0].startFraction, 1e-6)
        assertEquals(0.25, bins[0].endFraction, 1e-6)
        assertEquals(0.25, bins[0].widthFraction, 1e-6)
        assertEquals("light", bins[0].worstVipIntensity)
        assertEquals("#22c55e", bins[0].vipHexColor)
        assertTrue(bins[0].hasPrecipitation)

        // Bin 1 swapped [150, 50] -> [50, 150] / 200 = [0.25, 0.75]
        assertEquals(50.0, bins[1].startNm, 1e-6)
        assertEquals(150.0, bins[1].endNm, 1e-6)
        assertEquals(0.25, bins[1].startFraction, 1e-6)
        assertEquals(0.75, bins[1].endFraction, 1e-6)
        assertEquals(0.50, bins[1].widthFraction, 1e-6)
        assertEquals("heavy", bins[1].worstVipIntensity)
        assertEquals("#f97316", bins[1].vipHexColor)
        assertEquals(4, bins[1].flashCount)

        // Bin 2 clamped [150, 240] -> [150, 200] / 200 = [0.75, 1.0]
        assertEquals(150.0, bins[2].startNm, 1e-6)
        assertEquals(200.0, bins[2].endNm, 1e-6)
        assertEquals(0.75, bins[2].startFraction, 1e-6)
        assertEquals(1.0, bins[2].endFraction, 1e-6)
        assertEquals(0.25, bins[2].widthFraction, 1e-6)
        assertEquals("extreme", bins[2].worstVipIntensity)
        assertEquals("#7f1d1d", bins[2].vipHexColor)
    }

    @Test
    fun `projectStations clamps route fractions and filters by corridor offset`() {
        val stations = listOf(
            RibbonStation(icao = "LFML", enrouteDistanceNm = 210.0, offsetNm = 2.0, metarCategory = "VFR"),
            RibbonStation(icao = "LFMN", enrouteDistanceNm = 90.0, offsetNm = 40.0, metarCategory = "MVFR"),
            RibbonStation(icao = "LFMD", enrouteDistanceNm = -5.0, offsetNm = 0.0, metarCategory = "IFR"),
            RibbonStation(icao = "LFTH", enrouteDistanceNm = 100.0, offsetNm = -12.0, metarCategory = "vfr"),
        )

        val projected = RouteRibbonRules.projectStations(
            stations = stations,
            totalRouteNm = 200.0,
            maxOffsetNm = 25.0,
        )

        // LFMN (40 nm off route) filtered out; remaining sorted by enrouteDistanceNm
        assertEquals(listOf("LFMD", "LFTH", "LFML"), projected.map { it.icao })
        assertEquals(0.0, projected[0].routeFraction, 1e-6)
        assertEquals(0.5, projected[1].routeFraction, 1e-6)
        assertEquals(1.0, projected[2].routeFraction, 1e-6)
        assertEquals("VFR", projected[1].flightCategory)
    }

    @Test
    fun `clampSigmetSpans clamps spans to route bounds and drops out-of-range sigmets`() {
        val sigmets = listOf(
            RibbonSigmet(key = "sig-1", hazard = "TS", qualifier = "EMBD", startNm = -25.0, endNm = 80.0),
            RibbonSigmet(key = "sig-2", hazard = "ICE", qualifier = "SEV", startNm = 120.0, endNm = 260.0),
            RibbonSigmet(key = "sig-outside", hazard = "MTW", startNm = 250.0, endNm = 320.0),
        )

        val clamped = RouteRibbonRules.clampSigmetSpans(sigmets, totalRouteNm = 200.0)
        assertEquals(2, clamped.size)

        assertEquals("sig-1", clamped[0].id)
        assertEquals(0.0, clamped[0].startNm, 1e-6)
        assertEquals(80.0, clamped[0].endNm, 1e-6)
        assertEquals(0.0, clamped[0].startFraction, 1e-6)
        assertEquals(0.4, clamped[0].endFraction, 1e-6)

        assertEquals("sig-2", clamped[1].id)
        assertEquals(120.0, clamped[1].startNm, 1e-6)
        assertEquals(200.0, clamped[1].endNm, 1e-6)
        assertEquals(0.6, clamped[1].startFraction, 1e-6)
        assertEquals(1.0, clamped[1].endFraction, 1e-6)
    }

    @Test
    fun `flownFraction computes clamped progress and returns null for invalid inputs`() {
        assertNull(RouteRibbonRules.flownFraction(null, 200.0))
        assertNull(RouteRibbonRules.flownFraction(50.0, 0.0))
        assertNull(RouteRibbonRules.flownFraction(50.0, -10.0))

        assertEquals(0.0, RouteRibbonRules.flownFraction(-15.0, 200.0)!!, 1e-6)
        assertEquals(0.35, RouteRibbonRules.flownFraction(70.0, 200.0)!!, 1e-6)
        assertEquals(1.0, RouteRibbonRules.flownFraction(250.0, 200.0)!!, 1e-6)
    }

    @Test
    fun `inspectBin inspectStation and inspectSigmet format readable summaries`() {
        val segment = LiveRibbonSegment(
            index = 2,
            startNm = 40.0,
            endNm = 60.0,
            radarStatus = "heavy",
            radarMaxDbz = 44.0,
            radarCoverage = 0.45,
            topsMaxFt = 32000.0,
            topsStatus = "high",
            flashCount = 7,
            nearestFlashNm = 2.4,
            place = RoutePlace(text = "Near LFMD"),
        )
        val station = RibbonStation(
            icao = "LFMD",
            name = "Cannes Mandelieu",
            enrouteDistanceNm = 48.0,
            offsetNm = 3.2,
            metarCategory = "MVFR",
            tafCategory = "VFR",
            metarCeilingFt = 2200,
            metarVisibilityM = 6000,
            windAdvisory = "caution",
            hasChange = true,
            changeDirection = "worsened",
            weather = listOf(RibbonWeather(code = "RA", label = "Light Rain")),
            obsProxy = ObsProxy(icao = "LFMN", distanceNm = 12.5),
        )
        val sigmet = RibbonSigmet(
            key = "sig-ts",
            firId = "LFFF",
            hazard = "TS",
            qualifier = "EMBD",
            startNm = 30.0,
            endNm = 90.0,
            baseFt = 3000,
            topFt = 32000,
            validTo = "2026-04-12T13:00:00Z",
            isNew = true,
        )

        val binSummary = RouteRibbonRules.inspectBin(
            segment = segment,
            totalRouteNm = 200.0,
            stations = listOf(station),
            sigmets = listOf(sigmet),
        )
        assertEquals("Near LFMD (40–60 nm)", binSummary.title)
        assertTrue(binSummary.badges.contains("Heavy Rain"))
        assertTrue(binSummary.badges.contains("7 lightning"))
        assertTrue(binSummary.details.any { it.contains("44 dBZ") })
        assertTrue(binSummary.details.any { it.contains("LFMD") })
        assertEquals("#f97316", binSummary.severityColorHex)

        val stationSummary = RouteRibbonRules.inspectStation(station)
        assertEquals("LFMD — Cannes Mandelieu", stationSummary.title)
        assertTrue(stationSummary.badges.contains("MVFR"))
        assertTrue(stationSummary.badges.contains("Worsened"))
        assertTrue(stationSummary.details.any { it.contains("6 km") })
        assertTrue(stationSummary.details.any { it.contains("2200 ft") })
        assertTrue(stationSummary.details.any { it.contains("Light Rain") })

        val sigmetSummary = RouteRibbonRules.inspectSigmet(sigmet)
        assertEquals("SIGMET · EMBD TS (LFFF)", sigmetSummary.title)
        assertTrue(sigmetSummary.subtitle.contains("Valid until 13:00Z"))
        assertTrue(sigmetSummary.subtitle.contains("3000ft–FL320"))
        assertFalse(sigmetSummary.details.isEmpty())
    }
}
