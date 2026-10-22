package aero.flyfun.weather.map

import aero.flyfun.weather.model.ForecastDaysResponse
import aero.flyfun.weather.model.ForecastLinkedDate
import aero.flyfun.weather.model.ForecastLinkedDate.Fallback
import aero.flyfun.weather.model.ForecastLinkedDate.Resolved
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import java.time.Instant
import java.time.LocalDate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** `fc.date` resolution (#860): same rules as web `resolveLinkedDate` and iOS. */
class ForecastLinkedDateTest {

    private val today = LocalDate.of(2026, 10, 22)

    @Test
    fun `parse accepts only a strict calendar date`() {
        assertEquals(LocalDate.of(2026, 10, 25), ForecastLinkedDate.parse("2026-10-25"))
        assertEquals(LocalDate.of(2028, 2, 29), ForecastLinkedDate.parse("2028-02-29"))
        for (bad in listOf("2026-02-30", "2026-10-2", "+026-10-21", "tomorrow", "", "2026-13-01", "２０２６-10-21", null)) {
            assertNull(bad, ForecastLinkedDate.parse(bad))
        }
    }

    @Test
    fun `resolve in range past beyond and not fetched`() {
        val offered = listOf(0, 1, 2, 3, 5)
        fun r(date: String) = ForecastLinkedDate.resolve(date, offered, today)

        assertEquals(Resolved(3, null), r("2026-10-25"))
        assertEquals(Resolved(0, null), r("2026-10-22"))
        assertEquals(Resolved(0, Fallback.PAST), r("2026-10-21"))
        assertEquals(Resolved(5, Fallback.BEYOND), r("2026-10-30"))
        assertEquals(Resolved(3, Fallback.UNAVAILABLE), r("2026-10-26")) // D+4: tie 3/5 goes earlier
        assertNull(r("2026-02-30"))
        assertNull(ForecastLinkedDate.resolve("2026-10-25", emptyList(), today))
    }

    @Test
    fun `resolve does not assume the grid starts at today`() {
        // Late in the UTC day the server may no longer offer D+0
        assertEquals(Resolved(1, Fallback.PAST), ForecastLinkedDate.resolve("2026-10-22", listOf(3, 1, 2), today))
    }

    @Test
    fun `notice wording and the fallback grid rule`() {
        assertEquals(
            "This link was for Wed 21 Oct, which has passed: showing Thu 22 Oct instead.",
            ForecastLinkedDate.noticeFor("2026-10-21", Resolved(0, Fallback.PAST), gridIsFallback = false, today = today),
        )
        assertEquals(
            "This link was for Fri 30 Oct, beyond the forecast range: showing Tue 27 Oct, the last forecast day.",
            ForecastLinkedDate.noticeFor("2026-10-30", Resolved(5, Fallback.BEYOND), gridIsFallback = false, today = today),
        )
        assertEquals(
            "This link was for Mon 26 Oct, not forecast yet: showing Sun 25 Oct instead.",
            ForecastLinkedDate.noticeFor("2026-10-26", Resolved(3, Fallback.UNAVAILABLE), gridIsFallback = false, today = today),
        )
        assertNull(ForecastLinkedDate.noticeFor("2026-10-25", Resolved(3, null), gridIsFallback = false, today = today))

        // Against the guessed D+0…D+3 only "has passed" is a true claim
        assertNotNull(ForecastLinkedDate.noticeFor("2026-10-21", Resolved(0, Fallback.PAST), gridIsFallback = true, today = today))
        assertNull(ForecastLinkedDate.noticeFor("2026-10-30", Resolved(3, Fallback.BEYOND), gridIsFallback = true, today = today))
        assertNull(ForecastLinkedDate.noticeFor("2026-10-26", Resolved(3, Fallback.UNAVAILABLE), gridIsFallback = true, today = today))
    }

    @Test
    fun `forecast days decode the server shape`() {
        // `compute_day_availability`: hours are plain ints; a day past the horizon is unavailable
        val json = """
            {"days": [
              {"day": 0, "date": "2026-10-22", "available": true, "hours": [12, 15, 18], "models": ["ecmwf", "gfs", "icon"]},
              {"day": 7, "date": "2026-10-29", "available": false, "hours": [], "models": []}
            ], "max_day": 7}
        """.trimIndent()
        val resp = WbJson.decodeFromString(ForecastDaysResponse.serializer(), json)
        assertEquals(7, resp.maxDay)
        assertEquals(listOf(12, 15, 18), resp.days[0].hours)
        assertTrue(resp.days[0].available)
        assertFalse(resp.days[1].available)
    }

    private fun mockViewModel(now: String) = ForecastMapViewModel(
        isMockModeProvider = { true },
        scopeOverride = CoroutineScope(Dispatchers.Unconfined),
        clock = { Instant.parse(now) },
    )

    @Test
    fun `view model - fc date wins over fc day and picking a day clears the notice`() {
        // Resolution only uses the mock grid's day offsets (D+0…D+3), so a pinned clock is enough
        val todayUtc = today
        val vm = mockViewModel("2026-10-22T10:00:00Z")

        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(dayOffset = 3, date = todayUtc.plusDays(1).toString(), hourUtc = 15))
        assertEquals(1, vm.selectedDay.value)
        assertEquals(15, vm.selectedHour.value)
        assertNull(vm.linkedDateNotice.value)

        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(dayOffset = 1, date = todayUtc.minusDays(3).toString()))
        assertEquals(vm.days.value.minOf { it.day }, vm.selectedDay.value)
        assertTrue(vm.linkedDateNotice.value!!.contains("has passed"))

        vm.selectDay(2)
        assertNull(vm.linkedDateNotice.value)

        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(date = todayUtc.plusDays(10).toString()))
        assertEquals(vm.days.value.maxOf { it.day }, vm.selectedDay.value)
        assertTrue(vm.linkedDateNotice.value!!.contains("beyond the forecast range"))

        // A malformed date (e.g. a link built by hand) falls back to fc.day, with no notice
        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(dayOffset = 2, date = "2026-02-30"))
        assertEquals(2, vm.selectedDay.value)
        assertNull(vm.linkedDateNotice.value)
    }

    @Test
    fun `view model - hour chips follow the selected day and snap the hour`() {
        val vm = mockViewModel("2026-10-22T10:00:00Z")
        // Off-grid linked hour snaps to the nearest sample hour of the day
        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(date = today.plusDays(2).toString(), hourUtc = 13))
        assertEquals(2, vm.selectedDay.value)
        assertEquals(vm.days.value.first { it.day == 2 }.hours, vm.availableHours.value)
        assertEquals(12, vm.selectedHour.value)
    }
}
