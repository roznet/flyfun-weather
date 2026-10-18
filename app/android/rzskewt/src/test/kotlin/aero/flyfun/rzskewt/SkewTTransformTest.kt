package aero.flyfun.rzskewt

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SkewTTransformTest {

    @Test
    fun `pressureToY maps pBottom to bottom and pTop to top`() {
        val config = SkewTConfig(pBottom = 1050.0, pTop = 250.0)
        val transform = SkewTTransform(left = 40f, top = 20f, width = 500f, height = 400f, config = config)

        assertEquals(420f, transform.pressureToY(1050.0), 0.01f)
        assertEquals(20f, transform.pressureToY(250.0), 0.01f)
    }

    @Test
    fun `pressureToY and yToPressure round trip across all standard levels`() {
        val transform = SkewTTransform(left = 30f, top = 15f, width = 480f, height = 420f)
        val levels = listOf(1050.0, 1000.0, 925.0, 850.0, 700.0, 500.0, 400.0, 300.0, 250.0)
        for (p in levels) {
            val y = transform.pressureToY(p)
            val recoveredP = transform.yToPressure(y)
            assertEquals("Failed Y round-trip at $p hPa", p, recoveredP, 0.05)
        }
    }

    @Test
    fun `tempPressureToX and xPressureToTemp round trip across temperatures and pressures`() {
        val transform = SkewTTransform(left = 40f, top = 20f, width = 600f, height = 450f)
        val cases = listOf(
            -50.0 to 300.0,
            -20.0 to 500.0,
            0.0 to 700.0,
            15.0 to 850.0,
            30.0 to 1000.0,
        )
        for ((t, p) in cases) {
            val x = transform.tempPressureToX(t, p)
            val recoveredT = transform.xPressureToTemp(x, p)
            assertEquals("Failed X round-trip at T=$t, p=$p", t, recoveredT, 0.02)
        }
    }

    @Test
    fun `isotherms skew rightward as pressure decreases`() {
        val transform = SkewTTransform(left = 0f, top = 0f, width = 500f, height = 400f)
        val x1000 = transform.tempPressureToX(0.0, 1000.0)
        val x700 = transform.tempPressureToX(0.0, 700.0)
        val x500 = transform.tempPressureToX(0.0, 500.0)
        val x300 = transform.tempPressureToX(0.0, 300.0)

        assertTrue("Isotherm should shift right at 700 hPa", x700 > x1000)
        assertTrue("Isotherm should shift right at 500 hPa", x500 > x700)
        assertTrue("Isotherm should shift right at 300 hPa", x300 > x500)
    }

    @Test
    fun `at pBottom tempPressureToX has zero skew offset`() {
        val config = SkewTConfig(pBottom = 1050.0, pTop = 250.0, tMin = -60.0, tMax = 40.0)
        val transform = SkewTTransform(left = 50f, top = 20f, width = 400f, height = 400f, config = config)

        assertEquals(50f, transform.tempPressureToX(-60.0, 1050.0), 0.01f)
        assertEquals(450f, transform.tempPressureToX(40.0, 1050.0), 0.01f)
        assertEquals(290f, transform.tempPressureToX(0.0, 1050.0), 0.01f)
    }

    @Test
    fun `maxSkewFactor clamps skew on narrow portrait phone screens`() {
        val config = SkewTConfig(skewAngleDeg = 45.0, maxSkewFactor = 1.0)
        // Narrow phone plot: width = 220, height = 440 -> raw tan(45)*h/w = 2.0, clamped to 1.0
        val narrowTransform = SkewTTransform(left = 0f, top = 0f, width = 220f, height = 440f, config = config)
        assertEquals(1.0, narrowTransform.skewFactor, 1e-6)

        // Wide tablet plot: width = 600, height = 400 -> raw tan(45)*h/w = 0.667 <= 1.0
        val wideTransform = SkewTTransform(left = 0f, top = 0f, width = 600f, height = 400f, config = config)
        assertEquals(400.0 / 600.0, wideTransform.skewFactor, 1e-4)
    }

    @Test
    fun `altitudeFtToY and yToAltitudeFt round trip`() {
        val transform = SkewTTransform(left = 20f, top = 10f, width = 400f, height = 360f)
        val altitudes = listOf(0.0, 5000.0, 10000.0, 18000.0, 28000.0)
        for (alt in altitudes) {
            val y = transform.altitudeFtToY(alt)
            val recoveredAlt = transform.yToAltitudeFt(y)
            assertEquals("Altitude round-trip failed at $alt ft", alt, recoveredAlt, 1.0)
        }
    }

    @Test
    fun `isPressureVisible checks configured pressure bounds`() {
        val transform = SkewTTransform(left = 0f, top = 0f, width = 300f, height = 300f)
        assertTrue(transform.isPressureVisible(1000.0))
        assertTrue(transform.isPressureVisible(250.0))
        assertFalse(transform.isPressureVisible(1100.0))
        assertFalse(transform.isPressureVisible(200.0))
    }
}
