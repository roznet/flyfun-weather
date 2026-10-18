package aero.flyfun.weather.ui.crosssection

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.PathOperation
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import aero.flyfun.weather.model.AdvisoryHighlights
import aero.flyfun.weather.model.viz.CloudStyle
import aero.flyfun.weather.model.viz.ColorScales
import aero.flyfun.weather.model.viz.CoordTransform
import aero.flyfun.weather.model.viz.CrossSectionTheme
import aero.flyfun.weather.model.viz.MonotoneCubicSpline
import aero.flyfun.weather.model.viz.VizCloudLayer
import aero.flyfun.weather.model.viz.VizCurrentConditions
import aero.flyfun.weather.model.viz.VizFrontMarker
import aero.flyfun.weather.model.viz.VizNightInterval
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Full Jetpack Compose [Canvas] renderer drawing all cross-section layers in exact back-to-front order:
 * 1. Sky gradient + `night-shading` (twilight & night column washes)
 * 2. Altitude horizontal grid lines (with `ft` / `FL` labels in left & right margins) and distance vertical grid lines
 * 3. Cloud bands (`square`, `soft`, and `natural` styles for NWP & DD clouds)
 * 4. Convective backgrounds (`nwp-convective-bg` + `thermo-convective-bg` CAPE towers with hatching & `TCU`/`CB`/`+TS` top pill)
 * 5. Icing bands (`icing-ogimet-nwp-bands`, `icing-bands`, `sfip-bands`, `ieng-icing-bands`, `sld-bands`)
 * 6. Turbulence & Stability bands (`cat-bands`, `e-shear-bands`, `inversion-bands`, `surface-obscuration-bands`)
 * 7. Terrain fill (`terrain-fill`): smooth SRTM polygon down to `plotBottom` masking below-ground artifacts + ridge stroke
 * 8. Current conditions (`current-conditions`: D-0 METAR flight-category columns + SIGMET hatched boxes) & Front markers (`fronts-markers`)
 * 9. Smooth monotone cubic spline lines (`0°C`, `-10°C`, `-20°C`, `lcl`, `lfc`, `el`, `cruise-altitude`, `flight-ceiling`)
 * 10. Advisory Highlight (`highlight-layer`): translucent dim scrim with cutout windows + 6dp bottom verdict ribbon
 * 11. Waypoint markers along the bottom axis, active scrub vertical crosshair, selected route point indicator, and linked Skew-T hover altitude dashed line.
 */
@Composable
fun CrossSectionCanvas(
    vizData: VizRouteData,
    effectiveLayers: Map<String, Boolean>,
    cloudStyle: CloudStyle,
    theme: CrossSectionTheme,
    selectedModel: String,
    scrubDistanceNm: Double?,
    selectedPointIndex: Int,
    hoverAltitudeFt: Double?,
    onScrubDistanceChanged: (Double?) -> Unit,
    onSelectPointIndex: (Int) -> Unit,
    onHoverAltitudeChanged: ((Double?) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val viewConfig = LocalViewConfiguration.current
    val touchSlop = viewConfig.touchSlop
    val density = androidx.compose.ui.platform.LocalDensity.current
    val marginLeftPx = with(density) { 46.dp.toPx() }
    val marginRightPx = with(density) { 40.dp.toPx() }
    val marginTopPx = with(density) { 14.dp.toPx() }
    val marginBottomPx = with(density) { 26.dp.toPx() }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = Color(theme.skyTopColor),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(290.dp)
                .pointerInput(vizData.totalDistanceNm, vizData.maxAltitudeFt, vizData.points.size) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        val startPos = down.position
                        var lockedHorizontal: Boolean? = null
                        var lastPos = startPos

                        val transform = CoordTransform(
                            width = size.width.toFloat(),
                            height = size.height.toFloat(),
                            totalDistanceNm = max(1.0, vizData.totalDistanceNm),
                            maxAltitudeFt = max(5000.0, vizData.maxAltitudeFt),
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
                                    val finalDist = transform.xToDistance(lastPos.x)
                                    val nearestIdx = vizData.points.indices.minByOrNull { idx ->
                                        abs(vizData.points[idx].distanceNm - finalDist)
                                    }
                                    if (nearestIdx != null) {
                                        onSelectPointIndex(nearestIdx)
                                    }
                                    onScrubDistanceChanged(null)
                                    onHoverAltitudeChanged?.invoke(null)
                                } else if (lockedHorizontal == null) {
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
                                lockedHorizontal = dx >= dy * 1.1f
                            }

                            if (lockedHorizontal == true) {
                                change.consume()
                                lastPos = change.position
                                onScrubDistanceChanged(transform.xToDistance(lastPos.x))
                                if (lastPos.y in transform.plotTop..transform.plotBottom) {
                                    onHoverAltitudeChanged?.invoke(transform.yToAltitude(lastPos.y))
                                }
                            } else if (lockedHorizontal == false) {
                                // Let vertical scroll pass through to parent scroll container
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
                maxAltitudeFt = max(5000.0, vizData.maxAltitudeFt),
                marginLeft = marginLeftPx,
                marginRight = marginRightPx,
                marginTop = marginTopPx,
                marginBottom = marginBottomPx,
            )

            // 1. Sky gradient + Night shading
            drawSkyAndNightShading(
                transform = transform,
                nightIntervals = vizData.nightIntervals,
                showNightShading = isLayerOn(effectiveLayers, "night-shading", "day-night"),
                theme = theme,
            )

            // 2. Grid lines & Left/Right/Bottom Axis Labels
            drawGridAndAxes(
                transform = transform,
                vizData = vizData,
                theme = theme,
            )

            // Clip all weather layers to the plot rectangle
            clipRect(
                left = transform.plotLeft,
                top = transform.plotTop,
                right = transform.plotRight,
                bottom = transform.plotBottom,
            ) {
                // 3. Cloud bands (NWP & DD in Square, Soft, or Natural style)
                drawAllCloudLayers(
                    transform = transform,
                    points = vizData.points,
                    effectiveLayers = effectiveLayers,
                    cloudStyle = cloudStyle,
                    theme = theme,
                )

                // 4. Convective backgrounds (NWP column + Thermo CAPE tower)
                if (isLayerOn(effectiveLayers, "nwp-convective-bg", "convective-nwp")) {
                    drawNwpConvectiveBg(
                        transform = transform,
                        points = vizData.points,
                        theme = theme,
                    )
                }
                if (isLayerOn(effectiveLayers, "thermo-convective-bg", "convective")) {
                    drawThermoConvectiveBg(
                        transform = transform,
                        points = vizData.points,
                        theme = theme,
                    )
                }

                // 5. Icing bands (Ogimet-NWP, Ogimet-DD, SFIP, IENG, SLD)
                if (isLayerOn(effectiveLayers, "icing-ogimet-nwp-bands", "icing-ogimet-nwp")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.icingOgimetNwpZones.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.icingRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "icing-bands", "icing")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.icingZones.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.icingRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "sfip-bands", "icing-sfip")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.sfipZones.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.sfipRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "ieng-icing-bands", "icing-ieng")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.iengIcingZones.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.icingRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "sld-bands", "sld")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.sldZones.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.sldRiskColor(risk) },
                        hatched = true,
                    )
                }

                // 6. Turbulence & Stability bands (CAT, E-Shear, Inversions, Surface Obscuration)
                if (isLayerOn(effectiveLayers, "cat-bands", "cat")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.catLayers.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.catRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "e-shear-bands", "vertical-motion")) {
                    drawHazardBands(
                        transform = transform,
                        points = vizData.points,
                        extractZones = { pt ->
                            pt.eShearLayers.map { BandZone(it.baseFt, it.topFt, it.risk) }
                        },
                        colorForRisk = { risk -> ColorScales.catRiskColor(risk, theme.id) },
                    )
                }
                if (isLayerOn(effectiveLayers, "inversion-bands", "inversions")) {
                    drawInversionBands(
                        transform = transform,
                        points = vizData.points,
                        theme = theme,
                    )
                }
                if (isLayerOn(effectiveLayers, "surface-obscuration-bands")) {
                    drawSurfaceObscurationBands(
                        transform = transform,
                        points = vizData.points,
                    )
                }

                // 7. Terrain fill (masks below-ground band artifacts + ridge stroke)
                if (isLayerOn(effectiveLayers, "terrain-fill", "terrain", defaultOn = true)) {
                    drawTerrainLayer(
                        transform = transform,
                        vizData = vizData,
                        theme = theme,
                    )
                }

                // 8. Current conditions (METAR columns + SIGMET hatched boxes) & Front markers
                if (isLayerOn(effectiveLayers, "current-conditions")) {
                    vizData.currentConditions?.let { cond ->
                        drawCurrentConditionsLayer(
                            transform = transform,
                            conditions = cond,
                        )
                    }
                }
                if (isLayerOn(effectiveLayers, "fronts-markers", "fronts")) {
                    drawFrontMarkersLayer(
                        transform = transform,
                        fronts = vizData.fronts,
                    )
                }

                // 9. Smooth monotone cubic spline lines (Temperature, Stability, Reference)
                drawAltitudeLinesLayer(
                    transform = transform,
                    vizData = vizData,
                    effectiveLayers = effectiveLayers,
                    theme = theme,
                )

                // 10. Advisory Highlight scrim + cutout windows
                vizData.advisoryHighlights?.let { highlights ->
                    drawAdvisoryHighlightRegions(
                        transform = transform,
                        highlights = highlights,
                        selectedModel = selectedModel,
                    )
                }
            }

            // 10b. Advisory Highlight 6dp bottom verdict ribbon (just below plotBottom)
            vizData.advisoryHighlights?.let { highlights ->
                drawAdvisoryRibbon(
                    transform = transform,
                    highlights = highlights,
                    selectedModel = selectedModel,
                )
            }

            // 11. Waypoint markers, selected point indicator, active scrub crosshair, and linked Skew-T hover altitude line
            drawWaypointsAndOverlays(
                transform = transform,
                vizData = vizData,
                scrubDistanceNm = scrubDistanceNm,
                selectedPointIndex = selectedPointIndex,
                hoverAltitudeFt = hoverAltitudeFt,
            )
        }
    }
}

