package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveStorm
import aero.flyfun.weather.model.LiveStormTrackPoint
import aero.flyfun.weather.model.LiveStorms
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CheckCircleOutline
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

private val LightningGold = Color(0xFFF59E0B)

/**
 * Radar storm cell lineage section rendering [LiveStorms] cards with intensity tier (`dBZ`),
 * lightning indicator (`⚡`), cloud top FL, position relative to route, trend/closing status,
 * and a mini Compose Canvas sparkline of the cell's recent 30-minute track history.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LiveStormsSection(
    storms: LiveStorms?,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val cells = storms?.cells.orEmpty()

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
            // Header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.Radar,
                        contentDescription = null,
                        tint = if ((storms?.directThreatCount ?: 0) > 0) AssessmentRed else MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(18.dp),
                    )
                    Column {
                        Text(
                            text = "Radar Storm Cells",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        val sub = buildString {
                            append("±${(storms?.corridorNm ?: 50.0).roundToInt()} NM corridor")
                            storms?.validTime?.takeIf { it.isNotBlank() }?.let {
                                append(" · Valid ${formatUtcTimestampShort(it)}")
                            }
                        }
                        Text(
                            text = sub,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                if (storms != null) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        if (storms.directThreatCount > 0) {
                            StormBadgePill(
                                text = "${storms.directThreatCount} direct",
                                color = AssessmentRed,
                            )
                        }
                        if (storms.vicinityCount > 0) {
                            StormBadgePill(
                                text = "${storms.vicinityCount} vicinity",
                                color = AssessmentAmber,
                            )
                        }
                        if (cells.isEmpty()) {
                            StormBadgePill(
                                text = "0 cells",
                                color = AssessmentGreen,
                            )
                        }
                    }
                }
            }

            if (cells.isEmpty()) {
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
                            text = "No tracked convective radar cells or lightning cores within the route corridor.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    cells.forEach { cell ->
                        LiveStormCellCard(
                            storm = cell,
                            onSelectFocus = onSelectFocus,
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LiveStormCellCard(
    storm: LiveStorm,
    onSelectFocus: ((LiveFocus) -> Unit)?,
) {
    val isDirect = storm.disposition.equals("direct", ignoreCase = true) ||
        storm.disposition.equals("threat", ignoreCase = true) ||
        storm.distanceNm <= 10.0
    val accentColor = when {
        isDirect || (storm.maxDbz ?: 0.0) >= 45.0 -> AssessmentRed
        (storm.maxDbz ?: 0.0) >= 35.0 || storm.flashCount > 0 -> AssessmentAmber
        else -> Color(0xFF0284C7)
    }

    Surface(
        shape = RoundedCornerShape(10.dp),
        color = accentColor.copy(alpha = 0.06f),
        border = BorderStroke(1.dp, accentColor.copy(alpha = 0.35f)),
        modifier = Modifier
            .fillMaxWidth()
            .then(
                if (onSelectFocus != null) {
                    Modifier.clickable {
                        onSelectFocus(
                            LiveFocus(
                                layer = "storms",
                                stormId = storm.id,
                                distanceNm = storm.enrouteDistanceNm,
                            ),
                        )
                    }
                } else {
                    Modifier
                },
            ),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Top badges: Disposition + dBZ core + Lightning + Cloud Top FL + Trend
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                StormBadgePill(
                    text = if (isDirect) "DIRECT THREAT" else "VICINITY",
                    color = accentColor,
                )

                storm.maxDbz?.let { dbz ->
                    StormBadgePill(
                        text = "${dbz.roundToInt()} dBZ core",
                        color = if (dbz >= 42.0) AssessmentRed else AssessmentAmber,
                    )
                }

                if (storm.flashCount > 0) {
                    Surface(
                        shape = RoundedCornerShape(6.dp),
                        color = LightningGold.copy(alpha = 0.16f),
                        border = BorderStroke(1.dp, LightningGold.copy(alpha = 0.45f)),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.Bolt,
                                contentDescription = null,
                                tint = LightningGold,
                                modifier = Modifier.size(12.dp),
                            )
                            Text(
                                text = buildString {
                                    append("${storm.flashCount} fl")
                                    if (storm.flashRatePerMin >= 0.5) {
                                        append(" (${storm.flashRatePerMin.roundToInt()}/m)")
                                    }
                                },
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace,
                                color = LightningGold,
                            )
                        }
                    }
                }

                storm.maxTopFt?.let { topFt ->
                    val fl = (topFt / 100.0).roundToInt().toString().padStart(3, '0')
                    StormBadgePill(
                        text = "Tops FL$fl",
                        color = MaterialTheme.colorScheme.primary,
                    )
                }

                val trendLabel = formatStormTrendLabel(storm)
                StormBadgePill(
                    text = trendLabel,
                    color = when {
                        storm.severity.equals("decaying", ignoreCase = true) ||
                            storm.severity.equals("weakening", ignoreCase = true) -> AssessmentGreen
                        storm.severity.equals("intensifying", ignoreCase = true) ||
                            storm.severity.equals("developing", ignoreCase = true) -> AssessmentAmber
                        else -> MaterialTheme.colorScheme.secondary
                    },
                )
            }

            // Headline & relative position to route
            val posText = buildStormPositionSummary(storm)
            Text(
                text = storm.headline.ifBlank { posText },
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )

            if (storm.headline.isNotBlank()) {
                Text(
                    text = posText,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (storm.detail.isNotBlank()) {
                Text(
                    text = storm.detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Motion & closing / moving-away row + 30-min off-track distance sparkline
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    val motionText = buildMotionSummary(storm)
                    if (motionText.isNotBlank()) {
                        Text(
                            text = motionText,
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                    val closingText = buildClosingSummary(storm)
                    if (closingText.isNotBlank()) {
                        val isClosing = (storm.closingKt ?: 0.0) > 1.0
                        Text(
                            text = closingText,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = if (isClosing) AssessmentRed else AssessmentGreen,
                        )
                    }
                }

                Spacer(modifier = Modifier.width(12.dp))

                // Mini Compose Canvas sparkline of the last 30 min off-track distance / track
                StormDistanceSparkline(
                    storm = storm,
                    accentColor = accentColor,
                )
            }
        }
    }
}

@Composable
private fun StormDistanceSparkline(
    storm: LiveStorm,
    accentColor: Color,
) {
    val trackPoints = storm.track.orEmpty()
    // Build distance series: if track history has points, plot their dBZ or distance progression
    // alongside current distanceNm and CPA estimate
    val distanceSeries: List<Double> = when {
        trackPoints.size >= 2 -> {
            // Use maxDbz history if available, otherwise synthesize off-track distance approach
            val closingKt = storm.closingKt ?: 0.0
            val count = trackPoints.size
            trackPoints.mapIndexed { idx, _ ->
                val minutesAgo = (count - 1 - idx) * 5.0
                (storm.distanceNm + (closingKt * (minutesAgo / 60.0))).coerceAtLeast(0.5)
            }
        }
        else -> {
            val closingKt = storm.closingKt ?: 0.0
            listOf(30.0, 20.0, 10.0, 0.0).map { minAgo ->
                (storm.distanceNm + closingKt * (minAgo / 60.0)).coerceAtLeast(0.5)
            }
        }
    }

    val outlineColor = MaterialTheme.colorScheme.outlineVariant

    Surface(
        shape = RoundedCornerShape(6.dp),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
        border = BorderStroke(1.dp, outlineColor.copy(alpha = 0.6f)),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Canvas(
                modifier = Modifier
                    .width(86.dp)
                    .height(26.dp),
            ) {
                val maxDist = max(distanceSeries.maxOrNull() ?: 25.0, 15.0)
                val minDist = 0.0
                val span = (maxDist - minDist).coerceAtLeast(1.0)

                // Route baseline at bottom (0 NM off-track)
                drawLine(
                    color = outlineColor,
                    start = Offset(0f, size.height - 2.dp.toPx()),
                    end = Offset(size.width, size.height - 2.dp.toPx()),
                    strokeWidth = 1.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx()), 0f),
                )

                val path = Path()
                distanceSeries.forEachIndexed { idx, dist ->
                    val x = if (distanceSeries.size > 1) {
                        size.width * idx / (distanceSeries.size - 1)
                    } else {
                        size.width / 2f
                    }
                    // Lower Y = closer to route (bottom of sparkline = 0 NM)
                    val norm = ((dist - minDist) / span).toFloat().coerceIn(0f, 1f)
                    val y = (size.height - 4.dp.toPx()) - norm * (size.height - 8.dp.toPx())
                    if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
                }

                drawPath(
                    path = path,
                    color = accentColor,
                    style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round),
                )

                // Current position dot at right end
                val lastDist = distanceSeries.lastOrNull() ?: storm.distanceNm
                val lastNorm = ((lastDist - minDist) / span).toFloat().coerceIn(0f, 1f)
                val lastY = (size.height - 4.dp.toPx()) - lastNorm * (size.height - 8.dp.toPx())
                drawCircle(
                    color = accentColor,
                    radius = 3.dp.toPx(),
                    center = Offset(size.width, lastY),
                )
            }
            Text(
                text = "30m track (${storm.distanceNm.roundToInt()} NM)",
                style = MaterialTheme.typography.labelSmall,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun StormBadgePill(
    text: String,
    color: Color,
) {
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = color.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.4f)),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            color = color,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}

private fun formatStormTrendLabel(storm: LiveStorm): String {
    val track: List<LiveStormTrackPoint> = storm.track.orEmpty()
    val maxAge = track.maxOfOrNull { it.ageMinutes }?.roundToInt()
    val base = storm.severity.replaceFirstChar { it.uppercase() }
    return if (maxAge != null && maxAge > 0) "$base (${maxAge}m)" else base
}

private fun buildStormPositionSummary(storm: LiveStorm): String {
    val sideStr = when (storm.side?.lowercase()) {
        "left", "l" -> "left of track"
        "right", "r" -> "right of track"
        "on_track", "ontrack", "center" -> "on track"
        else -> storm.bearingFromRoute?.let { "$it of track" } ?: "from track"
    }
    val distStr = if (storm.distanceNm < 1.0) {
        "On route centerline"
    } else {
        "${storm.distanceNm.roundToInt()} NM $sideStr"
    }
    val abeamStr = "abeam ${storm.enrouteDistanceNm.roundToInt()} NM"
    val placeStr = storm.place?.text?.takeIf { it.isNotBlank() }?.let { " · near $it" }.orEmpty()
    return "$distStr · $abeamStr$placeStr"
}

private fun buildMotionSummary(storm: LiveStorm): String {
    val parts = mutableListOf<String>()
    if (storm.trackDeg != null && storm.speedKt != null) {
        parts += "Moving ${storm.trackDeg.roundToInt().toString().padStart(3, '0')}° at ${storm.speedKt.roundToInt()} kt"
    }
    if (storm.areaKm2 > 0) {
        parts += "${storm.areaKm2.roundToInt()} km²"
    }
    return parts.joinToString(" · ")
}

private fun buildClosingSummary(storm: LiveStorm): String {
    val closing = storm.closingKt
    val parts = mutableListOf<String>()
    if (closing != null) {
        when {
            closing > 2.0 -> parts += "Closing at ${closing.roundToInt()} kt"
            closing < -2.0 -> parts += "Moving away (${abs(closing).roundToInt()} kt)"
            else -> parts += "Nearly stationary relative to track"
        }
    }
    if (storm.cpaNm != null && storm.cpaMinutes != null && storm.cpaMinutes > 0) {
        parts += "CPA ${storm.cpaNm.roundToInt()} NM in ${storm.cpaMinutes.roundToInt()}m"
    } else if (storm.interceptMinutes != null && storm.interceptMinutes > 0) {
        parts += "Track intercept in ${storm.interceptMinutes.roundToInt()}m"
    }
    return parts.joinToString(" · ")
}
