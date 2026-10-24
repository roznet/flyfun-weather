package aero.flyfun.weather.ui.map

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
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
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.AlternateAirport
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.SigmetAlongRoute
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.ObservedMapRules
import aero.flyfun.weather.model.viz.VizDataExtractor
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import aero.flyfun.weather.ui.briefing.FlightCategoryPillBadge
import aero.flyfun.weather.ui.briefing.shortModelName
import aero.flyfun.weather.viewmodel.BriefingViewModel
import aero.flyfun.weather.viewmodel.RouteCellsModel
import aero.flyfun.weather.viewmodel.RouteLightningModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Toggleable map overlays on the Briefing Route Map.
 */
enum class RouteMapOverlay(val id: String, val label: String) {
    WIND_BARBS("wind_barbs", "Wind Barbs"),
    FRONTS("fronts", "Fronts"),
    RADAR("radar", "Radar"),
    SAT_IR("sat_ir", "Satellite IR"),
    STORM_CELLS("cells", "Storm Cells"),
    LIGHTNING("lightning", "Lightning"),
    SIGMETS("sigmets", "SIGMETs"),
    ALTERNATES("alternates", "Alternates"),
    OBS_AIRPORTS("obs_airports", "METAR Pins"),
}

/**
 * Selection state when the pilot taps a waypoint, observation airport, or alternate on the Route Map.
 */
sealed interface RouteMapSelection {
    data class RoutePoint(
        val index: Int,
        val point: VizPoint,
        val observation: AirportObservation? = null,
    ) : RouteMapSelection

    data class ObsAirport(
        val airport: AirportObservation,
    ) : RouteMapSelection

    data class AlternatePin(
        val alternate: AlternateAirport,
    ) : RouteMapSelection

    /** A radar-cell core from the raw cell feed: its measurements, as the iOS callout. */
    data class Cell(
        val cell: DisplayCell,
    ) : RouteMapSelection
}