private fun isLayerOn(
    effectiveLayers: Map<String, Boolean>,
    vararg keys: String,
    defaultOn: Boolean = false,
): Boolean {
    for (k in keys) {
        if (effectiveLayers[k] == true) return true
    }
    if (defaultOn && keys.none { it in effectiveLayers }) return true
    return false
}

// ============================================================================
// 1. Sky Gradient & Night Shading
// ============================================================================

private fun DrawScope.drawSkyAndNightShading(
    transform: CoordTransform,
    nightIntervals: List<VizNightInterval>,
    showNightShading: Boolean,
    theme: CrossSectionTheme,
) {
    val skyBrush = Brush.verticalGradient(
        colors = listOf(Color(theme.skyTopColor), Color(theme.skyBottomColor)),
        startY = transform.plotTop,
        endY = transform.plotBottom,
    )
    drawRect(
        brush = skyBrush,
        topLeft = Offset(transform.plotLeft, transform.plotTop),
        size = Size(transform.plotWidth, transform.plotHeight),
    )

    if (!showNightShading || nightIntervals.isEmpty()) return

    for (interval in nightIntervals) {
        val x1 = transform.distanceToX(interval.startDistanceNm)
        val x2 = transform.distanceToX(interval.endDistanceNm)
        if (x2 <= x1) continue
        val tint = if (interval.kind.equals("twilight", ignoreCase = true)) {
            Color(theme.twilightTintColor)
        } else {
            Color(theme.nightTintColor)
        }
        drawRect(
            color = tint,
            topLeft = Offset(x1, transform.plotTop),
            size = Size(x2 - x1, transform.plotHeight),
        )

        // Draw subtle dashed boundary line at sunrise/sunset transition inside plot
        if (interval.startDistanceNm > 0.5) {
            drawLine(
                color = Color(0x88F59E0B),
                start = Offset(x1, transform.plotTop),
                end = Offset(x1, transform.plotBottom),
                strokeWidth = 1f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3f, 4f), 0f),
            )
        }
        if (interval.endDistanceNm < transform.totalDistanceNm - 0.5) {
            drawLine(
                color = Color(0x88F59E0B),
                start = Offset(x2, transform.plotTop),
                end = Offset(x2, transform.plotBottom),
                strokeWidth = 1f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(3f, 4f), 0f),
            )
        }
    }
}

// ============================================================================
// 2. Grid Lines & Left/Right/Bottom Axes
// ============================================================================

private fun DrawScope.drawGridAndAxes(
    transform: CoordTransform,
    vizData: VizRouteData,
    theme: CrossSectionTheme,
) {
    val gridColor = Color(theme.gridLineColor)
    val altStep = when {
        vizData.maxAltitudeFt <= 12000.0 -> 2000.0
        vizData.maxAltitudeFt <= 26000.0 -> 4000.0
        else -> 5000.0
    }

    val leftTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(215, 203, 213, 225)
        textSize = 9.sp.toPx()
        textAlign = Paint.Align.RIGHT
        typeface = Typeface.MONOSPACE
    }
    val rightTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(215, 203, 213, 225)
        textSize = 9.sp.toPx()
        textAlign = Paint.Align.LEFT
        typeface = Typeface.MONOSPACE
    }
    val headerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(180, 148, 163, 184)
        textSize = 8.5.sp.toPx()
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }

    // Top axis headers: "ft" on left, "FL" on right
    headerPaint.textAlign = Paint.Align.RIGHT
    drawContext.canvas.nativeCanvas.drawText("ft", transform.plotLeft - 5f, transform.plotTop - 4f, headerPaint)
    headerPaint.textAlign = Paint.Align.LEFT
    drawContext.canvas.nativeCanvas.drawText("FL", transform.plotRight + 5f, transform.plotTop - 4f, headerPaint)

    // Altitude horizontal grid lines
    var alt = 0.0
    while (alt <= vizData.maxAltitudeFt) {
        val y = transform.altitudeToY(alt)
        drawLine(
            color = gridColor,
            start = Offset(transform.plotLeft, y),
            end = Offset(transform.plotRight, y),
            strokeWidth = if (alt == 0.0) 1.2f else 0.8f,
        )
        if (alt > 0.0) {
            val ftLabel = String.format(Locale.US, "%,d", alt.roundToInt())
            val flLabel = String.format(Locale.US, "FL%03d", (alt / 100.0).roundToInt())
            drawContext.canvas.nativeCanvas.drawText(ftLabel, transform.plotLeft - 5f, y + 3.5f, leftTextPaint)
            drawContext.canvas.nativeCanvas.drawText(flLabel, transform.plotRight + 5f, y + 3.5f, rightTextPaint)
        }
        alt += altStep
    }

    // Distance vertical grid lines
    val distStep = when {
        vizData.totalDistanceNm <= 80.0 -> 10.0
        vizData.totalDistanceNm <= 200.0 -> 25.0
        vizData.totalDistanceNm <= 500.0 -> 50.0
        else -> 100.0
    }
    val distPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(185, 148, 163, 184)
        textSize = 8.5.sp.toPx()
        textAlign = Paint.Align.CENTER
        typeface = Typeface.MONOSPACE
    }
    var dist = distStep
    while (dist < vizData.totalDistanceNm) {
        val x = transform.distanceToX(dist)
        drawLine(
            color = gridColor,
            start = Offset(x, transform.plotTop),
            end = Offset(x, transform.plotBottom),
            strokeWidth = 0.8f,
        )
        drawContext.canvas.nativeCanvas.drawText(
            "${dist.roundToInt()}nm",
            x,
            transform.plotBottom + 28f,
            distPaint,
        )
        dist += distStep
    }

    // Plot outline
    drawRect(
        color = gridColor.copy(alpha = 0.65f),
        topLeft = Offset(transform.plotLeft, transform.plotTop),
        size = Size(transform.plotWidth, transform.plotHeight),
        style = Stroke(width = 1f),
    )
}

