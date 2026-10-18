package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveRibbon
import aero.flyfun.weather.model.LiveRibbonSegment
import aero.flyfun.weather.model.RibbonSigmet
import aero.flyfun.weather.model.RibbonStation
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.AssessmentUnavailable
import aero.flyfun.weather.ui.theme.flightCategoryColor
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

// NWS VIP radar reflectivity colors
private val Vip1Green = Color(0xFF22C55E)      // <25 dBZ light
private val Vip2Yellow = Color(0xFFEAB308)     // 25–35 dBZ moderate
private val Vip3Amber = Color(0xFFF97316)      // 35–42 dBZ heavy
private val Vip4Red = Color(0xFFEF4444)        // 42–50 dBZ very heavy
private val Vip5Magenta = Color(0xFFD946EF)    // >=50 dBZ extreme
private val LightningFlashAmber = Color(0xFFF59E0B)
private val AircraftMarkerCyan = Color(0xFF0284C7)

/**
 * Selected item in the interactive [RouteRibbonCanvas].
 */
sealed interface RibbonInspectionTarget {
    data class SegmentBin(val segment: LiveRibbonSegment) : RibbonInspectionTarget
    data class StationDot(val station: RibbonStation) : RibbonInspectionTarget
    data class SigmetBar(val sigmet: RibbonSigmet) : RibbonInspectionTarget
}

