package aero.flyfun.weather.ui.skewt

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
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
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import aero.flyfun.rzskewt.SkewTConfig
import aero.flyfun.rzskewt.SkewTTransform
import aero.flyfun.rzskewt.Thermodynamics
import aero.flyfun.weather.model.EnhancedCloudLayer
import aero.flyfun.weather.model.IcingZone
import aero.flyfun.weather.model.InversionLayer
import aero.flyfun.weather.model.ParcelPoint
import aero.flyfun.weather.model.SfipZone
import aero.flyfun.weather.model.SoundingProfileLevel
import aero.flyfun.weather.model.SoundingProfileResponse
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

private val STANDARD_ISOBARS = listOf(1000, 925, 850, 700, 500, 400, 300, 250)
private val ISOBAR_FL_LABELS = mapOf(
    1000 to "FL000",
    925 to "FL025",
    850 to "FL050",
    700 to "FL100",
    500 to "FL185",
    400 to "FL235",
    300 to "FL300",
    250 to "FL340",
)
private val MIXING_RATIO_VALUES = listOf(0.4, 1.0, 2.0, 4.0, 7.0, 10.0, 16.0, 24.0)

private val COMPARE_MODEL_COLORS = mapOf(
    "ecmwf" to Color(0xFF2563EB),
    "gfs" to Color(0xFFDC2626),
    "icon" to Color(0xFF16A34A),
    "ukmo" to Color(0xFF9333EA),
    "meteofrance" to Color(0xFFD97706),
)
private val FALLBACK_MODEL_PALETTE = listOf(
    Color(0xFF2563EB),
    Color(0xFFDC2626),
    Color(0xFF16A34A),
    Color(0xFF9333EA),
    Color(0xFFD97706),
)

/**
 * Interactive Skew-T log-P diagram and synchronized right-hand variable Side Panel
 * rendered on a Jetpack Compose [Canvas].
 */
@Composable
fun SkewTCanvas(
    profile: SoundingProfileResponse,
    compareProfiles: Map<String, SoundingProfileResponse> = emptyMap(),
    isCompareMode: Boolean = false,
    enabledOverlays: Set<String>,
    primaryVariableId: String = "hw_xw",
    secondaryVariableId: String? = null,
    cruiseAltitudeFt: Int? = null,
    externalHoverAltitudeFt: Double? = null,
    onHoverAltitudeChanged: ((Double?) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val isDark = isSystemInDarkTheme()
    val density = LocalDensity.current

    var localHoverPressureHpa by remember(profile.pointIndex, profile.model) {
        mutableStateOf<Double?>(null)
    }

    val config = remember { SkewTConfig(pBottom = 1050.0, pTop = 250.0, tMin = -60.0, tMax = 40.0) }

    // Precompute background thermodynamic curves once per config
    val dryAdiabats = remember(config) {
        (-40..160 step 20).map { thetaC ->
            Thermodynamics.dryAdiabatCurve(
                thetaC = thetaC.toDouble(),
                startPressureHpa = config.pBottom,
                endPressureHpa = config.pTop,
                stepHpa = 25.0,
            )
        }
    }
    val moistAdiabats = remember(config) {
        (-20..36 step 8).map { startT ->
            Thermodynamics.moistAdiabatCurve(
                startTempC = startT.toDouble(),
                startPressureHpa = 1000.0,
                endPressureHpa = config.pTop,
                stepHpa = 25.0,
            )
        }
    }
    val isohumes = remember(config) {
        MIXING_RATIO_VALUES.map { w ->
            w to Thermodynamics.isohumeCurve(
                wGPerKg = w,
                startPressureHpa = config.pBottom,
                endPressureHpa = 400.0,
                stepHpa = 25.0,
            )
        }
    }

    val leftMarginPx = with(density) { 36.dp.toPx() }
    val topMarginPx = with(density) { 22.dp.toPx() }
    val bottomMarginPx = with(density) { 28.dp.toPx() }
    val rightMarginPx = with(density) { 8.dp.toPx() }
    val flColumnWidthPx = with(density) { 42.dp.toPx() }
    val sidePanelWidthPx = with(density) { 96.dp.toPx() }
    val labelTextSizePx = with(density) { 10.sp.toPx() }
    val smallTextSizePx = with(density) { 9.sp.toPx() }
    val badgeTextSizePx = with(density) { 10.5.sp.toPx() }

    val primaryVarDef = remember(primaryVariableId) {
        SkewTVariableCatalog.findById(primaryVariableId) ?: SkewTVariableCatalog.ALL_VARIABLES.first()
    }
    val secondaryVarDef = remember(secondaryVariableId, primaryVariableId) {
        secondaryVariableId
            ?.let { SkewTVariableCatalog.findById(it) }
            ?.takeIf { it.id != primaryVarDef.id }
    }

    val effectiveHoverPressureHpa = localHoverPressureHpa
        ?: externalHoverAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }

    Box(modifier = modifier.fillMaxSize()) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(profile.pointIndex, profile.model) {
                    detectTapGestures { offset ->
                        val plotHeight = (size.height - topMarginPx - bottomMarginPx).coerceAtLeast(1f)
                        val plotBottom = topMarginPx + plotHeight
                        if (offset.y in topMarginPx..plotBottom) {
                            val t = SkewTTransform(leftMarginPx, topMarginPx, 100f, plotHeight, config)
                            val p = t.yToPressure(offset.y).coerceIn(config.pTop, config.pBottom)
                            localHoverPressureHpa = p
                            onHoverAltitudeChanged?.invoke(Thermodynamics.pressureToAltitudeFt(p))
                        } else {
                            localHoverPressureHpa = null
                            onHoverAltitudeChanged?.invoke(null)
                        }
                    }
                }
                .pointerInput(profile.pointIndex, profile.model) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val plotHeight = (size.height - topMarginPx - bottomMarginPx).coerceAtLeast(1f)
                            val t = SkewTTransform(leftMarginPx, topMarginPx, 100f, plotHeight, config)
                            val p = t.yToPressure(offset.y).coerceIn(config.pTop, config.pBottom)
                            localHoverPressureHpa = p
                            onHoverAltitudeChanged?.invoke(Thermodynamics.pressureToAltitudeFt(p))
                        },
                        onDrag = { change, _ ->
                            change.consume()
                            val plotHeight = (size.height - topMarginPx - bottomMarginPx).coerceAtLeast(1f)
                            val t = SkewTTransform(leftMarginPx, topMarginPx, 100f, plotHeight, config)
                            val p = t.yToPressure(change.position.y).coerceIn(config.pTop, config.pBottom)
                            localHoverPressureHpa = p
                            onHoverAltitudeChanged?.invoke(Thermodynamics.pressureToAltitudeFt(p))
                        },
                    )
                },
        ) {
            val canvasW = size.width
            val canvasH = size.height
            val plotHeight = (canvasH - topMarginPx - bottomMarginPx).coerceAtLeast(60f)
            val totalAvailableW = (canvasW - leftMarginPx - rightMarginPx).coerceAtLeast(120f)
            val showSidePanel = totalAvailableW >= flColumnWidthPx + sidePanelWidthPx + 130f
            val plotWidth = if (showSidePanel) {
                totalAvailableW - flColumnWidthPx - sidePanelWidthPx
            } else {
                totalAvailableW - flColumnWidthPx
            }.coerceAtLeast(80f)

            val transform = SkewTTransform(
                left = leftMarginPx,
                top = topMarginPx,
                width = plotWidth,
                height = plotHeight,
                config = config,
            )

            val sidePanelLeft = transform.right + flColumnWidthPx

            // 1. Plot Background
            val skyBg = if (isDark) Color(0xFF111B2E) else Color(0xFFEAF2FF)
            drawRect(
                color = skyBg,
                topLeft = Offset(transform.left, transform.top),
                size = Size(transform.width, transform.height),
            )

            // 2. Clipped Skew-T Interior: Grid, Overlays, CAPE/CIN, Curves, Level Lines
            clipRect(
                left = transform.left,
                top = transform.top,
                right = transform.right,
                bottom = transform.bottom,
            ) {
                drawBackgroundGrid(
                    transform = transform,
                    dryAdiabats = dryAdiabats,
                    moistAdiabats = moistAdiabats,
                    isohumes = isohumes,
                    isDark = isDark,
                )

                if (!isCompareMode) {
                    drawOverlayBands(
                        transform = transform,
                        profile = profile,
                        enabledOverlays = enabledOverlays,
                    )
                }

                if (isCompareMode && compareProfiles.isNotEmpty()) {
                    drawCompareProfiles(
                        transform = transform,
                        primaryModel = profile.model,
                        compareProfiles = compareProfiles,
                    )
                } else {
                    drawSingleModelProfile(
                        transform = transform,
                        profile = profile,
                        isDark = isDark,
                    )
                }

                drawLevelMarkerLines(
                    transform = transform,
                    profile = profile,
                    cruiseAltitudeFt = cruiseAltitudeFt ?: profile.cruiseAltitudeFt,
                    isDark = isDark,
                )
            }

            // 3. Plot Border & Axes Labels (Pressure on Left, FL + Level tags in Center Strip, Temp on Bottom)
            drawSkewTAxesAndMarkers(
                transform = transform,
                profile = profile,
                cruiseAltitudeFt = cruiseAltitudeFt ?: profile.cruiseAltitudeFt,
                labelTextSizePx = labelTextSizePx,
                smallTextSizePx = smallTextSizePx,
                isDark = isDark,
            )

            // 4. Synchronized Right-Hand Side Panel
            if (showSidePanel) {
                drawSidePanel(
                    transform = transform,
                    sideLeft = sidePanelLeft,
                    sideWidth = sidePanelWidthPx,
                    profile = profile,
                    compareProfiles = compareProfiles,
                    isCompareMode = isCompareMode,
                    primaryVar = primaryVarDef,
                    secondaryVar = secondaryVarDef,
                    smallTextSizePx = smallTextSizePx,
                    isDark = isDark,
                )
            }

            // 5. Interactive / Linked Crosshair + Floating Readout Badge
            if (effectiveHoverPressureHpa != null && transform.isPressureVisible(effectiveHoverPressureHpa)) {
                drawHoverCrosshairAndReadout(
                    transform = transform,
                    hoverPressureHpa = effectiveHoverPressureHpa,
                    profile = profile,
                    primaryVar = primaryVarDef,
                    secondaryVar = secondaryVarDef,
                    sidePanelRight = if (showSidePanel) sidePanelLeft + sidePanelWidthPx else transform.right,
                    badgeTextSizePx = badgeTextSizePx,
                    isDark = isDark,
                )
            }
        }
    }
}