// ============================================================================
// 3. Cloud Bands (Square, Soft, and Natural styles for NWP & DD)
// ============================================================================

private fun DrawScope.drawAllCloudLayers(
    transform: CoordTransform,
    points: List<VizPoint>,
    effectiveLayers: Map<String, Boolean>,
    cloudStyle: CloudStyle,
    theme: CrossSectionTheme,
) {
    if (points.isEmpty()) return

    // Check explicit web/iOS layer IDs first; otherwise use CrossSectionPresets short IDs + cloudStyle
    val hasExplicitSquareNwp = effectiveLayers["square-nwp-clouds"] == true || effectiveLayers["square-nwp-cloud-bands"] == true
    val hasExplicitSquareDd = effectiveLayers["square-dd-clouds"] == true || effectiveLayers["square-cloud-bands"] == true
    val hasExplicitSoftNwp = effectiveLayers["soft-nwp-clouds"] == true || effectiveLayers["soft-nwp-cloud-bands"] == true
    val hasExplicitSoftDd = effectiveLayers["soft-dd-clouds"] == true || effectiveLayers["soft-cloud-bands"] == true
    val hasExplicitNaturalNwp = effectiveLayers["nwp-cloud-bands"] == true
    val hasExplicitNaturalDd = effectiveLayers["dd-cloud-bands"] == true || effectiveLayers["cloud-bands"] == true

    val shortNwpOn = effectiveLayers["clouds-nwp"] == true ||
        effectiveLayers["clouds-icing-nwp"] == true ||
        effectiveLayers["clouds-diag-nwp"] == true ||
        effectiveLayers["clouds-band-nwp"] == true
    val shortDdOn = effectiveLayers["clouds"] == true ||
        effectiveLayers["clouds-icing"] == true ||
        effectiveLayers["clouds-diag"] == true ||
        effectiveLayers["clouds-band"] == true

    if (hasExplicitSquareNwp || (shortNwpOn && cloudStyle == CloudStyle.SQUARE)) {
        drawSquareCloudBands(transform, points, useNwp = true, theme = theme)
    }
    if (hasExplicitSquareDd || (shortDdOn && cloudStyle == CloudStyle.SQUARE)) {
        drawSquareCloudBands(transform, points, useNwp = false, theme = theme)
    }

    if (hasExplicitSoftNwp || (shortNwpOn && cloudStyle == CloudStyle.SOFT)) {
        drawSoftCloudBands(transform, points, useNwp = true, theme = theme)
    }
    if (hasExplicitSoftDd || (shortDdOn && cloudStyle == CloudStyle.SOFT)) {
        drawSoftCloudBands(transform, points, useNwp = false, theme = theme)
    }

    if (hasExplicitNaturalNwp || (shortNwpOn && cloudStyle == CloudStyle.NATURAL)) {
        drawNaturalCloudBands(transform, points, useNwp = true, theme = theme)
    }
    if (hasExplicitNaturalDd || (shortDdOn && cloudStyle == CloudStyle.NATURAL)) {
        drawNaturalCloudBands(transform, points, useNwp = false, theme = theme)
    }
}

private fun extractCloudList(point: VizPoint, useNwp: Boolean): List<VizCloudLayer> =
    if (useNwp) {
        point.nwpCloudLayers.ifEmpty { point.cloudLayers }
    } else {
        point.cloudLayers
    }

private fun DrawScope.drawSquareCloudBands(
    transform: CoordTransform,
    points: List<VizPoint>,
    useNwp: Boolean,
    theme: CrossSectionTheme,
) {
    for (i in points.indices) {
        val pt = points[i]
        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val w = max(1f, xRight - xLeft)

        val layers = extractCloudList(pt, useNwp)
        for (layer in layers) {
            if (layer.topFt <= layer.baseFt) continue
            val fill = ColorScales.cloudFillColor(
                coverage = layer.coverage,
                dewpointDepressionC = layer.meanDewpointDepressionC,
                coveragePct = layer.coveragePct,
                theme = theme,
            )
            if (fill.alpha <= 0.01f) continue
            val yTop = transform.altitudeToY(layer.topFt)
            val yBot = transform.altitudeToY(layer.baseFt)
            val h = max(1.5f, yBot - yTop)
            drawRect(
                color = fill,
                topLeft = Offset(xLeft, yTop),
                size = Size(w, h),
            )
            drawRect(
                color = Color(theme.cloudStrokeColor).copy(alpha = fill.alpha * 0.55f),
                topLeft = Offset(xLeft, yTop),
                size = Size(w, h),
                style = Stroke(width = 0.7f),
            )
        }
    }
}

private data class MatchedCloudBand(
    val points: MutableList<Triple<Double, Double, Double>>, // (distanceNm, baseFt, topFt)
    var coverage: String,
    var meanDd: Double?,
    var coveragePct: Double?,
)

private fun buildMatchedCloudBands(
    points: List<VizPoint>,
    useNwp: Boolean,
): List<MatchedCloudBand> {
    val completed = mutableListOf<MatchedCloudBand>()
    var active = mutableListOf<MatchedCloudBand>()

    for (i in points.indices) {
        val pt = points[i]
        val d = pt.distanceNm
        val prevD = if (i > 0) points[i - 1].distanceNm else d
        val layers = extractCloudList(pt, useNwp).filter { it.topFt > it.baseFt }
        val usedLayerIndices = mutableSetOf<Int>()
        val nextActive = mutableListOf<MatchedCloudBand>()

        for (band in active) {
            val last = band.points.last()
            var bestIdx = -1
            var bestOverlap = 0.0
            for (lIdx in layers.indices) {
                if (lIdx in usedLayerIndices) continue
                val l = layers[lIdx]
                val overlap = min(last.third, l.topFt) - max(last.second, l.baseFt)
                if (overlap > -400.0 && overlap > bestOverlap - 400.0) {
                    bestOverlap = overlap
                    bestIdx = lIdx
                }
            }
            if (bestIdx >= 0) {
                usedLayerIndices.add(bestIdx)
                val l = layers[bestIdx]
                band.points.add(Triple(d, l.baseFt, l.topFt))
                if (ColorScales.coverageToPct(l.coverage) > ColorScales.coverageToPct(band.coverage)) {
                    band.coverage = l.coverage
                }
                if (l.coveragePct != null) {
                    band.coveragePct = max(band.coveragePct ?: 0.0, l.coveragePct)
                }
                nextActive.add(band)
            } else {
                // Taper unmatched band to midpoint toward current point
                val midD = (prevD + d) * 0.5
                val midAlt = (last.second + last.third) * 0.5
                band.points.add(Triple(midD, midAlt, midAlt))
                completed.add(band)
            }
        }

        // Start new bands for unmatched layers at this point
        for (lIdx in layers.indices) {
            if (lIdx in usedLayerIndices) continue
            val l = layers[lIdx]
            val bandPoints = mutableListOf<Triple<Double, Double, Double>>()
            if (i > 0) {
                val midD = (prevD + d) * 0.5
                val midAlt = (l.baseFt + l.topFt) * 0.5
                bandPoints.add(Triple(midD, midAlt, midAlt))
            }
            bandPoints.add(Triple(d, l.baseFt, l.topFt))
            nextActive.add(
                MatchedCloudBand(
                    points = bandPoints,
                    coverage = l.coverage,
                    meanDd = l.meanDewpointDepressionC,
                    coveragePct = l.coveragePct,
                ),
            )
        }

        active = nextActive
    }

    completed.addAll(active)
    return completed
}

