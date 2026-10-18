package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveRibbon
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.AssessmentUnavailable
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/**
 * Live aircraft progress along the route used by [ObservedNutshellCard], [RouteRibbonCanvas],
 * and the in-flight GPS tracking bar in [ObservedTabScreen].
 */
data class FlightProgress(
    val flownFraction: Double = 0.0,
    val flownNm: Double = 0.0,
    val totalNm: Double = 0.0,
    val groundSpeedKt: Double? = null,
    val altitudeFt: Double? = null,
    val phase: String? = null,
    val etaUtc: String? = null,
    val isTrackingActive: Boolean = false,
)

private data class NutshellPhaseRow(
    val phaseLabel: String,
    val status: String, // "alert", "warn", "ok", "unavailable"
    val summary: String,
    val detail: String? = null,
    val focus: LiveFocus? = null,
)

/**
 * Route-at-a-glance nutshell card combining:
 * 1. Phase-by-phase summary rows (`Departure`, `En route`, `Arrival`, `vs Briefing`) with distinct
 *    status dots (`alert` red/amber, `ok` green, `unavailable` gray — never conflating unavailable with clear).
 * 2. Degraded data sources notice when upstream feeds (e.g. radar/lightning/SIGMET) are unavailable.
 * 3. Interactive multi-lane [RouteRibbonCanvas] (`Radar`, `Lightning`, `SIGMETs`, `Stations`).
 */
