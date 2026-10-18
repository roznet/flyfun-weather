package aero.flyfun.weather.ui.crosssection

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import aero.flyfun.weather.model.viz.CoordTransform
import aero.flyfun.weather.model.viz.CrossSectionTheme
import aero.flyfun.weather.model.viz.MetricSample
import aero.flyfun.weather.model.viz.MonotoneCubicSpline
import aero.flyfun.weather.model.viz.RouteGraphMetric
import aero.flyfun.weather.model.viz.RouteGraphMetrics
import aero.flyfun.weather.model.viz.RouteGraphRenderType
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Pixel-aligned 150dp Compose [Canvas] below [CrossSectionCanvas] sharing the exact same
 * `marginLeft = 52f` and `marginRight = 44f`:
 * - Dual Y-axes (left metric ticks + unit, right metric ticks + unit).
 * - Renders bars (`precipitation`, `cloud-cover`, `cape`, `cin`) and smooth spline lines
 *   (`headwind`, `crosswind`, `temperature`, `isa-dev`, `qnh`, `freezing-level`, `ceiling-dd`, `ceiling-nwp`).
 * - Draws top-edge chevron markers for [MetricSample.AboveScale] (`> 5,000 ft AGL`).
 * - Synced vertical scrub crosshair + floating value badge.
 * - Controls bar immediately below: `[▼ Route Graph]` toggle + `Left: [...]` metric dropdown + `Right: [...]` metric dropdown.
 */
@Composable
fun RouteGraphSection(
    vizData: VizRouteData,
    visible: Boolean,
    leftMetricId: String,
    rightMetricId: String,
    scrubDistanceNm: Double?,
    selectedPointIndex: Int,
    theme: CrossSectionTheme,
    onToggleVisible: (Boolean) -> Unit,
    onSelectMetrics: (leftId: String?, rightId: String?) -> Unit,
    onScrubDistanceChanged: (Double?) -> Unit,
    onSelectPointIndex: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        AnimatedVisibility(
            visible = visible,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            RouteGraphCanvas(
                vizData = vizData,
                leftMetricId = leftMetricId,
                rightMetricId = rightMetricId,
                scrubDistanceNm = scrubDistanceNm,
                selectedPointIndex = selectedPointIndex,
                theme = theme,
                onScrubDistanceChanged = onScrubDistanceChanged,
                onSelectPointIndex = onSelectPointIndex,
            )
        }

        RouteGraphControlsBar(
            visible = visible,
            leftMetricId = leftMetricId,
            rightMetricId = rightMetricId,
            onToggleVisible = { onToggleVisible(!visible) },
            onSelectLeftMetric = { id -> onSelectMetrics(id, null) },
            onSelectRightMetric = { id -> onSelectMetrics(null, id) },
        )
    }
}

