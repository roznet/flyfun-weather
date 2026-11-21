package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.DebriefAnswerItem
import aero.flyfun.weather.model.DebriefQuestion
import aero.flyfun.weather.model.DebriefReport
import aero.flyfun.weather.model.DebriefResponse
import aero.flyfun.weather.model.FlightResponse

/**
 * The Debrief section (#1024): layout decisions only. Which questions are asked, their wording
 * and their options are the server's (#1023 report); nothing here judges the weather.
 * Twins: web `components/debrief-section.ts`, iOS `DebriefSectionViewModel`.
 */
object DebriefSectionRules {
    enum class Decision(val wire: String) { FLOWN("flown"), CANCELLED("cancelled") }

    /**
     * Cancel reasons, in the server's taxonomy order with its labels (`debriefs/taxonomy.py`
     * `TAG_LABELS`). A hand copy, like the web's `debrief-taxonomy.ts`: Android does not read
     * the help catalog's `debrief` key yet. Covered by the sync-ios-web audit.
     */
    val CANCEL_REASONS: List<Pair<String, String>> = listOf(
        "IMC" to "IMC",
        "ICE" to "Icing",
        "WIND" to "Wind",
        "TS" to "Thunderstorm",
        "TURB" to "Turbulence",
        "FRZ" to "Freezing precip",
        "VIS" to "Visibility",
        "OPS" to "Operational",
    )

    const val NOTE_MAX_LENGTH = 300

    /** The stored decision, else flown once any question has an answer; monitoring shows neither. */
    fun decision(debrief: DebriefResponse?, report: DebriefReport?): Decision? = when (debrief?.decision) {
        "flown" -> Decision.FLOWN
        "cancelled" -> Decision.CANCELLED
        else -> if (report?.questions?.any { it.answer != null } == true) Decision.FLOWN else null
    }

    /** Whether the section is due (#1024): owner, and the report is built or the window is over. */
    fun isDue(flight: FlightResponse?, nowEpochMs: Long = System.currentTimeMillis(), started: Boolean = false): Boolean {
        if (flight == null || !flight.isEditable) return false
        return flight.debriefReady || flight.hasLiveObservationWindowEnded(nowEpochMs, started)
    }

    /** The avoided toggle as shown: the stored answer's, else what was ticked before answering. */
    fun isAvoided(question: DebriefQuestion, pending: Map<String, Boolean>): Boolean =
        question.answer?.avoided ?: pending[question.id] ?: false

    /** Tapping an option: answer it, or clear it when it is the stored answer. */
    fun answerTap(question: DebriefQuestion, option: String, pending: Map<String, Boolean>): DebriefAnswerItem =
        DebriefAnswerItem(
            questionId = question.id,
            answer = if (question.answer?.answer == option) null else option,
            avoided = isAvoided(question, pending),
        )

    /** The reasons after toggling [tag], in taxonomy order. */
    fun toggledReasons(current: List<String>, tag: String): List<String> {
        val set = current.toMutableSet()
        if (!set.add(tag)) set.remove(tag)
        return CANCEL_REASONS.map { it.first }.filter { it in set }
    }

    /** Open questions in the loaded report (the server's `questions_open` once re-read). */
    fun openQuestions(report: DebriefReport?): Int = report?.questions?.count { it.answer == null } ?: 0
}
