package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveChange
import aero.flyfun.weather.model.LiveChangeTrail
import aero.flyfun.weather.model.LiveChanges
import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveTrailReport
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.flightCategoryColor
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircleOutline
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.History
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

private val ChangeInfoBlue = Color(0xFF0284C7)

/**
 * "Since this briefing" section rendering [LiveChanges] cards with severity badges
 * (`worse` red/amber, `better` green, `info` blue), station/SIGMET labels, expandable
 * [LiveChangeTrail] history strips (Nth time today badge + METAR flight-category strip over time),
 * and "Cleared in last hour" rows.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LiveChangesSection(
    liveChanges: LiveChanges?,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val activeChanges = liveChanges?.changes.orEmpty()
    val clearedChanges = liveChanges?.recentlyCleared.orEmpty()
    var showCleared by rememberSaveable { mutableStateOf(false) }

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
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Section header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = "Since This Briefing",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    val baselineLabel = liveChanges?.baselineAt?.let {
                        "Baseline ${formatUtcTimestampShort(it)}"
                    } ?: "Comparing live observations to briefing pack"
                    Text(
                        text = baselineLabel,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                // Summary counter pills
                if (liveChanges != null) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        if (liveChanges.worsenedCount > 0) {
                            ChangeCounterBadge(
                                text = "${liveChanges.worsenedCount} worse",
                                color = AssessmentRed,
                            )
                        }
                        if (liveChanges.improvedCount > 0) {
                            ChangeCounterBadge(
                                text = "${liveChanges.improvedCount} better",
                                color = AssessmentGreen,
                            )
                        }
                        if (liveChanges.worsenedCount == 0 && liveChanges.improvedCount == 0) {
                            ChangeCounterBadge(
                                text = "Stable",
                                color = AssessmentGreen,
                            )
                        }
                    }
                }
            }

            if (activeChanges.isEmpty()) {
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = AssessmentGreen.copy(alpha = 0.08f),
                    border = BorderStroke(1.dp, AssessmentGreen.copy(alpha = 0.25f)),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.CheckCircleOutline,
                            contentDescription = null,
                            tint = AssessmentGreen,
                            modifier = Modifier.size(18.dp),
                        )
                        Text(
                            text = "No significant weather deteriorations or new hazards since this briefing was generated.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    activeChanges.forEach { change ->
                        LiveChangeCard(
                            change = change,
                            isCleared = false,
                            onSelectFocus = onSelectFocus,
                        )
                    }
                }
            }

            // "Cleared in last hour" expandable fold
            if (clearedChanges.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f))
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { showCleared = !showCleared },
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 10.dp, vertical = 7.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.History,
                                contentDescription = null,
                                tint = AssessmentGreen,
                                modifier = Modifier.size(15.dp),
                            )
                            Text(
                                text = "Cleared in last hour (${clearedChanges.size})",
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        Icon(
                            imageVector = if (showCleared) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                            contentDescription = if (showCleared) "Collapse cleared" else "Expand cleared",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }

                AnimatedVisibility(visible = showCleared) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 4.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        clearedChanges.forEach { cleared ->
                            LiveChangeCard(
                                change = cleared,
                                isCleared = true,
                                onSelectFocus = onSelectFocus,
                            )
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LiveChangeCard(
    change: LiveChange,
    isCleared: Boolean,
    onSelectFocus: ((LiveFocus) -> Unit)?,
) {
    var trailExpanded by rememberSaveable(change.key) { mutableStateOf(false) }
    val trail = change.trail
    val hasTrail = trail != null && (trail.timesToday > 0 || !trail.reports.isNullOrEmpty() || trail.spans.isNotEmpty())

    val badgeColor = when {
        isCleared -> AssessmentGreen
        change.direction == "worse" && (change.tier == "alert" || change.newAlert) -> AssessmentRed
        change.direction == "worse" -> AssessmentAmber
        change.direction == "better" -> AssessmentGreen
        else -> ChangeInfoBlue
    }
    val badgeText = when {
        isCleared -> "CLEARED"
        change.direction == "worse" && (change.tier == "alert" || change.newAlert) -> "WORSE · ALERT"
        change.direction == "worse" -> "WORSE"
        change.direction == "better" -> "BETTER"
        else -> "INFO"
    }

    val stationOrSource = change.icao ?: change.stationId ?: change.source.uppercase()

    Surface(
        shape = RoundedCornerShape(10.dp),
        color = badgeColor.copy(alpha = 0.06f),
        border = BorderStroke(1.dp, badgeColor.copy(alpha = 0.32f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(11.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // Top badge row: severity pill + station/SIGMET label + transition pill + Nth time today badge
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    Surface(
                        shape = RoundedCornerShape(5.dp),
                        color = badgeColor.copy(alpha = 0.16f),
                    ) {
                        Text(
                            text = badgeText,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            color = badgeColor,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        )
                    }

                    Text(
                        text = stationOrSource,
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                    )

                    if (change.role.isNotBlank()) {
                        Text(
                            text = change.role.replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }

                    if (!change.fromValue.isNullOrBlank() && !change.toValue.isNullOrBlank()) {
                        Surface(
                            shape = RoundedCornerShape(5.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                        ) {
                            Text(
                                text = "${change.fromValue} → ${change.toValue}",
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }

                    if (trail != null && trail.timesToday > 1) {
                        Surface(
                            shape = RoundedCornerShape(5.dp),
                            color = AssessmentAmber.copy(alpha = 0.15f),
                        ) {
                            Text(
                                text = "${ordinal(trail.timesToday)} time today",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = AssessmentAmber,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }

                val timestamp = change.clearedAt ?: change.observedAt
                if (!timestamp.isNullOrBlank()) {
                    Text(
                        text = formatUtcTimestampShort(timestamp),
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Summary message
            if (change.message.isNotBlank()) {
                Text(
                    text = change.message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }

            // Route location + History Trail toggle + Map link
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val locParts = buildList {
                    change.enrouteDistanceNm?.let { add("${it.roundToInt()} NM en route") }
                    change.place?.text?.takeIf { it.isNotBlank() }?.let { add(it) }
                }
                Text(
                    text = locParts.joinToString(" · "),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                Row(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (hasTrail) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.clickable { trailExpanded = !trailExpanded },
                        ) {
                            Text(
                                text = if (trailExpanded) "Hide trail" else "History trail",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.primary,
                            )
                            Icon(
                                imageVector = if (trailExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(16.dp),
                            )
                        }
                    }

                    if (onSelectFocus != null && (change.icao != null || change.enrouteDistanceNm != null)) {
                        Text(
                            text = "Map ›",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.clickable {
                                onSelectFocus(
                                    LiveFocus(
                                        layer = if (change.source == "sigmet") "sigmets" else "stations",
                                        stationIcao = change.icao,
                                        distanceNm = change.enrouteDistanceNm,
                                    ),
                                )
                            },
                        )
                    }
                }
            }

            // Expandable History Trail
            if (trail != null) {
                AnimatedVisibility(visible = trailExpanded) {
                    LiveChangeTrailView(trail = trail)
                }
            }
        }
    }
}

@Composable
private fun LiveChangeTrailView(
    trail: LiveChangeTrail,
) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)),
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 4.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Observation History Trail",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
                if (trail.timesToday > 0) {
                    Text(
                        text = "${ordinal(trail.timesToday)} occurrence today",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // METAR flight-category strip over time
            val reports = trail.reports.orEmpty()
            if (reports.isNotEmpty()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    reports.forEach { report ->
                        TrailReportChip(report = report)
                    }
                }
            }

            // Active/past spans
            if (trail.spans.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    trail.spans.forEach { span ->
                        val startStr = formatUtcTimestampShort(span.start)
                        val endStr = span.end?.let { formatUtcTimestampShort(it) } ?: "now"
                        Text(
                            text = "• Active window: $startStr – $endStr",
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun TrailReportChip(
    report: LiveTrailReport,
) {
    val catColor = flightCategoryColor(report.category)
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = catColor.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, catColor.copy(alpha = 0.45f)),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .background(catColor, CircleShape),
                )
                Text(
                    text = report.category.ifBlank { "UNKN" },
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = catColor,
                )
            }
            Text(
                text = formatUtcTimestampShort(report.at),
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (report.ceilingFt != null) {
                Text(
                    text = "${report.ceilingFt} ft",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else if (report.crosswindKt != null) {
                Text(
                    text = "XW ${report.crosswindKt.roundToInt()} kt",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun ChangeCounterBadge(
    text: String,
    color: Color,
) {
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = color.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.35f)),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            color = color,
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
        )
    }
}

private fun ordinal(n: Int): String {
    val mod100 = n % 100
    if (mod100 in 11..13) return "${n}th"
    return when (n % 10) {
        1 -> "${n}st"
        2 -> "${n}nd"
        3 -> "${n}rd"
        else -> "${n}th"
    }
}
