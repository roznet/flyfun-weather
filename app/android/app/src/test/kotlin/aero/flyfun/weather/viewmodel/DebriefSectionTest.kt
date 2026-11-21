package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.data.FixtureBriefingData
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureFiles
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.DebriefAnswerItem
import aero.flyfun.weather.model.DebriefReport
import aero.flyfun.weather.model.DebriefResponse
import aero.flyfun.weather.model.FlightRole
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.model.rules.DebriefSectionRules
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Debrief section (#1024): the report DTO decodes the shared fixture
 * (`app/fixtures/api/debrief-report.json`, built by the #1023 server), the request bodies are the
 * server's shape, and the view model sends the taps back without deciding anything itself.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class DebriefSectionTest {

    private val report: DebriefReport get() = FixtureFiles.decode(DebriefReport.serializer(), "debrief-report")

    @Test
    fun `the shared fixture decodes`() {
        val r = report
        assertEquals("No radar along the route.", r.headline)
        assertEquals(listOf("icing", "cloud", "showers"), r.questions.map { it.kind })
        assertEquals(listOf("none", "light", "moderate_plus", "not_in_cloud"), r.questions.first().options.map { it.id })
        assertEquals(listOf(true, false, true), r.questions.map { it.avoidable })
        assertEquals(5, r.summary?.lines?.size)
        assertEquals(3, DebriefSectionRules.openQuestions(r))
    }

    @Test
    fun `a clear omits the answer and the debrief body carries the questions form`() {
        val clear = WbJson.encodeToString(DebriefAnswerItem.serializer(), DebriefAnswerItem("icing-1-2", null, false))
        assertFalse("answer" in clear)
        assertTrue("\"question_id\":\"icing-1-2\"" in clear)
        val body = WbJson.encodeToString(
            aero.flyfun.weather.model.DebriefRequest.serializer(),
            aero.flyfun.weather.model.DebriefRequest(decision = "flown"),
        )
        assertTrue("\"form\":\"questions\"" in body)
    }

    @Test
    fun `decision comes from the stored debrief, else from an answer`() {
        val r = report
        assertNull(DebriefSectionRules.decision(null, r))
        assertNull(DebriefSectionRules.decision(DebriefResponse(decision = "monitoring"), r))
        assertEquals(DebriefSectionRules.Decision.CANCELLED, DebriefSectionRules.decision(DebriefResponse(decision = "cancelled"), r))
        val q = r.questions.first()
        val answered = r.copy(questions = listOf(q.copy(answer = aero.flyfun.weather.model.DebriefAnswer("light"))))
        assertEquals(DebriefSectionRules.Decision.FLOWN, DebriefSectionRules.decision(null, answered))
    }

    @Test
    fun `an option tap answers, a second tap on the stored answer clears`() {
        val q = report.questions.first()
        assertEquals("light", DebriefSectionRules.answerTap(q, "light", mapOf(q.id to true)).answer)
        assertTrue(DebriefSectionRules.answerTap(q, "light", mapOf(q.id to true)).avoided)
        val stored = q.copy(answer = aero.flyfun.weather.model.DebriefAnswer("light"))
        assertNull(DebriefSectionRules.answerTap(stored, "light", emptyMap()).answer)
        assertEquals(listOf("IMC", "WIND"), DebriefSectionRules.toggledReasons(listOf("WIND"), "IMC"))
        assertEquals(listOf("IMC"), DebriefSectionRules.toggledReasons(listOf("IMC", "WIND"), "WIND"))
    }

    @Test
    fun `the list glyph follows the server count, owner only`() {
        val base = FixtureBriefingRepository().flights.first()
        assertFalse(base.copy(questionsOpen = 0, section = "past").needsDebrief)
        assertTrue(base.copy(questionsOpen = 2, section = "past").needsDebrief)
        assertFalse(base.copy(questionsOpen = 2, role = FlightRole.SUBSCRIBER).needsDebrief)
    }

    @Test
    fun `a ready report opens Observed on the section and an answer is sent as tapped`() = runTest {
        val testScope = TestScope(StandardTestDispatcher(testScheduler))
        val repo = FixtureBriefingRepository()
        val idx = repo.flights.indexOfFirst { it.id == FixtureBriefingData.FLIGHT_ID }
        repo.flights[idx] = repo.flights[idx].copy(debriefReady = true, questionsOpen = 3)
        repo.debriefReportFixture = report
        val vm = BriefingViewModel(flightId = FixtureBriefingData.FLIGHT_ID, repository = repo, scopeOverride = testScope)

        vm.loadInitialBriefing()
        testScope.testScheduler.advanceUntilIdle()

        assertNotNull(vm.debriefReport.value)
        assertTrue(BriefingTab.OBSERVED in vm.availableTabs.value)
        assertEquals(BriefingTab.OBSERVED, vm.selectedTab.value)

        vm.chooseDebriefDecision(DebriefSectionRules.Decision.FLOWN)
        testScope.testScheduler.advanceUntilIdle()
        assertEquals("flown", repo.savedDebrief?.decision)
        assertEquals("questions", repo.savedDebrief?.form)

        val icing = vm.debriefReport.value!!.questions.first()
        vm.setDebriefAvoided(icing, true)
        assertTrue(repo.savedDebriefAnswers.isEmpty())
        vm.answerDebriefQuestion(icing, "light")
        testScope.testScheduler.advanceUntilIdle()
        assertEquals(listOf(DebriefAnswerItem(icing.id, "light", true)), repo.savedDebriefAnswers.single().answers)
        assertEquals("light", vm.debriefReport.value!!.questions.first().answer?.answer)
        assertEquals(2, DebriefSectionRules.openQuestions(vm.debriefReport.value))
    }
}