// ============================================================================
// Background Grid Rendering
// ============================================================================

private fun DrawScope.drawBackgroundGrid(
    transform: SkewTTransform,
    dryAdiabats: List<List<Pair<Double, Double>>>,
    moistAdiabats: List<List<Pair<Double, Double>>>,
    isohumes: List<Pair<Double, List<Pair<Double, Double>>>>,
    isDark: Boolean,
) {
    val gridColor = if (isDark) Color(0xFF334155) else Color(0xFFCBD5E1)
    val isothermColor = if (isDark) Color(0xFF3B4D6B) else Color(0xFFB8C7DC)
    val zeroIsothermColor = Color(0xFF00B0DC)
    val icingIsothermColor = if (isDark) Color(0xFF38BDF8).copy(alpha = 0.65f) else Color(0xFF0284C7).copy(alpha = 0.55f)
    val dryAdiabatColor = if (isDark) Color(0xFF7C3F28).copy(alpha = 0.55f) else Color(0xFFC27856).copy(alpha = 0.45f)
    val moistAdiabatColor = if (isDark) Color(0xFF236B48).copy(alpha = 0.55f) else Color(0xFF3E9B6C).copy(alpha = 0.45f)
    val isohumeColor = if (isDark) Color(0xFF2A7B88).copy(alpha = 0.5f) else Color(0xFF4799A8).copy(alpha = 0.45f)

    // Isobars
    for (p in STANDARD_ISOBARS) {
        val y = transform.pressureToY(p.toDouble())
        drawLine(
            color = gridColor,
            start = Offset(transform.left, y),
            end = Offset(transform.right, y),
            strokeWidth = 1f,
        )
    }

    // Skewed Isotherms every 10°C
    for (t in -80..40 step 10) {
        val xBottom = transform.tempPressureToX(t.toDouble(), transform.config.pBottom)
        val xTop = transform.tempPressureToX(t.toDouble(), transform.config.pTop)
        val color = when (t) {
            0 -> zeroIsothermColor
            -10, -20 -> icingIsothermColor
            else -> isothermColor
        }
        val strokeW = if (t == 0) 2.2f else if (t == -10 || t == -20) 1.5f else 1f
        val dash = if (t == -10 || t == -20) PathEffect.dashPathEffect(floatArrayOf(8f, 5f)) else null
        drawLine(
            color = color,
            start = Offset(xBottom, transform.bottom),
            end = Offset(xTop, transform.top),
            strokeWidth = strokeW,
            pathEffect = dash,
        )
    }

    // Dry Adiabats
    for (curve in dryAdiabats) {
        drawCurvePairs(transform, curve, dryAdiabatColor, strokeWidth = 1f)
    }

    // Moist Adiabats (dashed)
    val moistDash = PathEffect.dashPathEffect(floatArrayOf(6f, 4f))
    for (curve in moistAdiabats) {
        drawCurvePairs(transform, curve, moistAdiabatColor, strokeWidth = 1f, pathEffect = moistDash)
    }

    // Mixing Ratio / Isohumes (dotted)
    val isohumeDash = PathEffect.dashPathEffect(floatArrayOf(3f, 4f))
    for ((_, curve) in isohumes) {
        drawCurvePairs(transform, curve, isohumeColor, strokeWidth = 1f, pathEffect = isohumeDash)
    }
}

