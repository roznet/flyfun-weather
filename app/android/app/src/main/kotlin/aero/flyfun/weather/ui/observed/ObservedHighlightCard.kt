package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveGlanceLine
import aero.flyfun.weather.model.LiveHighlight
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.AssessmentUnavailable
import androidx.compose.animation.AnimatedVisibility
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
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.outlined.ThumbDown
import androidx.compose.material.icons.outlined.ThumbUp
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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

/**
 * Primary flight-day highlight card signature for a single [LiveHighlight].
 * Displays the alert/headline prominently with an optional expandable "Details" fold,
 * experimental caption, generation timestamp, and thumbs-up / thumbs-down rating buttons.
 */
@Composable
fun ObservedHighlightCard(
    highlight: LiveHighlight,
    onRateHighlight: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
    secondaryHighlights: List<LiveHighlight> = emptyList(),
    secondaryLines: List<LiveGlanceLine> = emptyList(),
    updatedAt: String? = null,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    ObservedHighlightCardContent(
        primaryHighlights = listOf(highlight),
        secondaryHighlights = secondaryHighlights,
        secondaryLines = secondaryLines,
        quietSummary = null,
        updatedAt = updatedAt,
        onRateHighlight = onRateHighlight,
        onSelectFocus = onSelectFocus,
        modifier = modifier,
    )
}

/**
 * Overload accepting the full [LiveGlance] payload from [aero.flyfun.weather.model.LiveLayerResponse].
 * Splits primary alert lines from secondary detail lines into an expandable "Details" fold.
 */
@Composable
fun ObservedHighlightCard(
    glance: LiveGlance,
    onRateHighlight: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
    updatedAt: String? = null,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val allHighlights = glance.highlights
    val allLines = glance.lines.orEmpty()

    val alertHighlights = allHighlights.filter { it.level == "alert" || it.level == "warn" || it.level == "worse" }
    val primaryHighlights = when {
        alertHighlights.isNotEmpty() -> alertHighlights
        allHighlights.isNotEmpty() -> listOf(allHighlights.first())
        glance.headline.isNotBlank() -> listOf(
            LiveHighlight(
                text = glance.headline,
                level = if (glance.status == "alert") "alert" else "info",
            ),
        )
        else -> emptyList()
    }
    val secondaryHighlights = allHighlights.filter { it !in primaryHighlights }

    val alertLines = allLines.filter { it.level == "alert" || it.level == "worse" }
    val secondaryLines = if (primaryHighlights.isEmpty() && alertLines.isNotEmpty()) {
        allLines.filter { it !in alertLines }
    } else {
        allLines
    }

    val effectivePrimary = if (primaryHighlights.isNotEmpty()) {
        primaryHighlights
    } else {
        alertLines.map { line ->
            LiveHighlight(
                text = line.text,
                level = line.level,
                focus = line.focus,
                changeKey = line.changeKey,
            )
        }
    }

    ObservedHighlightCardContent(
        primaryHighlights = effectivePrimary,
        secondaryHighlights = secondaryHighlights,
        secondaryLines = secondaryLines,
        quietSummary = glance.quietSummary ?: glance.subline,
        updatedAt = updatedAt,
        onRateHighlight = onRateHighlight,
        onSelectFocus = onSelectFocus,
        modifier = modifier,
    )
}

