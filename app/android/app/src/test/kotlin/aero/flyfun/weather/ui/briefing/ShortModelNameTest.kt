package aero.flyfun.weather.ui.briefing

import org.junit.Assert.assertEquals
import org.junit.Test

class ShortModelNameTest {
    @Test
    fun slotsAreNamedAfterTheirModel() {
        assertEquals("AROME", "meteofrance".shortModelName())
        assertEquals("UKV", "ukmo".shortModelName())
        assertEquals("ECMWF", "ecmwf".shortModelName())
        assertEquals("GFS", "gfs".shortModelName())
        assertEquals("ICON", "icon_eu".shortModelName())
        assertEquals("GEM", "gem".shortModelName()) // default: upper-cased key
    }
}
