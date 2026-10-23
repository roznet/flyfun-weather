package aero.flyfun.weather.rules

import aero.flyfun.weather.model.LivePositionFix
import aero.flyfun.weather.model.rules.LivePositionRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LivePositionRulesTest {

    // Two-leg L-shaped route:
    // Leg 0: (48.0, 2.0) -> (48.0, 4.0) (~80.2 nm east)
    // Leg 1: (48.0, 4.0) -> (50.0, 4.0) (~120.0 nm north)
    private val sampleRoute = listOf(
        48.0 to 2.0,
        48.0 to 4.0,
        50.0 to 4.0,
    )

    @Test
    fun `projectOntoRoute projects aircraft onto multi-leg route and evaluates corridor membership`() {
        assertNull(
            LivePositionRules.projectOntoRoute(
                lat = 48.0,
                lon = 2.0,
                altitudeFt = 5500.0,
                groundSpeedKt = 120.0,
                trackDeg = 90.0,
                waypoints = listOf(48.0 to 2.0),
            ),
        )

        // Midpoint of Leg 0: (48.0, 3.0)
        val midLeg0 = LivePositionRules.projectOntoRoute(
            lat = 48.0,
            lon = 3.0,
            altitudeFt = 5500.0,
            groundSpeedKt = 120.0,
            trackDeg = 90.0,
            waypoints = sampleRoute,
            maxCorridorNm = 25.0,
        )
        assertNotNull(midLeg0)
        assertEquals(0, midLeg0!!.nearestSegmentIndex)
        assertEquals(0.0, midLeg0.crossTrackDistanceNm, 0.2)
        assertTrue(midLeg0.isOnRoute)
        assertTrue(midLeg0.routeFraction in 0.15..0.25)

        // Midpoint of Leg 1: (49.0, 4.0)
        val midLeg1 = LivePositionRules.projectOntoRoute(
            lat = 49.0,
            lon = 4.0,
            altitudeFt = 7500.0,
            groundSpeedKt = 135.0,
            trackDeg = 0.0,
            waypoints = sampleRoute,
            maxCorridorNm = 25.0,
        )
        assertNotNull(midLeg1)
        assertEquals(1, midLeg1!!.nearestSegmentIndex)
        assertEquals(0.0, midLeg1.crossTrackDistanceNm, 0.2)
        assertTrue(midLeg1.isOnRoute)
        assertTrue(midLeg1.routeFraction in 0.65..0.75)

        // Off-route point 42 nm south of Leg 0: (47.3, 3.0) -> 0.7 deg lat = 42 nm > 25 nm
        val offRoute = LivePositionRules.projectOntoRoute(
            lat = 47.3,
            lon = 3.0,
            altitudeFt = 5500.0,
            groundSpeedKt = 120.0,
            trackDeg = 90.0,
            waypoints = sampleRoute,
            maxCorridorNm = 25.0,
        )
        assertNotNull(offRoute)
        assertFalse(offRoute!!.isOnRoute)
        assertEquals(42.0, offRoute.crossTrackDistanceNm, 1.5)

        // Before departure -> clamped to routeFraction 0.0
        val beforeStart = LivePositionRules.projectOntoRoute(
            lat = 48.0,
            lon = 1.5,
            altitudeFt = 1500.0,
            groundSpeedKt = 90.0,
            trackDeg = 90.0,
            waypoints = sampleRoute,
        )
        assertEquals(0.0, beforeStart!!.routeFraction, 1e-6)

        // Past destination -> clamped to routeFraction 1.0
        val pastEnd = LivePositionRules.projectOntoRoute(
            lat = 50.4,
            lon = 4.0,
            altitudeFt = 2500.0,
            groundSpeedKt = 100.0,
            trackDeg = 0.0,
            waypoints = sampleRoute,
        )
        assertEquals(1.0, pastEnd!!.routeFraction, 1e-6)
    }

    @Test
    fun `shouldSendPositionFix throttles fixes until interval distance or altitude threshold is met`() {
        val baseFix = LivePositionFix(
            lat = 48.0,
            lon = 2.0,
            altitudeFt = 5000.0,
            groundSpeedKt = 120.0,
            trackDeg = 90.0,
            recordedAt = "2026-04-12T10:00:00Z",
            hAccuracyM = 10.0,
        )

        // First fix when lastFix is null -> always send
        assertTrue(LivePositionRules.shouldSendPositionFix(lastFix = null, candidate = baseFix))

        // 60 seconds later, moved only ~1.2 nm (0.02 deg lat), altitude +200 ft -> throttled (false)
        val minorFix = baseFix.copy(
            lat = 48.02,
            altitudeFt = 5200.0,
            recordedAt = "2026-04-12T10:01:00Z",
        )
        assertFalse(LivePositionRules.shouldSendPositionFix(lastFix = baseFix, candidate = minorFix))

        // 180 seconds later (exact minIntervalSec) -> send (true)
        val intervalFix = baseFix.copy(
            lat = 48.01,
            recordedAt = "2026-04-12T10:03:00Z",
        )
        assertTrue(LivePositionRules.shouldSendPositionFix(lastFix = baseFix, candidate = intervalFix))

        // Only 30 seconds later, but moved 0.15 deg lat (~9.0 nm >= 8.0 nm) -> send (true)
        val distanceFix = baseFix.copy(
            lat = 48.15,
            recordedAt = "2026-04-12T10:00:30Z",
        )
        assertTrue(LivePositionRules.shouldSendPositionFix(lastFix = baseFix, candidate = distanceFix))

        // Only 30 seconds later, same lat/lon, but climbed +850 ft (>= 800 ft) -> send (true)
        val altitudeFix = baseFix.copy(
            altitudeFt = 5850.0,
            recordedAt = "2026-04-12T10:00:30Z",
        )
        assertTrue(LivePositionRules.shouldSendPositionFix(lastFix = baseFix, candidate = altitudeFix))
    }

    @Test
    fun `failureAction and trimFixBuffer classify status codes and cap ring buffer`() {
        assertEquals(LivePositionRules.FailureAction.DROP_BATCH, LivePositionRules.failureAction(401))
        assertEquals(LivePositionRules.FailureAction.DROP_BATCH, LivePositionRules.failureAction(403))
        assertEquals(LivePositionRules.FailureAction.DROP_BATCH, LivePositionRules.failureAction(404))
        assertEquals(LivePositionRules.FailureAction.DROP_BATCH, LivePositionRules.failureAction(422))
        assertEquals(LivePositionRules.FailureAction.RETAIN_BATCH, LivePositionRules.failureAction(500))
        assertEquals(LivePositionRules.FailureAction.RETAIN_BATCH, LivePositionRules.failureAction(503))
        assertEquals(LivePositionRules.FailureAction.RETAIN_BATCH, LivePositionRules.failureAction(null))

        val fixes = (1..10).map { i ->
            LivePositionFix(lat = 48.0 + i * 0.01, lon = 2.0, recordedAt = "2026-04-12T10:00:0${i % 10}Z", hAccuracyM = 10.0)
        }
        val trimmed = LivePositionRules.trimFixBuffer(fixes, maxBufferedFixes = 4)
        assertEquals(4, trimmed.size)
        assertEquals(48.07, trimmed.first().lat, 1e-6)
        assertEquals(48.10, trimmed.last().lat, 1e-6)
    }

    @Test
    fun `isLanding needs a valid slow speed within 5 nm of the destination`() {
        // Destination (50.0, 4.0); 0.05 deg lat ~ 3 nm, 0.1 deg ~ 6 nm.
        assertTrue(LivePositionRules.isLanding(50.05, 4.0, groundSpeedKt = 12.0, waypoints = sampleRoute))
        assertFalse(LivePositionRules.isLanding(50.05, 4.0, groundSpeedKt = 30.0, waypoints = sampleRoute))
        assertFalse(LivePositionRules.isLanding(50.10, 4.0, groundSpeedKt = 12.0, waypoints = sampleRoute))
        assertFalse(LivePositionRules.isLanding(50.05, 4.0, groundSpeedKt = null, waypoints = sampleRoute))
        // Slow at the departure is not the landing.
        assertFalse(LivePositionRules.isLanding(48.0, 2.0, groundSpeedKt = 5.0, waypoints = sampleRoute))
        assertFalse(LivePositionRules.isLanding(50.0, 4.0, groundSpeedKt = 5.0, waypoints = emptyList()))
    }
}