private fun DrawScope.drawSoftCloudBands(
    transform: CoordTransform,
    points: List<VizPoint>,
    useNwp: Boolean,
    theme: CrossSectionTheme,
) {
    val bands = buildMatchedCloudBands(points, useNwp)
    for (band in bands) {
        if (band.points.size < 2) continue
        val fill = ColorScales.cloudFillColor(
            coverage = band.coverage,
            dewpointDepressionC = band.meanDd,
            coveragePct = band.coveragePct,
            theme = theme,
        )
        if (fill.alpha <= 0.01f) continue

        val stepCount = max(16, band.points.size * 5)
        val topCurve = MonotoneCubicSpline.sampleCurve(
            band.points.map { it.first to it.third },
            stepCount = stepCount,
        )
        val baseCurve = MonotoneCubicSpline.sampleCurve(
            band.points.map { it.first to it.second },
            stepCount = stepCount,
        )

        val path = Path()
        var minY = Float.POSITIVE_INFINITY
        var maxY = Float.NEGATIVE_INFINITY

        topCurve.forEachIndexed { idx, (d, alt) ->
            val x = transform.distanceToX(d)
            val y = transform.altitudeToY(alt)
            minY = min(minY, y)
            maxY = max(maxY, y)
            if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        for (idx in baseCurve.indices.reversed()) {
            val (d, alt) = baseCurve[idx]
            val x = transform.distanceToX(d)
            val y = transform.altitudeToY(alt)
            minY = min(minY, y)
            maxY = max(maxY, y)
            path.lineTo(x, y)
        }
        path.close()

        if (maxY - minY < 1f) maxY = minY + 2f
        val featherBrush = Brush.verticalGradient(
            0.0f to fill.copy(alpha = 0f),
            0.18f to fill,
            0.82f to fill,
            1.0f to fill.copy(alpha = 0f),
            startY = minY,
            endY = maxY,
        )
        drawPath(path = path, brush = featherBrush)
    }
}

private fun hash01(a: Int, b: Int, c: Int): Float {
    var h = a * 374761393 + b * 668265263 + c * 2147483647
    h = (h xor (h ushr 13)) * 1274126177
    h = h xor (h ushr 16)
    return ((h and 0x7fffffff).toFloat() / Int.MAX_VALUE.toFloat())
}

private fun DrawScope.drawNaturalCloudBands(
    transform: CoordTransform,
    points: List<VizPoint>,
    useNwp: Boolean,
    theme: CrossSectionTheme,
) {
    for (i in points.indices) {
        val pt = points[i]
        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val spanW = max(2f, xRight - xLeft)
        val layers = extractCloudList(pt, useNwp)

        layers.forEachIndexed { layerIdx, layer ->
            if (layer.topFt <= layer.baseFt) return@forEachIndexed
            val fill = ColorScales.cloudFillColor(
                coverage = layer.coverage,
                dewpointDepressionC = layer.meanDewpointDepressionC,
                coveragePct = layer.coveragePct,
                theme = theme,
            )
            if (fill.alpha <= 0.01f) return@forEachIndexed

            val covLower = layer.coverage.lowercase(Locale.US)
            val subSlots = when (covLower) {
                "few" -> 3
                "sct" -> 3
                else -> 1
            }
            val subW = spanW / subSlots.toFloat()

            for (slot in 0 until subSlots) {
                val keepThreshold = when (covLower) {
                    "few" -> 0.42f
                    "sct" -> 0.68f
                    else -> 1.0f
                }
                if (subSlots > 1 && hash01(i, layerIdx, slot) > keepThreshold && slot != 1) continue

                val sLeft = xLeft + slot * subW
                val sRight = sLeft + subW
                val yTop = transform.altitudeToY(layer.topFt)
                val yBot = transform.altitudeToY(layer.baseFt)
                val h = max(3f, yBot - yTop)
                val bumpAmp = min(8f, max(2.5f, h * 0.22f))
                val bumps = max(2, (subW / 10f).roundToInt())
                val bumpW = subW / bumps.toFloat()

                val puffPath = Path().apply {
                    // Flat bottom
                    moveTo(sLeft, yBot)
                    lineTo(sLeft, yTop + bumpAmp * 0.5f)
                    // Scalloped bumpy cloud top
                    for (b in 0 until bumps) {
                        val bx0 = sLeft + b * bumpW
                        val bx1 = bx0 + bumpW
                        val bxMid = (bx0 + bx1) * 0.5f
                        val peakOffset = bumpAmp * (0.65f + 0.35f * hash01(i + b, layerIdx, slot))
                        quadraticTo(bxMid, yTop - peakOffset, bx1, yTop + bumpAmp * 0.4f)
                    }
                    lineTo(sRight, yBot)
                    close()
                }

                drawPath(path = puffPath, color = fill)
                drawPath(
                    path = puffPath,
                    color = Color(theme.cloudStrokeColor).copy(alpha = fill.alpha * 0.7f),
                    style = Stroke(width = 1f, join = StrokeJoin.Round),
                )
            }
        }
    }
}

// ============================================================================
// 4. Convective Backgrounds (NWP column + Thermo CAPE tower)
// ============================================================================

private fun DrawScope.drawNwpConvectiveBg(
    transform: CoordTransform,
    points: List<VizPoint>,
    theme: CrossSectionTheme,
) {
    for (i in points.indices) {
        val pt = points[i]
        val conv = pt.nwpConvective ?: continue
        val risk = conv.riskLevel.lowercase(Locale.US)
        if (risk == "none" || risk.isBlank()) continue

        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val w = max(2f, xRight - xLeft)
        val baseColor = ColorScales.convectiveRiskColor(risk, theme.id)

        if (conv.isDepthUnresolved || conv.baseFt == null || conv.topFt == null || conv.topFt <= conv.baseFt) {
            // Unresolved depth ghost column from terrain to top of plot
            val yTop = transform.plotTop
            val yBot = transform.altitudeToY(pt.terrainElevationFt)
            drawRect(
                color = baseColor.copy(alpha = 0.16f),
                topLeft = Offset(xLeft, yTop),
                size = Size(w, max(2f, yBot - yTop)),
            )
            val dash = PathEffect.dashPathEffect(floatArrayOf(5f, 4f), 0f)
            drawLine(
                color = baseColor.copy(alpha = 0.65f),
                start = Offset(xLeft, yTop),
                end = Offset(xLeft, yBot),
                strokeWidth = 1.2f,
                pathEffect = dash,
            )
            drawLine(
                color = baseColor.copy(alpha = 0.65f),
                start = Offset(xRight, yTop),
                end = Offset(xRight, yBot),
                strokeWidth = 1.2f,
                pathEffect = dash,
            )
            drawPillLabel(
                text = "?",
                centerX = (xLeft + xRight) * 0.5f,
                topY = yTop + 4f,
                bgColor = baseColor.copy(alpha = 0.85f),
            )
        } else {
            val yTop = transform.altitudeToY(conv.topFt)
            val yBot = transform.altitudeToY(conv.baseFt)
            val h = max(3f, yBot - yTop)
            drawRect(
                color = baseColor.copy(alpha = 0.42f),
                topLeft = Offset(xLeft, yTop),
                size = Size(w, h),
            )
            drawRect(
                color = baseColor.copy(alpha = 0.8f),
                topLeft = Offset(xLeft, yTop),
                size = Size(w, h),
                style = Stroke(width = 1f),
            )
        }
    }
}

private fun estimateThermoTowerTop(pt: VizPoint): Double? {
    val conv = pt.thermoConvective
    conv.elFt?.let { if (it > 0.0) return it }
    conv.topFt?.let { if (it > 0.0) return it }
    val cape = conv.capeJkg ?: pt.capeSurfaceJkg ?: return null
    if (cape <= 25.0) return null
    val base = conv.lfcFt ?: conv.lclFt ?: conv.baseFt ?: (pt.terrainElevationFt + 3000.0)
    return base + min(28000.0, 2500.0 + sqrt(cape) * 420.0)
}

private fun DrawScope.drawThermoConvectiveBg(
    transform: CoordTransform,
    points: List<VizPoint>,
    theme: CrossSectionTheme,
) {
    for (i in points.indices) {
        val pt = points[i]
        val conv = pt.thermoConvective
        val risk = conv.riskLevel.lowercase(Locale.US)
        val cape = conv.capeJkg ?: pt.capeSurfaceJkg ?: 0.0
        if ((risk == "none" || risk.isBlank()) && cape < 80.0) continue

        val effectiveRisk = if (risk != "none" && risk.isNotBlank()) {
            risk
        } else {
            when {
                cape >= 1500.0 -> "high"
                cape >= 500.0 -> "moderate"
                else -> "marginal"
            }
        }

        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val w = max(2f, xRight - xLeft)
        val baseColor = ColorScales.convectiveRiskColor(effectiveRisk, theme.id)

        // Subtle column background wash
        drawRect(
            color = baseColor.copy(alpha = 0.12f),
            topLeft = Offset(xLeft, transform.plotTop),
            size = Size(w, transform.plotHeight),
        )

        val baseFt = conv.lfcFt ?: conv.baseFt ?: conv.lclFt ?: (pt.terrainElevationFt + 2500.0)
        val topFt = estimateThermoTowerTop(pt) ?: (baseFt + 8000.0)
        if (topFt <= baseFt) continue

        val yTop = transform.altitudeToY(topFt)
        val yBot = transform.altitudeToY(baseFt)
        val h = max(4f, yBot - yTop)

        val towerRect = Rect(xLeft, yTop, xRight, yBot)
        val towerPath = Path().apply { addRect(towerRect) }

        // Tower fill + 45-degree diagonal hatching
        drawRect(
            color = baseColor.copy(alpha = 0.36f),
            topLeft = Offset(xLeft, yTop),
            size = Size(w, h),
        )
        drawDiagonalHatching(
            bounds = towerRect,
            clip = towerPath,
            color = baseColor.copy(alpha = 0.72f),
            spacingPx = 7.dp.toPx(),
        )
        drawRect(
            color = baseColor.copy(alpha = 0.9f),
            topLeft = Offset(xLeft, yTop),
            size = Size(w, h),
            style = Stroke(width = 1.2f),
        )

        // Anvil cap near EL + TCU/CB/+TS pill label
        val anvilH = min(8f, h * 0.12f)
        drawRect(
            color = baseColor.copy(alpha = 0.65f),
            topLeft = Offset(max(transform.plotLeft, xLeft - 3f), yTop),
            size = Size(min(transform.plotRight - transform.plotLeft, w + 6f), anvilH),
        )

        val badgeLabel = when (effectiveRisk) {
            "extreme" -> "+TS"
            "high", "severe" -> "CB"
            else -> "TCU"
        }
        drawPillLabel(
            text = badgeLabel,
            centerX = (xLeft + xRight) * 0.5f,
            topY = max(transform.plotTop + 2f, yTop - 14f),
            bgColor = baseColor.copy(alpha = 0.92f),
        )
    }
}

// ============================================================================
// 5 & 6. Icing, Turbulence, Inversion & Surface Obscuration Bands
// ============================================================================

private data class BandZone(
    val baseFt: Double,
    val topFt: Double,
    val risk: String,
)

private data class MatchedHazardBand(
    val points: MutableList<Triple<Double, Double, Double>>,
    var risk: String,
)

private fun riskPriority(risk: String): Int = when (risk.lowercase(Locale.US)) {
    "severe", "high", "extreme" -> 3
    "moderate" -> 2
    "light", "low", "marginal" -> 1
    else -> 0
}

private fun buildMatchedHazardBands(
    points: List<VizPoint>,
    extractZones: (VizPoint) -> List<BandZone>,
): List<MatchedHazardBand> {
    val completed = mutableListOf<MatchedHazardBand>()
    var active = mutableListOf<MatchedHazardBand>()

    for (i in points.indices) {
        val pt = points[i]
        val d = pt.distanceNm
        val prevD = if (i > 0) points[i - 1].distanceNm else d
        val zones = extractZones(pt).filter { it.topFt > it.baseFt && riskPriority(it.risk) > 0 }
        val usedIndices = mutableSetOf<Int>()
        val nextActive = mutableListOf<MatchedHazardBand>()

        for (band in active) {
            val last = band.points.last()
            var bestIdx = -1
            var bestOverlap = 0.0
            for (zIdx in zones.indices) {
                if (zIdx in usedIndices) continue
                val z = zones[zIdx]
                val overlap = min(last.third, z.topFt) - max(last.second, z.baseFt)
                if (overlap > -300.0 && overlap > bestOverlap - 300.0) {
                    bestOverlap = overlap
                    bestIdx = zIdx
                }
            }
            if (bestIdx >= 0) {
                usedIndices.add(bestIdx)
                val z = zones[bestIdx]
                band.points.add(Triple(d, z.baseFt, z.topFt))
                if (riskPriority(z.risk) > riskPriority(band.risk)) {
                    band.risk = z.risk
                }
                nextActive.add(band)
            } else {
                val midD = (prevD + d) * 0.5
                val midAlt = (last.second + last.third) * 0.5
                band.points.add(Triple(midD, midAlt, midAlt))
                completed.add(band)
            }
        }

        for (zIdx in zones.indices) {
            if (zIdx in usedIndices) continue
            val z = zones[zIdx]
            val pts = mutableListOf<Triple<Double, Double, Double>>()
            if (i > 0) {
                val midD = (prevD + d) * 0.5
                val midAlt = (z.baseFt + z.topFt) * 0.5
                pts.add(Triple(midD, midAlt, midAlt))
            }
            pts.add(Triple(d, z.baseFt, z.topFt))
            if (i == points.lastIndex && pts.size == 1) {
                val prevMid = if (i > 0) (prevD + d) * 0.5 else max(0.0, d - 2.0)
                pts.add(0, Triple(prevMid, z.baseFt, z.topFt))
            }
            nextActive.add(MatchedHazardBand(points = pts, risk = z.risk))
        }

        active = nextActive
    }

    // Ensure single-point bands at route start have width
    for (band in active) {
        if (band.points.size == 1 && points.size > 1) {
            val first = band.points.first()
            val nextMid = (first.first + points[1].distanceNm) * 0.5
            band.points.add(Triple(nextMid, first.second, first.third))
        }
    }
    completed.addAll(active)
    return completed
}

private fun DrawScope.drawHazardBands(
    transform: CoordTransform,
    points: List<VizPoint>,
    extractZones: (VizPoint) -> List<BandZone>,
    colorForRisk: (String) -> Color,
    hatched: Boolean = false,
) {
    val bands = buildMatchedHazardBands(points, extractZones)
    for (band in bands) {
        if (band.points.size < 2) continue
        val fill = colorForRisk(band.risk)
        if (fill.alpha <= 0.01f) continue

        val stepCount = max(14, band.points.size * 5)
        val topCurve = MonotoneCubicSpline.sampleCurve(
            band.points.map { it.first to it.third },
            stepCount = stepCount,
        )
        val baseCurve = MonotoneCubicSpline.sampleCurve(
            band.points.map { it.first to it.second },
            stepCount = stepCount,
        )

        val path = Path()
        var minX = Float.POSITIVE_INFINITY
        var maxX = Float.NEGATIVE_INFINITY
        var minY = Float.POSITIVE_INFINITY
        var maxY = Float.NEGATIVE_INFINITY

        topCurve.forEachIndexed { idx, (d, alt) ->
            val x = transform.distanceToX(d)
            val y = transform.altitudeToY(alt)
            minX = min(minX, x)
            maxX = max(maxX, x)
            minY = min(minY, y)
            maxY = max(maxY, y)
            if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        for (idx in baseCurve.indices.reversed()) {
            val (d, alt) = baseCurve[idx]
            val x = transform.distanceToX(d)
            val y = transform.altitudeToY(alt)
            minX = min(minX, x)
            maxX = max(maxX, x)
            minY = min(minY, y)
            maxY = max(maxY, y)
            path.lineTo(x, y)
        }
        path.close()

        drawPath(path = path, color = fill)
        if (hatched && maxX > minX && maxY > minY) {
            drawDiagonalHatching(
                bounds = Rect(minX, minY, maxX, maxY),
                clip = path,
                color = fill.copy(alpha = min(1f, fill.alpha + 0.25f)),
                spacingPx = 6.dp.toPx(),
            )
        }
        drawPath(
            path = path,
            color = fill.copy(alpha = min(1f, fill.alpha + 0.25f)),
            style = Stroke(width = 1f),
        )
    }
}

private fun DrawScope.drawInversionBands(
    transform: CoordTransform,
    points: List<VizPoint>,
    theme: CrossSectionTheme,
) {
    for (i in points.indices) {
        val pt = points[i]
        if (pt.inversionLayers.isEmpty()) continue
        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val w = max(2f, xRight - xLeft)
        for (inv in pt.inversionLayers) {
            if (inv.topFt <= inv.baseFt) continue
            val color = ColorScales.inversionColor(inv.strengthC, theme.id)
            val yTop = transform.altitudeToY(inv.topFt)
            val yBot = transform.altitudeToY(inv.baseFt)
            drawRect(
                color = color,
                topLeft = Offset(xLeft, yTop),
                size = Size(w, max(2f, yBot - yTop)),
            )
        }
    }
}

private fun DrawScope.drawSurfaceObscurationBands(
    transform: CoordTransform,
    points: List<VizPoint>,
) {
    for (i in points.indices) {
        val pt = points[i]
        val visM = pt.visibilityMeters
        val ceilAgl = pt.soundingCeilingFt?.let { it - pt.terrainElevationFt }
            ?: pt.nwpCeilingFt?.let { it - pt.terrainElevationFt }

        val isLifr = (visM != null && visM < 1600.0) || (ceilAgl != null && ceilAgl < 500.0)
        val isIfr = (visM != null && visM < 5000.0) || (ceilAgl != null && ceilAgl < 1000.0)
        if (!isLifr && !isIfr) continue

        val xLeft = if (i == 0) {
            transform.plotLeft
        } else {
            transform.distanceToX((points[i - 1].distanceNm + pt.distanceNm) * 0.5)
        }
        val xRight = if (i == points.lastIndex) {
            transform.plotRight
        } else {
            transform.distanceToX((pt.distanceNm + points[i + 1].distanceNm) * 0.5)
        }
        val yTop = transform.altitudeToY(pt.terrainElevationFt + 900.0)
        val yBot = transform.altitudeToY(pt.terrainElevationFt)
        val bandColor = if (isLifr) Color(0x88D946EF) else Color(0x77EF4444)
        val rect = Rect(xLeft, yTop, xRight, max(yTop + 3f, yBot))
        val path = Path().apply { addRect(rect) }
        drawRect(
            color = bandColor,
            topLeft = rect.topLeft,
            size = rect.size,
        )
        drawDiagonalHatching(
            bounds = rect,
            clip = path,
            color = bandColor.copy(alpha = 0.9f),
            spacingPx = 5.dp.toPx(),
        )
    }
}

// ============================================================================
// 7. Terrain Fill & Ridge Stroke
// ============================================================================

private fun DrawScope.drawTerrainLayer(
    transform: CoordTransform,
    vizData: VizRouteData,
    theme: CrossSectionTheme,
) {
    val rawTerrain: List<Pair<Double, Double>> = if (vizData.terrainProfile.isNotEmpty()) {
        vizData.terrainProfile.map { it.distanceNm to it.elevationFt }
    } else {
        vizData.points.map { it.distanceNm to it.terrainElevationFt }
    }
    if (rawTerrain.size < 2) return

    val fillPath = Path()
    val ridgePath = Path()
    var minRidgeY = transform.plotBottom

    rawTerrain.forEachIndexed { idx, (d, elevFt) ->
        val x = transform.distanceToX(d)
        val y = transform.altitudeToY(elevFt.coerceAtLeast(0.0))
        minRidgeY = min(minRidgeY, y)
        if (idx == 0) {
            fillPath.moveTo(x, transform.plotBottom)
            fillPath.lineTo(x, y)
            ridgePath.moveTo(x, y)
        } else {
            fillPath.lineTo(x, y)
            ridgePath.lineTo(x, y)
        }
    }
    val lastX = transform.distanceToX(rawTerrain.last().first)
    fillPath.lineTo(lastX, transform.plotBottom)
    fillPath.close()

    val terrainBrush = Brush.verticalGradient(
        colors = listOf(Color(theme.terrainFillTopColor), Color(theme.terrainFillBottomColor)),
        startY = min(minRidgeY, transform.plotBottom - 10f),
        endY = transform.plotBottom,
    )
    drawPath(path = fillPath, brush = terrainBrush)
    drawPath(
        path = ridgePath,
        color = Color(theme.terrainStrokeColor),
        style = Stroke(width = 1.8f, join = StrokeJoin.Round),
    )
}

// ============================================================================
// 8. Current Conditions (METAR columns + SIGMET hatched boxes) & Front Markers
// ============================================================================

private fun DrawScope.drawCurrentConditionsLayer(
    transform: CoordTransform,
    conditions: VizCurrentConditions,
) {
    // SIGMET hatched boxes
    for (sigmet in conditions.sigmets) {
        val midNm = (sigmet.startDistanceNm + sigmet.endDistanceNm) * 0.5
        val spanNm = max(5.0, sigmet.endDistanceNm - sigmet.startDistanceNm)
        val xLeft = transform.distanceToX(midNm - spanNm * 0.5)
        val xRight = transform.distanceToX(midNm + spanNm * 0.5)
        val yTop = transform.altitudeToY(sigmet.topFt)
        val yBot = transform.altitudeToY(sigmet.baseFt)
        if (xRight <= xLeft || yBot <= yTop) continue

        val rect = Rect(xLeft, yTop, xRight, yBot)
        val clip = Path().apply { addRect(rect) }
        val sigColor = Color(0xFFEF4444)

        drawRect(
            color = sigColor.copy(alpha = 0.16f),
            topLeft = rect.topLeft,
            size = rect.size,
        )
        drawDiagonalHatching(
            bounds = rect,
            clip = clip,
            color = sigColor.copy(alpha = 0.55f),
            spacingPx = 8.dp.toPx(),
        )
        drawRect(
            color = sigColor.copy(alpha = 0.85f),
            topLeft = rect.topLeft,
            size = rect.size,
            style = Stroke(
                width = 1.3f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f), 0f),
            ),
        )
        drawPillLabel(
            text = sigmet.hazard.uppercase(Locale.US),
            centerX = (xLeft + xRight) * 0.5f,
            topY = max(transform.plotTop + 2f, yTop + 3f),
            bgColor = sigColor.copy(alpha = 0.9f),
        )
    }

    // METAR flight-category columns (±2 NM, 5000 ft tall above terrain)
    for (airport in conditions.airports) {
        val xLeft = transform.distanceToX(airport.distanceNm - 2.0)
        val xRight = transform.distanceToX(airport.distanceNm + 2.0)
        val colW = max(6f, xRight - xLeft)
        val xCenter = transform.distanceToX(airport.distanceNm)
        val yBot = transform.plotBottom
        val yTop = transform.altitudeToY(5000.0)
        val catColor = ColorScales.flightCategoryColor(airport.flightCategory)

        val colBrush = Brush.verticalGradient(
            colors = listOf(catColor.copy(alpha = 0.05f), catColor.copy(alpha = 0.55f)),
            startY = yTop,
            endY = yBot,
        )
        drawRect(
            brush = colBrush,
            topLeft = Offset(xCenter - colW * 0.5f, yTop),
            size = Size(colW, max(4f, yBot - yTop)),
        )
        drawPillLabel(
            text = "${airport.icao} ${airport.flightCategory}",
            centerX = xCenter,
            topY = max(transform.plotTop + 4f, yTop - 12f),
            bgColor = catColor.copy(alpha = 0.88f),
        )
    }
}