@Composable
fun ObservedNutshellCard(
    glance: LiveGlance?,
    ribbon: LiveRibbon?,
    progress: FlightProgress?,
    waypoints: List<String>,
    onFocusMap: () -> Unit,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val phaseRows = remember(glance, ribbon, waypoints) {
        buildPhaseRows(glance, ribbon, waypoints)
    }
    val overallStatus = glance?.status?.lowercase() ?: deriveOverallStatus(phaseRows)
    val statusColor = phaseStatusColor(overallStatus)

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Card header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .background(statusColor, CircleShape),
                    )
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = "Observed Route Nutshell",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        val routeStr = waypoints.takeIf { it.isNotEmpty() }?.joinToString(" → ")
                        if (!routeStr.isNullOrBlank()) {
                            Text(
                                text = routeStr,
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }

                TextButton(onClick = onFocusMap) {
                    Icon(
                        imageVector = Icons.Default.Map,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = "View on Map ›",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }

            // Degraded data sources banner (never conflate unavailable with clear!)
            val degraded = glance?.degradedSources.orEmpty()
            if (degraded.isNotEmpty()) {
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = AssessmentUnavailable.copy(alpha = 0.14f),
                    border = BorderStroke(1.dp, AssessmentUnavailable.copy(alpha = 0.45f)),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.WarningAmber,
                            contentDescription = null,
                            tint = AssessmentUnavailable,
                            modifier = Modifier.size(15.dp),
                        )
                        Text(
                            text = "Unavailable sources: ${degraded.joinToString(", ")} (shown as gray — not clear)",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            // Phase-by-phase glance summary rows
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                phaseRows.forEach { row ->
                    PhaseSummaryRow(
                        row = row,
                        onClick = {
                            if (row.focus != null && onSelectFocus != null) {
                                onSelectFocus(row.focus)
                            } else {
                                onFocusMap()
                            }
                        },
                    )
                }
            }

            // Interactive multi-lane Route Ribbon Canvas
            if (ribbon != null) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                RouteRibbonCanvas(
                    ribbon = ribbon,
                    progress = progress,
                    waypoints = waypoints,
                    onFocusMap = { focus ->
                        if (focus != null && onSelectFocus != null) {
                            onSelectFocus(focus)
                        } else {
                            onFocusMap()
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun PhaseSummaryRow(
    row: NutshellPhaseRow,
    onClick: () -> Unit,
) {
    val dotColor = phaseStatusColor(row.status)
    val badgeText = when (row.status.lowercase()) {
        "alert", "worse", "red" -> "ALERT"
        "warn", "amber", "caution" -> "CAUTION"
        "unavailable", "nodata" -> "NO DATA"
        else -> "OK"
    }

    Surface(
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.32f),
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .background(dotColor, CircleShape),
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = row.phaseLabel,
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.width(78.dp),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = row.summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                if (!row.detail.isNullOrBlank()) {
                    Text(
                        text = row.detail,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(modifier = Modifier.width(6.dp))
            Surface(
                shape = RoundedCornerShape(5.dp),
                color = dotColor.copy(alpha = 0.14f),
            ) {
                Text(
                    text = badgeText,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = dotColor,
                    modifier = Modifier.padding(horizontal = 5.dp, vertical = 2.dp),
                )
            }
        }
    }
}

private fun buildPhaseRows(
    glance: LiveGlance?,
    ribbon: LiveRibbon?,
    waypoints: List<String>,
): List<NutshellPhaseRow> {
    val depStation = ribbon?.stations?.firstOrNull { it.role == "departure" }
        ?: ribbon?.stations?.firstOrNull()
    val destStation = ribbon?.stations?.lastOrNull { it.role == "destination" || it.role == "arrival" }
        ?: ribbon?.stations?.lastOrNull()?.takeIf { it != depStation }

    // 1. Departure row
    val depIcao = depStation?.icao ?: waypoints.firstOrNull() ?: "Origin"
    val depRow = if (depStation != null) {
        val cat = depStation.metarCategory
        val proxyNote = depStation.obsProxy?.let { " via ${it.icao} (${it.distanceNm.roundToInt()} NM)" }.orEmpty()
        val status = categoryToPhaseStatus(cat, depStation.windAdvisory)
        val summary = if (cat != null) {
            val ceil = depStation.metarCeilingFt?.let { " · Ceil $it ft" }.orEmpty()
            val wind = depStation.windAdvisory?.takeIf { it != "green" && it != "ok" }?.let { " · Wind $it" }.orEmpty()
            "$depIcao $cat$proxyNote$ceil$wind"
        } else {
            "$depIcao observation unavailable"
        }
        NutshellPhaseRow(
            phaseLabel = "Departure",
            status = status,
            summary = summary,
            focus = LiveFocus(layer = "stations", stationIcao = depStation.icao, distanceNm = 0.0),
        )
    } else {
        NutshellPhaseRow(
            phaseLabel = "Departure",
            status = "unavailable",
            summary = "$depIcao observation unavailable",
        )
    }

    // 2. En route row (derived from radar/lightning/SIGMETs + corridor stations)
    val segments = ribbon?.segments.orEmpty()
    val sigmets = ribbon?.sigmets.orEmpty()
    val allRadarUnavailable = segments.isNotEmpty() && segments.all {
        it.radarStatus.equals("unavailable", ignoreCase = true) ||
            it.radarStatus.equals("nodata", ignoreCase = true)
    }
    val maxDbz = segments.mapNotNull { it.radarMaxDbz }.maxOrNull()
    val totalFlashes = segments.sumOf { it.flashCount }

    val enrouteRow = when {
        sigmets.isNotEmpty() || (maxDbz != null && maxDbz >= 35.0) || totalFlashes > 0 -> {
            val parts = mutableListOf<String>()
            if (maxDbz != null && maxDbz >= 20.0) parts += "Radar up to ${maxDbz.roundToInt()} dBZ"
            if (totalFlashes > 0) parts += "$totalFlashes lightning flashes"
            if (sigmets.isNotEmpty()) parts += "${sigmets.size} active SIGMET(s)"
            NutshellPhaseRow(
                phaseLabel = "En route",
                status = if ((maxDbz ?: 0.0) >= 40.0 || totalFlashes > 0 || sigmets.any { it.hazard == "TS" }) "alert" else "warn",
                summary = parts.joinToString(" · "),
                focus = LiveFocus(layer = "radar"),
            )
        }
        allRadarUnavailable -> {
            NutshellPhaseRow(
                phaseLabel = "En route",
                status = "unavailable",
                summary = "Radar coverage unavailable along corridor",
                focus = LiveFocus(layer = "radar"),
            )
        }
        segments.isNotEmpty() -> {
            val lightEcho = if (maxDbz != null && maxDbz >= 15.0) {
                "Light echoes (${maxDbz.roundToInt()} dBZ max), no lightning or SIGMETs"
            } else {
                "Clear of radar precipitation, lightning, and SIGMETs"
            }
            NutshellPhaseRow(
                phaseLabel = "En route",
                status = "ok",
                summary = lightEcho,
                focus = LiveFocus(layer = "radar"),
            )
        }
        else -> {
            NutshellPhaseRow(
                phaseLabel = "En route",
                status = "unavailable",
                summary = "Route corridor scan unavailable",
            )
        }
    }

    // 3. Arrival row
    val destIcao = destStation?.icao ?: waypoints.lastOrNull() ?: "Destination"
    val arrRow = if (destStation != null) {
        val cat = destStation.metarCategory
        val proxyNote = destStation.obsProxy?.let { " via ${it.icao} (${it.distanceNm.roundToInt()} NM)" }.orEmpty()
        val tafNote = destStation.tafCategory?.let { " · TAF @ ETA $it" }.orEmpty()
        val status = categoryToPhaseStatus(cat ?: destStation.tafCategory, destStation.windAdvisory)
        val summary = if (cat != null) {
            "$destIcao $cat$proxyNote$tafNote"
        } else if (destStation.tafCategory != null) {
            "$destIcao METAR unavailable · TAF ${destStation.tafCategory}"
        } else {
            "$destIcao observation unavailable"
        }
        NutshellPhaseRow(
            phaseLabel = "Arrival",
            status = status,
            summary = summary,
            focus = LiveFocus(
                layer = "stations",
                stationIcao = destStation.icao,
                distanceNm = destStation.enrouteDistanceNm,
            ),
        )
    } else {
        NutshellPhaseRow(
            phaseLabel = "Arrival",
            status = "unavailable",
            summary = "$destIcao observation unavailable",
        )
    }

    // 4. vs Briefing row
    val changeStations = ribbon?.stations?.filter { it.hasChange }.orEmpty()
    val worsenedStations = changeStations.filter { it.changeDirection == "worse" }
    val mismatchStations = ribbon?.stations?.filter {
        it.categoryMatch == "worse" || it.categoryMatch == "mismatch"
    }.orEmpty()

    val vsBriefingRow = when {
        worsenedStations.isNotEmpty() -> {
            NutshellPhaseRow(
                phaseLabel = "vs Briefing",
                status = "alert",
                summary = "${worsenedStations.joinToString(", ") { it.icao }} worsened vs briefing baseline",
            )
        }
        mismatchStations.isNotEmpty() -> {
            NutshellPhaseRow(
                phaseLabel = "vs Briefing",
                status = "warn",
                summary = "Observed category differs from NWP at ${mismatchStations.joinToString(", ") { it.icao }}",
            )
        }
        changeStations.isNotEmpty() -> {
            NutshellPhaseRow(
                phaseLabel = "vs Briefing",
                status = "ok",
                summary = "Conditions improved at ${changeStations.joinToString(", ") { it.icao }}",
            )
        }
        glance != null -> {
            val text = glance.quietSummary
                ?: glance.subline
                ?: glance.headline.takeIf { it.isNotBlank() }
                ?: "Tracking in line with briefing forecast"
            NutshellPhaseRow(
                phaseLabel = "vs Briefing",
                status = when (glance.status.lowercase()) {
                    "alert" -> "alert"
                    "unavailable" -> "unavailable"
                    else -> "ok"
                },
                summary = text,
            )
        }
        else -> {
            NutshellPhaseRow(
                phaseLabel = "vs Briefing",
                status = "unavailable",
                summary = "Comparison baseline unavailable",
            )
        }
    }

    return listOf(depRow, enrouteRow, arrRow, vsBriefingRow)
}

private fun categoryToPhaseStatus(category: String?, windAdvisory: String?): String {
    if (category == null) return "unavailable"
    val catUpper = category.uppercase()
    if (catUpper == "LIFR" || catUpper == "IFR" || windAdvisory == "red") return "alert"
    if (catUpper == "MVFR" || windAdvisory == "amber") return "warn"
    if (catUpper == "VFR") return "ok"
    return "unavailable"
}

private fun deriveOverallStatus(rows: List<NutshellPhaseRow>): String = when {
    rows.any { it.status == "alert" } -> "alert"
    rows.any { it.status == "warn" } -> "warn"
    rows.all { it.status == "unavailable" } -> "unavailable"
    else -> "ok"
}

internal fun phaseStatusColor(status: String): Color = when (status.lowercase()) {
    "alert", "worse", "red", "severe" -> AssessmentRed
    "warn", "warning", "amber", "caution" -> AssessmentAmber
    "ok", "clear", "quiet", "green", "better" -> AssessmentGreen
    else -> AssessmentUnavailable
}