@Composable
fun RouteGraphCanvas(
    vizData: VizRouteData,
    leftMetricId: String,
    rightMetricId: String,
    scrubDistanceNm: Double?,
    selectedPointIndex: Int,
    theme: CrossSectionTheme,
    onScrubDistanceChanged: (Double?) -> Unit,
    onSelectPointIndex: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val leftMetric = RouteGraphMetrics.byId(leftMetricId)
    val rightMetric = RouteGraphMetrics.byId(rightMetricId)
    val viewConfig = LocalViewConfiguration.current
    val touchSlop = viewConfig.touchSlop
    val density = androidx.compose.ui.platform.LocalDensity.current
    val marginLeftPx = with(density) { 46.dp.toPx() }
    val marginRightPx = with(density) { 40.dp.toPx() }
    val marginTopPx = with(density) { 12.dp.toPx() }
    val marginBottomPx = with(density) { 16.dp.toPx() }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = Color(theme.skyBottomColor).copy(alpha = 0.22f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(150.dp)
                .pointerInput(vizData.totalDistanceNm, vizData.points.size) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        val startPos = down.position
                        var lockedHorizontal: Boolean? = null
                        var currentX = startPos.x

                        val transform = CoordTransform(
                            width = size.width.toFloat(),
                            height = size.height.toFloat(),
                            totalDistanceNm = vizData.totalDistanceNm,
                            maxAltitudeFt = 100.0,
                            marginLeft = marginLeftPx,
                            marginRight = marginRightPx,
                            marginTop = marginTopPx,
                            marginBottom = marginBottomPx,
                        )

                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id } ?: break
                            if (!change.pressed) {
                                if (lockedHorizontal == true) {
                                    val finalDist = transform.xToDistance(currentX)
                                    val nearestIdx = vizData.points.indices.minByOrNull { idx ->
                                        abs(vizData.points[idx].distanceNm - finalDist)
                                    }
                                    if (nearestIdx != null) {
                                        onSelectPointIndex(nearestIdx)
                                    }
                                    onScrubDistanceChanged(null)
                                } else if (lockedHorizontal == null) {
                                    // Single tap -> select nearest route point
                                    if (startPos.x in transform.plotLeft..transform.plotRight) {
                                        val tapDist = transform.xToDistance(startPos.x)
                                        val nearestIdx = vizData.points.indices.minByOrNull { idx ->
                                            abs(vizData.points[idx].distanceNm - tapDist)
                                        }
                                        if (nearestIdx != null) {
                                            onSelectPointIndex(nearestIdx)
                                        }
                                        onScrubDistanceChanged(null)
                                    }
                                }
                                break
                            }

                            val dx = abs(change.position.x - startPos.x)
                            val dy = abs(change.position.y - startPos.y)
                            if (lockedHorizontal == null && (dx > touchSlop || dy > touchSlop)) {
                                lockedHorizontal = dx >= dy * 1.15f
                            }

                            if (lockedHorizontal == true) {
                                change.consume()
                                currentX = change.position.x
                                onScrubDistanceChanged(transform.xToDistance(currentX))
                            } else if (lockedHorizontal == false) {
                                break
                            }
                        }
                    }
                },
        ) {
            val transform = CoordTransform(
                width = size.width,
                height = size.height,
                totalDistanceNm = max(1.0, vizData.totalDistanceNm),
                maxAltitudeFt = 100.0,
                marginLeft = marginLeftPx,
                marginRight = marginRightPx,
                marginTop = marginTopPx,
                marginBottom = marginBottomPx,
            )

            val points = vizData.points
            if (points.isEmpty()) return@Canvas

            val leftSamples = leftMetric?.let { m -> points.map { RouteGraphMetrics.sampleMetric(m, it) } }.orEmpty()
            val rightSamples = rightMetric?.let { m -> points.map { RouteGraphMetrics.sampleMetric(m, it) } }.orEmpty()

            val leftScale = leftMetric?.let { RouteGraphMetrics.computeYScale(it, leftSamples) } ?: (0.0 to 100.0)
            val rightScale = rightMetric?.let { RouteGraphMetrics.computeYScale(it, rightSamples) } ?: (0.0 to 100.0)

            // 1. Plot background & grid
            drawRouteGraphGrid(
                transform = transform,
                vizData = vizData,
                theme = theme,
            )

            // 2. Zero reference line (if left or right metric shows zero line)
            if (leftMetric != null && leftMetric.showZeroLine) {
                drawZeroLine(
                    transform = transform,
                    metric = leftMetric,
                    scale = leftScale,
                    isLeft = true,
                )
            } else if (rightMetric != null && rightMetric.showZeroLine) {
                drawZeroLine(
                    transform = transform,
                    metric = rightMetric,
                    scale = rightScale,
                    isLeft = false,
                )
            }

            val bothBars = leftMetric?.renderType == RouteGraphRenderType.BAR &&
                rightMetric?.renderType == RouteGraphRenderType.BAR

            // 3. Draw BAR metrics first, then LINE metrics on top
            clipRect(
                left = transform.plotLeft,
                top = transform.plotTop - 6f,
                right = transform.plotRight,
                bottom = transform.plotBottom + 2f,
            ) {
                if (leftMetric != null && leftMetric.renderType == RouteGraphRenderType.BAR) {
                    drawMetricBars(
                        transform = transform,
                        points = points,
                        samples = leftSamples,
                        scale = leftScale,
                        color = Color(leftMetric.colorHex),
                        barSlot = if (bothBars) -1 else 0,
                    )
                }
                if (rightMetric != null && rightMetric.renderType == RouteGraphRenderType.BAR) {
                    drawMetricBars(
                        transform = transform,
                        points = points,
                        samples = rightSamples,
                        scale = rightScale,
                        color = Color(rightMetric.colorHex),
                        barSlot = if (bothBars) 1 else 0,
                    )
                }

                if (leftMetric != null && leftMetric.renderType == RouteGraphRenderType.LINE) {
                    drawMetricLineAndChevrons(
                        transform = transform,
                        points = points,
                        samples = leftSamples,
                        scale = leftScale,
                        color = Color(leftMetric.colorHex),
                    )
                }
                if (rightMetric != null && rightMetric.renderType == RouteGraphRenderType.LINE) {
                    drawMetricLineAndChevrons(
                        transform = transform,
                        points = points,
                        samples = rightSamples,
                        scale = rightScale,
                        color = Color(rightMetric.colorHex),
                    )
                }
            }

            // 4. Dual Y-Axes labels & units
            if (leftMetric != null) {
                drawYAxisLabels(
                    transform = transform,
                    metric = leftMetric,
                    scale = leftScale,
                    isLeft = true,
                )
            }
            if (rightMetric != null) {
                drawYAxisLabels(
                    transform = transform,
                    metric = rightMetric,
                    scale = rightScale,
                    isLeft = false,
                )
            }

            // 5. Synced scrub crosshair / selected point marker + floating value badge
            drawScrubAndSelection(
                transform = transform,
                points = points,
                scrubDistanceNm = scrubDistanceNm,
                selectedPointIndex = selectedPointIndex,
                leftMetric = leftMetric,
                leftSamples = leftSamples,
                rightMetric = rightMetric,
                rightSamples = rightSamples,
            )
        }
    }
}

