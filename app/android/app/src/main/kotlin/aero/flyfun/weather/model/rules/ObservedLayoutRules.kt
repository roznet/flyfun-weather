package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveGlanceLine

/**
 * Which of the server's glance lines go where on the Observed tab (#883), in
 * the order every client shares (web #881, iOS #882):
 *
 * offline banner → flight strip → alert-tier lines (never folded) → highlight
 * (or the headline when there is none) → airport cards → route ribbon →
 * Details fold (headline, the lines still ahead, passed lines, change rows).
 *
 * The lines, their `alert` and `passed` flags and the headline are the
 * server's (`live_glance`); this only sorts them into places.
 */
object ObservedLayoutRules {

    data class GlanceLayout(
        /** Alert-tier lines, shown above the highlight and never folded. */
        val alertLines: List<LiveGlanceLine>,
        /** What leads under the alerts: the highlight's text when the server wrote one, else the headline. */
        val leadIsHighlight: Boolean,
        /** The headline inside Details, only when the highlight took the lead. */
        val detailsHeadline: String?,
        /** Non-alert lines still ahead, in the server's order. */
        val aheadLines: List<LiveGlanceLine>,
        /** Non-alert lines the flight has passed, dimmed. */
        val passedLines: List<LiveGlanceLine>,
    )

    fun layout(glance: LiveGlance): GlanceLayout {
        val lines = glance.lines.orEmpty()
        val leadIsHighlight = glance.highlight?.text?.isNotBlank() == true
        return GlanceLayout(
            alertLines = lines.filter { it.alert },
            leadIsHighlight = leadIsHighlight,
            detailsHeadline = glance.headline.takeIf { leadIsHighlight && it.isNotBlank() },
            aheadLines = lines.filter { !it.alert && !it.passed },
            passedLines = lines.filter { !it.alert && it.passed },
        )
    }

    /** The phase label in front of a line ("Departure", "En route", "Arrival"). */
    fun phaseLabel(phase: String?): String = when (phase) {
        "departure" -> "Departure"
        "enroute" -> "En route"
        "arrival" -> "Arrival"
        else -> phase?.replaceFirstChar { it.uppercase() } ?: ""
    }
}
