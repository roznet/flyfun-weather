package aero.flyfun.weather.rules

import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.GrametStatusRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/** #994: the server says why a pack has no GRAMET; the app words it. */
class GrametStatusRulesTest {

    @Test
    fun wordsTheReasonsAPilotCanActOn() {
        for (code in listOf("refresh_to_fetch", "daily_limit", "failed", "expired")) {
            assertNotNull(code, GrametStatusRules.message(code))
        }
    }

    @Test
    fun staysHiddenForThePilotsOwnSetupAndUnknownCodes() {
        for (code in listOf(null, "not_linked", "disabled", "historical", "something_new")) {
            assertNull(code, GrametStatusRules.message(code))
        }
    }

    @Test
    fun decodesGrametStatusFromPackMeta() {
        val pack = WbJson.decodeFromString(
            PackMetaResponse.serializer(),
            """{"flight_id":"f","fetch_timestamp":"2026-11-15T10:00:00+00:00","has_gramet":false,"gramet_status":"refresh_to_fetch"}""",
        )
        assertEquals("refresh_to_fetch", pack.grametStatus)
        val legacy = WbJson.decodeFromString(
            PackMetaResponse.serializer(),
            """{"flight_id":"f","fetch_timestamp":"2026-11-15T10:00:00+00:00","has_gramet":true}""",
        )
        assertNull(legacy.grametStatus)
    }
}