@Composable
private fun RouteGraphControlsBar(
    visible: Boolean,
    leftMetricId: String,
    rightMetricId: String,
    onToggleVisible: () -> Unit,
    onSelectLeftMetric: (String) -> Unit,
    onSelectRightMetric: (String) -> Unit,
) {
    val allMetrics = RouteGraphMetrics.ALL
    val leftMetric = RouteGraphMetrics.byId(leftMetricId)
    val rightMetric = RouteGraphMetrics.byId(rightMetricId)

    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 8.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // [▼ Route Graph] toggle chip
            Surface(
                shape = RoundedCornerShape(14.dp),
                color = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
                modifier = Modifier
                    .clip(RoundedCornerShape(14.dp))
                    .clickable(onClick = onToggleVisible),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Icon(
                        imageVector = if (visible) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(15.dp),
                    )
                    Text(
                        text = "Route Graph",
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontWeight = FontWeight.Bold,
                            fontSize = 11.sp,
                        ),
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }

            if (visible) {
                // Left Metric Picker
                MetricDropdownPicker(
                    axisLabel = "Left",
                    selectedMetric = leftMetric,
                    allMetrics = allMetrics,
                    allowNone = false,
                    onSelectMetricId = onSelectLeftMetric,
                )

                // Right Metric Picker
                MetricDropdownPicker(
                    axisLabel = "Right",
                    selectedMetric = rightMetric,
                    allMetrics = allMetrics,
                    allowNone = true,
                    onSelectMetricId = onSelectRightMetric,
                )
            }
        }
    }
}

@Composable
private fun MetricDropdownPicker(
    axisLabel: String,
    selectedMetric: RouteGraphMetric?,
    allMetrics: List<RouteGraphMetric>,
    allowNone: Boolean,
    onSelectMetricId: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val dotColor = selectedMetric?.let { Color(it.colorHex) } ?: Color.Transparent

    Box {
        Surface(
            shape = RoundedCornerShape(14.dp),
            color = MaterialTheme.colorScheme.surface,
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            modifier = Modifier
                .clip(RoundedCornerShape(14.dp))
                .clickable { expanded = true },
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Text(
                    text = "$axisLabel:",
                    style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.5.sp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (selectedMetric != null) {
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .clip(CircleShape)
                            .background(dotColor),
                    )
                }
                Text(
                    text = selectedMetric?.label ?: "None",
                    style = MaterialTheme.typography.labelSmall.copy(
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 11.sp,
                    ),
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Icon(
                    imageVector = Icons.Default.ArrowDropDown,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(14.dp),
                )
            }
        }

        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            if (allowNone) {
                val isNone = selectedMetric == null
                DropdownMenuItem(
                    text = {
                        Text(
                            text = "None",
                            fontWeight = if (isNone) FontWeight.Bold else FontWeight.Normal,
                        )
                    },
                    leadingIcon = if (isNone) {
                        { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(15.dp)) }
                    } else null,
                    onClick = {
                        expanded = false
                        onSelectMetricId("none")
                    },
                )
                HorizontalDivider()
            }

            allMetrics.forEach { metric ->
                val isSelected = selectedMetric?.id == metric.id
                DropdownMenuItem(
                    text = {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(Color(metric.colorHex)),
                            )
                            Text(
                                text = "${metric.label} (${metric.unit})",
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                            )
                        }
                    },
                    leadingIcon = if (isSelected) {
                        { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(15.dp)) }
                    } else null,
                    onClick = {
                        expanded = false
                        onSelectMetricId(metric.id)
                    },
                )
            }
        }
    }
}

