package aero.flyfun.weather.rules

import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.LiveGlanceTerminal
import aero.flyfun.weather.model.LiveLayerResponse
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.AirportCardRules
import aero.flyfun.weather.model.rules.AirportCardRules.Role
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

class AirportCardRulesTest {

    private val now = IsoTime.parseEpochMs("2026-10-23T09:40:00Z")!!

    private fun dep(folded: Boolean = false) = LiveGlanceTerminal(icao = "EGTF", folded = folded, foldedText = "EGTF 290/15G25 · RWY 24 · departed ~09:52Z")
    private fun arr() = LiveGlanceTerminal(icao = "LFAT")

    @Test
    fun `departure leads until the server folds it, then the arrival comes first`() {
        assertEquals(listOf(Role.DEPARTURE, Role.ARRIVAL), AirportCardRules.slots(dep(), arr()).map { it.role })
        val folded = AirportCardRules.slots(dep(folded = true), arr())
        assertEquals(listOf(Role.ARRIVAL, Role.DEPARTURE), folded.map { it.role })
        assertEquals(listOf(false, true), folded.map { it.folded })
        assertEquals(listOf(Role.ARRIVAL), AirportCardRules.slots(null, arr()).map { it.role })
        assertTrue(AirportCardRules.slots(null, null).isEmpty())
    }

    @Test
    fun `header boxes are METAR now, TAF at ETD or ETA and a dashed TEMPO box only when the server sets one`() {
        val a = AirportObservation(
            icao = "EGTF",
            metarFlightCategory = "MVFR",
            tafPrevailingCategoryAtEta = "VFR",
            tafTemporaryCategoryAtEta = "IFR",
            tafTemporaryType = "PROB30 TEMPO",
        )
        val boxes = AirportCardRules.headerBoxes(LiveGlanceTerminal(icao = "EGTF", airport = a), Role.DEPARTURE)
        assertEquals(listOf("MVFR", "VFR", "IFR"), boxes.map { it.category })
        assertEquals(listOf("now", "at ETD", "PROB30"), boxes.map { it.caption })
        assertEquals(listOf(false, false, true), boxes.map { it.dashed })

        // No airport row: the block's METAR category alone.
        val bare = AirportCardRules.headerBoxes(LiveGlanceTerminal(icao = "LFAT", metarCategory = "IFR"), Role.ARRIVAL)
        assertEquals(listOf("IFR"), bare.map { it.category })
    }

    @Test
    fun `meter position is the server's value clamped, null draws no dot`() {
        assertEquals(0.42f, AirportCardRules.meterPosition(0.42)!!, 1e-6f)
        assertEquals(1f, AirportCardRules.meterPosition(1.7)!!, 1e-6f)
        assertEquals(0f, AirportCardRules.meterPosition(-0.2)!!, 1e-6f)
        assertNull(AirportCardRules.meterPosition(null))
        assertNull(AirportCardRules.meterPosition(Double.NaN))
    }

    @Test
    fun `crosswind glyph follows the server side and the tone is amber or red only`() {
        assertEquals("◀ ", AirportCardRules.crosswindGlyph("left"))
        assertEquals("▶ ", AirportCardRules.crosswindGlyph("right"))
        assertEquals("", AirportCardRules.crosswindGlyph(""))
        assertNull(AirportCardRules.crosswindTone(AirportObservation(metarWindAdvisory = "green")))
        assertEquals("amber", AirportCardRules.crosswindTone(AirportObservation(metarWindAdvisory = "amber")))
        assertEquals("red", AirportCardRules.crosswindTone(AirportObservation(metarWindAdvisory = "RED")))
        assertNull(AirportCardRules.crosswindTone(AirportObservation()))
    }

    @Test
    fun `trend reads the server's trend and previous report`() {
        val worse = AirportObservation(
            metarTrend = "worse",
            metarPreviousFlightCategory = "VFR",
            metarPreviousTime = "2026-10-23T09:00:00Z",
        )
        assertEquals("↘ was VFR 09:00Z", AirportCardRules.trendText(worse))
        assertEquals("↗ was MVFR", AirportCardRules.trendText(AirportObservation(metarTrend = "better", metarPreviousFlightCategory = "MVFR")))
        assertNull(AirportCardRules.trendText(AirportObservation(metarPreviousFlightCategory = "VFR")))
    }

    @Test
    fun `times, ages and the stale mark`() {
        assertEquals("09:30Z", AirportCardRules.hhmmZ("2026-10-23T09:30:00Z"))
        assertEquals("10Z", AirportCardRules.hourLabel("2026-10-23T10:00:00Z"))
        assertEquals("", AirportCardRules.hhmmZ(null))
        assertEquals(10L, AirportCardRules.minutesAgo("2026-10-23T09:30:00Z", now))
        assertFalse(AirportCardRules.isStale(30))
        assertTrue(AirportCardRules.isStale(31))
        assertEquals("8 min", AirportCardRules.ageText(8))
        assertEquals("1 h 05", AirportCardRules.ageText(65))
        assertEquals("ETD 10:00Z", AirportCardRules.timeText(LiveGlanceTerminal(etd = "2026-10-23T10:00:00Z"), Role.DEPARTURE))
        assertEquals("ETA 11:35Z", AirportCardRules.timeText(LiveGlanceTerminal(eta = "2026-10-23T11:35:00Z"), Role.ARRIVAL))
        assertNull(AirportCardRules.timeText(LiveGlanceTerminal(), Role.ARRIVAL))
    }