private fun DrawScope.drawCurvePairs(
    transform: SkewTTransform,
    points: List<Pair<Double, Double>>,
    color: Color,
    strokeWidth: Float,
    pathEffect: PathEffect? = null,
) {
    if (points.size < 2) return
    val path = Path()
    val (firstT, firstP) = points.first()
    path.moveTo(transform.tempPressureToX(firstT, firstP), transform.pressureToY(firstP))
    for (i in 1 until points.size) {
        val (t, p) = points[i]
        path.lineTo(transform.tempPressureToX(t, p), transform.pressureToY(p))
    }
    drawPath(
        path = path,
        color = color,
        style = Stroke(width = strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round, pathEffect = pathEffect),
    )
}

// ============================================================================
// Overlay Bands
// ============================================================================

private fun DrawScope.drawOverlayBands(
    transform: SkewTTransform,
    profile: SoundingProfileResponse,
    enabledOverlays: Set<String>,
) {
    if (SkewTOverlayBand.CLOUDS_DD.isEnabledIn(enabledOverlays)) {
        profile.cloudLayers.orEmpty().forEach { layer ->
            drawCloudBand(transform, layer, isNwp = false)
        }
    }
    if (SkewTOverlayBand.CLOUDS_NWP.isEnabledIn(enabledOverlays)) {
        profile.nwpCloudLayers.orEmpty().forEach { layer ->
            drawCloudBand(transform, layer, isNwp = true)
        }
    }
    if (SkewTOverlayBand.ICING_OGIMET_DD.isEnabledIn(enabledOverlays)) {
        profile.icingZones.orEmpty().forEach { zone ->
            drawIcingBand(transform, zone.basePressureHpa, zone.topPressureHpa, zone.baseFt, zone.topFt, zone.risk)
        }
    }
    if (SkewTOverlayBand.ICING_OGIMET_NWP.isEnabledIn(enabledOverlays)) {
        profile.icingOgimetNwpZones.orEmpty().forEach { zone ->
            drawIcingBand(transform, zone.basePressureHpa, zone.topPressureHpa, zone.baseFt, zone.topFt, zone.risk)
        }
    }
    if (SkewTOverlayBand.ICING_SFIP.isEnabledIn(enabledOverlays)) {
        profile.sfipZones.orEmpty().forEach { zone ->
            drawSfipBand(transform, zone)
        }
    }
    if (SkewTOverlayBand.INVERSIONS.isEnabledIn(enabledOverlays)) {
        profile.inversionLayers.orEmpty().forEach { inv ->
            drawInversionBand(transform, inv)
        }
    }
    if (SkewTOverlayBand.CONVECTIVE.isEnabledIn(enabledOverlays)) {
        drawConvectiveBand(transform, profile)
    }
}

private fun resolveBandPressures(
    basePressureHpa: Double?,
    topPressureHpa: Double?,
    baseFt: Double?,
    topFt: Double?,
): Pair<Double, Double>? {
    val bottomP = basePressureHpa ?: baseFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    val topP = topPressureHpa ?: topFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (bottomP == null || topP == null) return null
    return Pair(max(bottomP, topP), min(bottomP, topP))
}

private fun DrawScope.drawHorizontalPressureBand(
    transform: SkewTTransform,
    bottomPressureHpa: Double,
    topPressureHpa: Double,
    color: Color,
) {
    val yTop = transform.pressureToY(topPressureHpa)
    val yBottom = transform.pressureToY(bottomPressureHpa)
    val top = min(yTop, yBottom)
    val height = abs(yBottom - yTop).coerceAtLeast(2f)
    drawRect(
        color = color,
        topLeft = Offset(transform.left, top),
        size = Size(transform.width, height),
    )
}

private fun DrawScope.drawCloudBand(
    transform: SkewTTransform,
    layer: EnhancedCloudLayer,
    isNwp: Boolean,
) {
    val (bottomP, topP) = resolveBandPressures(
        layer.basePressureHpa,
        layer.topPressureHpa,
        layer.baseFt,
        layer.topFt,
    ) ?: return

    val alpha = when (layer.coverage?.uppercase()) {
        "OVC" -> 0.45f
        "BKN" -> 0.32f
        "SCT" -> 0.22f
        else -> 0.25f
    }
    val baseColor = if (isNwp) Color(0xFF788CAA) else Color(0xFF8C8C96)
    drawHorizontalPressureBand(transform, bottomP, topP, baseColor.copy(alpha = alpha))
}

private fun DrawScope.drawIcingBand(
    transform: SkewTTransform,
    basePressureHpa: Double?,
    topPressureHpa: Double?,
    baseFt: Double?,
    topFt: Double?,
    risk: String,
) {
    val (bottomP, topP) = resolveBandPressures(basePressureHpa, topPressureHpa, baseFt, topFt) ?: return
    val color = icingRiskColor(risk) ?: return
    drawHorizontalPressureBand(transform, bottomP, topP, color)
}

private fun DrawScope.drawSfipBand(
    transform: SkewTTransform,
    zone: SfipZone,
) {
    val (bottomP, topP) = resolveBandPressures(
        zone.basePressureHpa,
        zone.topPressureHpa,
        zone.baseFt,
        zone.topFt,
    ) ?: return
    val color = icingRiskColor(zone.risk) ?: Color(0xFF6495ED).copy(alpha = 0.28f)
    drawHorizontalPressureBand(transform, bottomP, topP, color)
}