@Composable
private fun ObservedHighlightCardContent(
    primaryHighlights: List<LiveHighlight>,
    secondaryHighlights: List<LiveHighlight>,
    secondaryLines: List<LiveGlanceLine>,
    quietSummary: String?,
    updatedAt: String?,
    onRateHighlight: ((Boolean) -> Unit)?,
    onSelectFocus: ((LiveFocus) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    var detailsExpanded by rememberSaveable { mutableStateOf(false) }
    var ratedPositive by rememberSaveable { mutableStateOf<Boolean?>(null) }

    val hasAlert = primaryHighlights.any { it.level == "alert" || it.level == "worse" }
    val accentColor = when {
        hasAlert -> AssessmentRed
        primaryHighlights.any { it.level == "warn" || it.level == "amber" } -> AssessmentAmber
        primaryHighlights.any { it.level == "better" || it.level == "ok" } -> AssessmentGreen
        else -> MaterialTheme.colorScheme.primary
    }

    val detailsCount = secondaryHighlights.size + secondaryLines.size

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(1.dp, accentColor.copy(alpha = 0.35f)),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Header row: Flight-Day Highlight badge + timestamp
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        imageVector = Icons.Default.AutoAwesome,
                        contentDescription = null,
                        tint = accentColor,
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        text = "Flight-Day Highlight",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    Surface(
                        shape = RoundedCornerShape(6.dp),
                        color = accentColor.copy(alpha = 0.12f),
                    ) {
                        Text(
                            text = if (hasAlert) "ALERT" else "LIVE",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            color = accentColor,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        )
                    }
                }

                if (!updatedAt.isNullOrBlank()) {
                    Text(
                        text = formatUtcTimestampShort(updatedAt),
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Prominent alert / primary lines
            if (primaryHighlights.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    primaryHighlights.forEach { item ->
                        HighlightBulletRow(
                            text = item.text,
                            level = item.level,
                            detail = null,
                            focus = item.focus,
                            onSelectFocus = onSelectFocus,
                            isProminent = true,
                        )
                    }
                }
            } else if (!quietSummary.isNullOrBlank()) {
                HighlightBulletRow(
                    text = quietSummary,
                    level = "ok",
                    detail = null,
                    focus = null,
                    onSelectFocus = null,
                    isProminent = true,
                )
            }

            // Expandable "Details" fold for secondary lines
            if (detailsCount > 0) {
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { detailsExpanded = !detailsExpanded },
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 10.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = if (detailsExpanded) {
                                "Hide details ($detailsCount)"
                            } else {
                                "Details ($detailsCount more)"
                            },
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Icon(
                            imageVector = if (detailsExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                            contentDescription = if (detailsExpanded) "Collapse details" else "Expand details",
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }

                AnimatedVisibility(visible = detailsExpanded) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 4.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        secondaryHighlights.forEach { item ->
                            HighlightBulletRow(
                                text = item.text,
                                level = item.level,
                                detail = null,
                                focus = item.focus,
                                onSelectFocus = onSelectFocus,
                                isProminent = false,
                            )
                        }
                        secondaryLines.forEach { line ->
                            HighlightBulletRow(
                                text = line.text,
                                level = line.level,
                                detail = line.detail,
                                focus = line.focus,
                                onSelectFocus = onSelectFocus,
                                isProminent = false,
                            )
                        }
                    }
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f))

            // Footer: Experimental caption + Thumbs-up / Thumbs-down rating buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Experimental · Verify with official METAR/SIGMETs",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )

                // No thumbs for a line the server can't key a rating to (null callback).
                if (onRateHighlight != null) Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    if (ratedPositive != null) {
                        Text(
                            text = "Thanks",
                            style = MaterialTheme.typography.labelSmall,
                            color = AssessmentGreen,
                            modifier = Modifier.padding(end = 4.dp),
                        )
                    }
                    IconButton(
                        onClick = {
                            ratedPositive = true
                            onRateHighlight(true)
                        },
                        modifier = Modifier.size(28.dp),
                    ) {
                        Icon(
                            imageVector = if (ratedPositive == true) Icons.Filled.ThumbUp else Icons.Outlined.ThumbUp,
                            contentDescription = "Helpful highlight",
                            tint = if (ratedPositive == true) AssessmentGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(15.dp),
                        )
                    }
                    IconButton(
                        onClick = {
                            ratedPositive = false
                            onRateHighlight(false)
                        },
                        modifier = Modifier.size(28.dp),
                    ) {
                        Icon(
                            imageVector = if (ratedPositive == false) Icons.Filled.ThumbDown else Icons.Outlined.ThumbDown,
                            contentDescription = "Unhelpful highlight",
                            tint = if (ratedPositive == false) AssessmentAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(15.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun HighlightBulletRow(
    text: String,
    level: String,
    detail: String?,
    focus: LiveFocus?,
    onSelectFocus: ((LiveFocus) -> Unit)?,
    isProminent: Boolean,
) {
    val dotColor = highlightLevelColor(level)
    val rowModifier = if (focus != null && onSelectFocus != null) {
        Modifier
            .fillMaxWidth()
            .clickable { onSelectFocus(focus) }
            .padding(vertical = 2.dp)
    } else {
        Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp)
    }

    Row(
        modifier = rowModifier,
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            modifier = Modifier
                .padding(top = 6.dp)
                .size(8.dp)
                .background(dotColor, CircleShape),
        )
        Spacer(modifier = Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = text,
                style = if (isProminent) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodySmall,
                fontWeight = if (isProminent) FontWeight.SemiBold else FontWeight.Normal,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (!detail.isNullOrBlank()) {
                Text(
                    text = detail,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (focus != null && onSelectFocus != null) {
            Text(
                text = "Map ›",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 8.dp, top = 2.dp),
            )
        }
    }
}

internal fun highlightLevelColor(level: String): Color = when (level.lowercase()) {
    "alert", "worse", "severe", "red" -> AssessmentRed
    "warn", "warning", "amber", "caution" -> AssessmentAmber
    "better", "improved", "ok", "green", "clear" -> AssessmentGreen
    "unavailable", "nodata", "unknown" -> AssessmentUnavailable
    else -> Color(0xFF0284C7)
}

internal fun formatUtcTimestampShort(isoString: String): String {
    // Extract HH:MMZ from ISO-8601 strings like "2026-04-17T08:50:00Z" or "2026-04-17T08:50:00+00:00"
    val tIndex = isoString.indexOf('T')
    if (tIndex != -1 && isoString.length >= tIndex + 6) {
        return "${isoString.substring(tIndex + 1, tIndex + 6)}Z"
    }
    return isoString
}
