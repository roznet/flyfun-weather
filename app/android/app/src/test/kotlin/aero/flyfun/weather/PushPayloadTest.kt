package aero.flyfun.weather

import aero.flyfun.weather.service.PushNotificationManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The FCM data the server sends (`notify/push.py::_apns_payload_to_fcm_message`). */
class PushPayloadTest {

    @Test
    fun `briefing refresh opens the briefing on the briefing channel`() {
        val payload = PushNotificationManager.parsePayload(
            mapOf(
                "flight_id" to "egtf_lfat",
                "timestamp" to "2026-10-27T18:00:00+00:00",
                "badge" to "2",
                "title" to "EGTF → LFAT",
                "body" to "Briefing updated",
            ),
        )!!
        assertEquals("egtf_lfat", payload.flightId)
        assertFalse(payload.openObserved)
        assertEquals(PushNotificationManager.CHANNEL_BRIEFING_ALERTS, payload.channelId)
        assertEquals("EGTF → LFAT", payload.title)
        assertEquals("Briefing updated", payload.body)
    }

    @Test
    fun `live alert opens the Observed tab on the live channel`() {
        val payload = PushNotificationManager.parsePayload(
            mapOf("flight_id" to "f1", "type" to "live_alert", "interruption_level" to "time-sensitive"),
        )!!
        assertTrue(payload.openObserved)
        assertEquals(PushNotificationManager.CHANNEL_LIVE_WEATHER_ALERTS, payload.channelId)
    }

    @Test
    fun `active live alert and live clear use the clear channel, as the server does`() {
        val active = PushNotificationManager.parsePayload(
            mapOf("flight_id" to "f1", "type" to "live_alert", "interruption_level" to "active"),
        )!!
        assertEquals(PushNotificationManager.CHANNEL_LIVE_CLEAR, active.channelId)
        val clear = PushNotificationManager.parsePayload(mapOf("flight_id" to "f1", "type" to "live_clear"))!!
        assertEquals(PushNotificationManager.CHANNEL_LIVE_CLEAR, clear.channelId)
        assertTrue(clear.openObserved)
    }

    @Test
    fun `flight-day brief uses the flight-day channel`() {
        val payload = PushNotificationManager.parsePayload(mapOf("flight_id" to "f1", "type" to "flight_day"))!!
        assertEquals(PushNotificationManager.CHANNEL_FLIGHT_DAY, payload.channelId)
        assertFalse(payload.openObserved)
    }

    @Test
    fun `local notification tap extras keep open_observed`() {
        val payload = PushNotificationManager.parsePayload(
            mapOf("flight_id" to "f1", "pack_timestamp" to "t", "open_observed" to "true"),
        )!!
        assertTrue(payload.openObserved)
        assertEquals("t", payload.packTimestamp)
    }

    @Test
    fun `no flight id is not a flight notification`() {
        assertNull(PushNotificationManager.parsePayload(mapOf("trip_id" to "t1")))
    }
}
