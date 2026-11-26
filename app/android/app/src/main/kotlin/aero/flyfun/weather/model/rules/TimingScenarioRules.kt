package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.TimeCandidate
import aero.flyfun.weather.model.TimeConfidence
import aero.flyfun.weather.model.TimeOptionsResponse
import aero.flyfun.weather.model.TimeScanJobStatus
import aero.flyfun.weather.model.TimeWindowScanDTO
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.roundToLong

/**
 * Wording and formatting of the Timing Scenarios section (#893), a port of iOS
 * `TimingScenariosView` / `TimingFormat`. Only presentation: the grades, diffs and confidence all
 * come from the server's scan. Times are UTC, like the web and iOS.
 */
object TimingScenarioRules {

    /** Which state the section shows, iOS's ladder: running, failed, skipped, results. */
    sealed interface PanelState {
        data object Running : PanelState
        data object Failed : PanelState
        data class Skipped(val message: String) : PanelState
        data class Results(val scan: TimeWindowScanDTO) : PanelState
    }

    fun panelState(options: TimeOptionsResponse): PanelState {
        val status = options.status?.status
        val scan = options.scan
        return when {
            status == TimeScanJobStatus.PENDING || status == TimeScanJobStatus.RUNNING ||
                (status == null && scan == null) -> PanelState.Running
            status == TimeScanJobStatus.FAILED -> PanelState.Failed
            status == TimeScanJobStatus.SKIPPED || scan == null ->
                PanelState.Skipped(skippedMessage(options.status?.reason))
            else -> PanelState.Results(scan)
        }
    }

    const val RUNNING_TEXT = "Scenarios running — checking other departure times against this briefing…"
    const val FAILED_TEXT = "Scenario check failed — it will run again on the next refresh."
    const val OFFLINE_TEXT = "Timing scenarios need a connection — reconnect to check other departure times."

    fun skippedMessage(reason: String?): String =
        if (reason == "no_alternate_time") {
            "Set an alternate departure time to grade it here."
        } else {
            "No scenario data for this briefing."
        }

    /** Never a verdict: the scan points at times worth a look, the pilot decides. */
    fun headline(scan: TimeWindowScanDTO): String {
        val better = scan.candidates.count { !it.isBaseline && !it.isAlternate }
        return when {
            better == 1 -> "1 departure window looks smoother than the planned time — worth a look, not a verdict."
            better > 1 -> "$better departure windows look smoother than the planned time — worth a look, not a verdict."
            else -> "No clearly better window found in the hours checkable from this briefing."
        }
    }

    /** Edge-of-window notes (refused hours, past-clipped, horizon-clipped), as on web and iOS. */
    fun footnotes(scan: TimeWindowScanDTO): List<String> = buildList {
        val refused = scan.refusedTimes.size
        if (refused > 0) {
            val s = if (refused > 1) "s" else ""
            add("$refused daylight hour$s couldn’t be checked from this briefing’s data (outside the fetched forecast window).")
        }
        if (scan.window?.pastClipped == true) {
            add("Hours that have already passed were left out — only upcoming departure times are shown.")
        }
        if (scan.window?.horizonClipped == true) {
            add("The window stops where ECMWF forecast fidelity ends — later hours aren’t checkable yet.")
        }
    }

    /** "HH:mm" plus a day suffix against the planned (baseline) row. */
    fun timeLabel(candidate: TimeCandidate, scan: TimeWindowScanDTO): String {
        val planned = scan.candidates.firstOrNull { it.isBaseline }?.departureTime
        return time(candidate.departureTime) + (planned?.let { daySuffix(candidate.departureTime, it) } ?: "")
    }

    fun shiftLabel(candidate: TimeCandidate): String {
        if (candidate.isBaseline) return "as planned"
        val h = candidate.departureShiftHours
        val sign = if (h >= 0) "+" else ""
        val num = if (h == Math.rint(h)) h.roundToLong().toString() else String.format(Locale.US, "%.1f", h)
        return "$sign${num}h"
    }

    /** improves / worsens: from the confirmed check once it ran, else the provisional one (never mixed). */
    fun diffText(candidate: TimeCandidate, names: Map<String, String>): String {
        val improves = candidate.confirmed?.improves ?: candidate.improves
        val worsens = candidate.confirmed?.worsens ?: candidate.worsens
        return buildList {
            if (improves.isNotEmpty()) add("improves: " + improves.joinToString(", ") { names[it] ?: it })
            if (worsens.isNotEmpty()) add("worsens: " + worsens.joinToString(", ") { names[it] ?: it })
        }.joinToString(" · ")
    }

