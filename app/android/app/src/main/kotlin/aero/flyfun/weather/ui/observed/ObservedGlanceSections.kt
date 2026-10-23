package aero.flyfun.weather.ui.observed

import android.content.Context
import aero.flyfun.weather.model.LiveChanges
import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveGlanceLine
import aero.flyfun.weather.model.LiveRibbon
import aero.flyfun.weather.model.rules.ObservedLayoutRules
import aero.flyfun.weather.ui.theme.AssessmentRed
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/*
 * The Observed tab's top block in the shared order (#883, web #881, iOS #882),
 * laid out from the server's glance lines by [ObservedLayoutRules].
 */

private const val PREFS_NAME = "observed_layout"

/** A per-flight on/off remembered across launches (the folded departure, the Details fold). */
@Composable
internal fun rememberFlightFlag(name: String, flightId: String): Pair<Boolean, () -> Unit> {
    val context = LocalContext.current
    val prefs = remember(context) { context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE) }
    val key = "${name}_$flightId"
    var value by remember(key) { mutableStateOf(prefs.getBoolean(key, false)) }
    return value to {
        value = !value
        prefs.edit().putBoolean(key, value).apply()
    }
}

/** One server line: phase, text, proxy caption; tap opens the map on its focus. */
@Composable
private fun GlanceLineRow(
    line: LiveGlanceLine,
    onSelectFocus: ((LiveFocus) -> Unit)?,
    dimmed: Boolean = false,
    alert: Boolean = false,
) {
    val focus = line.focus
    val tappable = focus != null && onSelectFocus != null
    val textColor = when {
        alert -> AssessmentRed
        dimmed -> MaterialTheme.colorScheme.onSurfaceVariant
        else -> MaterialTheme.colorScheme.onSurface
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (tappable) Modifier.clickable { onSelectFocus?.invoke(focus!!) } else Modifier)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (alert) {
            Icon(Icons.Default.WarningAmber, contentDescription = "Alert", tint = AssessmentRed, modifier = Modifier.size(16.dp))
        }
        Text(
            text = ObservedLayoutRules.phaseLabel(line.phase),
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold,
            color = if (dimmed) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.width(72.dp),
        )
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = line.text,
                style = MaterialTheme.typography.bodySmall,
                fontWeight = if (alert) FontWeight.SemiBold else FontWeight.Normal,
                color = textColor,
            )
            line.proxyCaption?.takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (tappable) {
            Icon(Icons.Default.ChevronRight, contentDescription = null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Alert-tier lines: rare by design, never folded. */
@Composable
fun ObservedAlertLines(
    lines: List<LiveGlanceLine>,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    if (lines.isEmpty()) return
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = AssessmentRed.copy(alpha = 0.07f),
        border = BorderStroke(1.dp, AssessmentRed.copy(alpha = 0.4f)),
    ) {
        Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) {
            lines.forEach { GlanceLineRow(it, onSelectFocus, alert = true) }
        }
    }
}

/** The server's headline, when there is no highlight to lead with. */
@Composable
fun ObservedHeadline(text: String, modifier: Modifier = Modifier) {
    if (text.isBlank()) return
    Text(
        text = text,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
        modifier = modifier.fillMaxWidth(),
    )
}

/** The route ribbon on its own card (the old nutshell card's lower half). */
@Composable
fun ObservedRibbonCard(
    ribbon: LiveRibbon,
    progress: FlightProgress?,
    waypoints: List<String>,
    onOpenMap: () -> Unit,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            RouteRibbonCanvas(
                ribbon = ribbon,
                progress = progress,
                waypoints = waypoints,
                onFocusMap = { focus ->
                    if (focus != null && onSelectFocus != null) onSelectFocus(focus) else onOpenMap()
                },
            )
        }
    }
}

/**
 * The Details fold, collapsed by default and remembered per flight: the headline
 * (when the highlight leads), the lines still ahead, the passed lines dimmed, and
 * the "since this briefing" change rows.
 */
@Composable
fun ObservedDetailsFold(
    glance: LiveGlance?,
    changes: LiveChanges?,
    flightId: String,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val layout = glance?.let { ObservedLayoutRules.layout(it) }
    val (open, toggle) = rememberFlightFlag("details_open", flightId)
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(onClick = toggle)
                    .padding(horizontal = 14.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Details",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                Icon(
                    imageVector = if (open) Icons.Default.ExpandMore else Icons.Default.ChevronRight,
                    contentDescription = if (open) "Hide details" else "Show details",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(20.dp),
                )
            }
            if (open) {
                Column(
                    modifier = Modifier.padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    layout?.detailsHeadline?.let {
                        Text(it, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                    }
                    layout?.aheadLines?.forEach { GlanceLineRow(it, onSelectFocus) }
                    if (!layout?.passedLines.isNullOrEmpty()) {
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                        layout?.passedLines?.forEach { GlanceLineRow(it, onSelectFocus, dimmed = true) }
                    }
                    LiveChangesSection(
                        liveChanges = changes,
                        onSelectFocus = onSelectFocus,
                    )
                }
            }
        }
    }
}
