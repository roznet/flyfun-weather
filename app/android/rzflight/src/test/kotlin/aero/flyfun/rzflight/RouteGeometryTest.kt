package aero.flyfun.rzflight

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RouteGeometryTest {

    @Test
    fun directDistanceNmZeroForIdenticalCoordinates() {
        val point = Coord(51.348, -0.559)
        assertEquals(0.0, RouteGeometry.directDistanceNm(point, point), 1e-9)
    }

    @Test
    fun directDistanceNmOneDegreeLatitudeIsSixtyNm() {
        val start = Coord(50.0, 0.0)
        val end = Coord(51.0, 0.0)
        val distance = RouteGeometry.directDistanceNm(start, end)
        assertEquals(60.04, distance, 0.05)
    }

    @Test
    fun directDistanceNmFairoaksToCannes() {
        val egtf = Coord(51.348, -0.559)
        val lfmd = Coord(43.542, 6.953)
        val distance = RouteGeometry.directDistanceNm(egtf, lfmd)
        assertTrue("Expected ~559 NM, got $distance", distance in 555.0..563.0)
    }

    @Test
    fun perpendicularDistanceAndRatioOnSegmentMidpoint() {
        val start = Coord(50.0, 0.0)
        val end = Coord(50.0, 2.0)
        val mid = Coord(50.0, 1.0)

        val (crossTrackNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
            from = mid,
            toSegmentStart = start,
            segmentEnd = end,
        )
        assertEquals(0.0, crossTrackNm, 0.01)
        assertEquals(0.5, ratio, 0.001)
    }

    @Test
    fun perpendicularDistanceAndRatioBeforeSegmentStartClampsToZero() {
        val start = Coord(50.0, 0.0)
        val end = Coord(50.0, 2.0)
        val before = Coord(50.0, -1.0)

        val (crossTrackNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
            from = before,
            toSegmentStart = start,
            segmentEnd = end,
        )
        assertEquals(0.0, ratio, 1e-6)
        assertEquals(RouteGeometry.directDistanceNm(before, start), crossTrackNm, 0.01)
    }

    @Test
    fun perpendicularDistanceAndRatioAfterSegmentEndClampsToOne() {
        val start = Coord(50.0, 0.0)
        val end = Coord(50.0, 2.0)
        val after = Coord(50.0, 3.0)

        val (crossTrackNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
            from = after,
            toSegmentStart = start,
            segmentEnd = end,
        )
        assertEquals(1.0, ratio, 1e-6)
        assertEquals(RouteGeometry.directDistanceNm(after, end), crossTrackNm, 0.01)
    }

    @Test
    fun perpendicularDistanceAndRatioOffTrackFromMidpoint() {
        val start = Coord(50.0, 0.0)
        val end = Coord(50.0, 2.0)
        // 0.5 deg north of the midpoint (~30 NM cross-track)
        val offTrack = Coord(50.5, 1.0)

        val (crossTrackNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
            from = offTrack,
            toSegmentStart = start,
            segmentEnd = end,
        )
        assertEquals(0.5, ratio, 0.01)
        assertEquals(30.02, crossTrackNm, 0.1)
    }

    @Test
    fun perpendicularDistanceAndRatioHandlesZeroLengthSegment() {
        val degen = Coord(51.0, 1.0)
        val aircraft = Coord(51.5, 1.0)

        val (crossTrackNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
            from = aircraft,
            toSegmentStart = degen,
            segmentEnd = degen,
        )
        assertEquals(0.0, ratio, 1e-9)
        assertEquals(RouteGeometry.directDistanceNm(aircraft, degen), crossTrackNm, 0.01)
    }

    @Test
    fun initialBearingDegCardinalDirections() {
        val origin = Coord(50.0, 0.0)
        assertEquals(0.0, RouteGeometry.initialBearingDeg(origin, Coord(51.0, 0.0)), 0.1)
        assertEquals(90.0, RouteGeometry.initialBearingDeg(origin, Coord(50.0, 1.0)), 1.0)
        assertEquals(180.0, RouteGeometry.initialBearingDeg(origin, Coord(49.0, 0.0)), 0.1)
        assertEquals(270.0, RouteGeometry.initialBearingDeg(origin, Coord(50.0, -1.0)), 1.0)
        assertEquals(0.0, RouteGeometry.initialBearingDeg(origin, origin), 1e-9)
    }
}