private fun icingRiskColor(risk: String?): Color? = when (risk?.lowercase()) {
    "none", null -> null
    "light" -> Color(0xFF6495ED).copy(alpha = 0.30f)
    "moderate" -> Color(0xFFFFA500).copy(alpha = 0.38f)
    "severe" -> Color(0xFFDC3545).copy(alpha = 0.46f)
    else -> Color(0xFF6495ED).copy(alpha = 0.28f)
}

private fun DrawScope.drawInversionBand(
    transform: SkewTTransform,
    inv: InversionLayer,
) {
    val (bottomP, topP) = resolveBandPressures(
        inv.basePressureHpa,
        inv.topPressureHpa,
        inv.baseFt,
        inv.topFt,
    ) ?: return
    val t = (inv.strengthC / 3.0).coerceIn(0.0, 1.0).toFloat()
    val alpha = (0.14f + t * 0.32f).coerceAtMost(0.48f)
    drawHorizontalPressureBand(transform, bottomP, topP, Color(0xFFE91E63).copy(alpha = alpha))
}

private fun DrawScope.drawConvectiveBand(
    transform: SkewTTransform,
    profile: SoundingProfileResponse,
) {
    val idx = profile.indices
    val lfcP = idx?.lfcPressureHpa
        ?: (idx?.lfcAltitudeFt ?: profile.convective?.lfcFt ?: profile.convective?.baseFt)
            ?.let { Thermodynamics.altitudeToPressureHpa(it) }
    val elP = idx?.elPressureHpa
        ?: (idx?.elAltitudeFt ?: profile.convective?.elFt ?: profile.convective?.topFt)
            ?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (lfcP != null && elP != null) {
        drawHorizontalPressureBand(
            transform = transform,
            bottomPressureHpa = max(lfcP, elP),
            topPressureHpa = min(lfcP, elP),
            color = Color(0xFFFF6432).copy(alpha = 0.16f),
        )
    }
}

// ============================================================================
// Profile Curves & CAPE/CIN Shading
// ============================================================================

private fun DrawScope.drawSingleModelProfile(
    transform: SkewTTransform,
    profile: SoundingProfileResponse,
    isDark: Boolean,
) {
    val sortedLevels = profile.levels.sortedByDescending { it.pressureHpa }
    if (sortedLevels.size < 2) return

    val parcelPoints = profile.parcelPath?.takeIf { it.size >= 2 }
        ?: synthesizeParcelPath(sortedLevels)

    val idx = profile.indices
    val lclP = idx?.lclPressureHpa
        ?: idx?.lclAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    val elP = idx?.elPressureHpa
        ?: idx?.elAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }

    // 1. CAPE / CIN Shading between LCL and EL
    if (parcelPoints.size >= 2 && elP != null) {
        drawCapeCinShading(
            transform = transform,
            levels = sortedLevels,
            parcelPath = parcelPoints,
            lclPressureHpa = lclP,
            elPressureHpa = elP,
            isDark = isDark,
        )
    }

    // 2. Parcel Path (Amber/Orange Dashed Curve)
    if (parcelPoints.size >= 2) {
        val parcelPairs = parcelPoints
            .sortedByDescending { it.pressureHpa }
            .map { Pair(it.temperatureC, it.pressureHpa) }
        drawCurvePairs(
            transform = transform,
            points = parcelPairs,
            color = if (isDark) Color(0xFFF59E0B) else Color(0xFFD97706),
            strokeWidth = 3.2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 6f)),
        )
    }

    // 3. Dewpoint Td (Bold Blue Solid Line)
    val tdPairs = sortedLevels.mapNotNull { lv ->
        lv.dewpointC?.let { td -> Pair(td, lv.pressureHpa) }
    }
    drawCurvePairs(
        transform = transform,
        points = tdPairs,
        color = if (isDark) Color(0xFF38BDF8) else Color(0xFF0284C7),
        strokeWidth = 4.5f,
    )

    // 4. Environmental Temperature T (Bold Red Solid Line)
    val tPairs = sortedLevels.map { lv -> Pair(lv.temperatureC, lv.pressureHpa) }
    drawCurvePairs(
        transform = transform,
        points = tPairs,
        color = Color(0xFFEF4444),
        strokeWidth = 4.5f,
    )
}

private fun DrawScope.drawCompareProfiles(
    transform: SkewTTransform,
    primaryModel: String,
    compareProfiles: Map<String, SoundingProfileResponse>,
) {
    val entries = compareProfiles.entries.toList()
    // Draw non-primary models first, then primary on top
    val ordered = entries.sortedBy { it.key.equals(primaryModel, ignoreCase = true) }
    ordered.forEachIndexed { idx, (modelKey, modelProfile) ->
        val isPrimary = modelKey.equals(primaryModel, ignoreCase = true)
        val baseColor = COMPARE_MODEL_COLORS[modelKey.lowercase()]
            ?: FALLBACK_MODEL_PALETTE[idx % FALLBACK_MODEL_PALETTE.size]
        val color = if (isPrimary) baseColor else baseColor.copy(alpha = 0.65f)
        val strokeW = if (isPrimary) 4.5f else 2.8f

        val sorted = modelProfile.levels.sortedByDescending { it.pressureHpa }
        val tPairs = sorted.map { Pair(it.temperatureC, it.pressureHpa) }
        val tdPairs = sorted.mapNotNull { lv -> lv.dewpointC?.let { Pair(it, lv.pressureHpa) } }

        drawCurvePairs(
            transform = transform,
            points = tdPairs,
            color = color,
            strokeWidth = strokeW,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 5f)),
        )
        drawCurvePairs(
            transform = transform,
            points = tPairs,
            color = color,
            strokeWidth = strokeW,
        )
    }
}