    @Test
    fun `offline banner uses live_updated_at and stays away when online`() {
        assertNull(AirportCardRules.offlineBannerText(false, "2026-10-23T09:28:00Z", now))
        assertEquals(
            "Offline · showing data from 09:28Z, 12 min ago",
            AirportCardRules.offlineBannerText(true, "2026-10-23T09:28:00Z", now),
        )
        assertEquals("Offline · no live data yet", AirportCardRules.offlineBannerText(true, null, now))
    }

    @Test
    fun `glance departure and destination decode with the airport card fields`() {
        val json = """
            {"flight_id": "f1", "live_updated_at": "2026-10-23T09:38:00Z",
             "glance": {"as_of": "2026-10-23T09:38:00Z", "headline": "Observed", "comparison": "as_briefed",
              "flight": {"phase": "enroute", "text": "40 of 120 NM · ETA LFAT 10:32Z"},
              "departure": {"icao": "EGTF", "etd": "2026-10-23T09:50:00Z", "metar_text": "EGTF MVFR",
                "taf_text": "TAF at ETD VFR", "cells_text": "no cells within 30 NM", "folded": true,
                "folded_source": "takeoff", "folded_text": "EGTF 290/15G25 · RWY 24 · departed 09:52Z",
                "lightning_alert": true,
                "airport": {"icao": "EGTF", "metar_wind_short": "290/15G25", "metar_cavok": false,
                  "metar_ceiling_meter": 0.61, "metar_sky_text": "BKN 2,500 ft",
                  "metar_weather_words": [{"code": "-RA", "text": "Light rain", "icon": "rain"}],
                  "metar_crosswind": {"runway": "24", "side": "right", "kt": 10, "gust_kt": 16, "text": "10G16 kt"},
                  "taf_hours": [{"time": "2026-10-23T10:00:00Z", "prevailing_category": "VFR",
                    "temporary_category": "MVFR", "temporary_type": "TEMPO", "is_eta": true}],
                  "runway_wind": {"icao": "EGTF", "runways": [{"id": "06/24", "ends": [
                    {"ident": "06", "heading_true": 58.0}, {"ident": "24", "heading_true": 238.0}]}],
                    "winds": [{"wind": {"source": "metar", "direction_true": 290, "speed_kt": 15, "gust_kt": 25},
                      "best_end": "24", "advisory": "amber"}]}}},
              "destination": {"icao": "LFAT", "eta": "2026-10-23T10:32:00Z", "metar_text": "LFAT VFR",
                "taf_text": "no TAF for ETA", "cells_text": "nearest cell 9 NM NE",
                "cells": [{"id": "c1", "text": "9 NM NE · 45 dBZ"}], "cells_alert": true}}}
        """.trimIndent()
        val layer = WbJson.decodeFromString(LiveLayerResponse.serializer(), json)
        val glance = layer.glance!!
        assertEquals("40 of 120 NM · ETA LFAT 10:32Z", glance.flight?.text)
        val d = glance.departure!!
        assertTrue(d.folded)
        assertTrue(d.lightningAlert)
        assertFalse(d.cellsAlert)
        val a = d.airport!!
        assertEquals("10G16 kt", a.metarCrosswind?.text)
        assertEquals("rain", a.metarWeatherWords.single().icon)
        assertTrue(a.tafHours.single().isEta)
        assertEquals("24", a.runwayWind?.wind("metar")?.bestEnd)
        assertEquals(238.0, a.runwayWind!!.runways.single().ends[1].headingTrue, 1e-9)
        val arr = glance.destination!!
        assertTrue(arr.cellsAlert)
        assertEquals("c1", arr.cells.single().id)
        assertNull(arr.airport)
        assertEquals("no TAF for ETA", AirportCardRules.tafReason(arr))
    }

    /** The shared fixture every client checks (web/tests/fixtures/airport_cards.json, from PR 887). */
    @Test
    fun `shared airport card fixture decodes`() {
        val file = listOf("../../../web/tests/fixtures/airport_cards.json", "../../web/tests/fixtures/airport_cards.json")
            .map { File(it) }
            .firstOrNull { it.exists() }
        assumeTrue("shared fixture not on this branch yet", file != null)
        val root = WbJson.parseToJsonElement(file!!.readText()).jsonObject
        listOf("before", "after").forEach { key ->
            val pair = root[key] as JsonObject
            listOf("departure", "destination").forEach { role ->
                val block = WbJson.decodeFromJsonElement(LiveGlanceTerminal.serializer(), pair[role]!!)
                assertTrue(block.icao.isNotBlank())
                val a = block.airport!!
                assertTrue("taf_hours for $key/$role", a.tafHours.isNotEmpty())
            }
        }
        val after = WbJson.decodeFromJsonElement(LiveGlanceTerminal.serializer(), (root["after"] as JsonObject)["departure"]!!)
        assertTrue(after.folded)
        assertNotNull(after.foldedText)
    }
}
