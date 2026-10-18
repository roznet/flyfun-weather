package aero.flyfun.weather

import aero.flyfun.weather.model.FlightDuration
import org.junit.Assert.assertEquals
import org.junit.Test

class FlightDurationTest {

    @Test
    fun `split exact hours and quarter hours without epsilon drift`() {
        assertEquals(FlightDuration.Parts(0, 0), FlightDuration.split(0.0))
        assertEquals(FlightDuration.Parts(2, 0), FlightDuration.split(2.0))
        assertEquals(FlightDuration.Parts(1, 15), FlightDuration.split(1.25))
        assertEquals(FlightDuration.Parts(1, 30), FlightDuration.split(1.5))
        assertEquals(FlightDuration.Parts(1, 45), FlightDuration.split(1.75))
    }

    @Test
    fun `split rounds up non-quarter values and carries 60 minutes into next hour`() {
        assertEquals(FlightDuration.Parts(2, 15), FlightDuration.split(2.01))
        assertEquals(FlightDuration.Parts(2, 30), FlightDuration.split(2.26))
        assertEquals(FlightDuration.Parts(2, 45), FlightDuration.split(2.51))
        assertEquals(FlightDuration.Parts(2, 0), FlightDuration.split(1.9))
        assertEquals(FlightDuration.Parts(3, 0), FlightDuration.split(2.76))
    }

    @Test
    fun `split clamps negative values to zero`() {
        assertEquals(FlightDuration.Parts(0, 0), FlightDuration.split(-1.5))
    }

    @Test
    fun `combine recombines hours and minutes into decimal hours`() {
        assertEquals(0.0, FlightDuration.combine(0, 0), 1e-9)
        assertEquals(2.0, FlightDuration.combine(2, 0), 1e-9)
        assertEquals(2.25, FlightDuration.combine(2, 15), 1e-9)
        assertEquals(2.5, FlightDuration.combine(2, 30), 1e-9)
        assertEquals(2.75, FlightDuration.combine(2, 45), 1e-9)
    }

    @Test
    fun `clampToPickerRange bounds between 0 and 12_75`() {
        assertEquals(12.75, FlightDuration.maxDecimalHours, 1e-9)
        assertEquals(0.0, FlightDuration.clampToPickerRange(-2.0), 1e-9)
        assertEquals(3.5, FlightDuration.clampToPickerRange(3.5), 1e-9)
        assertEquals(12.75, FlightDuration.clampToPickerRange(15.0), 1e-9)
    }

    @Test
    fun `format and label produce human readable strings`() {
        assertEquals("0h", FlightDuration.format(0.0))
        assertEquals("2h", FlightDuration.format(2.0))
        assertEquals("2h 30m", FlightDuration.format(2.5))
        assertEquals("1h 15m", FlightDuration.label(1.25))
        assertEquals("2h", FlightDuration.label(1.9))
        assertEquals("2h30", FlightDuration.formatCompact(2.5))
        assertEquals("1h05", "%dh%02d".format(1, 5))
    }
}
