package aero.flyfun.weather.ui.map

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.ObservedLightningRules
import aero.flyfun.weather.model.rules.ObservedSummaryChip
import kotlin.math.hypot

// The route map's observed layers (#907): radar-cell outlines, arrows and markers, and the
// lightning bolts, painted from what `CellsOverlayRules` / `ObservedLightningRules` decided.
// SYNC: iOS `RouteMapKitView.updateCells` / `CellArrowRenderer` / `CellMarkerView` /
// `LightningRenderer`, and the footer + details sheet of `RouteMapView`.

/**
 * Outlines (core35 under core41), then the 30-min motion arrows, then a trend-coloured marker
 * per core. Rain areas are not drawn on the route map (web/iOS parity).
 */
fun DrawScope.drawCellsOverlayOnMap(display: CellDisplay, projection: SlippyMapProjection) {
    for (tier in CellsOverlayRules.OUTLINE_TIERS) {
        val (argb, widthDp) = CellsOverlayRules.outlineStyle(tier)
        val color = Color(argb).copy(alpha = 0.95f)
        CellsOverlayRules.outlines(display, tier).forEach { line ->
            val path = Path()
            line.forEachIndexed { i, (lat, lon) ->
                val p = projection.latLonToScreen(lat, lon)
                if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
            }
            drawPath(path, color, style = Stroke(width = widthDp.dp.toPx(), join = StrokeJoin.Round))
        }
    }
    val cores = CellsOverlayRules.drawnCells(display)
    val arrowColor = Color(CellsOverlayRules.ARROW_ARGB)
    cores.forEach { cell ->
        val end = CellsOverlayRules.arrowEnd(cell) ?: return@forEach
        drawCellArrow(
            start = projection.latLonToScreen(cell.lat, cell.lon),
            end = projection.latLonToScreen(end.first, end.second),
            color = arrowColor,
        )
    }
    val rim = Color(CellsOverlayRules.MARKER_RIM_ARGB)
    cores.forEach { cell ->
        val center = projection.latLonToScreen(cell.lat, cell.lon)
        val r = CellsOverlayRules.markerRadiusDp(cell.tier).dp.toPx()
        drawCircle(Color(CellsOverlayRules.trendColorArgb(cell.trend?.state)).copy(alpha = 0.9f), r, center)
        drawCircle(rim, r, center, style = Stroke(width = 1.dp.toPx()))
    }
}

/** The arrow line and, when it is long enough on screen to carry one, an arrowhead (iOS `CellArrowRenderer`). */
private fun DrawScope.drawCellArrow(start: Offset, end: Offset, color: Color) {
    drawLine(color, start, end, strokeWidth = 2.5f.dp.toPx(), cap = StrokeCap.Round)
    val dx = end.x - start.x
    val dy = end.y - start.y
    val length = hypot(dx, dy)
    // Too short to carry a 10 dp head: it would sit on the cell's own marker.
    if (length < 14.dp.toPx()) return
    val ux = dx / length
    val uy = dy / length
    val head = 10.dp.toPx()
    val half = 5.dp.toPx()
    val baseX = end.x - ux * head
    val baseY = end.y - uy * head
    val path = Path().apply {
        moveTo(end.x, end.y)
        lineTo(baseX - uy * half, baseY + ux * half)
        lineTo(end.x - ux * head * 0.75f, end.y - uy * head * 0.75f)
        lineTo(baseX + uy * half, baseY - ux * half)
        close()
    }
    drawPath(path, color, style = Fill)
}

