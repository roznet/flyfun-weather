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
    fun `skew factor keeps the visual angle on tall plots`() {
        val t = SkewTTransform(left = 0f, top = 0f, width = 220f, height = 440f)
        assertEquals(2.0, t.skewFactor, 1e-6)
    }

    private fun isothermAngle(fit: SkewTFit): Double {
        val cfg = SkewTConfig(tMin = fit.tMin, tMax = fit.tMax)
        val t = SkewTTransform(0f, 0f, fit.width, fit.height, cfg)
        val dx = (t.tempPressureToX(0.0, cfg.pTop) - t.tempPressureToX(0.0, cfg.pBottom)).toDouble()
        val dy = (t.pressureToY(cfg.pBottom) - t.pressureToY(cfg.pTop)).toDouble()
        return Math.toDegrees(kotlin.math.atan2(dy, dx))
    }

    @Test
    fun `fit keeps the classic wide layout`() {
        val fit = fitSkewT(1300f, 382f, -20.0..20.0)
        assertEquals(-60.0, fit.tMin, 1e-9)
        assertEquals(40.0, fit.tMax, 1e-9)
        assertEquals(382f, fit.height, 1e-3f)
        assertEquals(382f * 1.8f, fit.width, 1e-2f)
    }

    @Test
    fun `fit narrow box centres the window on the sounding at 45 degrees`() {
        val fit = fitSkewT(300f, 360f, -12.0..18.0)
        assertEquals(300f, fit.width, 1e-3f)
        assertEquals(360f, fit.height, 1e-3f)
        assertEquals(SKEW_SHIFT_DEG * 300 / 360, fit.tMax - fit.tMin, 1e-4)
        assertEquals(3.0, (fit.tMin + fit.tMax) / 2, 1e-4)
        assertEquals(45.0, isothermAngle(fit), 1e-3)
    }

    @Test
    fun `fit shrinks a tall narrow box so the sounding fits`() {
        val fit = fitSkewT(220f, 765f, -18.0..18.0)
        assertEquals((SKEW_SHIFT_DEG * 220 / 46).toFloat(), fit.height, 1e-2f)
        assertEquals(46.0, fit.tMax - fit.tMin, 1e-3)
        assertEquals(45.0, isothermAngle(fit), 1e-3)
    }

    @Test
    fun `fit stops at the minimum height and stays inside the config range`() {
        assertEquals(220f, fitSkewT(140f, 380f, -50.0..20.0).height, 1e-3f)
        assertEquals(40.0, fitSkewT(300f, 360f, 30.0..45.0).tMax, 1e-6)
        assertEquals(-60.0, fitSkewT(300f, 360f, -80.0..-70.0).tMin, 1e-6)
        val wide = fitSkewT(500f, 600f, -70.0..50.0)
        assertEquals(-60.0, wide.tMin, 1e-9)
        assertEquals(45.0, isothermAngle(wide), 1e-3)
    }

    @Test
    fun `skewedExtent reads points at the bottom axis`() {
        val ext = skewedExtent(listOf(12.0 to 1050.0, -45.0 to 250.0, null to 500.0, -60.0 to 200.0))!!
        assertEquals(-45.0 + SKEW_SHIFT_DEG, ext.start, 1e-6)
        assertEquals(12.0, ext.endInclusive, 1e-6)
        assertEquals(null, skewedExtent(emptyList()))
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