/**
 * Interactive multi-lane route weather ribbon Canvas (`Radar`, `Lightning`, `SIGMETs`, `Stations`)
 * with waypoint ticks, live aircraft progress marker, and tap-to-inspect detail card.
 *
 * Strictly distinguishes `unavailable`/`nodata` (neutral gray stipple) from `clear`/`none` (subtle green/clear).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RouteRibbonCanvas(
    ribbon: LiveRibbon,
    progress: FlightProgress?,
    waypoints: List<String>,
    onFocusMap: (LiveFocus?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val totalNm = max(ribbon.totalDistanceNm, 1.0)
    var selectedTarget by remember(ribbon) { mutableStateOf<RibbonInspectionTarget?>(null) }

    val textMeasurer = rememberTextMeasurer()
    val labelColor = MaterialTheme.colorScheme.onSurfaceVariant
    val trackBgColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f)
    val dividerColor = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f)
    val surfaceColor = MaterialTheme.colorScheme.surface

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // Multi-lane Canvas
        Surface(
            shape = RoundedCornerShape(10.dp),
            color = surfaceColor,
            border = BorderStroke(1.dp, dividerColor),
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(142.dp)
                    .padding(horizontal = 8.dp, vertical = 6.dp)
                    .pointerInput(ribbon, totalNm) {
                        detectTapGestures { tapOffset ->
                            val leftLabelWidth = 58.dp.toPx()
                            val rightPad = 8.dp.toPx()
                            val trackWidth = (size.width - leftLabelWidth - rightPad).coerceAtLeast(1f)
                            if (tapOffset.x < leftLabelWidth) return@detectTapGestures

                            val frac = ((tapOffset.x - leftLabelWidth) / trackWidth).coerceIn(0f, 1f)
                            val tappedNm = frac * totalNm

                            // Determine lane by Y coordinate
                            val headerH = 22.dp.toPx()
                            val laneH = 24.dp.toPx()
                            val relY = tapOffset.y - headerH

                            val target: RibbonInspectionTarget? = when {
                                // Lane 2: SIGMETs (relY in 2*laneH..3*laneH)
                                relY in (laneH * 2f)..(laneH * 3f) && ribbon.sigmets.isNotEmpty() -> {
                                    ribbon.sigmets.firstOrNull { sig ->
                                        val s = sig.startNm ?: 0.0
                                        val e = sig.endNm ?: totalNm
                                        tappedNm in (s - totalNm * 0.03)..(e + totalNm * 0.03)
                                    }?.let { RibbonInspectionTarget.SigmetBar(it) }
                                }
                                // Lane 3: Stations (relY in 3*laneH..4*laneH)
                                relY >= laneH * 3f && ribbon.stations.isNotEmpty() -> {
                                    ribbon.stations.minByOrNull { st ->
                                        abs(st.enrouteDistanceNm - tappedNm)
                                    }?.takeIf { st ->
                                        abs(st.enrouteDistanceNm - tappedNm) <= totalNm * 0.08
                                    }?.let { RibbonInspectionTarget.StationDot(it) }
                                }
                                else -> null
                            } ?: run {
                                // Default to nearest segment bin or station
                                ribbon.segments.firstOrNull { seg ->
                                    tappedNm in seg.startNm..seg.endNm
                                }?.let { RibbonInspectionTarget.SegmentBin(it) }
                            }

                            selectedTarget = if (selectedTarget == target) null else target
                        }
                    },
            ) {
                val leftColWidth = 58.dp.toPx()
                val rightPad = 8.dp.toPx()
                val trackLeft = leftColWidth
                val trackRight = (size.width - rightPad).coerceAtLeast(trackLeft + 10f)
                val trackWidth = trackRight - trackLeft

                val headerHeight = 22.dp.toPx()
                val laneHeight = 24.dp.toPx()

                // 1. Waypoint header ticks & labels
                drawWaypointHeader(
                    ribbon = ribbon,
                    fallbackWaypoints = waypoints,
                    totalNm = totalNm,
                    trackLeft = trackLeft,
                    trackWidth = trackWidth,
                    headerHeight = headerHeight,
                    labelColor = labelColor,
                    dividerColor = dividerColor,
                    textMeasurer = textMeasurer,
                )

                // Lane 0: Radar VIP
                val radarY = headerHeight + laneHeight * 0.5f
                drawLaneLabel("Radar", radarY, labelColor, textMeasurer)
                drawRadarLane(
                    segments = ribbon.segments,
                    totalNm = totalNm,
                    trackLeft = trackLeft,
                    trackWidth = trackWidth,
                    centerY = radarY,
                    barHeight = 12.dp.toPx(),
                    trackBgColor = trackBgColor,
                )

                // Lane 1: Lightning
                val ltgY = headerHeight + laneHeight * 1.5f
                drawLaneLabel("Ltg ⚡", ltgY, labelColor, textMeasurer)
                drawLightningLane(
                    segments = ribbon.segments,
                    totalNm = totalNm,
                    trackLeft = trackLeft,
                    trackWidth = trackWidth,
                    centerY = ltgY,
                    barHeight = 10.dp.toPx(),
                    trackBgColor = trackBgColor,
                )

                // Lane 2: SIGMETs
                val sigmetY = headerHeight + laneHeight * 2.5f
                drawLaneLabel("SIGMET", sigmetY, labelColor, textMeasurer)
                drawSigmetLane(
                    sigmets = ribbon.sigmets,
                    totalNm = totalNm,
                    trackLeft = trackLeft,
                    trackWidth = trackWidth,
                    centerY = sigmetY,
                    barHeight = 11.dp.toPx(),
                    trackBgColor = trackBgColor,
                    textMeasurer = textMeasurer,
                )

                // Lane 3: Stations (METAR flight category dots)
                val stationsY = headerHeight + laneHeight * 3.5f
                drawLaneLabel("METAR", stationsY, labelColor, textMeasurer)
                drawStationsLane(
                    stations = ribbon.stations,
                    totalNm = totalNm,
                    trackLeft = trackLeft,
                    trackWidth = trackWidth,
                    centerY = stationsY,
                    trackBgColor = trackBgColor,
                    surfaceColor = surfaceColor,
                )

                // Live aircraft progress marker vertical line & triangle
                val flownFraction = progress?.flownFraction?.toFloat()?.coerceIn(0f, 1f)
                if (flownFraction != null && (progress.isTrackingActive || flownFraction > 0f)) {
                    val progressX = trackLeft + trackWidth * flownFraction
                    drawLine(
                        color = AircraftMarkerCyan,
                        start = Offset(progressX, headerHeight - 2.dp.toPx()),
                        end = Offset(progressX, size.height - 2.dp.toPx()),
                        strokeWidth = 2.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 3.dp.toPx()), 0f),
                    )
                    val triPath = Path().apply {
                        moveTo(progressX, headerHeight + 3.dp.toPx())
                        lineTo(progressX - 5.dp.toPx(), headerHeight - 4.dp.toPx())
                        lineTo(progressX + 5.dp.toPx(), headerHeight - 4.dp.toPx())
                        close()
                    }
                    drawPath(triPath, color = AircraftMarkerCyan)
                }
            }
        }

        // Compact legend row
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            RibbonLegendChip(color = Vip1Green, label = "<25 dBZ")
            RibbonLegendChip(color = Vip2Yellow, label = "25–35")
            RibbonLegendChip(color = Vip3Amber, label = "35–42")
            RibbonLegendChip(color = Vip4Red, label = "42+ dBZ")
            RibbonLegendChip(color = AssessmentUnavailable, label = "No data")
            Text(
                text = "Tap ribbon to inspect",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // Tap-to-inspect detail card below the ribbon
        val currentSelection = selectedTarget
        if (currentSelection != null) {
            RibbonInspectionCard(
                target = currentSelection,
                onDismiss = { selectedTarget = null },
                onViewOnMap = { focus -> onFocusMap(focus) },
            )
        }
    }
}

private fun DrawScope.drawLaneLabel(
    label: String,
    centerY: Float,
    color: Color,
    textMeasurer: TextMeasurer,
) {
    val style = TextStyle(
        color = color,
        fontSize = 10.sp,
        fontWeight = FontWeight.Medium,
    )
    val measured = textMeasurer.measure(label, style)
    drawText(
        textLayoutResult = measured,
        topLeft = Offset(0f, centerY - measured.size.height / 2f),
    )
}

private fun DrawScope.drawWaypointHeader(
    ribbon: LiveRibbon,
    fallbackWaypoints: List<String>,
    totalNm: Double,
    trackLeft: Float,
    trackWidth: Float,
    headerHeight: Float,
    labelColor: Color,
    dividerColor: Color,
    textMeasurer: TextMeasurer,
) {
    val points: List<Pair<String, Double>> = if (ribbon.waypoints.isNotEmpty()) {
        ribbon.waypoints.map { it.icao to it.distanceNm }
    } else if (fallbackWaypoints.isNotEmpty()) {
        val count = fallbackWaypoints.size
        fallbackWaypoints.mapIndexed { idx, icao ->
            val dist = if (count > 1) totalNm * idx / (count - 1) else 0.0
            icao to dist
        }
    } else {
        emptyList()
    }

    val labelStyle = TextStyle(
        color = labelColor,
        fontSize = 9.5.sp,
        fontWeight = FontWeight.Bold,
        fontFamily = FontFamily.Monospace,
    )

    var lastLabelRight = -100f
    points.forEachIndexed { idx, (icao, distNm) ->
        val frac = (distNm / totalNm).toFloat().coerceIn(0f, 1f)
        val x = trackLeft + trackWidth * frac

        // Subtle vertical guide line across lanes
        drawLine(
            color = dividerColor,
            start = Offset(x, headerHeight - 3.dp.toPx()),
            end = Offset(x, size.height),
            strokeWidth = 1.dp.toPx(),
        )

        val measured = textMeasurer.measure(icao, labelStyle)
        val idealLeft = when (idx) {
            0 -> x
            points.lastIndex -> x - measured.size.width
            else -> x - measured.size.width / 2f
        }
        if (idealLeft > lastLabelRight + 4.dp.toPx()) {
            drawText(
                textLayoutResult = measured,
                topLeft = Offset(idealLeft, 2.dp.toPx()),
            )
            lastLabelRight = idealLeft + measured.size.width
        }
    }
}

private fun DrawScope.drawRadarLane(
    segments: List<LiveRibbonSegment>,
    totalNm: Double,
    trackLeft: Float,
    trackWidth: Float,
    centerY: Float,
    barHeight: Float,
    trackBgColor: Color,
) {
    val top = centerY - barHeight / 2f
    drawRoundRect(
        color = trackBgColor,
        topLeft = Offset(trackLeft, top),
        size = Size(trackWidth, barHeight),
        cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
    )

    segments.forEach { seg ->
        val startFrac = (seg.startNm / totalNm).toFloat().coerceIn(0f, 1f)
        val endFrac = (seg.endNm / totalNm).toFloat().coerceIn(startFrac, 1f)
        val segLeft = trackLeft + trackWidth * startFrac
        val segWidth = max((endFrac - startFrac) * trackWidth - 1.dp.toPx(), 2f)

        val color = radarSegmentColor(seg)
        drawRoundRect(
            color = color,
            topLeft = Offset(segLeft, top),
            size = Size(segWidth, barHeight),
            cornerRadius = CornerRadius(2.dp.toPx(), 2.dp.toPx()),
        )
    }
}

private fun DrawScope.drawLightningLane(
    segments: List<LiveRibbonSegment>,
    totalNm: Double,
    trackLeft: Float,
    trackWidth: Float,
    centerY: Float,
    barHeight: Float,
    trackBgColor: Color,
) {
    val top = centerY - barHeight / 2f
    drawRoundRect(
        color = trackBgColor,
        topLeft = Offset(trackLeft, top),
        size = Size(trackWidth, barHeight),
        cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
    )

    segments.forEach { seg ->
        val startFrac = (seg.startNm / totalNm).toFloat().coerceIn(0f, 1f)
        val endFrac = (seg.endNm / totalNm).toFloat().coerceIn(startFrac, 1f)
        val segLeft = trackLeft + trackWidth * startFrac
        val segWidth = max((endFrac - startFrac) * trackWidth - 1.dp.toPx(), 2f)

        val status = seg.lightningStatus.lowercase()
        val color = when {
            status == "unavailable" || status == "nodata" -> AssessmentUnavailable.copy(alpha = 0.45f)
            seg.flashCount > 0 || status == "active" || status == "direct" -> {
                val nearest = seg.nearestFlashNm ?: 10.0
                if (nearest <= 10.0) AssessmentRed else LightningFlashAmber
            }
            status == "nearby" || status == "vicinity" -> LightningFlashAmber.copy(alpha = 0.65f)
            else -> AssessmentGreen.copy(alpha = 0.18f)
        }
        drawRoundRect(
            color = color,
            topLeft = Offset(segLeft, top),
            size = Size(segWidth, barHeight),
            cornerRadius = CornerRadius(2.dp.toPx(), 2.dp.toPx()),
        )
    }
}

private fun DrawScope.drawSigmetLane(
    sigmets: List<RibbonSigmet>,
    totalNm: Double,
    trackLeft: Float,
    trackWidth: Float,
    centerY: Float,
    barHeight: Float,
    trackBgColor: Color,
    textMeasurer: TextMeasurer,
) {
    val top = centerY - barHeight / 2f
    drawRoundRect(
        color = trackBgColor,
        topLeft = Offset(trackLeft, top),
        size = Size(trackWidth, barHeight),
        cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
    )

    if (sigmets.isEmpty()) {
        // Subtle green clear bar when SIGMET feed was checked and no active SIGMETs intersect route
        drawRoundRect(
            color = AssessmentGreen.copy(alpha = 0.18f),
            topLeft = Offset(trackLeft, top),
            size = Size(trackWidth, barHeight),
            cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
        )
        return
    }

    sigmets.forEach { sig ->
        val startFrac = ((sig.startNm ?: 0.0) / totalNm).toFloat().coerceIn(0f, 1f)
        val endFrac = ((sig.endNm ?: totalNm) / totalNm).toFloat().coerceIn(startFrac, 1f)
        val spanLeft = trackLeft + trackWidth * startFrac
        val spanWidth = max((endFrac - startFrac) * trackWidth, 14.dp.toPx())
        val sigColor = sigmetHazardColor(sig.hazard)

        drawRoundRect(
            color = sigColor.copy(alpha = 0.85f),
            topLeft = Offset(spanLeft, top),
            size = Size(spanWidth.coerceAtMost(trackLeft + trackWidth - spanLeft), barHeight),
            cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx()),
        )

        if (spanWidth >= 26.dp.toPx()) {
            val tagStyle = TextStyle(
                color = Color.White,
                fontSize = 8.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
            )
            val measured = textMeasurer.measure(sig.hazard.uppercase(), tagStyle)
            if (measured.size.width + 4.dp.toPx() <= spanWidth) {
                drawText(
                    textLayoutResult = measured,
                    topLeft = Offset(
                        x = spanLeft + (spanWidth - measured.size.width) / 2f,
                        y = centerY - measured.size.height / 2f,
                    ),
                )
            }
        }
    }
}

private fun DrawScope.drawStationsLane(
    stations: List<RibbonStation>,
    totalNm: Double,
    trackLeft: Float,
    trackWidth: Float,
    centerY: Float,
    trackBgColor: Color,
    surfaceColor: Color,
) {
    // Baseline spine
    drawLine(
        color = trackBgColor,
        start = Offset(trackLeft, centerY),
        end = Offset(trackLeft + trackWidth, centerY),
        strokeWidth = 3.dp.toPx(),
        cap = StrokeCap.Round,
    )

    stations.forEach { st ->
        val frac = (st.enrouteDistanceNm / totalNm).toFloat().coerceIn(0f, 1f)
        val x = trackLeft + trackWidth * frac
        val catColor = flightCategoryColor(st.metarCategory)
        val isEndpoint = st.role == "departure" || st.role == "destination" || st.role == "arrival"
        val radius = if (isEndpoint) 6.dp.toPx() else 4.8.dp.toPx()

        // Outer halo
        drawCircle(
            color = surfaceColor,
            radius = radius + 1.5.dp.toPx(),
            center = Offset(x, centerY),
        )
        // Category dot
        drawCircle(
            color = catColor,
            radius = radius,
            center = Offset(x, centerY),
        )
        // Highlight ring when station has a recent change
        if (st.hasChange) {
            val ringColor = if (st.changeDirection == "worse") AssessmentRed else AssessmentGreen
            drawCircle(
                color = ringColor,
                radius = radius + 2.5.dp.toPx(),
                center = Offset(x, centerY),
                style = Stroke(width = 1.5.dp.toPx()),
            )
        }
    }
}

@Composable
private fun RibbonInspectionCard(
    target: RibbonInspectionTarget,
    onDismiss: () -> Unit,
    onViewOnMap: (LiveFocus?) -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            val (title, subtitle, focus) = when (target) {
                is RibbonInspectionTarget.SegmentBin -> {
                    val seg = target.segment
                    val rangeStr = "${seg.startNm.roundToInt()}–${seg.endNm.roundToInt()} NM"
                    val placeStr = seg.place?.text?.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()
                    val radarPart = when (seg.radarStatus.lowercase()) {
                        "unavailable", "nodata" -> "Radar unavailable"
                        "none", "clear" -> "No precipitation echoes"
                        else -> {
                            val dbz = seg.radarMaxDbz?.roundToInt()?.let { "$it dBZ" } ?: seg.radarStatus
                            val tops = seg.topsMaxFt?.let { " · Tops FL${(it / 100.0).roundToInt().toString().padStart(3, '0')}" }.orEmpty()
                            "Radar $dbz$tops"
                        }
                    }
                    val ltgPart = when {
                        seg.lightningStatus.equals("unavailable", ignoreCase = true) -> "Lightning unavailable"
                        seg.flashCount > 0 -> {
                            val near = seg.nearestFlashNm?.roundToInt()?.let { " (nearest $it NM)" }.orEmpty()
                            "${seg.flashCount} lightning flashes$near"
                        }
                        else -> "No lightning"
                    }
                    Triple(
                        "Segment $rangeStr$placeStr",
                        "$radarPart · $ltgPart",
                        LiveFocus(
                            layer = "radar",
                            segmentIndex = seg.index,
                            distanceNm = (seg.startNm + seg.endNm) / 2.0,
                        ),
                    )
                }

                is RibbonInspectionTarget.StationDot -> {
                    val st = target.station
                    val cat = st.metarCategory ?: "UNKN"
                    val proxyStr = st.obsProxy?.let { " (via ${it.icao} · ${it.distanceNm.roundToInt()} NM)" }.orEmpty()
                    val ceilStr = st.metarCeilingFt?.let { "Ceil $it ft" } ?: "Ceil clear"
                    val visStr = st.metarVisibilityM?.let { "Vis ${formatVisibilityMeters(it)}" }.orEmpty()
                    val tafStr = st.tafCategory?.let { " · TAF $it" }.orEmpty()
                    Triple(
                        "${st.icao} ${st.name.orEmpty()} [$cat]$proxyStr".trim(),
                        listOfNotNull(
                            "${st.enrouteDistanceNm.roundToInt()} NM along route",
                            ceilStr,
                            visStr.takeIf { it.isNotEmpty() },
                        ).joinToString(" · ") + tafStr,
                        LiveFocus(
                            layer = "stations",
                            stationIcao = st.icao,
                            distanceNm = st.enrouteDistanceNm,
                        ),
                    )
                }

                is RibbonInspectionTarget.SigmetBar -> {
                    val sig = target.sigmet
                    val flBand = formatFlightLevelBand(sig.baseFt, sig.topFt)
                    val spanStr = if (sig.startNm != null && sig.endNm != null) {
                        "${sig.startNm.roundToInt()}–${sig.endNm.roundToInt()} NM"
                    } else {
                        "Along route"
                    }
                    Triple(
                        "SIGMET ${sig.firId} · ${sig.hazard} ${sig.qualifier.orEmpty()}".trim(),
                        "$flBand · $spanStr · Valid to ${formatUtcTimestampShort(sig.validTo)}",
                        LiveFocus(
                            layer = "sigmets",
                            sigmetKey = sig.key,
                            distanceNm = sig.startNm,
                        ),
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = onDismiss,
                    modifier = Modifier.size(22.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "Close inspection",
                        modifier = Modifier.size(14.dp),
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                TextButton(
                    onClick = { onViewOnMap(focus) },
                ) {
                    Text(
                        text = "View on Map ›",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
    }
}

@Composable
private fun RibbonLegendChip(
    color: Color,
    label: String,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(color, CircleShape),
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * Maps a [LiveRibbonSegment] to its NWS VIP reflectivity color, keeping `unavailable`/`nodata`
 * distinct from `none`/`clear`.
 */