/** Every flash as a small bolt of fixed screen size, oldest first so the newest sit on top. */
fun DrawScope.drawLightningOnMap(
    flashes: List<ObservedLightningRules.DrawableFlash>,
    projection: SlippyMapProjection,
) {
    val width = ObservedLightningRules.GLYPH_WIDTH_DP.dp.toPx()
    val height = ObservedLightningRules.GLYPH_HEIGHT_DP.dp.toPx()
    val fill = Color(ObservedLightningRules.FILL_ARGB)
    val rim = Color(ObservedLightningRules.RIM_ARGB)
    val rimStroke = Stroke(width = 1.dp.toPx(), join = StrokeJoin.Round)
    flashes.forEach { flash ->
        val c = projection.latLonToScreen(flash.lat, flash.lon)
        if (c.x < -width || c.y < -height || c.x > size.width + width || c.y > size.height + height) return@forEach
        val path = Path()
        ObservedLightningRules.glyphPoints(c.x, c.y, width, height).forEachIndexed { i, (x, y) ->
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        path.close()
        val alpha = flash.opacity.toFloat()
        drawPath(path, fill, alpha = alpha, style = Fill)
        drawPath(path, rim, alpha = alpha, style = rimStroke)
    }
}

/** The drawn core nearest [tap] within [thresholdPx], for the measurements card. */
fun hitCell(display: CellDisplay?, tap: Offset, projection: SlippyMapProjection, thresholdPx: Float): DisplayCell? {
    display ?: return null
    return CellsOverlayRules.drawnCells(display)
        .map { it to projection.latLonToScreen(it.lat, it.lon) }
        .minByOrNull { (_, s) -> hypot(s.x - tap.x, s.y - tap.y) }
        ?.takeIf { (_, s) -> hypot(s.x - tap.x, s.y - tap.y) <= thresholdPx }
        ?.first
}

/**
 * One tappable line on the map with each observed layer's age ("Cells 12 min · Lightning 6 min"),
 * orange where a layer is stale or failed so an empty map never reads as "nothing there". The
 * full lines, legends and caveats are one tap away.
 */
@Composable
fun ObservedLayersFooter(
    chips: List<ObservedSummaryChip>,
    badges: List<String>,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (chips.isEmpty()) return
    Surface(
        modifier = modifier
            .clickable(onClick = onClick)
            .testTag("map.observedBadge")
            .semantics { contentDescription = badges.joinToString(". ") },
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.88f),
        tonalElevation = 2.dp,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            chips.forEachIndexed { i, chip ->
                if (i > 0) {
                    Text("·", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text(
                    text = chip.text,
                    style = MaterialTheme.typography.labelSmall,
                    color = if (chip.isWarning) Color(0xFFF59E0B) else MaterialTheme.colorScheme.onSurface,
                )
            }
            Icon(
                imageVector = Icons.Default.Info,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(14.dp),
            )
        }
    }
}

/** Everything the footer leaves out: each layer's full line, the legends and the cell caveat. */
@Composable
fun ObservedLayersDetailsDialog(
    badges: List<String>,
    showsCells: Boolean,
    showsLightning: Boolean,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
        title = { Text("Observed") },
        modifier = Modifier.testTag("map.observedDetails"),
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                DetailsHeader("Layers")
                badges.forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
                DetailsFootnote(
                    "Observed imagery shows the sky as it was, not as it is: at 120 kt, ten minutes is twenty nautical miles.",
                )
                if (showsLightning) {
                    DetailsHeader("Lightning")
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("⚡", color = Color(ObservedLightningRules.RIM_ARGB), style = MaterialTheme.typography.labelSmall)
                        Text(ObservedLightningRules.LEGEND, style = MaterialTheme.typography.labelSmall)
                    }
                    DetailsFootnote(
                        "Satellite total lightning (in-cloud and cloud-to-ground) in the corridor box, each flash at its own time.",
                    )
                }
                if (showsCells) {
                    DetailsHeader("Radar cells (experimental)")
                    CellsLegend()
                    DetailsFootnote(
                        "Markers are coloured by trend; the magenta arrow points to where a core would be in 30 min if its " +
                            "motion continued. Tap a marker for its measurements. " + CellsOverlayRules.CAVEAT,
                    )
                }
            }
        },
    )
}

/** Trend colours (evolution, not safety) and the 30-min arrow. */
@Composable
private fun CellsLegend() {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
        CellsOverlayRules.TREND_ORDER.forEach { state ->
            Row(horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .background(Color(CellsOverlayRules.trendColorArgb(state)), CircleShape)
                        .border(0.5.dp, Color(CellsOverlayRules.MARKER_RIM_ARGB), CircleShape),
                )
                Text(state, style = MaterialTheme.typography.labelSmall)
            }
        }
        Text("━", color = Color(CellsOverlayRules.ARROW_ARGB), style = MaterialTheme.typography.labelSmall)
        Text("30 min", style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun DetailsHeader(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun DetailsFootnote(text: String) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}
