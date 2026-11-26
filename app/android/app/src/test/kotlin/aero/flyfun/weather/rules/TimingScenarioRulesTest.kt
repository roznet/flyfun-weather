package aero.flyfun.weather.rules

import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.TimeConfidence
import aero.flyfun.weather.model.TimeOptionsResponse
import aero.flyfun.weather.model.TimeScanJobStatus
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.TimingScenarioRules
import aero.flyfun.weather.model.rules.TimingScenarioRules.PanelState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The timing-scenario poll payload and its wording (#893), checked against the server's shape. */
class TimingScenarioRulesTest {

    // Shape of `GET /api/flights/{id}/packs/{ts}/time-options` (server `time_scan.py`),
    // including keys the client ignores (coverage, models, disposition, advisory_status).
    private val doneJson = """
        {
          "status": {"status": "done", "flexibility": "same_day", "reason": "", "updated_at": "2099-06-30T06:05:00Z"},
          "scan": {
            "schema_version": 1,
            "flexibility": "same_day",
            "baseline": {"departure_time": "2099-06-30T09:00:00Z", "assessment": "RED",
                         "assessment_reason": "icing=RED, convective=AMBER", "models_used": ["ecmwf"]},
            "window": {"start": "2099-06-30T06:00:00Z", "end": "2099-06-30T18:00:00Z", "flexibility": "same_day",
                       "daylight_clipped": false, "horizon_clipped": true, "past_clipped": false},
            "candidates": [
              {"departure_time": "2099-06-30T09:00:00Z", "departure_shift_hours": 0, "assessment": "RED",
               "assessment_reason": "icing=RED, convective=AMBER", "models_used": ["ecmwf"],
               "confidence": "ecmwf_only", "is_baseline": true, "disposition": "neutral", "advisory_status": {}},
              {"departure_time": "2099-06-30T13:30:00Z", "departure_shift_hours": 4.5, "assessment": "AMBER",
               "assessment_reason": "convective=AMBER", "models_used": ["ecmwf"], "improves": ["icing"],
               "valid_times": ["2099-06-30T13:30:00Z", "2099-06-30T15:00:00Z"],
               "confidence": "ecmwf_only", "confirm_pending": true},
              {"departure_time": "2099-07-01T08:00:00Z", "departure_shift_hours": 23, "assessment": "AMBER",
               "assessment_reason": "icing=AMBER", "models_used": ["ecmwf", "gfs", "icon"],
               "confidence": "confirmed",
               "confirmed": {"models_checked": ["ecmwf", "gfs", "icon"], "assessment": "GREEN",
                             "assessment_reason": "", "better_than_baseline": true, "improves": ["icing", "convective"],
                             "confirmed_at": "2099-06-30T06:10:00Z"}}
            ],
            "refused_times": ["2099-06-30T17:00:00Z"],
            "coverage": [{"model": "gfs", "uniform": true}],
            "models": ["ecmwf"],
            "generated_at": "2099-06-30T06:05:00Z"
          }
        }
    """.trimIndent()

    private val names = mapOf("icing" to "Icing", "convective" to "Convection")

    @Test
    fun `decodes the server poll shape`() {
        val resp = WbJson.decodeFromString(TimeOptionsResponse.serializer(), doneJson)
        assertEquals(TimeScanJobStatus.DONE, resp.status?.status)
        assertEquals(FlightFlexibility.SAME_DAY, resp.status?.flexibility)
        val scan = resp.scan!!
        assertEquals(3, scan.candidates.size)
        assertTrue(scan.candidates[0].isBaseline)
        assertEquals(TimeConfidence.CONFIRMED, scan.candidates[2].confidence)
        assertEquals(Assessment.GREEN, scan.candidates[2].effectiveAssessment)
        assertTrue(scan.window!!.horizonClipped)
        // A confirm in flight keeps the poll going even though the scan is done.
        assertTrue(resp.anyConfirmPending)
        assertTrue(resp.isNonTerminal)
    }

    @Test
    fun `unknown status is terminal and an empty response keeps polling`() {
        val drift = WbJson.decodeFromString(
            TimeOptionsResponse.serializer(),
            """{"status": {"status": "exploded"}, "scan": null}""",
        )
        assertEquals(TimeScanJobStatus.DONE, drift.status?.status)
        assertFalse(drift.isNonTerminal)

        val nothing = WbJson.decodeFromString(TimeOptionsResponse.serializer(), """{"status": null, "scan": null}""")
        assertTrue(nothing.isNonTerminal)
        assertEquals(PanelState.Running, TimingScenarioRules.panelState(nothing))
    }

    @Test
    fun `panel state ladder`() {
        fun state(json: String) = TimingScenarioRules.panelState(WbJson.decodeFromString(TimeOptionsResponse.serializer(), json))
        assertEquals(PanelState.Running, state("""{"status": {"status": "running"}, "scan": null}"""))
        assertEquals(PanelState.Failed, state("""{"status": {"status": "failed"}, "scan": null}"""))
        assertEquals(
            PanelState.Skipped("Set an alternate departure time to grade it here."),
            state("""{"status": {"status": "skipped", "reason": "no_alternate_time"}, "scan": null}"""),
        )
        assertTrue(state(doneJson) is PanelState.Results)
    }

    @Test
    fun `row wording mirrors iOS`() {
        val scan = WbJson.decodeFromString(TimeOptionsResponse.serializer(), doneJson).scan!!
        val (planned, later, nextDay) = scan.candidates

        assertEquals("2 departure windows look smoother than the planned time — worth a look, not a verdict.",
            TimingScenarioRules.headline(scan))
        assertEquals("09:00", TimingScenarioRules.timeLabel(planned, scan))
        assertEquals("08:00 · next day", TimingScenarioRules.timeLabel(nextDay, scan))
        assertEquals("as planned", TimingScenarioRules.shiftLabel(planned))
        assertEquals("+4.5h", TimingScenarioRules.shiftLabel(later))
        assertEquals("+23h", TimingScenarioRules.shiftLabel(nextDay))

        assertEquals("improves: Icing", TimingScenarioRules.diffText(later, names))
        // The confirmed diff replaces the provisional one.
        assertEquals("improves: Icing, Convection", TimingScenarioRules.diffText(nextDay, names))

        assertEquals("ECMWF only", TimingScenarioRules.confidenceText(planned))
        assertNull(TimingScenarioRules.confidenceText(later)) // checking all models…
        assertEquals("checked with ECMWF, GFS, ICON — still looks smoother", TimingScenarioRules.confidenceText(nextDay))

        assertEquals(
            listOf(
                "1 daylight hour couldn’t be checked from this briefing’s data (outside the fetched forecast window).",
                "The window stops where ECMWF forecast fidelity ends — later hours aren’t checkable yet.",
            ),
            TimingScenarioRules.footnotes(scan),
        )
        assertEquals("Graded with ECMWF · route ETAs 13:30 → 15:00", TimingScenarioRules.gradedFootnote(later))
    }

    @Test
    fun `actions follow the server's rules`() {
        val scan = WbJson.decodeFromString(TimeOptionsResponse.serializer(), doneJson).scan!!
        val (planned, later, nextDay) = scan.candidates
        // Baseline is ECMWF-only and unconfirmed: it can be checked, never set as the alternate.
        assertTrue(TimingScenarioRules.showsConfirmButton(planned))
        assertFalse(TimingScenarioRules.showsSetAlternateButton(planned, scan))
        // Being checked: no second check.
        assertFalse(TimingScenarioRules.showsConfirmButton(later))
        assertTrue(TimingScenarioRules.showsSetAlternateButton(later, scan))
        // Already confirmed, and on another day: neither.
        assertFalse(TimingScenarioRules.showsConfirmButton(nextDay))
        assertFalse(TimingScenarioRules.showsSetAlternateButton(nextDay, scan))
    }

    @Test
    fun `detail table columns and order`() {
        val scan = WbJson.decodeFromString(TimeOptionsResponse.serializer(), doneJson).scan!!
        val nextDay = scan.candidates[2]
        val cols = TimingScenarioRules.detailColumns(nextDay, scan)
        assertEquals(listOf("Current", "ECMWF only", "All models"), cols.map { it.label })
        assertEquals(mapOf("icing" to "RED", "convective" to "AMBER"), cols[0].statuses)
        assertEquals(listOf("icing", "convective"), TimingScenarioRules.detailRowIds(cols, names))
        assertEquals(listOf("Current"), TimingScenarioRules.detailColumns(scan.candidates[0], scan).map { it.label })
    }

    @Test
    fun `day suffix and reason parsing`() {
        assertEquals(" · previous day", TimingScenarioRules.daySuffix("2099-06-29T20:00:00Z", "2099-06-30T09:00:00Z"))
        assertEquals(" · +2 days", TimingScenarioRules.daySuffix("2099-07-02T09:00:00Z", "2099-06-30T09:00:00Z"))
        assertEquals("", TimingScenarioRules.daySuffix("2099-06-30T23:00:00Z", "2099-06-30T09:00:00Z"))
        assertEquals(mapOf("a" to "RED"), TimingScenarioRules.parseReason("a=RED, junk, =AMBER"))
    }
}
