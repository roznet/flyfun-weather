package aero.flyfun.rzskewt

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class ThermodynamicsTest {

    @Test
    fun `sea level altitude converts to 1013_25 hPa`() {
        val p = Thermodynamics.altitudeToPressureHpa(0.0)
        assertEquals(1013.25, p, 0.01)
    }

    @Test
    fun `1013_25 hPa converts to 0 ft altitude`() {
        val alt = Thermodynamics.pressureToAltitudeFt(1013.25)
        assertEquals(0.0, alt, 0.1)
    }

    @Test
    fun `altitude to pressure to altitude round trip across standard levels`() {
        val altitudes = listOf(0.0, 2500.0, 5000.0, 10000.0, 18000.0, 24000.0, 30000.0, 34000.0)
        for (alt in altitudes) {
            val p = Thermodynamics.altitudeToPressureHpa(alt)
            val roundTrip = Thermodynamics.pressureToAltitudeFt(p)
            assertEquals("Round-trip failed at $alt ft", alt, roundTrip, 0.5)
        }
    }

    @Test
    fun `pressure to altitude to pressure round trip across standard isobars`() {
        val pressures = listOf(1000.0, 925.0, 850.0, 700.0, 500.0, 400.0, 300.0, 250.0)
        for (p in pressures) {
            val alt = Thermodynamics.pressureToAltitudeFt(p)
            val roundTrip = Thermodynamics.altitudeToPressureHpa(alt)
            assertEquals("Round-trip failed at $p hPa", p, roundTrip, 0.05)
        }
    }

    @Test
    fun `standard pressure levels match approximate aviation flight levels`() {
        assertEquals(48.0, Thermodynamics.flightLevelFromPressure(850.0).toDouble(), 3.0)
        assertEquals(99.0, Thermodynamics.flightLevelFromPressure(700.0).toDouble(), 3.0)
        assertEquals(183.0, Thermodynamics.flightLevelFromPressure(500.0).toDouble(), 4.0)
        assertEquals(301.0, Thermodynamics.flightLevelFromPressure(300.0).toDouble(), 4.0)
        assertEquals(340.0, Thermodynamics.flightLevelFromPressure(250.0).toDouble(), 4.0)
    }

    @Test
    fun `standard atmosphere temperature at sea level is 15C`() {
        assertEquals(15.0, Thermodynamics.standardAtmosphereTemperatureC(0.0), 0.01)
    }

    @Test
    fun `standard atmosphere temperature decreases at 1_98C per 1000ft`() {
        val t10k = Thermodynamics.standardAtmosphereTemperatureC(10000.0)
        assertEquals(-4.81, t10k, 0.05)
    }

    @Test
    fun `standard atmosphere temperature clamps at tropopause -56_5C`() {
        assertEquals(-56.5, Thermodynamics.standardAtmosphereTemperatureC(37000.0), 0.01)
        assertEquals(-56.5, Thermodynamics.standardAtmosphereTemperatureC(45000.0), 0.01)
    }

    @Test
    fun `isaDeviationC returns positive on warm day and negative on cold day`() {
        assertEquals(10.0, Thermodynamics.isaDeviationC(25.0, 0.0), 0.01)
        assertEquals(-5.0, Thermodynamics.isaDeviationC(10.0, 0.0), 0.01)
        val isa8k = Thermodynamics.standardAtmosphereTemperatureC(8000.0)
        assertEquals(7.5, Thermodynamics.isaDeviationC(isa8k + 7.5, 8000.0), 0.01)
    }

    @Test
    fun `celsius and kelvin conversions round trip`() {
        assertEquals(273.15, Thermodynamics.celsiusToKelvin(0.0), 1e-6)
        assertEquals(0.0, Thermodynamics.kelvinToCelsius(273.15), 1e-6)
        assertEquals(-40.0, Thermodynamics.kelvinToCelsius(Thermodynamics.celsiusToKelvin(-40.0)), 1e-6)
    }

    @Test
    fun `saturationVaporPressureHpa at 0C is 6_112 hPa`() {
        assertEquals(6.112, Thermodynamics.saturationVaporPressureHpa(0.0), 0.001)
    }

    @Test
    fun `saturationVaporPressureHpa increases monotonically with temperature`() {
        val temps = listOf(-40.0, -20.0, 0.0, 15.0, 30.0)
        val pressures = temps.map { Thermodynamics.saturationVaporPressureHpa(it) }
        for (i in 0 until pressures.size - 1) {
            assertTrue(pressures[i + 1] > pressures[i])
        }
        assertEquals(17.04, Thermodynamics.saturationVaporPressureHpa(15.0), 0.1)
    }

    @Test
    fun `relativeHumidityPct is 100 when T equals Td`() {
        assertEquals(100.0, Thermodynamics.relativeHumidityPct(18.0, 18.0), 0.001)
    }

    @Test
    fun `relativeHumidityPct is clamped to 0_100 range`() {
        assertEquals(100.0, Thermodynamics.relativeHumidityPct(10.0, 12.0), 0.001)
        assertTrue(Thermodynamics.relativeHumidityPct(25.0, -40.0) in 0.0..5.0)
    }

    @Test
    fun `dewpointFromRhC and relativeHumidityPct round trip`() {
        val pairs = listOf(
            20.0 to 50.0,
            15.0 to 75.0,
            0.0 to 80.0,
            -15.0 to 60.0,
            30.0 to 25.0,
        )
        for ((tempC, rh) in pairs) {
            val td = Thermodynamics.dewpointFromRhC(tempC, rh)
            val computedRh = Thermodynamics.relativeHumidityPct(tempC, td)
            assertEquals("Round-trip failed for T=$tempC, RH=$rh", rh, computedRh, 0.05)
        }
    }

    @Test
    fun `mixingRatioGPerKg and temperatureForMixingRatioC round trip`() {
        val cases = listOf(
            15.0 to 1000.0,
            5.0 to 850.0,
            -5.0 to 700.0,
            -20.0 to 500.0,
        )
        for ((tempC, p) in cases) {
            val w = Thermodynamics.mixingRatioGPerKg(tempC, p)
            val recoveredT = Thermodynamics.temperatureForMixingRatioC(w, p)
            assertEquals("Isohume round-trip failed for T=$tempC, p=$p", tempC, recoveredT, 0.05)
        }
    }

    @Test
    fun `mixingRatioGPerKg at 1000 hPa and 14C is approximately 10 g per kg`() {
        val w = Thermodynamics.mixingRatioGPerKg(14.0, 1000.0)
        assertEquals(10.1, w, 0.3)
    }

    @Test
    fun `potentialTemperatureK at 1000 hPa equals temperature in Kelvin`() {
        val thetaK = Thermodynamics.potentialTemperatureK(20.0, 1000.0)
        assertEquals(293.15, thetaK, 0.01)
    }

    @Test
    fun `potentialTemperatureK and dryAdiabatTemperatureC round trip`() {
        val cases = listOf(
            20.0 to 850.0,
            5.0 to 700.0,
            -15.0 to 500.0,
            -45.0 to 300.0,
        )
        for ((tempC, p) in cases) {
            val thetaK = Thermodynamics.potentialTemperatureK(tempC, p)
            val thetaC = Thermodynamics.kelvinToCelsius(thetaK)
            val recoveredT = Thermodynamics.dryAdiabatTemperatureC(thetaC, p)
            assertEquals("Dry adiabat round-trip failed at T=$tempC, p=$p", tempC, recoveredT, 0.01)
        }
    }

    @Test
    fun `equivalentPotentialTemperatureK exceeds potentialTemperatureK for moist air`() {
        val thetaK = Thermodynamics.potentialTemperatureK(20.0, 1000.0)
        val thetaEK = Thermodynamics.equivalentPotentialTemperatureK(20.0, 15.0, 1000.0)
        assertTrue("theta_e ($thetaEK) should exceed theta ($thetaK)", thetaEK > thetaK + 25.0)
    }

    @Test
    fun `lclTemperatureAndPressure returns surface when saturated`() {
        val (lclT, lclP) = Thermodynamics.lclTemperatureAndPressure(15.0, 15.0, 1000.0)
        assertEquals(15.0, lclT, 0.01)
        assertEquals(1000.0, lclP, 0.01)
    }

    @Test
    fun `lclTemperatureAndPressure computes realistic LCL for unsaturated parcel`() {
        val (lclT, lclP) = Thermodynamics.lclTemperatureAndPressure(20.0, 10.0, 1000.0)
        assertTrue("LCL temp ($lclT) should be below dewpoint 10C", lclT < 10.0 && lclT > 6.0)
        assertTrue("LCL pressure ($lclP) should be between 800 and 920 hPa", lclP in 800.0..920.0)
    }

    @Test
    fun `moistAdiabaticLapseRateCPerHpa is positive and smaller than dry lapse rate in warm air`() {
        val moistDtdp = Thermodynamics.moistAdiabaticLapseRateCPerHpa(20.0, 1000.0)
        val dryDtdp = (20.0 - Thermodynamics.dryAdiabatTemperatureC(20.0, 990.0)) / 10.0
        assertTrue(moistDtdp > 0.0)
        assertTrue("Moist dT/dp ($moistDtdp) should be less than dry dT/dp ($dryDtdp)", moistDtdp < dryDtdp)
    }

    @Test
    fun `moistAdiabatCurve cools monotonically with decreasing pressure`() {
        val curve = Thermodynamics.moistAdiabatCurve(20.0, 1000.0, 250.0, 25.0)
        assertTrue(curve.size >= 30)
        assertEquals(1000.0, curve.first().second, 0.01)
        assertEquals(250.0, curve.last().second, 0.01)
        for (i in 0 until curve.size - 1) {
            assertTrue("Temp should decrease upward", curve[i + 1].first < curve[i].first)
            assertTrue("Pressure should decrease upward", curve[i + 1].second < curve[i].second)
        }
    }

    @Test
    fun `dryAdiabatCurve cools monotonically and preserves potential temperature`() {
        val curve = Thermodynamics.dryAdiabatCurve(25.0, 1050.0, 250.0, 50.0)
        assertTrue(curve.size >= 15)
        for ((t, p) in curve) {
            val thetaC = Thermodynamics.kelvinToCelsius(Thermodynamics.potentialTemperatureK(t, p))
            assertEquals(25.0, thetaC, 0.01)
        }
    }

    @Test
    fun `isohumeCurve preserves mixing ratio along all pressure steps`() {
        val curve = Thermodynamics.isohumeCurve(8.0, 1000.0, 400.0, 50.0)
        assertTrue(curve.size >= 12)
        for ((t, p) in curve) {
            val w = Thermodynamics.mixingRatioGPerKg(t, p)
            assertEquals(8.0, w, 0.05)
        }
    }

    @Test
    fun `headwindCrosswindKt decomposes pure headwind tailwind and crosswind`() {
        // Pure headwind (wind from 270, track 270)
        val (hw1, xw1) = Thermodynamics.headwindCrosswindKt(25.0, 270.0, 270.0)
        assertEquals(25.0, hw1, 0.01)
        assertEquals(0.0, abs(xw1), 0.01)

        // Pure tailwind (wind from 090, track 270)
        val (hw2, xw2) = Thermodynamics.headwindCrosswindKt(25.0, 90.0, 270.0)
        assertEquals(-25.0, hw2, 0.01)
        assertEquals(0.0, abs(xw2), 0.01)

        // Pure right crosswind (wind from 360, track 270)
        val (hw3, xw3) = Thermodynamics.headwindCrosswindKt(20.0, 360.0, 270.0)
        assertEquals(0.0, abs(hw3), 0.01)
        assertEquals(20.0, xw3, 0.01)

        // Pure left crosswind (wind from 180, track 270)
        val (hw4, xw4) = Thermodynamics.headwindCrosswindKt(20.0, 180.0, 270.0)
        assertEquals(0.0, abs(hw4), 0.01)
        assertEquals(-20.0, xw4, 0.01)
    }

    @Test
    fun `headwindCrosswindKt oblique 45 degree wind gives equal headwind and crosswind`() {
        val (hw, xw) = Thermodynamics.headwindCrosswindKt(28.284, 315.0, 270.0)
        assertEquals(20.0, hw, 0.1)
        assertEquals(20.0, xw, 0.1)
    }

    @Test
    fun `headwindCrosswindKt wraps across 360 degree boundary`() {
        val (hw, xw) = Thermodynamics.headwindCrosswindKt(20.0, 10.0, 350.0)
        assertTrue(hw > 18.0)
        assertTrue(xw > 6.0)
    }

    @Test
    fun `lclTemperatureAndPressure lies on the parcel dry adiabat`() {
        val tempC = 24.0
        val dewpointC = 12.0
        val surfaceP = 1000.0
        val (lclT, lclP) = Thermodynamics.lclTemperatureAndPressure(tempC, dewpointC, surfaceP)
        val dryAdiabatAtLcl = Thermodynamics.dryAdiabatTemperatureC(tempC, lclP)
        assertEquals(lclT, dryAdiabatAtLcl, 0.05)
    }

    @Test
    fun `equivalentPotentialTemperatureK is conserved along moist adiabat`() {
        val curve = Thermodynamics.moistAdiabatCurve(18.0, 1000.0, 500.0, 50.0)
        val thetaEStart = Thermodynamics.equivalentPotentialTemperatureK(18.0, 18.0, 1000.0)
        val (t500, p500) = curve.last()
        val thetaE500 = Thermodynamics.equivalentPotentialTemperatureK(t500, t500, p500)
        assertEquals(thetaEStart, thetaE500, 2.5)
    }

    @Test
    fun `flightLevelFromPressure at sea level is FL000`() {
        assertEquals(0, Thermodynamics.flightLevelFromPressure(1013.25))
    }

    @Test
    fun `dewpointFromRhC is always less than or equal to temperature`() {
        for (rh in listOf(10.0, 30.0, 50.0, 75.0, 95.0, 100.0)) {
            val td = Thermodynamics.dewpointFromRhC(22.0, rh)
            assertTrue("Td ($td) should be <= 22.0 for RH=$rh", td <= 22.001)
        }
    }
}
