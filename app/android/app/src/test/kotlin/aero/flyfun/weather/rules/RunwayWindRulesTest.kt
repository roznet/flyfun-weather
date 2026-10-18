package aero.flyfun.weather.rules

import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.EndComponents
import aero.flyfun.weather.model.RunwayEnd
import aero.flyfun.weather.model.WindSample
import aero.flyfun.weather.model.rules.RunwayWindRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.sqrt

class RunwayWindRulesTest {

    @Test
    fun `computeComponents calculates headwind tailwind and signed crosswind accurately`() {
        // Pure headwind on RWY 27 (270°)
        val (hw27, xw27) = RunwayWindRules.computeComponents(
            windDirDeg = 270,
            windSpeedKt = 20,
            windGustKt = null,
            runwayHeadingDeg = 270.0,
        )
        assertEquals(20.0, hw27, 1e-6)
        assertEquals(0.0, xw27, 1e-6)

        // Pure tailwind on RWY 09 (090°) when wind is 270° at 20 kt
        val (hw09, xw09) = RunwayWindRules.computeComponents(
            windDirDeg = 270,
            windSpeedKt = 20,
            windGustKt = null,
            runwayHeadingDeg = 90.0,
        )
        assertEquals(-20.0, hw09, 1e-6)
        assertEquals(0.0, xw09, 1e-6)

        // Pure right crosswind on RWY 36 (360°) when wind is 090° at 15 kt
        val (hwRight, xwRight) = RunwayWindRules.computeComponents(
            windDirDeg = 90,
            windSpeedKt = 15,
            windGustKt = null,
            runwayHeadingDeg = 360.0,
        )
        assertEquals(0.0, hwRight, 1e-6)
        assertEquals(15.0, xwRight, 1e-6)

        // Pure left crosswind on RWY 36 (360°) when wind is 270° at 15 kt
        val (hwLeft, xwLeft) = RunwayWindRules.computeComponents(
            windDirDeg = 270,
            windSpeedKt = 15,
            windGustKt = null,
            runwayHeadingDeg = 360.0,
        )
        assertEquals(0.0, hwLeft, 1e-6)
        assertEquals(-15.0, xwLeft, 1e-6)

        // 45-degree wind from left on RWY 18 (180°) with wind 135° at 20 kt
        val expected45 = 20.0 * sqrt(2.0) / 2.0
        val (hw45, xw45) = RunwayWindRules.computeComponents(
            windDirDeg = 135,
            windSpeedKt = 20,
            windGustKt = null,
            runwayHeadingDeg = 180.0,
        )
        assertEquals(expected45, hw45, 1e-5)
        assertEquals(-expected45, xw45, 1e-5)

        // Calm or null wind returns (0.0, 0.0)
        assertEquals(0.0 to 0.0, RunwayWindRules.computeComponents(null, 15, null, 180.0))
        assertEquals(0.0 to 0.0, RunwayWindRules.computeComponents(180, 0, null, 180.0))
    }

    @Test
    fun `pickBestRunwayEnd chooses into-wind end with lowest crosswind`() {
        val ends = listOf(
            RunwayEnd(id = "09", headingDeg = 90.0),
            RunwayEnd(id = "27", headingDeg = 270.0),
            RunwayEnd(id = "18", headingDeg = 180.0),
            RunwayEnd(id = "36", headingDeg = 360.0),
        )

        // Wind 250° at 18 kt -> RWY 27 has smallest crosswind (20° off) and positive headwind
        assertEquals("27", RunwayWindRules.pickBestRunwayEnd(ends, windDirDeg = 250, windSpeedKt = 18))

        // Wind 350° at 12 kt -> RWY 36 has smallest crosswind (10° off) and positive headwind
        assertEquals("36", RunwayWindRules.pickBestRunwayEnd(ends, windDirDeg = 350, windSpeedKt = 12))

        // Wind 100° at 15 kt -> RWY 09
        assertEquals("09", RunwayWindRules.pickBestRunwayEnd(ends, windDirDeg = 100, windSpeedKt = 15))

        // Calm/variable wind -> defaults to first runway end
        assertEquals("09", RunwayWindRules.pickBestRunwayEnd(ends, windDirDeg = null, windSpeedKt = 0))
        assertNull(RunwayWindRules.pickBestRunwayEnd(emptyList(), windDirDeg = 270, windSpeedKt = 10))
    }