private fun DrawScope.drawFrontMarkersLayer(
    transform: CoordTransform,
    fronts: List<VizFrontMarker>,
) {
    for (front in fronts) {
        val x = transform.distanceToX(front.distanceNm)
        val color = ColorScales.frontTypeColor(front.frontType)
        val strokeW = (1.5f + front.intensity.toFloat() * 1.2f).coerceIn(1.5f, 3.5f)
        val dashEffect = if (!front.isWet) {
            PathEffect.dashPathEffect(floatArrayOf(6f, 4f), 0f)
        } else {
            PathEffect.dashPathEffect(floatArrayOf(10f, 3f), 0f)
        }

        drawLine(
            color = color.copy(alpha = 0.85f),
            start = Offset(x, transform.plotTop),
            end = Offset(x, transform.plotBottom),
            strokeWidth = strokeW,
            pathEffect = dashEffect,
        )

        val badgeLetter = front.frontType.firstOrNull()?.uppercaseChar()?.toString() ?: "F"
        drawPillLabel(
            text = badgeLetter,
            centerX = x,
            topY = transform.plotTop + 3f,
            bgColor = color.copy(alpha = 0.92f),
        )
    }
}

// ============================================================================
// 9. Smooth Monotone Cubic Spline Altitude Lines
// ============================================================================