private fun DrawScope.drawCapeCinShading(
    transform: SkewTTransform,
    levels: List<SoundingProfileLevel>,
    parcelPath: List<ParcelPoint>,
    lclPressureHpa: Double?,
    elPressureHpa: Double,
    isDark: Boolean,
) {
    val sortedParcel = parcelPath.sortedByDescending { it.pressureHpa }
    val capeFill = if (isDark) Color(0xFFFF5050).copy(alpha = 0.28f) else Color(0xFFDC3C3C).copy(alpha = 0.16f)
    val cinFill = if (isDark) Color(0xFF5080FF).copy(alpha = 0.28f) else Color(0xFF3C50DC).copy(alpha = 0.16f)

    for (i in 0 until sortedParcel.size - 1) {
        val pp0 = sortedParcel[i]
        val pp1 = sortedParcel[i + 1]

        if (lclPressureHpa != null && pp0.pressureHpa > lclPressureHpa) continue
        if (pp1.pressureHpa < elPressureHpa) continue

        val envT0 = interpolateEnvTempAtPressure(levels, pp0.pressureHpa) ?: continue
        val envT1 = interpolateEnvTempAtPressure(levels, pp1.pressureHpa) ?: continue

        val avgBuoyancy = ((pp0.temperatureC - envT0) + (pp1.temperatureC - envT1)) * 0.5
        if (abs(avgBuoyancy) < 0.1) continue

        val poly = Path().apply {
            moveTo(transform.tempPressureToX(pp0.temperatureC, pp0.pressureHpa), transform.pressureToY(pp0.pressureHpa))
            lineTo(transform.tempPressureToX(pp1.temperatureC, pp1.pressureHpa), transform.pressureToY(pp1.pressureHpa))
            lineTo(transform.tempPressureToX(envT1, pp1.pressureHpa), transform.pressureToY(pp1.pressureHpa))
            lineTo(transform.tempPressureToX(envT0, pp0.pressureHpa), transform.pressureToY(pp0.pressureHpa))
            close()
        }
        drawPath(path = poly, color = if (avgBuoyancy > 0) capeFill else cinFill)
    }
}

private fun interpolateEnvTempAtPressure(
    sortedLevelsDesc: List<SoundingProfileLevel>,
    pressureHpa: Double,
): Double? {
    if (sortedLevelsDesc.isEmpty()) return null
    for (i in 0 until sortedLevelsDesc.size - 1) {
        val l0 = sortedLevelsDesc[i]
        val l1 = sortedLevelsDesc[i + 1]
        if (pressureHpa <= l0.pressureHpa && pressureHpa >= l1.pressureHpa) {
            val dp = l0.pressureHpa - l1.pressureHpa
            if (dp <= 1e-6) return l0.temperatureC
            val frac = (l0.pressureHpa - pressureHpa) / dp
            return l0.temperatureC + frac * (l1.temperatureC - l0.temperatureC)
        }
    }
    return sortedLevelsDesc.minByOrNull { abs(it.pressureHpa - pressureHpa) }?.temperatureC
}

private fun synthesizeParcelPath(sortedLevelsDesc: List<SoundingProfileLevel>): List<ParcelPoint> {
    val sfc = sortedLevelsDesc.firstOrNull() ?: return emptyList()
    val sfcTd = sfc.dewpointC ?: return emptyList()
    val (lclT, lclP) = Thermodynamics.lclTemperatureAndPressure(sfc.temperatureC, sfcTd, sfc.pressureHpa)
    val thetaC = Thermodynamics.kelvinToCelsius(Thermodynamics.potentialTemperatureK(sfc.temperatureC, sfc.pressureHpa))
    val drySegment = Thermodynamics.dryAdiabatCurve(
        thetaC = thetaC,
        startPressureHpa = sfc.pressureHpa,
        endPressureHpa = lclP,
        stepHpa = 25.0,
    )
    val moistSegment = Thermodynamics.moistAdiabatCurve(
        startTempC = lclT,
        startPressureHpa = lclP,
        endPressureHpa = 250.0,
        stepHpa = 25.0,
    )
    return (drySegment + moistSegment.drop(1)).map { (t, p) ->
        ParcelPoint(pressureHpa = p, temperatureC = t)
    }
}

// ============================================================================
// Level Markers & Axes
// ============================================================================

private data class LevelMarkerSpec(
    val label: String,
    val pressureHpa: Double,
    val color: Color,
    val dash: FloatArray,
)

private fun resolveLevelMarkers(
    profile: SoundingProfileResponse,
    cruiseAltitudeFt: Int?,
): List<LevelMarkerSpec> {
    val idx = profile.indices
    val markers = mutableListOf<LevelMarkerSpec>()

    val lclP = idx?.lclPressureHpa ?: idx?.lclAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (lclP != null) {
        markers.add(LevelMarkerSpec("LCL", lclP, Color(0xFF16A34A), floatArrayOf(4f, 6f)))
    }
    val lfcP = idx?.lfcPressureHpa ?: idx?.lfcAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (lfcP != null) {
        markers.add(LevelMarkerSpec("LFC", lfcP, Color(0xFFD97706), floatArrayOf(10f, 6f)))
    }
    val elP = idx?.elPressureHpa ?: idx?.elAltitudeFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (elP != null) {
        markers.add(LevelMarkerSpec("EL", elP, Color(0xFFDC2626), floatArrayOf(14f, 5f, 4f, 5f)))
    }
    val fzP = idx?.freezingLevelFt?.let { Thermodynamics.altitudeToPressureHpa(it) }
    if (fzP != null) {
        markers.add(LevelMarkerSpec("0°C", fzP, Color(0xFF00B0DC), floatArrayOf(10f, 5f)))
    }
    if (cruiseAltitudeFt != null && cruiseAltitudeFt > 0) {
        val cruiseP = Thermodynamics.altitudeToPressureHpa(cruiseAltitudeFt.toDouble())
        markers.add(LevelMarkerSpec("Cruise", cruiseP, Color(0xFF818CF8), floatArrayOf(12f, 6f)))
    }
    return markers
}

private fun DrawScope.drawLevelMarkerLines(
    transform: SkewTTransform,
    profile: SoundingProfileResponse,
    cruiseAltitudeFt: Int?,
    isDark: Boolean,
) {
    val markers = resolveLevelMarkers(profile, cruiseAltitudeFt)
    for (marker in markers) {
        if (!transform.isPressureVisible(marker.pressureHpa)) continue
        val y = transform.pressureToY(marker.pressureHpa)
        drawLine(
            color = marker.color.copy(alpha = if (isDark) 0.85f else 0.75f),
            start = Offset(transform.left, y),
            end = Offset(transform.right, y),
            strokeWidth = 2f,
            pathEffect = PathEffect.dashPathEffect(marker.dash),
        )
    }
}