    @Test
    fun `evaluateRunwayEnds populates gust crosswind left-crosswind flag and favored flag`() {
        val ends = listOf(
            RunwayEnd(id = "17", headingDeg = 170.0),
            RunwayEnd(id = "35", headingDeg = 350.0),
        )
        // Wind 140° (30° left of RWY 17) at 20G30kt -> sin(30°) = 0.5
        val evaluated = RunwayWindRules.evaluateRunwayEnds(
            ends = ends,
            windDirDeg = 140,
            windSpeedKt = 20,
            windGustKt = 30,
        )
        assertEquals(2, evaluated.size)

        val rwy17 = evaluated[0]
        assertTrue(rwy17.isFavored)
        assertTrue(rwy17.isLeftCrosswind)
        assertEquals(10.0, rwy17.crosswindKt, 1e-5)
        assertEquals(15.0, rwy17.gustCrosswindKt!!, 1e-5)
        assertTrue(rwy17.headwindKt > 17.0)

        val rwy35 = evaluated[1]
        assertFalse(rwy35.isFavored)
        assertFalse(rwy35.isLeftCrosswind)
        assertTrue(rwy35.headwindKt < -17.0)
    }

    @Test
    fun `crosswindSeverity classifies green amber and red for default and custom limits`() {
        // Default limit = 15 kt (amber >= 10 kt, red >= 15 kt)
        assertEquals(Assessment.GREEN, RunwayWindRules.crosswindSeverity(0.0))
        assertEquals(Assessment.GREEN, RunwayWindRules.crosswindSeverity(9.9))
        assertEquals(Assessment.AMBER, RunwayWindRules.crosswindSeverity(10.0))
        assertEquals(Assessment.AMBER, RunwayWindRules.crosswindSeverity(14.9))
        assertEquals(Assessment.RED, RunwayWindRules.crosswindSeverity(15.0))
        assertEquals(Assessment.RED, RunwayWindRules.crosswindSeverity(-18.0))

        // Custom aircraft limit = 24 kt (amber >= 16 kt, red >= 24 kt)
        assertEquals(Assessment.GREEN, RunwayWindRules.crosswindSeverity(15.0, maxCrosswindKt = 24))
        assertEquals(Assessment.AMBER, RunwayWindRules.crosswindSeverity(16.0, maxCrosswindKt = 24))
        assertEquals(Assessment.RED, RunwayWindRules.crosswindSeverity(24.0, maxCrosswindKt = 24))
    }

    @Test
    fun `dialTransform and formatting helpers match cockpit wind display conventions`() {
        val dial = RunwayWindRules.dialTransform(
            headingDeg = 170.0,
            windDirDeg = 200.0,
            favoredEndIndex = 1,
        )
        assertEquals(80.0, dial.stripRotationDeg, 1e-6)
        assertEquals(290.0, dial.windRotationDeg!!, 1e-6)
        assertTrue(dial.showEndZeroAtTop)
        assertTrue(dial.showWindArrow)

        val endHw = EndComponents(headwindKt = 14.2, crosswindSignedKt = -6.8)
        val prefHw = RunwayWindRules.preferredComponent(endHw)
        assertEquals("HW", prefHw.label)
        assertEquals(14, prefHw.valueKt)
        assertFalse(prefHw.isTailwind)
        assertEquals("RWY 17 · HW 14kt · XW 7kt", RunwayWindRules.bestEndSummary("17", endHw))

        val wind = WindSample(source = "metar", directionDeg = 210.0, speedKt = 12.0, gustKt = 22.0)
        assertEquals("210° 12G22kt", RunwayWindRules.windSummaryLine(wind))
        assertEquals("12G22kt", RunwayWindRules.windBadgeText(12.0, 22.0, isVariable = false))
        assertEquals("CALM", RunwayWindRules.windBadgeText(0.0, null, isVariable = false))
    }
}