private fun DrawScope.drawAltitudeLinesLayer(
    transform: CoordTransform,
    vizData: VizRouteData,
    effectiveLayers: Map<String, Boolean>,
    theme: CrossSectionTheme,
) {
    val points = vizData.points
    if (points.isEmpty()) return

    // Temperature lines
    if (isLayerOn(effectiveLayers, "freezing-level", "freezing")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.freezingLevelFt },
            color = Color(theme.freezingLineColor),
            strokeWidth = 2.0f,
            dashPattern = floatArrayOf(8f, 4f),
            label = "0°C",
        )
    }
    if (isLayerOn(effectiveLayers, "minus-10c", "minus-10")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.minus10cLevelFt },
            color = Color(theme.minus10LineColor),
            strokeWidth = 1.7f,
            dashPattern = floatArrayOf(6f, 4f),
            label = "-10°C",
        )
    }
    if (isLayerOn(effectiveLayers, "minus-20c", "minus-20")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.minus20cLevelFt },
            color = Color(theme.minus20LineColor),
            strokeWidth = 1.7f,
            dashPattern = floatArrayOf(4f, 4f),
            label = "-20°C",
        )
    }

    // Stability lines
    if (isLayerOn(effectiveLayers, "lcl")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.lclFt },
            color = Color(theme.lclLineColor),
            strokeWidth = 1.6f,
            dashPattern = floatArrayOf(2f, 4f),
            label = "LCL",
        )
    }
    if (isLayerOn(effectiveLayers, "lfc")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.lfcFt },
            color = Color(theme.lfcLineColor),
            strokeWidth = 1.6f,
            dashPattern = floatArrayOf(6f, 4f),
            label = "LFC",
        )
    }
    if (isLayerOn(effectiveLayers, "el")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.altitudeLines.elFt },
            color = Color(theme.elLineColor),
            strokeWidth = 1.6f,
            dashPattern = floatArrayOf(9f, 3f, 2f, 3f),
            label = "EL",
        )
    }
    if (isLayerOn(effectiveLayers, "ceiling")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.soundingCeilingFt },
            color = Color(theme.ceilingLineColor),
            strokeWidth = 1.6f,
            dashPattern = floatArrayOf(5f, 4f),
            label = "Ceil DD",
        )
    }
    if (isLayerOn(effectiveLayers, "ceiling-nwp")) {
        drawSmoothAltitudeLine(
            transform = transform,
            points = points,
            extractAlt = { it.nwpCeilingFt },
            color = Color(theme.ceilingLineColor),
            strokeWidth = 1.6f,
            dashPattern = floatArrayOf(8f, 3f),
            label = "Ceil NWP",
        )
    }

    // Reference lines: Cruise Altitude & Flight Ceiling
    if (isLayerOn(effectiveLayers, "cruise-altitude", "cruise", "reference-lines", defaultOn = true)) {
        val cruiseY = transform.altitudeToY(vizData.cruiseAltitudeFt)
        val cruiseColor = Color(theme.cruiseLineColor)
        drawLine(
            color = cruiseColor,
            start = Offset(transform.plotLeft, cruiseY),
            end = Offset(transform.plotRight, cruiseY),
            strokeWidth = 2.0f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 5f), 0f),
        )
        val cruiseLabel = if (vizData.cruiseAltitudeFt >= 10000.0) {
            String.format(Locale.US, "FL%03d", (vizData.cruiseAltitudeFt / 100.0).roundToInt())
        } else {
            "${String.format(Locale.US, "%,d", vizData.cruiseAltitudeFt.roundToInt())} ft"
        }
        drawPillLabel(
            text = cruiseLabel,
            centerX = transform.plotLeft + 28f,
            topY = max(transform.plotTop + 2f, cruiseY - 15f),
            bgColor = Color(0xCC0F172A),
            textColor = cruiseColor,
        )
    }

    if (isLayerOn(effectiveLayers, "flight-ceiling", "reference-lines", "cruise")) {
        if (vizData.flightCeilingFt > 0.0 && vizData.flightCeilingFt < vizData.maxAltitudeFt - 500.0) {
            val ceilY = transform.altitudeToY(vizData.flightCeilingFt)
            val ceilColor = Color(0xFFA855F7)
            drawLine(
                color = ceilColor.copy(alpha = 0.85f),
                start = Offset(transform.plotLeft, ceilY),
                end = Offset(transform.plotRight, ceilY),
                strokeWidth = 1.5f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 5f), 0f),
            )
            drawPillLabel(
                text = "Ceil ${String.format(Locale.US, "FL%03d", (vizData.flightCeilingFt / 100.0).roundToInt())}",
                centerX = transform.plotRight - 32f,
                topY = max(transform.plotTop + 2f, ceilY - 15f),
                bgColor = ceilColor.copy(alpha = 0.85f),
            )
        }
    }
}

