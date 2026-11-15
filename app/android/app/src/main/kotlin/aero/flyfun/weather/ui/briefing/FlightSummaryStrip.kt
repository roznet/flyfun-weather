package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FlightSummary

/**
 * One quiet line at the top of the Advisory tab (#996): the flight's ends and times, then date,
 * duration, level and aircraft; the route under it, one line until tapped. No card: it scrolls
 * away with the content. Mirrors iOS `FlightSummaryStrip`.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FlightSummaryStrip(flight: FlightResponse, modifier: Modifier = Modifier) {
    val lines = FlightSummary.lines(flight)
    var routeExpanded by rememberSaveable(flight.id) { mutableStateOf(false) }
    Column(
        modifier = modifier.fillMaxWidth().testTag("flightSummaryStrip"),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = lines.main,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                text = lines.detail,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.align(Alignment.CenterVertically),
            )
        }
        lines.route?.let { route ->
            Row(
                modifier = Modifier.clickable { routeExpanded = !routeExpanded },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "via $route",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = if (routeExpanded) Int.MAX_VALUE else 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                Icon(
                    imageVector = if (routeExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = if (routeExpanded) "Collapse route" else "Show full route",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(16.dp),
                )
            }
        }
    }
}
