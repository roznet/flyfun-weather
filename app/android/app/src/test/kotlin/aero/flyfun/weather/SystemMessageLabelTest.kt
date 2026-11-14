package aero.flyfun.weather

import aero.flyfun.weather.model.SystemMessageResponse
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * What's New chip labels: the same words as the web (`messages.category.*`) and
 * iOS `SystemMessage.categoryLabel`, never the raw category (#985).
 */
class SystemMessageLabelTest {

    private fun label(category: String) = SystemMessageResponse(category = category).categoryLabel

    @Test
    fun `known categories carry the web's chip labels`() {
        assertEquals("New", label("feature"))
        assertEquals("Change", label("change"))
        assertEquals("Fix", label("fix"))
        assertEquals("iOS app", label("app_release"))
        assertEquals("Android app", label("android_release"))
    }

    @Test
    fun `an unknown category falls back to its raw value, capitalised`() {
        assertEquals("Deprecation", label("deprecation"))
    }
}