/**
 * Interactive **Briefing Route Map** tab screen (`BriefingTab.MAP`):
 * - Renders route corridor segments colored and sized by any of the 13 [RouteMapMetric]s.
 * - Supports interactive altitude scrubbing, NWP model switching, wind barbs, frontal boundaries,
 *   SIGMET polygons, radar/IR imagery tiles, the raw radar-cell feed (outlines, cores, the feed's own
 *   30-min motion arrows) and lightning flashes fading by age (#907),
 *   en-route METAR airport pins, and alternate airport diamonds.
 * - Tapping any route point or airport opens a rich inspector card with a 1-tap jump to the
 *   Cross-Section & Skew-T diagram at that exact route distance.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RouteMapTabScreen(
    briefingViewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val flight by briefingViewModel.flight.collectAsState()
    val selectedPack by briefingViewModel.selectedPack.collectAsState()
    val routeAnalyses by briefingViewModel.routeAnalyses.collectAsState()
    val elevation by briefingViewModel.elevation.collectAsState()
    val snapshot by briefingViewModel.snapshot.collectAsState()
    val advisories by briefingViewModel.advisories.collectAsState()
    val alternates by briefingViewModel.alternates.collectAsState()
    val liveLayer by briefingViewModel.liveLayer.collectAsState()
    val selectedModel by briefingViewModel.selectedModel.collectAsState()
    val availableModels by briefingViewModel.availableModels.collectAsState()
    val focusIntent by briefingViewModel.focusIntent.collectAsState()

    val vizData: VizRouteData? = remember(
        routeAnalyses,
        elevation,
        selectedPack,
        flight,
        snapshot,
        advisories,
        selectedModel,
    ) {
        val analyses = routeAnalyses ?: return@remember null
        VizDataExtractor.extractVizData(
            routeAnalyses = analyses,
            elevation = elevation,
            snapshot = snapshot,
            advisories = advisories,
            selectedModel = selectedModel,
            flight = flight,
        )
    }

    val cruiseAltFt = vizData?.cruiseAltitudeFt ?: flight?.cruiseAltitudeFt?.toDouble() ?: 8000.0
    val maxAltFt = max(18000.0, vizData?.flightCeilingFt ?: vizData?.maxAltitudeFt ?: 24000.0)

    var selectedMetric by remember { mutableStateOf(RouteMapMetric.FLIGHT_CATEGORY) }
    var selectedAltitudeFt by remember(cruiseAltFt) { mutableDoubleStateOf(cruiseAltFt) }
    var metricMenuExpanded by remember { mutableStateOf(false) }

    var activeOverlays by remember {
        mutableStateOf(
            setOf(
                RouteMapOverlay.WIND_BARBS,
                RouteMapOverlay.FRONTS,
                RouteMapOverlay.SIGMETS,
                RouteMapOverlay.ALTERNATES,
                RouteMapOverlay.OBS_AIRPORTS,
                // On by default, as on iOS: a measurement, not an experimental analysis. Storm
                // Cells stays opt-in (experimental), and both need observed conditions.
                RouteMapOverlay.LIGHTNING,
            ),
        )
    }

    var selection by remember { mutableStateOf<RouteMapSelection?>(null) }
    var showObservedDetails by remember { mutableStateOf(false) }

    // Apply FocusIntent when navigated from Observed Tab ("Show radar & cells on map") or Advisories
    LaunchedEffect(focusIntent, vizData) {
        val intent = focusIntent ?: return@LaunchedEffect
        if (intent.tab != BriefingTab.MAP) return@LaunchedEffect

        if (intent.preset.equals("observed", ignoreCase = true)) {
            activeOverlays = activeOverlays + setOf(
                RouteMapOverlay.RADAR,
                RouteMapOverlay.STORM_CELLS,
                RouteMapOverlay.SIGMETS,
            )
        } else if (!intent.preset.isNullOrBlank()) {
            selectedMetric = RouteMapMetric.fromId(intent.preset)
        }

        val pts = vizData?.points.orEmpty()
        if (pts.isNotEmpty()) {
            val matchedIdx = when {
                !intent.waypointIcao.isNullOrBlank() ->
                    pts.indexOfFirst { it.waypointIcao.equals(intent.waypointIcao, ignoreCase = true) }
                intent.distanceNm != null ->
                    pts.indices.minByOrNull { abs(pts[it].distanceNm - intent.distanceNm) } ?: -1
                else -> -1
            }
            if (matchedIdx in pts.indices) {
                val pt = pts[matchedIdx]
                val obs = (liveLayer?.routeObservations ?: snapshot?.routeObservations)
                    ?.airports
                    ?.firstOrNull { it.icao.equals(pt.waypointIcao, ignoreCase = true) }
                selection = RouteMapSelection.RoutePoint(matchedIdx, pt, obs)
            }
        }
        briefingViewModel.clearFocusIntent()
    }

    val effectiveObservations = liveLayer?.routeObservations ?: snapshot?.routeObservations
    val effectiveSigmets = liveLayer?.routeSigmets ?: snapshot?.routeSigmets

    // Observed layers (#907): the raw cell feed and lightning, polled only while this map is
    // composed (the LaunchedEffects end when the tab is left) and only for a briefing that
    // collected observed conditions, as on iOS. The `/live` storm dots are gone: like iOS, the
    // map draws the raw feed and the Observed tab lists the storms.
    val observedConditions = snapshot?.observedConditions
    val showsObserved = ObservedMapRules.showsObserved(observedConditions)
    val showsCells = showsObserved && RouteMapOverlay.STORM_CELLS in activeOverlays
    val showsLightning = showsObserved && RouteMapOverlay.LIGHTNING in activeOverlays
    val routeLatLons = remember(vizData) {
        vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }.map { it.lat to it.lon }
    }
    val corridorNm = ObservedMapRules.corridorRadius(observedConditions)
    val cellsBox = remember(routeLatLons, corridorNm) { CellsOverlayRules.cellsBox(routeLatLons, corridorNm) }
    val lightningBox = remember(routeLatLons, corridorNm) {
        corridorNm?.let { ObservedMapRules.corridorBox(routeLatLons, it) }
    }
    val cellsModel = remember(briefingViewModel.repository) { RouteCellsModel(briefingViewModel.repository) }
    val lightningModel = remember(briefingViewModel.repository) { RouteLightningModel(briefingViewModel.repository) }
    val cellsState by cellsModel.state.collectAsState()
    val lightningState by lightningModel.state.collectAsState()
    LaunchedEffect(cellsModel, showsCells, cellsBox) {
        val box = cellsBox
        if (showsCells && box != null) cellsModel.poll(box)
    }
    LaunchedEffect(lightningModel, showsLightning, lightningBox) {
        val box = lightningBox
        // Hidden → forget the flashes, so toggling back on shows no stale bolts or badge.
        if (!showsLightning || box == null) {
            lightningModel.reset()
        } else {
            lightningModel.poll(box)
        }
    }
    // Cells first (the overlay the pilot opted into), then lightning (iOS order).
    val observedChips = listOfNotNull(
        cellsState.chip.takeIf { showsCells },
        lightningState.chip.takeIf { showsLightning },
    )
    val observedBadges = listOfNotNull(
        cellsState.badge.takeIf { showsCells },
        lightningState.badge.takeIf { showsLightning },
    )

    val routeBounds: GeoBounds? = remember(vizData, alternates) {
        val pts = vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }
        if (pts.isEmpty()) return@remember null
        var minLat = pts.minOf { it.lat }
        var maxLat = pts.maxOf { it.lat }
        var minLon = pts.minOf { it.lon }
        var maxLon = pts.maxOf { it.lon }
        alternates?.candidates.orEmpty().forEach { alt ->
            minLat = min(minLat, alt.lat)
            maxLat = max(maxLat, alt.lat)
            minLon = min(minLon, alt.lon)
            maxLon = max(maxLon, alt.lon)
        }
        GeoBounds(
            minLat = minLat - 0.35,
            maxLat = maxLat + 0.35,
            minLon = minLon - 0.55,
            maxLon = maxLon + 0.55,
        )
    }

    val cameraState = rememberSlippyMapCameraState(
        initialCenterLat = routeBounds?.centerLat ?: 48.8,
        initialCenterLon = routeBounds?.centerLon ?: 2.4,
        initialZoom = 6.2f,
    )

    val observedStamp = remember(liveLayer, snapshot) {
        val raw = liveLayer?.liveUpdatedAt ?: effectiveObservations?.fetchTime ?: "2026-04-18T12:00:00Z"
        raw.filter { it.isDigit() }.take(12).ifEmpty { "202604181200" }
    }

    val activeRasterSpec: ObservedRasterLayerSpec? = remember(activeOverlays, observedStamp) {
        when {
            RouteMapOverlay.RADAR in activeOverlays ->
                ObservedRasterLayerSpec(layer = "radar", stamp = observedStamp, alpha = 0.62f)
            RouteMapOverlay.SAT_IR in activeOverlays ->
                ObservedRasterLayerSpec(layer = "sat-ir", stamp = observedStamp, alpha = 0.55f)
            else -> null
        }
    }

    val density = LocalDensity.current

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // 1. Top Control Strip: Model Selector + Segment Metric Picker + Altitude Slider
        RouteMapControlsCard(
            availableModels = availableModels,
            selectedModel = selectedModel,
            onSelectModel = { briefingViewModel.selectModel(it) },
            selectedMetric = selectedMetric,
            metricMenuExpanded = metricMenuExpanded,
            onToggleMetricMenu = { metricMenuExpanded = !metricMenuExpanded },
            onSelectMetric = {
                selectedMetric = it
                metricMenuExpanded = false
            },
            selectedAltitudeFt = selectedAltitudeFt,
            cruiseAltitudeFt = cruiseAltFt,
            maxAltitudeFt = maxAltFt,
            onAltitudeChange = { selectedAltitudeFt = it },
        )

        // 2. Observed & Vector Overlays Toggle Chip Bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = Icons.Default.Layers,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(18.dp),
            )
            RouteMapOverlay.entries.forEach { overlay ->
                val observedOnly = overlay == RouteMapOverlay.STORM_CELLS || overlay == RouteMapOverlay.LIGHTNING
                if (observedOnly && !showsObserved) return@forEach
                val isSelected = overlay in activeOverlays
                FilterChip(
                    modifier = Modifier.testTag("map.overlayChip.${overlay.id}"),
                    selected = isSelected,
                    onClick = {
                        activeOverlays = if (isSelected) {
                            activeOverlays - overlay
                        } else {
                            // Keep Radar and Sat-IR mutually exclusive for clarity
                            val cleaned = when (overlay) {
                                RouteMapOverlay.RADAR -> activeOverlays - RouteMapOverlay.SAT_IR
                                RouteMapOverlay.SAT_IR -> activeOverlays - RouteMapOverlay.RADAR
                                else -> activeOverlays
                            }
                            cleaned + overlay
                        }
                    },
                    label = {
                        Text(
                            text = overlay.label,
                            style = MaterialTheme.typography.labelSmall,
                        )
                    },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                        selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    ),
                )
            }

            // Cells and lightning carry their own times in the footer chips.
            if (RouteMapOverlay.RADAR in activeOverlays || RouteMapOverlay.SAT_IR in activeOverlays) {
                Surface(
                    shape = RoundedCornerShape(50),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        text = "Obs ${observedStamp.takeLast(4).chunked(2).joinToString(":")}Z",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
        }

        // 3. Interactive Slippy Map Canvas with Route + Weather Overlays
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            SlippyMapCanvas(
                cameraState = cameraState,
                modifier = Modifier.fillMaxSize(),
                defaultBounds = routeBounds,
                observedRasterLayer = activeRasterSpec,
                onTap = { tapOffset, _, proj ->
                    val pts = vizData?.points.orEmpty()
                    val hitThresholdPx = with(density) { 28.dp.toPx() }

                    // 0. A radar-cell core (drawn above the route): its measurements
                    if (showsCells) {
                        hitCell(cellsState.display, tapOffset, proj, with(density) { 14.dp.toPx() })?.let {
                            selection = RouteMapSelection.Cell(it)
                            return@SlippyMapCanvas
                        }
                    }

                    // 1. Check Alternate airports first
                    if (RouteMapOverlay.ALTERNATES in activeOverlays) {
                        val hitAlt = alternates?.candidates.orEmpty().minByOrNull { alt ->
                            val s = proj.latLonToScreen(alt.lat, alt.lon)
                            hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                        }
                        if (hitAlt != null) {
                            val s = proj.latLonToScreen(hitAlt.lat, hitAlt.lon)
                            if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= hitThresholdPx) {
                                selection = RouteMapSelection.AlternatePin(hitAlt)
                                return@SlippyMapCanvas
                            }
                        }
                    }

                    // 2. Check Route points / waypoints
                    if (pts.isNotEmpty()) {
                        val closestIdx = pts.indices.minByOrNull { idx ->
                            val pt = pts[idx]
                            val s = proj.latLonToScreen(pt.lat, pt.lon)
                            hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                        } ?: -1
                        if (closestIdx in pts.indices) {
                            val pt = pts[closestIdx]
                            val s = proj.latLonToScreen(pt.lat, pt.lon)
                            if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= hitThresholdPx * 1.25f) {
                                val obs = effectiveObservations?.airports?.firstOrNull {
                                    it.icao.equals(pt.waypointIcao, ignoreCase = true)
                                }
                                selection = RouteMapSelection.RoutePoint(closestIdx, pt, obs)
                                return@SlippyMapCanvas
                            }
                        }
                    }

                    // 3. Check En-route observation airports
                    if (RouteMapOverlay.OBS_AIRPORTS in activeOverlays) {
                        val hitObs = effectiveObservations?.airports.orEmpty()
                            .filter { it.lat != null && it.lon != null }
                            .minByOrNull { ap ->
                                val s = proj.latLonToScreen(ap.lat!!, ap.lon!!)
                                hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                            }
                        if (hitObs != null && hitObs.lat != null && hitObs.lon != null) {
                            val s = proj.latLonToScreen(hitObs.lat, hitObs.lon)
                            if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= hitThresholdPx) {
                                selection = RouteMapSelection.ObsAirport(hitObs)
                                return@SlippyMapCanvas
                            }
                        }
                    }

                    selection = null
                },
            ) { proj ->
                val pts = vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }

                // A. Draw SIGMET Polygons
                if (RouteMapOverlay.SIGMETS in activeOverlays) {
                    val sigList = effectiveSigmets?.sigmets.orEmpty()
                    sigList.forEach { sig ->
                        drawSigmetPolygonOnMap(
                            polygonLatLon = sigmetPolygon(sig),
                            label = "${sig.firId} ${sig.hazard.uppercase()}",
                            isSevere = sig.qualifier?.contains("SEV", ignoreCase = true) == true,
                            projection = proj,
                        )
                    }
                }

                // C. Draw Alternate airport dashed connectors + diamond pins
                if (RouteMapOverlay.ALTERNATES in activeOverlays) {
                    val destPt = pts.lastOrNull()
                    alternates?.candidates.orEmpty().forEach { alt ->
                        val altScreen = proj.latLonToScreen(alt.lat, alt.lon)
                        if (destPt != null) {
                            val destScreen = proj.latLonToScreen(destPt.lat, destPt.lon)
                            drawLine(
                                color = Color(0xFF94A3B8).copy(alpha = 0.75f),
                                start = destScreen,
                                end = altScreen,
                                strokeWidth = 2.0f,
                                pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f)),
                            )
                        }
                        val cat = alt.worstCategory.ifBlank { "VFR" }
                        val isSel = (selection as? RouteMapSelection.AlternatePin)?.alternate?.icao == alt.icao
                        drawAirportPinOnMap(
                            center = altScreen,
                            radiusPx = 8.5f.dp.toPx(),
                            fillColor = flightCategoryColor(cat),
                            isSelected = isSel,
                            isAlternate = true,
                            label = "${alt.icao} (ALT)",
                        )
                    }
                }

                // D. Draw En-route METAR/TAF Observation Airport Pins
                if (RouteMapOverlay.OBS_AIRPORTS in activeOverlays) {
                    effectiveObservations?.airports.orEmpty().forEach { ap ->
                        val lat = ap.lat ?: return@forEach
                        val lon = ap.lon ?: return@forEach
                        // Skip if it's already a route waypoint
                        if (pts.any { it.waypointIcao.equals(ap.icao, ignoreCase = true) }) return@forEach
                        val screen = proj.latLonToScreen(lat, lon)
                        val cat = ap.metarFlightCategory ?: ap.tafFlightCategoryAtEta
                        val isSel = (selection as? RouteMapSelection.ObsAirport)?.airport?.icao == ap.icao
                        drawAirportPinOnMap(
                            center = screen,
                            radiusPx = 6.5f.dp.toPx(),
                            fillColor = flightCategoryColor(cat),
                            isSelected = isSel,
                            label = ap.icao,
                        )
                    }
                }

                // E. Draw Route Segments colored & sized by selectedMetric at selectedAltitudeFt
                if (pts.size >= 2) {
                    for (i in 0 until pts.size - 1) {
                        val p0 = pts[i]
                        val p1 = pts[i + 1]
                        val s0 = proj.latLonToScreen(p0.lat, p0.lon)
                        val s1 = proj.latLonToScreen(p1.lat, p1.lon)
                        val v0 = selectedMetric.extractValue(p0, selectedAltitudeFt)
                        val v1 = selectedMetric.extractValue(p1, selectedAltitudeFt)
                        val avgVal = (v0 + v1) / 2.0
                        val segWidthPx = selectedMetric.strokeWidthDp(avgVal).dp.toPx()

                        drawLine(
                            color = Color(0xFF0F172A).copy(alpha = 0.85f),
                            start = s0,
                            end = s1,
                            strokeWidth = segWidthPx + 3.5f.dp.toPx(),
                            cap = StrokeCap.Round,
                        )
                        drawLine(
                            color = selectedMetric.color(avgVal),
                            start = s0,
                            end = s1,
                            strokeWidth = segWidthPx,
                            cap = StrokeCap.Round,
                        )
                    }
                }

                // F. The raw radar-cell feed over the route, then lightning on top (iOS order)
                if (showsCells) cellsState.display?.let { drawCellsOverlayOnMap(it, proj) }
                if (showsLightning) drawLightningOnMap(lightningState.drawable, proj)

                // G. Draw Wind Barbs along route sample points
                if (RouteMapOverlay.WIND_BARBS in activeOverlays && pts.isNotEmpty()) {
                    pts.forEachIndexed { idx, pt ->
                        if (pts.size <= 10 || idx % 2 == 0 || idx == pts.lastIndex) {
                            val s = proj.latLonToScreen(pt.lat, pt.lon)
                            drawWindBarbOnMap(
                                center = s,
                                windSpeedKt = pt.windSpeedKt ?: 0.0,
                                windDirDeg = pt.windDirectionDeg ?: 0.0,
                                color = Color(0xFFE2E8F0),
                                staffLengthPx = 22.dp.toPx(),
                            )
                        }
                    }
                }

                // H. Draw Frontal Boundaries crossing the route
                if (RouteMapOverlay.FRONTS in activeOverlays && vizData != null) {
                    vizData.fronts.forEach { front ->
                        val pt = pts.minByOrNull { abs(it.distanceNm - front.distanceNm) } ?: return@forEach
                        val s = proj.latLonToScreen(pt.lat, pt.lon)
                        drawFrontSymbolOnMap(
                            center = s,
                            routeBearingDeg = 90.0,
                            frontType = front.frontType,
                            lengthPx = 36.dp.toPx(),
                        )
                    }
                }

                // I. Draw Waypoint & Route Sample Pins
                pts.forEachIndexed { idx, pt ->
                    val s = proj.latLonToScreen(pt.lat, pt.lon)
                    val metricVal = selectedMetric.extractValue(pt, selectedAltitudeFt)
                    val ptColor = selectedMetric.color(metricVal)
                    val isTerminal = idx == 0 || idx == pts.lastIndex
                    val isNamedWaypoint = !pt.waypointIcao.isNullOrBlank()
                    val isSelected = (selection as? RouteMapSelection.RoutePoint)?.index == idx

                    val radiusDp = when {
                        isTerminal -> 8.5f
                        isNamedWaypoint -> 7.0f
                        else -> 5.0f
                    }
                    drawAirportPinOnMap(
                        center = s,
                        radiusPx = radiusDp.dp.toPx(),
                        fillColor = ptColor,
                        isSelected = isSelected,
                        label = pt.waypointIcao?.takeIf { it.isNotBlank() },
                    )
                }
            }

            // Floating Altitude & Metric Badge in Top-Start of Map
            Surface(
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(10.dp),
                shape = RoundedCornerShape(8.dp),
                color = MaterialTheme.colorScheme.surface.copy(alpha = 0.88f),
                tonalElevation = 2.dp,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = selectedMetric.label,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = "·",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "FL${(selectedAltitudeFt / 100.0).roundToInt().toString().padStart(3, '0')} (${selectedAltitudeFt.roundToInt()} ft)",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            // Each observed layer's age, tappable for the full lines, legends and caveats.
            ObservedLayersFooter(
                chips = observedChips,
                badges = observedBadges,
                onClick = { showObservedDetails = true },
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(10.dp),
            )
        }

        if (showObservedDetails) {
            ObservedLayersDetailsDialog(
                badges = observedBadges,
                showsCells = showsCells,
                showsLightning = showsLightning,
                onDismiss = { showObservedDetails = false },
            )
        }

        // 4. Horizontal Color Scale Legend Bar
        MapLegendBar(
            metricTitle = selectedMetric.shortLabel,
            swatches = selectedMetric.legendSwatches(),
        )

        // 5. Tapped Waypoint / Airport / Alternate Inspector Bottom Card
        selection?.let { sel ->
            RouteMapSelectionCard(
                selection = sel,
                selectedMetric = selectedMetric,
                selectedAltitudeFt = selectedAltitudeFt,
                onDismiss = { selection = null },
                onOpenSkewT = { distNm, icao ->
                    briefingViewModel.applyFocusIntent(
                        FocusIntent(
                            tab = BriefingTab.CROSS_SECTION,
                            distanceNm = distNm,
                            waypointIcao = icao,
                        ),
                    )
                },
            )
        }
    }
}

@Composable
private fun RouteMapControlsCard(
    availableModels: List<String>,
    selectedModel: String,
    onSelectModel: (String) -> Unit,
    selectedMetric: RouteMapMetric,
    metricMenuExpanded: Boolean,
    onToggleMetricMenu: () -> Unit,
    onSelectMetric: (RouteMapMetric) -> Unit,
    selectedAltitudeFt: Double,
    cruiseAltitudeFt: Double,
    maxAltitudeFt: Double,
    onAltitudeChange: (Double) -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Model chips
                Row(
                    modifier = Modifier
                        .weight(1f)
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    availableModels.forEach { model ->
                        val selected = model == selectedModel
                        FilterChip(
                            selected = selected,
                            onClick = { onSelectModel(model) },
                            label = {
                                Text(
                                    text = model.shortModelName(),
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                )
                            },
                        )
                    }
                }

                Spacer(modifier = Modifier.width(8.dp))

                // Metric Selector Dropdown Button
                Box {
                    OutlinedButton(
                        onClick = onToggleMetricMenu,
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            horizontal = 10.dp,
                            vertical = 4.dp,
                        ),
                    ) {
                        Text(
                            text = selectedMetric.shortLabel,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Icon(
                            imageVector = Icons.Default.ArrowDropDown,
                            contentDescription = "Choose route metric",
                            modifier = Modifier.size(18.dp),
                        )
                    }

                    DropdownMenu(
                        expanded = metricMenuExpanded,
                        onDismissRequest = onToggleMetricMenu,
                    ) {
                        RouteMapMetric.entries.forEach { metric ->
                            DropdownMenuItem(
                                text = {
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Text(
                                            text = metric.label,
                                            fontWeight = if (metric == selectedMetric) FontWeight.Bold else FontWeight.Normal,
                                        )
                                        if (metric.unit.isNotBlank()) {
                                            Spacer(modifier = Modifier.width(12.dp))
                                            Text(
                                                text = metric.unit,
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            )
                                        }
                                    }
                                },
                                onClick = { onSelectMetric(metric) },
                            )
                        }
                    }
                }
            }

            // Altitude Slider Row
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = "Alt ${(selectedAltitudeFt / 1000.0).roundToInt()}k ft",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.width(64.dp),
                )
                Slider(
                    value = selectedAltitudeFt.toFloat().coerceIn(2000f, maxAltitudeFt.toFloat()),
                    onValueChange = { raw ->
                        val stepped = ((raw / 1000f).roundToInt() * 1000).toDouble()
                        onAltitudeChange(stepped)
                    },
                    valueRange = 2000f..maxAltitudeFt.toFloat(),
                    modifier = Modifier
                        .weight(1f)
                        .height(26.dp),
                )
                if (abs(selectedAltitudeFt - cruiseAltitudeFt) >= 500.0) {
                    TextButton(
                        onClick = { onAltitudeChange(cruiseAltitudeFt) },
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 6.dp),
                    ) {
                        Text(
                            text = "Cruise",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
        }
    }
}

/**
 * Reusable horizontal color scale legend bar used by both `RouteMapTabScreen` and `PanEuropeanMapScreen`.
 */