private fun DrawScope.drawSmoothAltitudeLine(
    transform: CoordTransform,
    points: List<VizPoint>,
    extractAlt: (VizPoint) -> Double?,
    color: Color,
    strokeWidth: Float,
    dashPattern: FloatArray,
    label: String,
) {
    val runs = mutableListOf<MutableList<Pair<Double, Double>>>()
    var currentRun = mutableListOf<Pair<Double, Double>>()

    for (pt in points) {
        val alt = extractAlt(pt)
        if (alt != null && alt.isFinite()) {
            currentRun.add(pt.distanceNm to alt)
        } else if (currentRun.isNotEmpty()) {
            runs.add(currentRun)
            currentRun = mutableListOf()
        }
    }
    if (currentRun.isNotEmpty()) {
        runs.add(currentRun)
    }

    var lastPointOffset: Offset? = null

    for (run in runs) {
        if (run.size == 1) {
            val (d, alt) = run.first()
            val offset = Offset(transform.distanceToX(d), transform.altitudeToY(alt))
            drawCircle(color = color, radius = 2.5f, center = offset)
            lastPointOffset = offset
        } else if (run.size >= 2) {
            val sampled = MonotoneCubicSpline.sampleCurve(
                points = run,
                stepCount = max(20, run.size * 5),
            )
            val path = Path()
            sampled.forEachIndexed { idx, (d, alt) ->
                val x = transform.distanceToX(d)
                val y = transform.altitudeToY(alt)
                if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
                if (idx == sampled.lastIndex) {
                    lastPointOffset = Offset(x, y)
                }
            }
            drawPath(
                path = path,
                color = color,
                style = Stroke(
                    width = strokeWidth,
                    cap = StrokeCap.Round,
                    join = StrokeJoin.Round,
                    pathEffect = PathEffect.dashPathEffect(dashPattern, 0f),
                ),
            )
        }
    }

    lastPointOffset?.let { endOffset ->
        drawPillLabel(
            text = label,
            centerX = (endOffset.x - 18f).coerceIn(transform.plotLeft + 18f, transform.plotRight - 18f),
            topY = (endOffset.y - 14f).coerceIn(transform.plotTop + 2f, transform.plotBottom - 16f),
            bgColor = Color(0xCC0F172A),
            textColor = color,
        )
    }
}

// ============================================================================
// 10. Advisory Highlight Scrim + Cutout Windows + 6dp Bottom Verdict Ribbon
// ============================================================================