private fun valueToY(
    value: Double,
    scale: Pair<Double, Double>,
    transform: CoordTransform,
): Float {
    val span = max(1e-6, scale.second - scale.first)
    val ratio = ((value - scale.first) / span).coerceIn(0.0, 1.0)
    return transform.plotBottom - (ratio * transform.plotHeight).toFloat()
}

private fun DrawScope.drawRouteGraphGrid(
    transform: CoordTransform,
    vizData: VizRouteData,
    theme: CrossSectionTheme,
) {
    val gridColor = Color(theme.gridLineColor)
    val borderColor = gridColor.copy(alpha = 0.55f)

    // Plot frame
    drawRect(
        color = Color(0x220F172A),
        topLeft = Offset(transform.plotLeft, transform.plotTop),
        size = Size(transform.plotWidth, transform.plotHeight),
    )

    // Horizontal grid lines (4 intervals)
    for (i in 0..4) {
        val y = transform.plotTop + transform.plotHeight * (i / 4f)
        drawLine(
            color = if (i == 0 || i == 4) borderColor else gridColor,
            start = Offset(transform.plotLeft, y),
            end = Offset(transform.plotRight, y),
            strokeWidth = if (i == 0 || i == 4) 1.2f else 0.8f,
        )
    }

    // Distance vertical grid lines
    val stepNm = chooseDistanceGridStep(vizData.totalDistanceNm)
    var d = stepNm
    val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(180, 148, 163, 184)
        textSize = 9.sp.toPx()
        textAlign = Paint.Align.CENTER
        typeface = Typeface.MONOSPACE
    }
    while (d < vizData.totalDistanceNm) {
        val x = transform.distanceToX(d)
        drawLine(
            color = gridColor,
            start = Offset(x, transform.plotTop),
            end = Offset(x, transform.plotBottom),
            strokeWidth = 0.8f,
        )
        drawContext.canvas.nativeCanvas.drawText(
            "${d.roundToInt()}",
            x,
            transform.plotBottom + 13f,
            textPaint,
        )
        d += stepNm
    }
}

private fun DrawScope.drawZeroLine(
    transform: CoordTransform,
    metric: RouteGraphMetric,
    scale: Pair<Double, Double>,
    isLeft: Boolean,
) {
    if (0.0 < scale.first || 0.0 > scale.second) return
    val y0 = valueToY(0.0, scale, transform)
    drawLine(
        color = Color(0x9994A3B8),
        start = Offset(transform.plotLeft, y0),
        end = Offset(transform.plotRight, y0),
        strokeWidth = 1.2f,
        pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f), 0f),
    )

    val labels = metric.zeroLineLabels ?: return
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color(metric.colorHex).copy(alpha = 0.85f).toArgb()
        textSize = 8.5.sp.toPx()
        textAlign = if (isLeft) Paint.Align.LEFT else Paint.Align.RIGHT
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }
    val x = if (isLeft) transform.plotLeft + 4f else transform.plotRight - 4f
    if (y0 - 4f > transform.plotTop + 8f) {
        drawContext.canvas.nativeCanvas.drawText(labels.first, x, y0 - 3f, paint)
    }
    if (y0 + 10f < transform.plotBottom - 2f) {
        drawContext.canvas.nativeCanvas.drawText(labels.second, x, y0 + 10f, paint)
    }
}

