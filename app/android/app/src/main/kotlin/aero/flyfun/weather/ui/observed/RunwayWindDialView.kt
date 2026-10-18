package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.EndComponents
import aero.flyfun.weather.model.RunwayEnd
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
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
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

private val MetarWindCyan = Color(0xFF0284C7)
private val MetarGustPurple = Color(0xFF7C3AED)
private val TafWindAmber = Color(0xFFD97706)
private val RunwayAsphalt = Color(0xFF1E293B)
private val RunwaySecondaryAsphalt = Color(0xFF334155)
private val RunwayMarkingWhite = Color(0xFFF8FAFC)

/**
 * Physical runway paired from opposite [RunwayEnd] entries (e.g. 04 / 22).
 */
internal data class PhysicalRunwayPair(
    val primary: RunwayEnd,
    val reciprocal: RunwayEnd?,
) {
    val key: String = if (reciprocal != null) "${primary.id}/${reciprocal.id}" else primary.id
}

/**
 * Evaluated wind components for a single [RunwayEnd].
 */
internal data class EvaluatedRunwayEnd(
    val end: RunwayEnd,
    val metar: EndComponents?,
    val gust: EndComponents?,
    val taf: EndComponents?,
    val isFavored: Boolean,
)

/**
 * Cockpit-grade Jetpack Compose Canvas runway & wind dial with favored runway computation,
 * headwind/crosswind component pills, and full runway ends breakdown table.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RunwayWindDialView(
    icao: String,
    runwayEnds: List<RunwayEnd>,
    windDirDeg: Int?,
    windSpeedKt: Int?,
    windGustKt: Int? = null,
    tafWindDirDeg: Int? = null,
    tafWindSpeedKt: Int? = null,
    modifier: Modifier = Modifier,
    tafWindGustKt: Int? = null,
    dialSize: Dp = 210.dp,
) {
    val physicalPairs = remember(runwayEnds) { pairRunwayEnds(runwayEnds) }
    val favoredEnd = remember(runwayEnds, windDirDeg, windSpeedKt, tafWindDirDeg, tafWindSpeedKt) {
        pickFavoredRunwayEnd(
            ends = runwayEnds,
            windDirDeg = windDirDeg ?: tafWindDirDeg,
            windSpeedKt = windSpeedKt ?: tafWindSpeedKt,
        )
    }

    val evaluatedEnds = remember(
        runwayEnds,
        favoredEnd,
        windDirDeg,
        windSpeedKt,
        windGustKt,
        tafWindDirDeg,
        tafWindSpeedKt,
    ) {
        runwayEnds.map { end ->
            val metarComp = if (windDirDeg != null && windSpeedKt != null && windSpeedKt > 0) {
                EndComponents.compute(windDirDeg.toDouble(), windSpeedKt.toDouble(), end.headingDeg)
            } else {
                null
            }
            val gustComp = if (windDirDeg != null && windGustKt != null && windGustKt > 0) {
                EndComponents.compute(windDirDeg.toDouble(), windGustKt.toDouble(), end.headingDeg)
            } else {
                null
            }
            val tafComp = if (tafWindDirDeg != null && tafWindSpeedKt != null && tafWindSpeedKt > 0) {
                EndComponents.compute(tafWindDirDeg.toDouble(), tafWindSpeedKt.toDouble(), end.headingDeg)
            } else {
                null
            }
            EvaluatedRunwayEnd(
                end = end,
                metar = metarComp,
                gust = gustComp,
                taf = tafComp,
                isFavored = end.id == favoredEnd?.id,
            )
        }
    }

    val favoredEval = evaluatedEnds.firstOrNull { it.isFavored } ?: evaluatedEnds.firstOrNull()
    val activeComponents = favoredEval?.metar ?: favoredEval?.taf

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Header row: ICAO + wind readouts
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "$icao Runway & Wind",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    if (favoredEnd != null) {
                        Surface(
                            shape = RoundedCornerShape(6.dp),
                            color = AssessmentGreen.copy(alpha = 0.16f),
                            border = BorderStroke(1.dp, AssessmentGreen.copy(alpha = 0.45f)),
                        ) {
                            Text(
                                text = "RWY ${favoredEnd.id} favored",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = AssessmentGreen,
                                fontFamily = FontFamily.Monospace,
                                modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                            )
                        }
                    }
                }

                // Legend dots
                Row(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (windDirDeg != null && windSpeedKt != null) {
                        WindLegendItem(
                            color = MetarWindCyan,
                            label = formatWindShort("METAR", windDirDeg, windSpeedKt, windGustKt),
                        )
                    }
                    if (tafWindDirDeg != null && tafWindSpeedKt != null) {
                        WindLegendItem(
                            color = TafWindAmber,
                            label = formatWindShort("TAF", tafWindDirDeg, tafWindSpeedKt, tafWindGustKt),
                            isDashed = true,
                        )
                    }
                }
            }

            // Cockpit compass & runway Canvas dial
            Box(
                modifier = Modifier.fillMaxWidth(),
                contentAlignment = Alignment.Center,
            ) {
                RunwayCompassCanvas(
                    physicalPairs = physicalPairs,
                    favoredEnd = favoredEnd,
                    windDirDeg = windDirDeg,
                    windSpeedKt = windSpeedKt,
                    windGustKt = windGustKt,
                    tafWindDirDeg = tafWindDirDeg,
                    tafWindSpeedKt = tafWindSpeedKt,
                    dialSize = dialSize,
                )
            }

            // Favored runway headwind/tailwind & signed crosswind pills
            if (favoredEnd != null && activeComponents != null) {
                val isHeadwind = activeComponents.headwindKt >= 0.0
                val hwKt = abs(activeComponents.headwindKt).roundToInt()
                val hwLabel = if (isHeadwind) "$hwKt kt HW" else "$hwKt kt TW"
                val hwColor = if (isHeadwind) AssessmentGreen else AssessmentAmber

                val xwKt = activeComponents.crosswindKt.roundToInt()
                val xwSide = when {
                    xwKt == 0 -> ""
                    activeComponents.crosswindSignedKt > 0 -> " from right"
                    else -> " from left"
                }
                val xwLabel = "$xwKt kt XW$xwSide"
                val xwColor = crosswindSeverityColor(activeComponents.crosswindKt)

                FlowRow(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    ComponentPill(
                        text = "RWY ${favoredEnd.id} (${favoredEnd.headingDeg.roundToInt().toString().padStart(3, '0')}°)",
                        color = MaterialTheme.colorScheme.primary,
                    )
                    ComponentPill(
                        text = hwLabel,
                        color = hwColor,
                    )
                    ComponentPill(
                        text = xwLabel,
                        color = xwColor,
                    )
                    if (favoredEval?.gust != null && windGustKt != null && windSpeedKt != null && windGustKt > windSpeedKt) {
                        val gustXw = favoredEval.gust.crosswindKt.roundToInt()
                        ComponentPill(
                            text = "Gust XW $gustXw kt",
                            color = MetarGustPurple,
                        )
                    }
                }
            } else if (runwayEnds.isEmpty()) {
                Text(
                    text = "Runway geometry not available for $icao",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.align(Alignment.CenterHorizontally),
                )
            }

            // All Runway Ends Table
            if (evaluatedEnds.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                RunwayEndsTable(
                    evaluatedEnds = evaluatedEnds,
                    showTafColumn = tafWindDirDeg != null && tafWindSpeedKt != null,
                )
            }
        }
    }
}

@Composable
private fun RunwayCompassCanvas(
    physicalPairs: List<PhysicalRunwayPair>,
    favoredEnd: RunwayEnd?,
    windDirDeg: Int?,
    windSpeedKt: Int?,
    windGustKt: Int?,
    tafWindDirDeg: Int?,
    tafWindSpeedKt: Int?,
    dialSize: Dp,
) {
    val textMeasurer = rememberTextMeasurer()
    val outlineColor = MaterialTheme.colorScheme.outline
    val onSurfaceColor = MaterialTheme.colorScheme.onSurface
    val onSurfaceVariantColor = MaterialTheme.colorScheme.onSurfaceVariant
    val dialBackground = MaterialTheme.colorScheme.surface

    Canvas(modifier = Modifier.size(dialSize)) {
        val center = Offset(size.width / 2f, size.height / 2f)
        val outerRadius = (size.minDimension / 2f) - 6.dp.toPx()

        // 1. Dial background & outer bezel
        drawCircle(
            color = dialBackground,
            radius = outerRadius,
            center = center,
        )
        drawCircle(
            color = outlineColor.copy(alpha = 0.45f),
            radius = outerRadius,
            center = center,
            style = Stroke(width = 1.5.dp.toPx()),
        )

        // 2. Gust sector / arc around rim when gusts are reported
        if (windDirDeg != null && windSpeedKt != null && windGustKt != null && windGustKt > windSpeedKt) {
            val gustSpreadDeg = 18f
            // Canvas drawArc 0 deg is 3 o'clock; compass 0 deg is 12 o'clock (-90 deg)
            val startAngle = windDirDeg.toFloat() - 90f - gustSpreadDeg
            drawArc(
                color = MetarGustPurple.copy(alpha = 0.35f),
                startAngle = startAngle,
                sweepAngle = gustSpreadDeg * 2f,
                useCenter = false,
                topLeft = Offset(center.x - outerRadius + 3.dp.toPx(), center.y - outerRadius + 3.dp.toPx()),
                size = Size((outerRadius - 3.dp.toPx()) * 2f, (outerRadius - 3.dp.toPx()) * 2f),
                style = Stroke(width = 5.dp.toPx(), cap = StrokeCap.Round),
            )
        }

        // 3. Compass ticks every 10° and cardinal/30° labels
        drawCompassRose(
            center = center,
            outerRadius = outerRadius,
            tickColor = onSurfaceVariantColor,
            cardinalColor = onSurfaceColor,
            textMeasurer = textMeasurer,
        )

        // 4. Physical runways (draw secondary first, favored pair on top)
        val sortedPairs = physicalPairs.sortedBy { pair ->
            pair.primary.id == favoredEnd?.id || pair.reciprocal?.id == favoredEnd?.id
        }
        sortedPairs.forEach { pair ->
            val isFavoredPair = pair.primary.id == favoredEnd?.id || pair.reciprocal?.id == favoredEnd?.id
            drawPhysicalRunway(
                center = center,
                outerRadius = outerRadius,
                pair = pair,
                favoredEndId = favoredEnd?.id,
                isFavoredPair = isFavoredPair,
                textMeasurer = textMeasurer,
            )
        }

        // 5. Optional TAF wind vector (amber dashed arrow)
        if (tafWindDirDeg != null && tafWindSpeedKt != null && tafWindSpeedKt > 0) {
            drawWindVector(
                center = center,
                outerRadius = outerRadius,
                windDirDeg = tafWindDirDeg.toDouble(),
                windSpeedKt = tafWindSpeedKt,
                windGustKt = null,
                color = TafWindAmber,
                isDashed = true,
            )
        }

        // 6. METAR wind vector (bold cyan solid arrow + gust ring on shaft)
        if (windDirDeg != null && windSpeedKt != null && windSpeedKt > 0) {
            drawWindVector(
                center = center,
                outerRadius = outerRadius,
                windDirDeg = windDirDeg.toDouble(),
                windSpeedKt = windSpeedKt,
                windGustKt = windGustKt,
                color = MetarWindCyan,
                isDashed = false,
            )
        } else if (windSpeedKt == 0) {
            // Calm wind indicator ring in center
            drawCircle(
                color = MetarWindCyan.copy(alpha = 0.6f),
                radius = 12.dp.toPx(),
                center = center,
                style = Stroke(width = 2.dp.toPx()),
            )
        }
    }
}

private fun DrawScope.drawCompassRose(
    center: Offset,
    outerRadius: Float,
    tickColor: Color,
    cardinalColor: Color,
    textMeasurer: TextMeasurer,
) {
    for (deg in 0 until 360 step 10) {
        val isCardinal = deg % 90 == 0
        val isMajor = deg % 30 == 0
        val tickLength = when {
            isCardinal -> 10.dp.toPx()
            isMajor -> 7.dp.toPx()
            else -> 4.dp.toPx()
        }
        val strokeWidth = when {
            isCardinal -> 2.dp.toPx()
            isMajor -> 1.3.dp.toPx()
            else -> 0.8.dp.toPx()
        }
        val alpha = when {
            isCardinal -> 0.85f
            isMajor -> 0.6f
            else -> 0.3f
        }

        val rad = (deg - 90) * PI / 180.0
        val cosA = cos(rad).toFloat()
        val sinA = sin(rad).toFloat()

        val start = Offset(
            x = center.x + (outerRadius - tickLength) * cosA,
            y = center.y + (outerRadius - tickLength) * sinA,
        )
        val end = Offset(
            x = center.x + outerRadius * cosA,
            y = center.y + outerRadius * sinA,
        )

        drawLine(
            color = tickColor.copy(alpha = alpha),
            start = start,
            end = end,
            strokeWidth = strokeWidth,
            cap = StrokeCap.Round,
        )

        if (isMajor) {
            val label = when (deg) {
                0 -> "N"
                90 -> "E"
                180 -> "S"
                270 -> "W"
                else -> (deg / 10).toString().padStart(2, '0')
            }
            val labelStyle = TextStyle(
                color = if (isCardinal) cardinalColor else tickColor.copy(alpha = 0.8f),
                fontSize = if (isCardinal) 11.sp else 9.sp,
                fontWeight = if (isCardinal) FontWeight.ExtraBold else FontWeight.Medium,
                fontFamily = FontFamily.Monospace,
            )
            val measured = textMeasurer.measure(label, labelStyle)
            val labelRadius = outerRadius - tickLength - 9.dp.toPx()
            val labelCenter = Offset(
                x = center.x + labelRadius * cosA - measured.size.width / 2f,
                y = center.y + labelRadius * sinA - measured.size.height / 2f,
            )
            drawText(measured, topLeft = labelCenter)
        }
    }
}

private fun DrawScope.drawPhysicalRunway(
    center: Offset,
    outerRadius: Float,
    pair: PhysicalRunwayPair,
    favoredEndId: String?,
    isFavoredPair: Boolean,
    textMeasurer: TextMeasurer,
) {
    // Runway strip is aligned with primary.headingDeg.
    // The threshold of `primary` sits at the opposite end (headingDeg + 180) so an aircraft
    // landing on `primary` flies along `primary.headingDeg` from bottom of strip toward top.
    val stripHalfLength = outerRadius * if (isFavoredPair) 0.56f else 0.48f
    val stripWidth = if (isFavoredPair) 22.dp.toPx() else 15.dp.toPx()

    withTransform({
        rotate(degrees = pair.primary.headingDeg.toFloat(), pivot = center)
    }) {
        val topLeft = Offset(center.x - stripWidth / 2f, center.y - stripHalfLength)
        val stripSize = Size(stripWidth, stripHalfLength * 2f)

        // Asphalt body
        drawRoundRect(
            color = if (isFavoredPair) RunwayAsphalt else RunwaySecondaryAsphalt.copy(alpha = 0.72f),
            topLeft = topLeft,
            size = stripSize,
            cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx()),
        )

        // Subtle border for favored runway
        if (isFavoredPair) {
            drawRoundRect(
                color = RunwayMarkingWhite.copy(alpha = 0.55f),
                topLeft = topLeft,
                size = stripSize,
                cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx()),
                style = Stroke(width = 1.dp.toPx()),
            )
        }

        // Centerline dashes
        val dashBoundary = stripHalfLength * 0.48f
        drawLine(
            color = RunwayMarkingWhite.copy(alpha = if (isFavoredPair) 0.75f else 0.4f),
            start = Offset(center.x, center.y - dashBoundary),
            end = Offset(center.x, center.y + dashBoundary),
            strokeWidth = 1.2.dp.toPx(),
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 5.dp.toPx()), 0f),
        )
    }

    // Draw threshold designator badges at both ends in upright screen coordinates
    drawRunwayThresholdBadge(
        center = center,
        stripHalfLength = stripHalfLength,
        headingToThresholdDeg = pair.primary.headingDeg + 180.0,
        designator = pair.primary.id,
        isFavoredEnd = pair.primary.id == favoredEndId,
        isFavoredPair = isFavoredPair,
        textMeasurer = textMeasurer,
    )

    if (pair.reciprocal != null) {
        drawRunwayThresholdBadge(
            center = center,
            stripHalfLength = stripHalfLength,
            headingToThresholdDeg = pair.primary.headingDeg,
            designator = pair.reciprocal.id,
            isFavoredEnd = pair.reciprocal.id == favoredEndId,
            isFavoredPair = isFavoredPair,
            textMeasurer = textMeasurer,
        )
    }
}

private fun DrawScope.drawRunwayThresholdBadge(
    center: Offset,
    stripHalfLength: Float,
    headingToThresholdDeg: Double,
    designator: String,
    isFavoredEnd: Boolean,
    isFavoredPair: Boolean,
    textMeasurer: TextMeasurer,
) {
    val rad = (headingToThresholdDeg - 90.0) * PI / 180.0
    val badgeDist = stripHalfLength * 0.74f
    val pos = Offset(
        x = center.x + badgeDist * cos(rad).toFloat(),
        y = center.y + badgeDist * sin(rad).toFloat(),
    )

    val textStyle = TextStyle(
        color = if (isFavoredEnd) Color(0xFFECFDF5) else RunwayMarkingWhite.copy(alpha = if (isFavoredPair) 0.9f else 0.65f),
        fontSize = if (isFavoredEnd) 10.sp else 8.5.sp,
        fontWeight = if (isFavoredEnd) FontWeight.ExtraBold else FontWeight.SemiBold,
        fontFamily = FontFamily.Monospace,
    )
    val measured = textMeasurer.measure(designator, textStyle)

    if (isFavoredEnd) {
        val padX = 4.dp.toPx()
        val padY = 1.5.dp.toPx()
        drawRoundRect(
            color = AssessmentGreen.copy(alpha = 0.85f),
            topLeft = Offset(
                x = pos.x - measured.size.width / 2f - padX,
                y = pos.y - measured.size.height / 2f - padY,
            ),
            size = Size(
                width = measured.size.width + padX * 2f,
                height = measured.size.height + padY * 2f,
            ),
            cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx()),
        )
    }

    drawText(
        textLayoutResult = measured,
        topLeft = Offset(
            x = pos.x - measured.size.width / 2f,
            y = pos.y - measured.size.height / 2f,
        ),
    )
}

private fun DrawScope.drawWindVector(
    center: Offset,
    outerRadius: Float,
    windDirDeg: Double,
    windSpeedKt: Int,
    windGustKt: Int?,
    color: Color,
    isDashed: Boolean,
) {
    // Wind blows FROM windDirDeg toward the center of the dial
    val fromRad = (windDirDeg - 90.0) * PI / 180.0
    val unitFrom = Offset(cos(fromRad).toFloat(), sin(fromRad).toFloat())

    val rimPoint = Offset(
        x = center.x + (outerRadius - 14.dp.toPx()) * unitFrom.x,
        y = center.y + (outerRadius - 14.dp.toPx()) * unitFrom.y,
    )
    val tipPoint = Offset(
        x = center.x + 12.dp.toPx() * unitFrom.x,
        y = center.y + 12.dp.toPx() * unitFrom.y,
    )

    val pathEffect = if (isDashed) {
        PathEffect.dashPathEffect(floatArrayOf(7.dp.toPx(), 4.dp.toPx()), 0f)
    } else {
        null
    }

    // Shaft from rim toward center
    drawLine(
        color = color,
        start = rimPoint,
        end = tipPoint,
        strokeWidth = if (isDashed) 2.2.dp.toPx() else 3.2.dp.toPx(),
        cap = StrokeCap.Round,
        pathEffect = pathEffect,
    )

    // Origin dot at compass rim
    drawCircle(
        color = color,
        radius = if (isDashed) 3.5.dp.toPx() else 4.5.dp.toPx(),
        center = rimPoint,
    )

    // Gust chevron / collar on the shaft when gusts exceed sustained wind
    if (windGustKt != null && windGustKt > windSpeedKt) {
        val midPoint = Offset(
            x = (rimPoint.x + tipPoint.x) / 2f,
            y = (rimPoint.y + tipPoint.y) / 2f,
        )
        drawCircle(
            color = MetarGustPurple,
            radius = 4.dp.toPx(),
            center = midPoint,
        )
    }

    // Arrowhead at tipPoint pointing toward center (-unitFrom direction)
    val headLength = if (isDashed) 9.dp.toPx() else 11.dp.toPx()
    val headHalfWidth = if (isDashed) 5.dp.toPx() else 6.5.dp.toPx()
    val perp = Offset(-unitFrom.y, unitFrom.x)
    val baseCenter = Offset(
        x = tipPoint.x + headLength * unitFrom.x,
        y = tipPoint.y + headLength * unitFrom.y,
    )

    val arrowPath = Path().apply {
        moveTo(tipPoint.x, tipPoint.y)
        lineTo(baseCenter.x + headHalfWidth * perp.x, baseCenter.y + headHalfWidth * perp.y)
        lineTo(baseCenter.x - headHalfWidth * perp.x, baseCenter.y - headHalfWidth * perp.y)
        close()
    }
    drawPath(path = arrowPath, color = color)
}

@Composable
private fun RunwayEndsTable(
    evaluatedEnds: List<EvaluatedRunwayEnd>,
    showTafColumn: Boolean,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        // Table header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "RWY",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.width(56.dp),
            )
            Text(
                text = "HDG",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.width(48.dp),
            )
            Text(
                text = "METAR HW/TW",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = "METAR XW",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            if (showTafColumn) {
                Text(
                    text = "TAF @ ETA",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        evaluatedEnds.forEach { item ->
            val rowBg = if (item.isFavored) {
                AssessmentGreen.copy(alpha = 0.10f)
            } else {
                Color.Transparent
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(rowBg, RoundedCornerShape(6.dp))
                    .padding(horizontal = 6.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    modifier = Modifier.width(56.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = item.end.id,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = if (item.isFavored) FontWeight.Bold else FontWeight.Medium,
                        fontFamily = FontFamily.Monospace,
                    )
                    if (item.isFavored) {
                        Box(
                            modifier = Modifier
                                .size(6.dp)
                                .background(AssessmentGreen, CircleShape),
                        )
                    }
                }

                Text(
                    text = "${item.end.headingDeg.roundToInt().toString().padStart(3, '0')}°",
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(48.dp),
                )

                // METAR Headwind / Tailwind
                val metarHwText = item.metar?.let { comp ->
                    val kt = abs(comp.headwindKt).roundToInt()
                    if (comp.headwindKt >= 0) "$kt kt HW" else "$kt kt TW"
                } ?: "—"
                val metarHwColor = item.metar?.let { comp ->
                    if (comp.headwindKt >= 0) AssessmentGreen else AssessmentAmber
                } ?: MaterialTheme.colorScheme.onSurfaceVariant

                Text(
                    text = metarHwText,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                    color = metarHwColor,
                    modifier = Modifier.weight(1f),
                )

                // METAR Crosswind
                val metarXwText = item.metar?.let { comp ->
                    val kt = comp.crosswindKt.roundToInt()
                    val dir = when {
                        kt == 0 -> ""
                        comp.crosswindSignedKt > 0 -> " R"
                        else -> " L"
                    }
                    "$kt kt$dir"
                } ?: "—"
                val metarXwColor = item.metar?.let { comp ->
                    crosswindSeverityColor(comp.crosswindKt)
                } ?: MaterialTheme.colorScheme.onSurfaceVariant

                Text(
                    text = metarXwText,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                    color = metarXwColor,
                    modifier = Modifier.weight(1f),
                )

                if (showTafColumn) {
                    val tafText = item.taf?.let { comp ->
                        val hw = abs(comp.headwindKt).roundToInt()
                        val hwTag = if (comp.headwindKt >= 0) "HW" else "TW"
                        val xw = comp.crosswindKt.roundToInt()
                        "$hw$hwTag / ${xw}XW"
                    } ?: "—"
                    Text(
                        text = tafText,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = TafWindAmber,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

@Composable
private fun WindLegendItem(
    color: Color,
    label: String,
    isDashed: Boolean = false,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Box(
            modifier = Modifier
                .width(12.dp)
                .height(3.dp)
                .background(color, RoundedCornerShape(2.dp)),
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            fontFamily = FontFamily.Monospace,
            color = if (isDashed) color else MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
private fun ComponentPill(
    text: String,
    color: Color,
) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = color.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.4f)),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.SemiBold,
            fontFamily = FontFamily.Monospace,
            color = color,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
        )
    }
}

internal fun pairRunwayEnds(ends: List<RunwayEnd>): List<PhysicalRunwayPair> {
    if (ends.isEmpty()) return emptyList()
    val used = mutableSetOf<String>()
    val pairs = mutableListOf<PhysicalRunwayPair>()

    for (end in ends) {
        if (end.id in used) continue
        used += end.id
        val recip = ends.firstOrNull { candidate ->
            candidate.id !in used && angularDiffDeg(end.headingDeg + 180.0, candidate.headingDeg) < 25.0
        }
        if (recip != null) {
            used += recip.id
        }
        pairs += PhysicalRunwayPair(primary = end, reciprocal = recip)
    }
    return pairs
}

internal fun pickFavoredRunwayEnd(
    ends: List<RunwayEnd>,
    windDirDeg: Int?,
    windSpeedKt: Int?,
): RunwayEnd? {
    if (ends.isEmpty()) return null
    if (windDirDeg == null || windSpeedKt == null || windSpeedKt <= 0) {
        return ends.first()
    }
    // Prefer ends with headwind (headwindKt >= 0) and lowest crosswind; break ties by highest headwind
    val scored = ends.map { end ->
        end to EndComponents.compute(windDirDeg.toDouble(), windSpeedKt.toDouble(), end.headingDeg)
    }
    val headwindCandidates = scored.filter { it.second.headwindKt >= -0.5 }
    val pool = headwindCandidates.ifEmpty { scored }
    return pool.minWithOrNull(
        compareBy<Pair<RunwayEnd, EndComponents>> { it.second.crosswindKt }
            .thenByDescending { it.second.headwindKt },
    )?.first
}

private fun angularDiffDeg(a: Double, b: Double): Double {
    val diff = ((a - b) % 360.0 + 540.0) % 360.0 - 180.0
    return abs(diff)
}

private fun crosswindSeverityColor(crosswindKt: Double): Color = when {
    crosswindKt >= 20.0 -> AssessmentRed
    crosswindKt >= 12.0 -> AssessmentAmber
    else -> AssessmentGreen
}

private fun formatWindShort(
    prefix: String,
    dirDeg: Int,
    speedKt: Int,
    gustKt: Int?,
): String {
    val dirStr = dirDeg.toString().padStart(3, '0')
    val gustStr = if (gustKt != null && gustKt > speedKt) "G$gustKt" else ""
    return "$prefix ${dirStr}°/${speedKt}${gustStr}kt"
}