    /** The model-coverage line; null while a check is running (the row shows a spinner instead). */
    fun confidenceText(candidate: TimeCandidate): String? {
        val confirmed = candidate.confirmed
        return when {
            confirmed != null -> {
                val models = confirmed.modelsChecked.joinToString(", ") { it.uppercase() }
                if (confirmed.betterThanBaseline) {
                    "checked with $models — still looks smoother"
                } else {
                    "checked with $models — not clearly better after all"
                }
            }
            candidate.confirmPending -> null
            candidate.confidence == TimeConfidence.ECMWF_ONLY -> "ECMWF only"
            else -> "all models checked"
        }
    }

    const val CHECKING_TEXT = "checking all models…"

    /** "Check all models" on an unconfirmed ECMWF-only row that isn't being checked. */
    fun showsConfirmButton(candidate: TimeCandidate): Boolean =
        candidate.confirmed == null && !candidate.confirmPending && candidate.confidence == TimeConfidence.ECMWF_ONLY

    /** "Set as alternate" on same-day rows other than the planned and the current alternate. */
    fun showsSetAlternateButton(candidate: TimeCandidate, scan: TimeWindowScanDTO): Boolean {
        if (candidate.isBaseline || candidate.isAlternate) return false
        return sameUtcDay(candidate.departureTime, scan.baseline.departureTime)
    }

    /** One column of the per-advisory detail table: its header and advisory id → status. */
    data class DetailColumn(val label: String, val statuses: Map<String, String>)

    /** Current (planned) vs this row's grade vs the confirmed all-models check when present. */
    fun detailColumns(candidate: TimeCandidate, scan: TimeWindowScanDTO): List<DetailColumn> = buildList {
        val baselineReason = scan.candidates.firstOrNull { it.isBaseline }?.assessmentReason.orEmpty()
        add(DetailColumn("Current", parseReason(baselineReason)))
        if (!candidate.isBaseline) {
            val provisional = if (candidate.confirmed != null || candidate.confidence == TimeConfidence.ECMWF_ONLY) {
                "ECMWF only"
            } else {
                "All models"
            }
            add(DetailColumn(provisional, parseReason(candidate.assessmentReason)))
            candidate.confirmed?.let { add(DetailColumn("All models", parseReason(it.assessmentReason))) }
        }
    }

    /** Advisory ids in the table, worst status first, then by display name. */
    fun detailRowIds(columns: List<DetailColumn>, names: Map<String, String>): List<String> {
        val ids = columns.flatMap { it.statuses.keys }.toSet()
        fun worst(id: String) = columns.maxOfOrNull { statusRank(it.statuses[id]) } ?: 0
        return ids.sortedWith(
            compareByDescending<String> { worst(it) }.thenBy { (names[it] ?: it).lowercase() },
        )
    }

    private fun statusRank(status: String?): Int = when (status) {
        "RED" -> 2
        "AMBER" -> 1
        else -> 0
    }

    fun gradedFootnote(candidate: TimeCandidate): String {
        val models = candidate.modelsUsed.joinToString(", ") { it.uppercase() }
        val first = candidate.validTimes.firstOrNull()
        val last = candidate.validTimes.lastOrNull()
        return if (first != null && last != null) {
            "Graded with $models · route ETAs ${time(first)} → ${time(last)}"
        } else {
            "Graded with $models"
        }
    }

    // ---- formatting -------------------------------------------------------------------------

    private val UTC_TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.US).withZone(ZoneOffset.UTC)

    /** "HH:mm" in UTC, or the raw string when unparseable. */
    fun time(iso: String): String {
        val ms = IsoTime.parseEpochMs(iso) ?: return iso
        return UTC_TIME.format(Instant.ofEpochMilli(ms))
    }

    private fun utcDay(iso: String): LocalDate? =
        IsoTime.parseEpochMs(iso)?.let { Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate() }

    /** " · next day" / " · previous day" / " · ±N days" against the planned day; empty on the same day. */
    fun daySuffix(iso: String, plannedIso: String): String {
        val cand = utcDay(iso) ?: return ""
        val planned = utcDay(plannedIso) ?: return ""
        val diff = ChronoUnit.DAYS.between(planned, cand)
        return when {
            diff == 0L -> ""
            diff == 1L -> " · next day"
            diff == -1L -> " · previous day"
            diff > 0 -> " · +$diff days"
            else -> " · $diff days"
        }
    }

    fun sameUtcDay(a: String, b: String): Boolean {
        val da = utcDay(a) ?: return false
        val db = utcDay(b) ?: return false
        return da == db
    }

    /** `"id=STATUS, id2=STATUS"` → id → status. */
    fun parseReason(reason: String): Map<String, String> = buildMap {
        reason.split(",").forEach { part ->
            val kv = part.split("=", limit = 2)
            if (kv.size == 2) {
                val id = kv[0].trim()
                val status = kv[1].trim()
                if (id.isNotEmpty() && status.isNotEmpty()) put(id, status)
            }
        }
    }
}