private fun DrawScope.drawMetricBars(
    transform: CoordTransform,
    points: List<VizPoint>,
    samples: List<MetricSample>,
    scale: Pair<Double, Double>,
    color: Color,
    barSlot: Int,
) {
    if (points.isEmpty()) return
    val baseSpacing = if (points.size > 1) {
        transform.plotWidth / (points.size - 1).toFloat()
    } else {
        20f
    }
    val fullBarWidth = (baseSpacing * 0.58f).coerceIn(3f, 18f)
    val barWidth = if (barSlot != 0) fullBarWidth * 0.48f else fullBarWidth
    val xOffset = when (barSlot) {
        -1 -> -barWidth * 0.55f
        1 -> barWidth * 0.55f
        else -> 0f
    }

    val baselineVal = if (0.0 in scale.first..scale.second) 0.0 else scale.first
    val baseY = valueToY(baselineVal, scale, transform)

    for (i in points.indices) {
        val sample = samples.getOrNull(i) ?: continue
        val xCenter = transform.distanceToX(points[i].distanceNm) + xOffset
        when (sample) {
            is MetricSample.Value -> {
                val yVal = valueToY(sample.value, scale, transform)
                val top = min(baseY, yVal)
                val bottom = max(baseY, yVal)
                val h = max(1.5f, bottom - top)
                drawRoundRect(
                    color = color.copy(alpha = 0.62f),
                    topLeft = Offset(xCenter - barWidth / 2f, top),
                    size = Size(barWidth, h),
                    cornerRadius = CornerRadius(2f, 2f),
                )
                drawRoundRect(
                    color = color.copy(alpha = 0.9f),
                    topLeft = Offset(xCenter - barWidth / 2f, top),
                    size = Size(barWidth, h),
                    cornerRadius = CornerRadius(2f, 2f),
                    style = Stroke(width = 1f),
                )
            }
            is MetricSample.AboveScale -> {
                drawAboveScaleChevron(xCenter, transform.plotTop + 2f, color)
            }
            is MetricSample.Unavailable -> Unit
        }
    }
}

private fun DrawScope.drawMetricLineAndChevrons(
    transform: CoordTransform,
    points: List<VizPoint>,
    samples: List<MetricSample>,
    scale: Pair<Double, Double>,
    color: Color,
) {
    // Split into contiguous runs of numeric values
    val runs = mutableListOf<MutableList<Pair<Double, Double>>>()
    var currentRun = mutableListOf<Pair<Double, Double>>()

    for (i in points.indices) {
        val d = points[i].distanceNm
        when (val s = samples.getOrNull(i) ?: MetricSample.Unavailable) {
            is MetricSample.Value -> {
                currentRun.add(d to s.value)
            }
            is MetricSample.AboveScale -> {
                if (currentRun.isNotEmpty()) {
                    runs.add(currentRun)
                    currentRun = mutableListOf()
                }
                // Draw top-edge chevron marker for AboveScale (> 5,000 ft AGL / > 15,000 ft)
                val x = transform.distanceToX(d)
                drawAboveScaleChevron(x, transform.plotTop + 2f, color)
            }
            is MetricSample.Unavailable -> {
                if (currentRun.isNotEmpty()) {
                    runs.add(currentRun)
                    currentRun = mutableListOf()
                }
            }
        }
    }
    if (currentRun.isNotEmpty()) {
        runs.add(currentRun)
    }

    for (run in runs) {
        if (run.size == 1) {
            val (d, v) = run.first()
            drawCircle(
                color = color,
                radius = 3.2f,
                center = Offset(transform.distanceToX(d), valueToY(v, scale, transform)),
            )
        } else if (run.size >= 2) {
            val sampled = MonotoneCubicSpline.sampleCurve(
                points = run,
                stepCount = max(24, run.size * 6),
            )
            val path = Path()
            sampled.forEachIndexed { idx, (d, v) ->
                val x = transform.distanceToX(d)
                val y = valueToY(v, scale, transform)
                if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            drawPath(
                path = path,
                color = color,
                style = Stroke(
                    width = 2.2f,
                    cap = StrokeCap.Round,
                    join = StrokeJoin.Round,
                ),
            )
            // Data dots at actual route points
            for ((d, v) in run) {
                drawCircle(
                    color = color,
                    radius = 2.6f,
                    center = Offset(transform.distanceToX(d), valueToY(v, scale, transform)),
                )
            }
        }
    }
}

private fun DrawScope.drawAboveScaleChevron(
    x: Float,
    topY: Float,
    color: Color,
) {
    // Upward chevron / triangle at the top edge indicating value is AboveScale
    val path = Path().apply {
        moveTo(x - 4.5f, topY + 6.5f)
        lineTo(x, topY + 1f)
        lineTo(x + 4.5f, topY + 6.5f)
    }
    drawPath(
        path = path,
        color = color,
        style = Stroke(width = 1.8f, cap = StrokeCap.Round, join = StrokeJoin.Round),
    )
}

private fun DrawScope.drawYAxisLabels(
    transform: CoordTransform,
    metric: RouteGraphMetric,
    scale: Pair<Double, Double>,
    isLeft: Boolean,
) {
    val axisColor = Color(metric.colorHex)
    val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = axisColor.toArgb()
        textSize = 9.sp.toPx()
        textAlign = if (isLeft) Paint.Align.RIGHT else Paint.Align.LEFT
        typeface = Typeface.MONOSPACE
    }
    val unitPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = axisColor.copy(alpha = 0.85f).toArgb()
        textSize = 8.5.sp.toPx()
        textAlign = if (isLeft) Paint.Align.RIGHT else Paint.Align.LEFT
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }

    val x = if (isLeft) transform.plotLeft - 5f else transform.plotRight + 5f

    // Unit header at top margin
    drawContext.canvas.nativeCanvas.drawText(
        metric.unit,
        x,
        transform.plotTop - 4f,
        unitPaint,
    )

    // 3 tick labels: bottom (min), middle, top (max)
    val ticks = listOf(scale.first, (scale.first + scale.second) * 0.5, scale.second)
    ticks.forEach { tickVal ->
        val y = valueToY(tickVal, scale, transform)
        val formatted = formatTickCompact(tickVal)
        drawContext.canvas.nativeCanvas.drawText(
            formatted,
            x,
            y + 3.5f,
            labelPaint,
        )
    }
}