private fun DrawScope.drawSkewTAxesAndMarkers(
    transform: SkewTTransform,
    profile: SoundingProfileResponse,
    cruiseAltitudeFt: Int?,
    labelTextSizePx: Float,
    smallTextSizePx: Float,
    isDark: Boolean,
) {
    val borderColor = if (isDark) Color(0xFF475569) else Color(0xFF94A3B8)
    val textColor = if (isDark) Color(0xFFCBD5E1) else Color(0xFF334155)

    // Plot Border
    drawRect(
        color = borderColor,
        topLeft = Offset(transform.left, transform.top),
        size = Size(transform.width, transform.height),
        style = Stroke(width = 1.5f),
    )

    val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = textColor.toArgb()
        textSize = labelTextSizePx
        typeface = Typeface.DEFAULT
    }

    // Left Pressure Labels & Right FL Labels
    for (p in STANDARD_ISOBARS) {
        if (!transform.isPressureVisible(p.toDouble())) continue
        val y = transform.pressureToY(p.toDouble())

        // Left: hPa
        textPaint.textAlign = Paint.Align.RIGHT
        textPaint.color = textColor.toArgb()
        drawContext.canvas.nativeCanvas.drawText(
            "$p",
            transform.left - 5f,
            y + labelTextSizePx * 0.35f,
            textPaint,
        )

        // Right: Flight Level
        val fl = ISOBAR_FL_LABELS[p]
        if (fl != null) {
            textPaint.textAlign = Paint.Align.LEFT
            textPaint.textSize = smallTextSizePx
            drawContext.canvas.nativeCanvas.drawText(
                fl,
                transform.right + 4f,
                y + smallTextSizePx * 0.35f,
                textPaint,
            )
            textPaint.textSize = labelTextSizePx
        }
    }

    // Bottom Temperature Labels
    textPaint.textAlign = Paint.Align.CENTER
    for (t in -50..40 step 10) {
        val x = transform.tempPressureToX(t.toDouble(), transform.config.pBottom)
        if (x in (transform.left + 8f)..(transform.right - 8f)) {
            textPaint.color = if (t == 0) Color(0xFF00B0DC).toArgb() else textColor.toArgb()
            drawContext.canvas.nativeCanvas.drawText(
                "${t}°",
                x,
                transform.bottom + labelTextSizePx + 3f,
                textPaint,
            )
        }
    }

    // Right-Edge Level Marker Badges inside the Skew-T plot
    val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = smallTextSizePx
        typeface = Typeface.DEFAULT_BOLD
        textAlign = Paint.Align.RIGHT
    }
    val markers = resolveLevelMarkers(profile, cruiseAltitudeFt)
    for (marker in markers) {
        if (!transform.isPressureVisible(marker.pressureHpa)) continue
        val y = transform.pressureToY(marker.pressureHpa)
        markerPaint.color = marker.color.toArgb()
        drawContext.canvas.nativeCanvas.drawText(
            marker.label,
            transform.right - 5f,
            (y - 3f).coerceIn(transform.top + smallTextSizePx, transform.bottom - 2f),
            markerPaint,
        )
    }
}

// ============================================================================
// Right-Hand Side Panel (Dual-Axis Variable Plot)
// ============================================================================