internal fun radarSegmentColor(seg: LiveRibbonSegment): Color {
    val status = seg.radarStatus.lowercase()
    if (status == "unavailable" || status == "nodata" || status == "unknown") {
        return AssessmentUnavailable.copy(alpha = 0.45f)
    }
    val dbz = seg.radarMaxDbz
    if (status == "none" || status == "clear" || (dbz != null && dbz < 15.0)) {
        return AssessmentGreen.copy(alpha = 0.20f)
    }
    if (dbz != null) {
        return when {
            dbz >= 50.0 -> Vip5Magenta
            dbz >= 42.0 -> Vip4Red
            dbz >= 35.0 -> Vip3Amber
            dbz >= 25.0 -> Vip2Yellow
            else -> Vip1Green
        }
    }
    return when (status) {
        "extreme" -> Vip5Magenta
        "heavy", "intense", "severe" -> Vip4Red
        "moderate_heavy" -> Vip3Amber
        "moderate" -> Vip2Yellow
        "light", "weak" -> Vip1Green
        else -> AssessmentGreen.copy(alpha = 0.20f)
    }
}

internal fun sigmetHazardColor(hazard: String): Color = when (hazard.uppercase()) {
    "TS", "TSGR", "CB", "SQL" -> AssessmentRed
    "ICE", "SEV ICE", "TURB", "SEV TURB", "MTW" -> AssessmentAmber
    else -> AssessmentAmber
}

internal fun formatFlightLevelBand(baseFt: Int?, topFt: Int?): String {
    val baseStr = baseFt?.let {
        if (it <= 0) "SFC" else "FL${(it / 100.0).roundToInt().toString().padStart(3, '0')}"
    } ?: "SFC"
    val topStr = topFt?.let {
        "FL${(it / 100.0).roundToInt().toString().padStart(3, '0')}"
    } ?: "UNL"
    return "$baseStr–$topStr"
}

internal fun formatVisibilityMeters(visMeters: Int): String = when {
    visMeters >= 9999 -> "10+ km"
    visMeters >= 1000 -> String.format("%.1f km", visMeters / 1000.0)
    else -> "$visMeters m"
}
