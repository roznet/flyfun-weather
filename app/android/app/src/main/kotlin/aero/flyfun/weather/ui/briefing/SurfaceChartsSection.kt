package aero.flyfun.weather.ui.briefing

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.ZoomIn
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SecondaryScrollableTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import aero.flyfun.weather.model.ChartOverlay
import aero.flyfun.weather.model.ChartOverlayEntry
import aero.flyfun.weather.model.PackChart
import aero.flyfun.weather.model.PackChartSource
import aero.flyfun.weather.model.PackChartsManifest
import aero.flyfun.weather.viewmodel.BriefingViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.hypot
import kotlin.math.max

/**
 * Surface pressure & fronts charts section on the Discussion tab (mirrors `SurfaceChartsView.swift`):
 * - Source tabs (`DWD`, `Met Office`, `TEMSI`) and validity time chips from `pack.charts`.
 * - Decodes chart image bytes via [BitmapFactory.decodeByteArray].
 * - Draws [ChartRouteOverlay] on a Compose [Canvas] scaling native coordinates (`4389x3114` or source size)
 *   to the drawn image size, rendering the magenta route line, waypoint dots, and endpoint ICAO labels.
 * - Opens a pinch-to-zoom fullscreen dialog on tap.
 */
@Composable
fun SurfaceChartsSection(
    viewModel: BriefingViewModel,
    manifest: PackChartsManifest,
    modifier: Modifier = Modifier,
) {
    if (manifest.sources.isEmpty()) {
        if (manifest.inCoverage) {
            SurfaceChartsUnavailableCard(modifier = modifier)
        }
        return
    }

    var selectedSourceId by rememberSaveable(manifest) {
        mutableStateOf(manifest.sources.first().source)
    }
    val chosenChartsBySource = remember { mutableStateMapOf<String, String>() }
    var showRouteOverlay by rememberSaveable { mutableStateOf(true) }
    val overlaysBySource = remember { mutableStateMapOf<String, ChartOverlay>() }

    val activeSource: PackChartSource = manifest.sources.firstOrNull { it.source == selectedSourceId }
        ?: manifest.sources.first()
    val selectedChartId = chosenChartsBySource[activeSource.source] ?: activeSource.defaultChartId
    val activeChart: PackChart? = activeSource.charts.firstOrNull { it.chartId == selectedChartId }
        ?: activeSource.charts.firstOrNull()

    var imageLoadState by remember(activeChart?.cacheKey, activeChart?.chartId) {
        mutableStateOf<ChartImageLoadState>(ChartImageLoadState.Loading)
    }
    var showZoomDialog by remember { mutableStateOf(false) }

    // Fetch route overlay for active source
    LaunchedEffect(activeSource.source) {
        if (overlaysBySource[activeSource.source] == null && !activeSource.overlayUrl.isNullOrBlank()) {
            val loaded = viewModel.loadChartOverlay(activeSource)
            if (loaded != null) {
                overlaysBySource[activeSource.source] = loaded
            }
        }
    }

    // Fetch chart image bytes and decode bitmap
    LaunchedEffect(activeChart?.cacheKey, activeChart?.chartId, activeChart?.available) {
        val chart = activeChart ?: return@LaunchedEffect
        if (!chart.available) {
            imageLoadState = ChartImageLoadState.Unavailable
            return@LaunchedEffect
        }
        imageLoadState = ChartImageLoadState.Loading
        val bytes = viewModel.loadChartImage(chart)
        if (bytes == null || bytes.isEmpty()) {
            imageLoadState = ChartImageLoadState.Failed
            return@LaunchedEffect
        }
        val bitmap = withContext(Dispatchers.Default) {
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }
        imageLoadState = if (bitmap != null) {
            ChartImageLoadState.Loaded(bitmap)
        } else {
            ChartImageLoadState.Failed
        }
    }

    val overlayEntry: ChartOverlayEntry? = remember(
        showRouteOverlay,
        activeSource.source,
        activeChart?.overlayKey,
        activeChart?.chartId,
        overlaysBySource[activeSource.source],
    ) {
        if (!showRouteOverlay || activeChart == null) {
            null
        } else {
            val map = overlaysBySource[activeSource.source]
            val key = activeChart.overlayKey ?: activeChart.chartId
            map?.get(key) ?: map?.values?.firstOrNull()
        }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            text = "Surface Charts",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )

        // Source Tabs (DWD / Met Office / TEMSI)
        if (manifest.sources.size > 1) {
            val selectedTabIndex = manifest.sources.indexOfFirst { it.source == activeSource.source }.coerceAtLeast(0)
            SecondaryScrollableTabRow(
                selectedTabIndex = selectedTabIndex,
                edgePadding = 0.dp,
            ) {
                manifest.sources.forEach { src ->
                    Tab(
                        selected = src.source == activeSource.source,
                        onClick = { selectedSourceId = src.source },
                        text = { Text(src.label) },
                    )
                }
            }
        }

        // Validity time chips + Route overlay toggle
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            LazyRow(
                modifier = Modifier.weight(1f),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                contentPadding = PaddingValues(vertical = 2.dp),
            ) {
                items(activeSource.charts, key = { it.chartId }) { chart ->
                    val isSelected = chart.chartId == activeChart?.chartId
                    FilterChip(
                        selected = isSelected,
                        onClick = { chosenChartsBySource[activeSource.source] = chart.chartId },
                        label = {
                            Text(
                                text = chart.label,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal,
                            )
                        },
                        enabled = true,
                    )
                }
            }

            FilterChip(
                selected = showRouteOverlay,
                onClick = { showRouteOverlay = !showRouteOverlay },
                label = { Text("Route") },
            )
        }

        // Chart Image + Route Overlay Canvas
        when (val state = imageLoadState) {
            is ChartImageLoadState.Loading -> {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 180.dp),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                ) {
                    Box(contentAlignment = Alignment.Center, modifier = Modifier.padding(32.dp)) {
                        CircularProgressIndicator()
                    }
                }
            }
            is ChartImageLoadState.Loaded -> {
                val bitmap = state.bitmap
                val ratio = if (bitmap.height > 0) {
                    bitmap.width.toFloat() / bitmap.height.toFloat()
                } else {
                    4389f / 3114f
                }
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(ratio)
                        .clip(RoundedCornerShape(8.dp))
                        .clickable { showZoomDialog = true },
                ) {
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "${activeSource.label} chart ${activeChart?.label.orEmpty()}",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    if (overlayEntry != null) {
                        ChartRouteOverlay(
                            entry = overlayEntry,
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                    Surface(
                        color = Color.Black.copy(alpha = 0.45f),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(8.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.ZoomIn,
                                contentDescription = "Tap to zoom",
                                tint = Color.White,
                                modifier = Modifier.size(14.dp),
                            )
                            Text(
                                text = "Zoom",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color.White,
                            )
                        }
                    }
                }
            }
            is ChartImageLoadState.Unavailable -> {
                ChartPlaceholderBox(
                    icon = Icons.Default.Schedule,
                    message = buildUnavailableChartText(activeChart?.validTime),
                )
            }
            is ChartImageLoadState.Failed -> {
                ChartPlaceholderBox(
                    icon = Icons.Default.CloudOff,
                    message = "Chart couldn't be loaded — connect to the internet to fetch it.",
                )
            }
        }

        // Caption & attribution
        if (activeChart != null) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                if (activeChart.caption.isNotBlank()) {
                    Text(
                        text = activeChart.caption,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (activeSource.attribution.isNotBlank()) {
                    Text(
                        text = "Source: ${activeSource.attribution}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }

    // Fullscreen Pinch-to-Zoom Dialog
    val loadedBitmap = (imageLoadState as? ChartImageLoadState.Loaded)?.bitmap
    if (showZoomDialog && loadedBitmap != null) {
        val captionParts = listOfNotNull(
            activeChart?.caption?.takeIf { it.isNotBlank() },
            activeSource.attribution.takeIf { it.isNotBlank() }?.let { "Source: $it" },
        ).joinToString(" · ")

        ZoomableChartDialog(
            title = "${activeSource.label} ${activeChart?.label.orEmpty()}",
            caption = captionParts,
            bitmap = loadedBitmap,
            overlayEntry = overlayEntry,
            onDismiss = { showZoomDialog = false },
        )
    }
}

private sealed interface ChartImageLoadState {
    data object Loading : ChartImageLoadState
    data class Loaded(val bitmap: Bitmap) : ChartImageLoadState
    data object Unavailable : ChartImageLoadState
    data object Failed : ChartImageLoadState
}

/**
 * Draws the flight route on a surface chart (port of `ChartRouteOverlay.swift` and `renderRouteSvg`):
 * - Scales source coordinates (`entry.width` × `entry.height`, defaulting to `4389` × `3114`)
 *   to the drawn canvas size.
 * - Draws a magenta polyline, magenta waypoint dots with white borders, and endpoint ICAO labels
 *   pushed away from the route's bounding-box center.
 */
@Composable
fun ChartRouteOverlay(
    entry: ChartOverlayEntry,
    modifier: Modifier = Modifier,
) {
    Canvas(modifier = modifier) {
        drawChartRouteOverlay(entry)
    }
}

private val RouteMagenta = Color(0xFFFF00FF)

internal fun DrawScope.drawChartRouteOverlay(entry: ChartOverlayEntry) {
    val wps = entry.waypoints
    if (wps.isEmpty() || size.width <= 0f || size.height <= 0f) return

    val nativeWidth = if (entry.width > 0) entry.width.toFloat() else 4389f
    val nativeHeight = if (entry.height > 0) entry.height.toFloat() else 3114f
    val sx = size.width / nativeWidth
    val sy = size.height / nativeHeight

    val points = wps.map { wp ->
        Offset(
            x = (wp.x.toFloat() * sx),
            y = (wp.y.toFloat() * sy),
        )
    }

    val w = size.width
    val lineWidth = max(2.5f, w / 320f)
    val dotRadius = max(2.5f, w / 250f)
    val labelFontPx = max(18f, w / 35f)
    val labelDistance = max(dotRadius * 5f, w / 60f)

    // 1. Route polyline
    if (points.size >= 2) {
        val path = Path().apply {
            moveTo(points.first().x, points.first().y)
            for (pt in points.drop(1)) {
                lineTo(pt.x, pt.y)
            }
        }
        drawPath(
            path = path,
            color = RouteMagenta,
            style = Stroke(
                width = lineWidth,
                cap = StrokeCap.Round,
                join = StrokeJoin.Round,
            ),
        )
    }

    // 2. Waypoint dots
    points.forEach { pt ->
        drawCircle(
            color = RouteMagenta,
            radius = dotRadius,
            center = pt,
        )
        drawCircle(
            color = Color.White,
            radius = dotRadius,
            center = pt,
            style = Stroke(width = max(1f, dotRadius / 3f)),
        )
    }

    // 3. Endpoint ICAO labels pushed outward from bounding-box center
    val minX = points.minOf { it.x }
    val maxX = points.maxOf { it.x }
    val minY = points.minOf { it.y }
    val maxY = points.maxOf { it.y }
    val cx = (minX + maxX) / 2f
    val cy = (minY + maxY) / 2f

    val endpoints = if (wps.size >= 2) {
        listOf(points.first() to wps.first().icao, points.last() to wps.last().icao)
    } else {
        listOf(points.first() to wps.first().icao)
    }

    val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = labelFontPx
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }

    endpoints.forEach { (pt, icao) ->
        if (icao.isBlank()) return@forEach
        var dx = pt.x - cx
        var dy = pt.y - cy
        val mag = hypot(dx, dy)
        if (mag == 0f) {
            dx = 1f
            dy = 0f
        } else {
            dx /= mag
            dy /= mag
        }
        val anchorX = pt.x + dx * labelDistance
        val anchorY = pt.y + dy * labelDistance

        textPaint.textAlign = when {
            dx > 0.2f -> Paint.Align.LEFT
            dx < -0.2f -> Paint.Align.RIGHT
            else -> Paint.Align.CENTER
        }
        val fontMetrics = textPaint.fontMetrics
        val baselineOffset = when {
            dy > 0.2f -> -fontMetrics.ascent
            dy < -0.2f -> -fontMetrics.descent
            else -> -(fontMetrics.ascent + fontMetrics.descent) / 2f
        }
        val drawY = anchorY + baselineOffset

        // Halo stroke + white fill
        textPaint.style = Paint.Style.STROKE
        textPaint.strokeWidth = max(3f, labelFontPx / 5f)
        textPaint.color = android.graphics.Color.BLACK
        drawContext.canvas.nativeCanvas.drawText(icao, anchorX, drawY, textPaint)

        textPaint.style = Paint.Style.FILL
        textPaint.color = android.graphics.Color.WHITE
        drawContext.canvas.nativeCanvas.drawText(icao, anchorX, drawY, textPaint)
    }
}

@Composable
internal fun ZoomableChartDialog(
    title: String,
    caption: String,
    bitmap: Bitmap,
    overlayEntry: ChartOverlayEntry?,
    onDismiss: () -> Unit,
) {
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }

    val ratio = if (bitmap.height > 0) {
        bitmap.width.toFloat() / bitmap.height.toFloat()
    } else {
        1.4f
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = Color.Black.copy(alpha = 0.94f),
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // Top bar
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = Color.White,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = onDismiss) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Close fullscreen chart",
                            tint = Color.White,
                        )
                    }
                }

                // Pinch-to-zoom viewport
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .clipToBounds()
                        .pointerInput(Unit) {
                            detectTransformGestures { _, pan, zoom, _ ->
                                val newScale = (scale * zoom).coerceIn(1f, 5f)
                                scale = newScale
                                offset = if (newScale > 1f) {
                                    offset + pan
                                } else {
                                    Offset.Zero
                                }
                            }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .aspectRatio(ratio)
                            .graphicsLayer(
                                scaleX = scale,
                                scaleY = scale,
                                translationX = offset.x,
                                translationY = offset.y,
                            ),
                    ) {
                        Image(
                            bitmap = bitmap.asImageBitmap(),
                            contentDescription = title,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier.fillMaxSize(),
                        )
                        if (overlayEntry != null) {
                            ChartRouteOverlay(
                                entry = overlayEntry,
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                    }
                }

                if (caption.isNotBlank()) {
                    Text(
                        text = caption,
                        style = MaterialTheme.typography.bodySmall,
                        color = Color.White.copy(alpha = 0.78f),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 12.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun ChartPlaceholderBox(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    message: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 120.dp),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
internal fun SurfaceChartsUnavailableCard(
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(
            text = "Surface Charts",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Text(
            text = "Surface charts unavailable for this briefing — the flight is beyond the chart horizon, or the chart refresh failed.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

internal fun buildUnavailableChartText(validTime: String?): String {
    if (validTime.isNullOrBlank()) return "Chart no longer available."
    return "Chart no longer available (valid ${formatUtcTimestamp(validTime)})."
}