private fun DrawScope.drawSidePanel(
    transform: SkewTTransform,
    sideLeft: Float,
    sideWidth: Float,
    profile: SoundingProfileResponse,
    compareProfiles: Map<String, SoundingProfileResponse>,
    isCompareMode: Boolean,
    primaryVar: SkewTVariableDef,
    secondaryVar: SkewTVariableDef?,
    smallTextSizePx: Float,
    isDark: Boolean,
) {
    val bgColor = if (isDark) Color(0xFF162032) else Color(0xFFF1F5F9)
    val borderColor = if (isDark) Color(0xFF475569) else Color(0xFF94A3B8)
    val gridColor = if (isDark) Color(0xFF334155) else Color(0xFFCBD5E1)

    drawRect(
        color = bgColor,
        topLeft = Offset(sideLeft, transform.top),
        size = Size(sideWidth, transform.height),
    )

    // Isobar grid lines across Side Panel
    for (p in STANDARD_ISOBARS) {
        if (!transform.isPressureVisible(p.toDouble())) continue
        val y = transform.pressureToY(p.toDouble())
        drawLine(
            color = gridColor,
            start = Offset(sideLeft, y),
            end = Offset(sideLeft + sideWidth, y),
            strokeWidth = 0.8f,
        )
    }

    val primarySeries = SkewTVariableCatalog.extractSeries(primaryVar.id, profile)
    val primaryCrosswindSeries = SkewTVariableCatalog.extractSecondarySeries(primaryVar.id, profile)
    val allPrimaryValues = buildList {
        addAll(primarySeries.map { it.second })
        addAll(primaryCrosswindSeries.map { it.second })
        if (isCompareMode) {
            compareProfiles.values.forEach { cp ->
                addAll(SkewTVariableCatalog.extractSeries(primaryVar.id, cp).map { it.second })
            }
        }
    }
    val primaryRange = computeAxisRange(primaryVar, allPrimaryValues)

    val secondarySeries = secondaryVar?.let { SkewTVariableCatalog.extractSeries(it.id, profile) }.orEmpty()
    val secondaryRange = secondaryVar?.let { computeAxisRange(it, secondarySeries.map { pt -> pt.second }) }

    clipRect(
        left = sideLeft,
        top = transform.top,
        right = sideLeft + sideWidth,
        bottom = transform.bottom,
    ) {
        // Zero line for primary variable if applicable
        if (primaryVar.showZeroLine && primaryRange.first < 0.0 && primaryRange.second > 0.0) {
            val zeroX = valueToSideX(0.0, primaryRange, sideLeft, sideWidth)
            drawLine(
                color = borderColor,
                start = Offset(zeroX, transform.top),
                end = Offset(zeroX, transform.bottom),
                strokeWidth = 1.2f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(5f, 4f)),
            )
        }

        if (isCompareMode && compareProfiles.isNotEmpty()) {
            compareProfiles.entries.forEachIndexed { idx, (modelKey, cp) ->
                val isPrimaryModel = modelKey.equals(profile.model, ignoreCase = true)
                val color = COMPARE_MODEL_COLORS[modelKey.lowercase()]
                    ?: FALLBACK_MODEL_PALETTE[idx % FALLBACK_MODEL_PALETTE.size]
                val series = SkewTVariableCatalog.extractSeries(primaryVar.id, cp)
                drawSideVariableCurve(
                    transform = transform,
                    series = series,
                    range = primaryRange,
                    sideLeft = sideLeft,
                    sideWidth = sideWidth,
                    color = if (isPrimaryModel) color else color.copy(alpha = 0.6f),
                    strokeWidth = if (isPrimaryModel) 3.5f else 2.2f,
                )
            }
        } else {
            // Primary variable curve
            drawSideVariableCurve(
                transform = transform,
                series = primarySeries,
                range = primaryRange,
                sideLeft = sideLeft,
                sideWidth = sideWidth,
                color = Color(primaryVar.colorHex),
                strokeWidth = 3.2f,
            )

            // Secondary component of primary variable (Crosswind for HW/XW)
            if (primaryCrosswindSeries.size >= 2 && primaryVar.secondaryColorHex != null) {
                drawSideVariableCurve(
                    transform = transform,
                    series = primaryCrosswindSeries,
                    range = primaryRange,
                    sideLeft = sideLeft,
                    sideWidth = sideWidth,
                    color = Color(primaryVar.secondaryColorHex),
                    strokeWidth = 2.8f,
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(7f, 4f)),
                )
            }

            // Optional secondary variable (top axis)
            if (secondaryVar != null && secondaryRange != null && secondarySeries.size >= 2) {
                drawSideVariableCurve(
                    transform = transform,
                    series = secondarySeries,
                    range = secondaryRange,
                    sideLeft = sideLeft,
                    sideWidth = sideWidth,
                    color = Color(secondaryVar.colorHex),
                    strokeWidth = 2.6f,
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(5f, 4f)),
                )
            }
        }
    }

    // Border
    drawRect(
        color = borderColor,
        topLeft = Offset(sideLeft, transform.top),
        size = Size(sideWidth, transform.height),
        style = Stroke(width = 1.5f),
    )

    // Top & Bottom Axis Labels
    val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = smallTextSizePx
        typeface = Typeface.DEFAULT_BOLD
    }

    // Bottom axis: primary min / label / max
    val bottomY = transform.bottom + smallTextSizePx + 3f
    axisPaint.color = Color(primaryVar.colorHex).toArgb()
    axisPaint.textAlign = Paint.Align.LEFT
    drawContext.canvas.nativeCanvas.drawText(formatAxisValue(primaryRange.first), sideLeft, bottomY, axisPaint)
    axisPaint.textAlign = Paint.Align.RIGHT
    drawContext.canvas.nativeCanvas.drawText(formatAxisValue(primaryRange.second), sideLeft + sideWidth, bottomY, axisPaint)
    axisPaint.textAlign = Paint.Align.CENTER
    val unitSuffix = if (primaryVar.unit.isNotEmpty()) " (${primaryVar.unit})" else ""
    drawContext.canvas.nativeCanvas.drawText(
        "${primaryVar.shortLabel}$unitSuffix",
        sideLeft + sideWidth * 0.5f,
        bottomY + smallTextSizePx + 2f,
        axisPaint,
    )

    // Top axis: secondary variable or primary title
    val topY = transform.top - 5f
    if (secondaryVar != null && secondaryRange != null) {
        axisPaint.color = Color(secondaryVar.colorHex).toArgb()
        axisPaint.textAlign = Paint.Align.LEFT
        drawContext.canvas.nativeCanvas.drawText(formatAxisValue(secondaryRange.first), sideLeft, topY, axisPaint)
        axisPaint.textAlign = Paint.Align.RIGHT
        drawContext.canvas.nativeCanvas.drawText(formatAxisValue(secondaryRange.second), sideLeft + sideWidth, topY, axisPaint)
        axisPaint.textAlign = Paint.Align.CENTER
        drawContext.canvas.nativeCanvas.drawText(secondaryVar.shortLabel, sideLeft + sideWidth * 0.5f, topY, axisPaint)
    } else {
        axisPaint.color = Color(primaryVar.colorHex).toArgb()
        axisPaint.textAlign = Paint.Align.CENTER
        drawContext.canvas.nativeCanvas.drawText(primaryVar.shortLabel, sideLeft + sideWidth * 0.5f, topY, axisPaint)
    }
}

private fun computeAxisRange(
    variable: SkewTVariableDef,
    values: List<Double>,
): Pair<Double, Double> {
    variable.fixedRange?.let { return it }
    if (values.isEmpty()) return if (variable.showZeroLine) (-20.0 to 20.0) else (0.0 to 50.0)
    var minV = values.minOrNull() ?: 0.0
    var maxV = values.maxOrNull() ?: 1.0
    val margin = ((maxV - minV) * 0.1).coerceAtLeast(1.0)
    minV -= margin
    maxV += margin
    if (variable.showZeroLine) {
        val absMax = max(abs(minV), abs(maxV)).coerceAtLeast(5.0)
        return -absMax to absMax
    }
    return minV to maxV
}

private fun valueToSideX(
    value: Double,
    range: Pair<Double, Double>,
    sideLeft: Float,
    sideWidth: Float,
): Float {
    val span = (range.second - range.first).takeIf { abs(it) > 1e-6 } ?: 1.0
    val frac = ((value - range.first) / span).coerceIn(0.0, 1.0)
    return (sideLeft + frac * sideWidth).toFloat()
}

private fun DrawScope.drawSideVariableCurve(
    transform: SkewTTransform,
    series: List<Pair<Double, Double>>,
    range: Pair<Double, Double>,
    sideLeft: Float,
    sideWidth: Float,
    color: Color,
    strokeWidth: Float,
    pathEffect: PathEffect? = null,
) {
    if (series.size < 2) return
    val path = Path()
    val (firstP, firstVal) = series.first()
    path.moveTo(valueToSideX(firstVal, range, sideLeft, sideWidth), transform.pressureToY(firstP))
    for (i in 1 until series.size) {
        val (p, v) = series[i]
        path.lineTo(valueToSideX(v, range, sideLeft, sideWidth), transform.pressureToY(p))
    }
    drawPath(
        path = path,
        color = color,
        style = Stroke(width = strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round, pathEffect = pathEffect),
    )
}

private fun formatAxisValue(v: Double): String = when {
    abs(v) >= 100.0 -> "${v.roundToInt()}"
    abs(v) >= 10.0 -> "${v.roundToInt()}"
    else -> String.format(Locale.US, "%.1f", v)
}

// ============================================================================
// Interactive Crosshair & Floating Readout Badge
// ============================================================================