@Composable
fun MapLegendBar(
    metricTitle: String,
    swatches: List<MapLegendSwatch>,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 12.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "$metricTitle:",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            swatches.forEach { swatch ->
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(swatch.color),
                    )
                    Text(
                        text = swatch.label,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RouteMapSelectionCard(
    selection: RouteMapSelection,
    selectedMetric: RouteMapMetric,
    selectedAltitudeFt: Double,
    onDismiss: () -> Unit,
    onOpenSkewT: (Double, String?) -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.35f)),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            when (selection) {
                is RouteMapSelection.RoutePoint -> {
                    val pt = selection.point
                    val metricVal = selectedMetric.extractValue(pt, selectedAltitudeFt)
                    val formattedMetric = selectedMetric.formatValue(metricVal)
                    val metricColor = selectedMetric.color(metricVal)

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                text = pt.waypointIcao?.takeIf { it.isNotBlank() } ?: "Route Pt #${selection.index + 1}",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            Text(
                                text = "${pt.distanceNm.roundToInt()} NM",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Surface(
                                shape = RoundedCornerShape(50),
                                color = metricColor.copy(alpha = 0.18f),
                            ) {
                                Text(
                                    text = "${selectedMetric.shortLabel}: $formattedMetric",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = metricColor,
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                                )
                            }
                        }
                        IconButton(
                            onClick = onDismiss,
                            modifier = Modifier.size(24.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.Close,
                                contentDescription = "Close",
                                modifier = Modifier.size(16.dp),
                            )
                        }
                    }

                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val hw = pt.headwindKt ?: 0.0
                        val xw = pt.crosswindKt ?: 0.0
                        val spd = pt.windSpeedKt ?: 0.0
                        val dir = pt.windDirectionDeg ?: 0.0
                        val hwLabel = if (hw >= 0.0) {
                            "+${hw.roundToInt()}kt HW"
                        } else {
                            "${abs(hw).roundToInt()}kt TW"
                        }
                        Text(
                            text = String.format(
                                Locale.US,
                                "Wind %03d°/%dkt (%s, %dkt XW)",
                                dir.roundToInt(),
                                spd.roundToInt(),
                                hwLabel,
                                abs(xw).roundToInt(),
                            ),
                            style = MaterialTheme.typography.bodySmall,
                        )
                        pt.altitudeLines.freezingLevelFt?.let { fzl ->
                            Text(
                                text = "0°C: ${fzl.roundToInt()} ft",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        Text(
                            text = "Clouds: ${(pt.cloudCoverTotalPct ?: 0.0).roundToInt()}% (Low ${(pt.cloudCoverLowPct ?: 0.0).roundToInt()}%)",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }

                    selection.observation?.metarRaw?.takeIf { it.isNotBlank() }?.let { raw ->
                        Text(
                            text = raw,
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.End,
                    ) {
                        OutlinedButton(
                            onClick = { onOpenSkewT(pt.distanceNm, pt.waypointIcao) },
                            contentPadding = ButtonDefaults.TextButtonContentPadding,
                        ) {
                            Icon(
                                imageVector = Icons.Default.ShowChart,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                            Text(
                                text = "Open Skew-T & Cross-Section here ›",
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }

                is RouteMapSelection.ObsAirport -> {
                    val ap = selection.airport
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                text = ap.icao,
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            ap.metarFlightCategory?.let { FlightCategoryPillBadge(it) }
                            Text(
                                text = "${ap.distanceFromRouteNm.roundToInt()} NM off route",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                            Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                        }
                    }
                    if (!ap.name.isNullOrBlank()) {
                        Text(
                            text = ap.name,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (!ap.metarRaw.isNullOrBlank()) {
                        Text(
                            text = ap.metarRaw,
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                    if (!ap.tafRaw.isNullOrBlank()) {
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        Text(
                            text = ap.tafRaw,
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                is RouteMapSelection.Cell -> {
                    val cell = selection.cell
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = CellsOverlayRules.tierLabel(cell.tier) + " · experimental",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                            Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                        }
                    }
                    CellsOverlayRules.detailLines(cell).forEach { line ->
                        Text(text = line, style = MaterialTheme.typography.bodySmall)
                    }
                }

                is RouteMapSelection.AlternatePin -> {
                    val alt = selection.alternate
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                text = "${alt.icao} · Alternate",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            alt.worstCategory.takeIf { it.isNotBlank() }?.let {
                                FlightCategoryPillBadge(it)
                            }
                            Text(
                                text = "${alt.distanceToDestNm.roundToInt()} NM from dest",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                            Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                        }
                    }
                    Text(
                        text = alt.name,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Medium,
                    )
                    if (alt.summary.isNotBlank()) {
                        Text(
                            text = alt.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

// ============================================================================
// Geometry for SIGMETs
// ============================================================================

/**
 * The SIGMET's own outline from the server (`coords`, (lon, lat) vertices). Empty for a
 * SIGMET matched by FIR only: nothing is drawn rather than a shape the server never sent.
 */
private fun sigmetPolygon(sigmet: SigmetAlongRoute): List<LatLon> =
    sigmet.coords.mapNotNull { pair ->
        if (pair.size < 2) null else LatLon(lat = pair[1], lon = pair[0])
    }
