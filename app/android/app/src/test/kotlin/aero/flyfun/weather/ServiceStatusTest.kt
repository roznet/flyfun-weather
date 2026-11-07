package aero.flyfun.weather

import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.data.ServiceStatus
import aero.flyfun.weather.data.ServiceStatusMonitor
import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The server's status page (scripts/ops/maintenance.py + Caddy handle_errors):
 * parsing its JSON and the message a gateway 5xx shows.
 */
class ServiceStatusTest {

    private fun body(s: String) = s.toByteArray()

    @Test
    fun `parses the maintenance body`() {
        val status = ServiceStatus.parse(
            503,
            body(
                """{"service_status":"maintenance",
                   "detail":"FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.",
                   "until":"2026-11-07T14:30:00Z","updated_at":"2026-11-07T13:00:00Z"}""",
            ),
        )
        assertEquals(ServiceStatus.Kind.MAINTENANCE, status?.kind)
        assertEquals(Instant.parse("2026-11-07T14:30:00Z"), status?.until)
        assertEquals(true, status?.message?.startsWith("FlyFun Weather is down"))
    }

    @Test
    fun `generic outage body with empty detail and bad until`() {
        val status = ServiceStatus.parse(
            502,
            body("""{"service_status":"unavailable","detail":"","until":"soon"}"""),
        )
        assertEquals(
            ServiceStatus(ServiceStatus.Kind.UNAVAILABLE, ServiceStatus.GENERIC_MESSAGE, null),
            status,
        )
    }

    @Test
    fun `app errors are left alone`() {
        assertNull(ServiceStatus.parse(503, body("""{"detail":"Pack not ready"}""")))
        assertNull(ServiceStatus.parse(500, body("<html>oops</html>")))
        assertNull(ServiceStatus.parse(409, body("""{"service_status":"maintenance"}""")))
        assertNull(ServiceStatus.parse(503, body("""{"service_status":"weird"}""")))
        assertNull(ServiceStatus.parse(503, ByteArray(0)))
    }

    @Test
    fun `gateway errors read as a sentence`() {
        assertEquals("Down for maintenance.", ApiError.ServerError(503, "Down for maintenance.").message)
        assertEquals(ServiceStatus.GENERIC_MESSAGE, ApiError.ServerError(502, null).message)
        assertEquals(
            ServiceStatus.GENERIC_MESSAGE,
            ApiError.ServerError(502, "<html><body>Bad Gateway</body></html>").message,
        )
        assertEquals("boom", ApiError.ServerError(500, "boom").message)
        assertEquals("Server error (500)", ApiError.ServerError(500, null).message)
    }

    @Test
    fun `monitor reports and clears`() {
        val status = ServiceStatus(ServiceStatus.Kind.MAINTENANCE, "x", null)
        ServiceStatusMonitor.report(status)
        assertEquals(status, ServiceStatusMonitor.current.value)
        ServiceStatusMonitor.clear()
        assertNull(ServiceStatusMonitor.current.value)
    }
}
