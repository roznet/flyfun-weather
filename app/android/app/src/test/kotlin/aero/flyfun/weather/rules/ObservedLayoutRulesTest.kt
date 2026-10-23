package aero.flyfun.weather.rules

import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveGlanceLine
import aero.flyfun.weather.model.LiveHighlight
import aero.flyfun.weather.model.rules.ObservedLayoutRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ObservedLayoutRulesTest {

    private val lines = listOf(
        LiveGlanceLine(phase = "departure", text = "EGTF VFR", passed = true),
        LiveGlanceLine(phase = "enroute", text = "SIGMET TS ahead", alert = true),
        LiveGlanceLine(phase = "enroute", text = "no cells within 30 NM"),
        LiveGlanceLine(phase = "arrival", text = "LFAT MVFR"),
    )

    @Test
    fun `alert lines lead, the rest split into ahead and passed in server order`() {
        val layout = ObservedLayoutRules.layout(LiveGlance(headline = "Observed 09:40Z · as briefed", lines = lines))
        assertEquals(listOf("SIGMET TS ahead"), layout.alertLines.map { it.text })
        assertEquals(listOf("no cells within 30 NM", "LFAT MVFR"), layout.aheadLines.map { it.text })
        assertEquals(listOf("EGTF VFR"), layout.passedLines.map { it.text })
    }

    @Test
    fun `the headline leads without a highlight and moves into Details with one`() {
        val plain = ObservedLayoutRules.layout(LiveGlance(headline = "Observed 09:40Z · as briefed", lines = lines))
        assertFalse(plain.leadIsHighlight)
        assertNull(plain.detailsHeadline)

        val withHighlight = ObservedLayoutRules.layout(
            LiveGlance(headline = "Observed 09:40Z · as briefed", lines = lines, highlight = LiveHighlight(text = "Storm 9 NM NE of LFAT")),
        )
        assertTrue(withHighlight.leadIsHighlight)
        assertEquals("Observed 09:40Z · as briefed", withHighlight.detailsHeadline)
    }

    @Test
    fun `phase labels`() {
        assertEquals("Departure", ObservedLayoutRules.phaseLabel("departure"))
        assertEquals("En route", ObservedLayoutRules.phaseLabel("enroute"))
        assertEquals("Arrival", ObservedLayoutRules.phaseLabel("arrival"))
        assertEquals("", ObservedLayoutRules.phaseLabel(null))
    }
}