private fun DrawScope.drawHoverCrosshairAndReadout(
    transform: SkewTTransform,
    hoverPressureHpa: Double,
    profile: SoundingProfileResponse,
    primaryVar: SkewTVariableDef,
    secondaryVar: SkewTVariableDef?,
    sidePanelRight: Float,
    badgeTextSizePx: Float,
    isDark: Boolean,
) {
    val nearestLevel = profile.levels.minByOrNull { abs(it.pressureHpa - hoverPressureHpa) } ?: return
    val y = transform.pressureToY(hoverPressureHpa)

    val crosshairColor = if (isDark) Color(0xFFF8FAFC) else Color(0xFF0F172A)
    drawLine(
        color = crosshairColor.copy(alpha = 0.85f),
        start = Offset(transform.left, y),
        end = Offset(sidePanelRight, y),
        strokeWidth = 2f,
        pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 4f)),
    )

    // Dots on T and Td at nearest level
    val tX = transform.tempPressureToX(nearestLevel.temperatureC, nearestLevel.pressureHpa)
    val tY = transform.pressureToY(nearestLevel.pressureHpa)
    if (tX in transform.left..transform.right) {
        drawCircle(color = Color(0xFFEF4444), radius = 5f, center = Offset(tX, tY))
    }
    nearestLevel.dewpointC?.let { td ->
        val tdX = transform.tempPressureToX(td, nearestLevel.pressureHpa)
        if (tdX in transform.left..transform.right) {
            drawCircle(color = Color(0xFF38BDF8), radius = 5f, center = Offset(tdX, tY))
        }
    }

    // Build readout lines
    val altFt = nearestLevel.altitudeFt ?: Thermodynamics.pressureToAltitudeFt(nearestLevel.pressureHpa)
    val fl = (altFt / 100.0).roundToInt()
    val rh = nearestLevel.relativeHumidityPct ?: nearestLevel.dewpointC?.let {
        Thermodynamics.relativeHumidityPct(nearestLevel.temperatureC, it)
    }
    val tdText = nearestLevel.dewpointC?.let { String.format(Locale.US, "%.1f°C", it) } ?: "—"
    val rhText = rh?.let { "${it.roundToInt()}%" } ?: "—"

    val line1 = String.format(
        Locale.US,
        "FL%03d (%d ft) · %d hPa",
        fl,
        altFt.roundToInt(),
        nearestLevel.pressureHpa.roundToInt(),
    )
    val line2 = String.format(
        Locale.US,
        "T %.1f°C · Td %s · RH %s",
        nearestLevel.temperatureC,
        tdText,
        rhText,
    )

    val ws = nearestLevel.windSpeedKt
    val wd = nearestLevel.windDirectionDeg
    val track = profile.trackDeg ?: 0.0
    val line3 = if (ws != null && wd != null) {
        val (hw, xw) = Thermodynamics.headwindCrosswindKt(ws, wd, track)
        val hwTag = if (hw >= 0) "${hw.roundToInt()}kt HW" else "${abs(hw).roundToInt()}kt TW"
        val xwTag = "${abs(xw).roundToInt()}kt XW"
        String.format(Locale.US, "Wind %03d°/%dkt (%s, %s)", wd.roundToInt(), ws.roundToInt(), hwTag, xwTag)
    } else {
        null
    }

    val sortedLevels = profile.levels.sortedByDescending { it.pressureHpa }
    val primaryVal = SkewTVariableCatalog.extractLevelValue(primaryVar.id, nearestLevel, sortedLevels, profile.trackDeg)
    val secondaryVal = secondaryVar?.let {
        SkewTVariableCatalog.extractLevelValue(it.id, nearestLevel, sortedLevels, profile.trackDeg)
    }
    val line4 = if (primaryVar.id != "hw_xw" && primaryVal != null) {
        val pStr = "${primaryVar.shortLabel}: ${formatAxisValue(primaryVal)}${if (primaryVar.unit.isNotEmpty()) " ${primaryVar.unit}" else ""}"
        val sStr = if (secondaryVar != null && secondaryVal != null) {
            " · ${secondaryVar.shortLabel}: ${formatAxisValue(secondaryVal)}${if (secondaryVar.unit.isNotEmpty()) " ${secondaryVar.unit}" else ""}"
        } else {
            ""
        }
        pStr + sStr
    } else if (secondaryVar != null && secondaryVal != null) {
        "${secondaryVar.shortLabel}: ${formatAxisValue(secondaryVal)}${if (secondaryVar.unit.isNotEmpty()) " ${secondaryVar.unit}" else ""}"
    } else {
        null
    }

    val lines = listOfNotNull(line1, line2, line3, line4)

    val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = (if (isDark) Color(0xFFF8FAFC) else Color(0xFF0F172A)).toArgb()
        textSize = badgeTextSizePx
        typeface = Typeface.MONOSPACE
    }

    val padH = 12f
    val padV = 10f
    val lineStep = badgeTextSizePx * 1.35f
    val maxLineWidth = lines.maxOfOrNull { textPaint.measureText(it) } ?: 140f
    val boxW = maxLineWidth + padH * 2f
    val boxH = lines.size * lineStep + padV * 1.6f

    val badgeX = (transform.left + 8f).coerceAtMost((transform.right - boxW - 4f).coerceAtLeast(transform.left + 2f))
    val badgeY = if (y > transform.top + boxH + 16f) {
        transform.top + 8f
    } else {
        (transform.bottom - boxH - 8f).coerceAtLeast(transform.top + 8f)
    }

    val badgeBg = if (isDark) Color(0xFF0F172A).copy(alpha = 0.90f) else Color(0xFFFFFFFF).copy(alpha = 0.92f)
    val badgeBorder = if (isDark) Color(0xFF38BDF8).copy(alpha = 0.6f) else Color(0xFF0284C7).copy(alpha = 0.6f)

    drawRoundRect(
        color = badgeBg,
        topLeft = Offset(badgeX, badgeY),
        size = Size(boxW, boxH),
        cornerRadius = CornerRadius(10f, 10f),
    )
    drawRoundRect(
        color = badgeBorder,
        topLeft = Offset(badgeX, badgeY),
        size = Size(boxW, boxH),
        cornerRadius = CornerRadius(10f, 10f),
        style = Stroke(width = 1.5f),
    )

    lines.forEachIndexed { idx, text ->
        drawContext.canvas.nativeCanvas.drawText(
            text,
            badgeX + padH,
            badgeY + padV + badgeTextSizePx * 0.85f + idx * lineStep,
            textPaint,
        )
    }
}
