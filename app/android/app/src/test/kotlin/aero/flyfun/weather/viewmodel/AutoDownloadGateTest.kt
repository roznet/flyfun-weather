package aero.flyfun.weather.viewmodel

import java.time.LocalDate
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Date half of the pack auto-download gate: past flights are never auto-downloaded. */
class AutoDownloadGateTest {
    private val today = LocalDate.of(2026, 11, 26)

    @Test fun todayAndLaterQualify() {
        assertTrue(BriefingViewModel.isTodayOrFuture("2026-11-26", today))
        assertTrue(BriefingViewModel.isTodayOrFuture("2026-12-01", today))
    }

    @Test fun pastFlightIsSkipped() {
        assertFalse(BriefingViewModel.isTodayOrFuture("2026-11-25", today))
    }

    @Test fun missingOrOddDateStillDownloads() {
        assertTrue(BriefingViewModel.isTodayOrFuture(null, today))
        assertTrue(BriefingViewModel.isTodayOrFuture("", today))
        assertTrue(BriefingViewModel.isTodayOrFuture("soon", today))
    }
}