private fun DrawScope.drawAdvisoryHighlightRegions(
    transform: CoordTransform,
    highlights: AdvisoryHighlights,
    selectedModel: String,
) {
    val regionsMap = highlights.regions ?: return
    val modelRegions = regionsMap[selectedModel]
        ?: regionsMap.entries.firstOrNull { it.key.equals(selectedModel, ignoreCase = true) }?.value
        ?: regionsMap.values.firstOrNull()
        ?: return

    if (modelRegions.isEmpty()) return

    val fullPlotPath = Path().apply {
        addRect(Rect(transform.plotLeft, transform.plotTop, transform.plotRight, transform.plotBottom))
    }
    val cutoutsPath = Path()

    for (region in modelRegions) {
        val x1 = transform.distanceToX(region.fromNm)
        val x2 = transform.distanceToX(region.toNm)
        val yTop = region.topFt?.let { transform.altitudeToY(it) } ?: transform.plotTop
        val yBot = region.baseFt?.let { transform.altitudeToY(it) } ?: transform.plotBottom
        if (x2 > x1 && yBot > yTop) {
            cutoutsPath.addRect(Rect(x1, yTop, x2, yBot))
        }
    }

    val scrimPath = Path().apply {
        op(fullPlotPath, cutoutsPath, PathOperation.Difference)
    }
    drawPath(
        path = scrimPath,
        color = Color(0x73000000),
    )

    // Outline each highlight window in its status color
    for (region in modelRegions) {
        val x1 = transform.distanceToX(region.fromNm)
        val x2 = transform.distanceToX(region.toNm)
        val yTop = region.topFt?.let { transform.altitudeToY(it) } ?: transform.plotTop
        val yBot = region.baseFt?.let { transform.altitudeToY(it) } ?: transform.plotBottom
        if (x2 <= x1 || yBot <= yTop) continue
        val statusColor = ColorScales.assessmentColor(region.status)
        drawRect(
            color = statusColor,
            topLeft = Offset(x1, yTop),
            size = Size(x2 - x1, yBot - yTop),
            style = Stroke(width = 1.8f),
        )
    }
}

private fun DrawScope.drawAdvisoryRibbon(
    transform: CoordTransform,
    highlights: AdvisoryHighlights,
    selectedModel: String,
) {
    val ribbonMap = highlights.ribbon ?: return
    val segments = ribbonMap[selectedModel]
        ?: ribbonMap.entries.firstOrNull { it.key.equals(selectedModel, ignoreCase = true) }?.value
        ?: ribbonMap.values.firstOrNull()
        ?: return

    if (segments.isEmpty()) return
    val ribbonTop = transform.plotBottom + 2f
    val ribbonH = 6.dp.toPx()

    for (seg in segments) {
        val x1 = transform.distanceToX(seg.fromNm)
        val x2 = transform.distanceToX(seg.toNm)
        if (x2 <= x1) continue
        drawRect(
            color = ColorScales.assessmentColor(seg.status),
            topLeft = Offset(x1, ribbonTop),
            size = Size(x2 - x1, ribbonH),
        )
    }
}

// ============================================================================
// 11. Waypoint Markers, Scrub Crosshair, Selected Point & Skew-T Hover Altitude
// ============================================================================

private fun DrawScope.drawWaypointsAndOverlays(
    transform: CoordTransform,
    vizData: VizRouteData,
    scrubDistanceNm: Double?,
    selectedPointIndex: Int,
    hoverAltitudeFt: Double?,
) {
    // Waypoint markers along the bottom axis
    val wpPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.WHITE
        textSize = 9.5.sp.toPx()
        textAlign = Paint.Align.CENTER
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }

    var lastWpLabelX = -100f
    for (wp in vizData.waypoints) {
        val x = transform.distanceToX(wp.distanceNm)
        drawLine(
            color = Color(0x55E2E8F0),
            start = Offset(x, transform.plotTop),
            end = Offset(x, transform.plotBottom),
            strokeWidth = 0.9f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(3f, 5f), 0f),
        )
        // Small tick at bottom of plot
        drawLine(
            color = Color.White,
            start = Offset(x, transform.plotBottom),
            end = Offset(x, transform.plotBottom + 5f),
            strokeWidth = 1.4f,
        )
        if (abs(x - lastWpLabelX) >= 28f) {
            val clampedX = x.coerceIn(transform.plotLeft + 14f, transform.plotRight - 14f)
            drawContext.canvas.nativeCanvas.drawText(
                wp.icao,
                clampedX,
                transform.plotBottom + 16f,
                wpPaint,
            )
            lastWpLabelX = clampedX
        }
    }

    // Selected route point indicator (when not actively scrubbing or alongside scrub)
    val points = vizData.points
    if (points.isNotEmpty() && selectedPointIndex in points.indices) {
        val selPt = points[selectedPointIndex]
        val selX = transform.distanceToX(selPt.distanceNm)
        drawLine(
            color = Color(0xAA38BDF8),
            start = Offset(selX, transform.plotTop),
            end = Offset(selX, transform.plotBottom),
            strokeWidth = 1.2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(4f, 4f), 0f),
        )
        // Downward pointer triangle at plotTop
        val tri = Path().apply {
            moveTo(selX - 4.5f, transform.plotTop - 5f)
            lineTo(selX + 4.5f, transform.plotTop - 5f)
            lineTo(selX, transform.plotTop + 1f)
            close()
        }
        drawPath(path = tri, color = Color(0xFF38BDF8))
    }

    // Active scrub vertical crosshair
    if (scrubDistanceNm != null) {
        val scrubX = transform.distanceToX(scrubDistanceNm)
        drawLine(
            color = Color(0xFF38BDF8),
            start = Offset(scrubX, transform.plotTop),
            end = Offset(scrubX, transform.plotBottom),
            strokeWidth = 1.8f,
        )
        drawPillLabel(
            text = "${scrubDistanceNm.roundToInt()} NM",
            centerX = scrubX.coerceIn(transform.plotLeft + 24f, transform.plotRight - 24f),
            topY = transform.plotTop + 2f,
            bgColor = Color(0xEE0284C7),
        )
    }

    // Linked Skew-T / Cross-section hover altitude horizontal dashed line
    if (hoverAltitudeFt != null && hoverAltitudeFt in 0.0..vizData.maxAltitudeFt) {
        val hoverY = transform.altitudeToY(hoverAltitudeFt)
        drawLine(
            color = Color(0xFFFACC15),
            start = Offset(transform.plotLeft, hoverY),
            end = Offset(transform.plotRight, hoverY),
            strokeWidth = 1.5f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f), 0f),
        )
        val altText = String.format(Locale.US, "%,d ft", ((hoverAltitudeFt / 100.0).roundToInt() * 100))
        drawPillLabel(
            text = altText,
            centerX = transform.plotRight - 28f,
            topY = (hoverY - 14f).coerceIn(transform.plotTop + 2f, transform.plotBottom - 16f),
            bgColor = Color(0xDD854D0E),
            textColor = Color(0xFFFEF08A),
        )
    }
}

// ============================================================================
// Shared Drawing Utilities (Hatching & Pill Labels)
// ============================================================================

private fun DrawScope.drawDiagonalHatching(
    bounds: Rect,
    clip: Path,
    color: Color,
    spacingPx: Float,
) {
    val step = max(4f, spacingPx)
    clipPath(clip) {
        var xStart = bounds.left - bounds.height
        while (xStart < bounds.right + bounds.height) {
            drawLine(
                color = color,
                start = Offset(xStart, bounds.bottom),
                end = Offset(xStart + bounds.height, bounds.top),
                strokeWidth = 1.0f,
            )
            xStart += step
        }
    }
}

private fun DrawScope.drawPillLabel(
    text: String,
    centerX: Float,
    topY: Float,
    bgColor: Color,
    textColor: Color = Color.White,
) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = textColor.toArgb()
        textSize = 8.5.sp.toPx()
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
    }
    val textW = paint.measureText(text)
    val padH = 4.dp.toPx()
    val pillW = textW + padH * 2f
    val pillH = 13.dp.toPx()
    val left = centerX - pillW * 0.5f

    drawRoundRect(
        color = bgColor,
        topLeft = Offset(left, topY),
        size = Size(pillW, pillH),
        cornerRadius = CornerRadius(3.5.dp.toPx(), 3.5.dp.toPx()),
    )
    drawContext.canvas.nativeCanvas.drawText(
        text,
        left + padH,
        topY + pillH - 3.5f,
        paint,
    )
}