private fun DrawScope.drawScrubAndSelection(
    transform: CoordTransform,
    points: List<VizPoint>,
    scrubDistanceNm: Double?,
    selectedPointIndex: Int,
    leftMetric: RouteGraphMetric?,
    leftSamples: List<MetricSample>,
    rightMetric: RouteGraphMetric?,
    rightSamples: List<MetricSample>,
) {
    val activeIdx = if (scrubDistanceNm != null) {
        points.indices.minByOrNull { abs(points[it].distanceNm - scrubDistanceNm) } ?: selectedPointIndex
    } else {
        selectedPointIndex
    }.coerceIn(0, points.lastIndex)

    val activeDist = scrubDistanceNm ?: points[activeIdx].distanceNm
    val x = transform.distanceToX(activeDist)

    // Crosshair line
    drawLine(
        color = if (scrubDistanceNm != null) Color(0xFF38BDF8) else Color(0x99CBD5E1),
        start = Offset(x, transform.plotTop),
        end = Offset(x, transform.plotBottom),
        strokeWidth = if (scrubDistanceNm != null) 1.6f else 1.1f,
        pathEffect = if (scrubDistanceNm != null) null else PathEffect.dashPathEffect(floatArrayOf(4f, 4f), 0f),
    )

    // Floating value badge when scrubbing or for selected point
    val parts = mutableListOf<String>()
    parts.add("${activeDist.roundToInt()} NM")
    if (leftMetric != null && activeIdx in leftSamples.indices) {
        parts.add(RouteGraphMetrics.formatSample(leftMetric, leftSamples[activeIdx]))
    }
    if (rightMetric != null && activeIdx in rightSamples.indices) {
        parts.add(RouteGraphMetrics.formatSample(rightMetric, rightSamples[activeIdx]))
    }
    val badgeText = parts.joinToString(" · ")

    val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.WHITE
        textSize = 9.5.sp.toPx()
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }
    val textWidth = textPaint.measureText(badgeText)
    val padH = 6.dp.toPx()
    val badgeW = textWidth + padH * 2f
    val badgeH = 16.dp.toPx()
    val badgeX = (x - badgeW / 2f).coerceIn(transform.plotLeft + 2f, max(transform.plotLeft + 2f, transform.plotRight - badgeW - 2f))
    val badgeY = transform.plotTop + 2f

    drawRoundRect(
        color = Color(0xCC0F172A),
        topLeft = Offset(badgeX, badgeY),
        size = Size(badgeW, badgeH),
        cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
    )
    drawContext.canvas.nativeCanvas.drawText(
        badgeText,
        badgeX + padH,
        badgeY + badgeH - 4.5f,
        textPaint,
    )
}

private fun formatTickCompact(v: Double): String {
    val absV = abs(v)
    return when {
        absV >= 1000.0 && absV % 1000.0 == 0.0 -> String.format(Locale.US, "%.0fk", v / 1000.0)
        absV >= 1000.0 -> String.format(Locale.US, "%.1fk", v / 1000.0)
        absV < 10.0 && absV % 1.0 != 0.0 -> String.format(Locale.US, "%.1f", v)
        else -> String.format(Locale.US, "%.0f", v)
    }
}

private fun chooseDistanceGridStep(totalDistanceNm: Double): Double = when {
    totalDistanceNm <= 80.0 -> 10.0
    totalDistanceNm <= 200.0 -> 25.0
    totalDistanceNm <= 500.0 -> 50.0
    else -> 100.0
}
