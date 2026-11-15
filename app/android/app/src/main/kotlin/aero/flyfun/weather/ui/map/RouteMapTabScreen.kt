package aero.flyfun.weather.ui.map

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.FitScreen
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.HelpCatalogStore
import aero.flyfun.weather.data.MapMetricsCatalog
import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.ForecastModelMode
import aero.flyfun.weather.model.FrontChain
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.rules.CellsOverlayRules
import aero.flyfun.weather.model.rules.ObservedImageryRules
import aero.flyfun.weather.model.rules.ObservedMapRules
import aero.flyfun.weather.model.viz.CrossSectionPresets
import aero.flyfun.weather.model.viz.VizDataExtractor
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import aero.flyfun.weather.ui.briefing.shortModelName
import aero.flyfun.weather.viewmodel.BriefingViewModel
import aero.flyfun.weather.viewmodel.RouteAirportOverlayModel
import aero.flyfun.weather.viewmodel.RouteCellsModel
import aero.flyfun.weather.viewmodel.RouteLightningModel
import aero.flyfun.weather.viewmodel.RouteObservedImageryModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.roundToInt

// SYNC: web web/ts/visualization/route-map/map-chrome.ts + briefing-main.ts (map pane),
//       iOS app/flyfun-weather/flyfun-weather/Views/Map/RouteMapView.swift

/**
 * The briefing route map's picks, kept for the app session (#998): leaving the Map tab or opening
 * another flight keeps them, as the web keeps its viz settings and iOS its AppStorage. Hide is
 * session-only on every surface.
 */
object RouteMapSession {
    var hidden by mutableStateOf(false)
    /** Catalog metric id; null = the catalog default. */
    var metricId by mutableStateOf<String?>(null)
    var airportsVisible by mutableStateOf(true)
    var airportMetric by mutableStateOf("flight_category")
    /** Radar source id, or "" for none. */
    var radarPick by mutableStateOf(ObservedImageryRules.DEFAULT_SELECTION)
    var radarOpacity by mutableFloatStateOf(ObservedImageryRules.DEFAULT_OPACITY)
    /** On by default: radar over the satellite picture is the view pilots know. */
    var satellite by mutableStateOf(true)
    /** A measurement, on by default; cells are experimental and opted into. */
    var lightning by mutableStateOf(true)
    var cells by mutableStateOf(false)
    /** Off by default, as on web and iOS. */
    var fronts by mutableStateOf(false)
}

/** Selection when the pilot taps a route point or a radar-cell core. */
sealed interface RouteMapSelection {
    data class RoutePoint(
        val index: Int,
        val point: VizPoint,
        val observation: AirportObservation? = null,
    ) : RouteMapSelection

    /** A radar-cell core from the raw cell feed: its measurements, as the iOS callout. */
    data class Cell(val cell: DisplayCell) : RouteMapSelection
}

/**
 * Advisory focus preset → route metric (web `advisory-presets.ts` `map.metric`). An unknown id is
 * tried as a catalog metric id as is.
 */
internal fun routeMetricForFocusPreset(preset: String): String {
    val id = CrossSectionPresets.ADVISORY_TO_PRESET[preset.lowercase(Locale.US)] ?: preset.lowercase(Locale.US)
    return when (id) {
        "icing", "ifr" -> "icing-risk-at-level"
        "clouds" -> "cloud-at-level"
        "convective" -> "convective-risk"
        "turbulence" -> "cat-risk-at-level"
        "vfr" -> "nwp-ceiling"
        else -> preset
    }
}

private val OBSERVED_FOCUS_PRESETS = setOf("observed", "radar", "cells", "storms", "lightning")

/** Where a linked front chain crosses the route: its lowest node, placed on the viz points. */
internal fun frontPosition(chain: FrontChain, points: List<VizPoint>): Pair<VizPoint, VizPoint>? {
    val node = chain.nodes.firstOrNull() ?: return null
    val d = node.distanceKm / 1.852
    val first = points.firstOrNull() ?: return null
    val last = points.last()
    if (d < first.distanceNm || d > last.distanceNm) return null
    for (i in 0 until points.lastIndex) {
        val a = points[i]
        val b = points[i + 1]
        if (d >= a.distanceNm && d <= b.distanceNm) {
            val span = b.distanceNm - a.distanceNm
            val t = if (span > 0) (d - a.distanceNm) / span else 0.0
            val at = a.copy(lat = a.lat + t * (b.lat - a.lat), lon = a.lon + t * (b.lon - a.lon), distanceNm = d)
            return at to b
        }
    }
    return null
}

/**
 * The **Briefing Route Map** tab (`BriefingTab.MAP`), the #996 map chrome (#998):
 * - Top: the `◀ FL080 ▶` altitude stepper (altitude metrics only; tapping the level resets to
 *   cruise) and the Hide pill.
 * - Map: the route coloured and sized by one catalog metric, observed imagery (radar over
 *   satellite IR), lightning, radar cells, the airport-forecast markers and fronts.
 * - Floating: the observed badge (bottom-left), Forecast map and Fit route (bottom-right).
 * - Bottom panel: SHOWING / MODEL / LAYERS and one legend area (route scale + airport line).
 * Tapping a route point opens an inspector with a jump to the cross-section at that distance.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RouteMapTabScreen(
    briefingViewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
    /** Opens the full forecast map at the overlay's slot; null hides the button. */
    onOpenForecastMap: ((NavigationTarget.ForecastMap) -> Unit)? = null,
) {
    val flight by briefingViewModel.flight.collectAsState()
    val selectedPack by briefingViewModel.selectedPack.collectAsState()
    val routeAnalyses by briefingViewModel.routeAnalyses.collectAsState()
    val elevation by briefingViewModel.elevation.collectAsState()
    val snapshot by briefingViewModel.snapshot.collectAsState()
    val advisories by briefingViewModel.advisories.collectAsState()
    val liveLayer by briefingViewModel.liveLayer.collectAsState()
    val selectedModel by briefingViewModel.selectedModel.collectAsState()
    val availableModels by briefingViewModel.availableModels.collectAsState()
    val focusIntent by briefingViewModel.focusIntent.collectAsState()

    val context = LocalContext.current
    val helpStore = briefingViewModel.helpStore ?: remember(context) { HelpCatalogStore(context) }
    val routeCatalog by helpStore.routeMapCatalog.collectAsState()
    val mapCatalog by helpStore.mapCatalog.collectAsState()
    val metrics = remember(routeCatalog) { routeCatalog?.let { RouteMapMetrics(it) } }

    val vizData: VizRouteData? = remember(routeAnalyses, elevation, selectedPack, flight, snapshot, advisories, selectedModel) {
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
    val ceilingFt = max(18000.0, vizData?.flightCeilingFt ?: vizData?.maxAltitudeFt ?: 24000.0)
    var altitudeFt by remember(cruiseAltFt) { mutableDoubleStateOf(cruiseAltFt) }

    val session = RouteMapSession
    val metric = metrics?.resolve(session.metricId)
    val metricAltitude: Double? = if (metric?.usesAltitude == true) altitudeFt else null

    var selection by remember { mutableStateOf<RouteMapSelection?>(null) }
    var showObservedDetails by remember { mutableStateOf(false) }
    var showLayers by remember { mutableStateOf(false) }

    // Observed layers: offered only for a briefing that collected observed conditions (web/iOS gate).
    val observedConditions = snapshot?.observedConditions
    val showsObserved = ObservedMapRules.showsObserved(observedConditions)
    val radarOptions = remember(observedConditions) { ObservedImageryRules.availableRadar(observedConditions) }
    val radarSource = if (showsObserved) ObservedImageryRules.resolveSelection(session.radarPick, radarOptions) else ""
    val showsSatellite = showsObserved && session.satellite
    val showsCells = showsObserved && session.cells
    // Lightning is offered only when the briefing collected lightning (web/iOS).
    val lightningCollected = showsObserved && observedConditions?.lightning != null
    val showsLightning = lightningCollected && session.lightning

    // Apply a FocusIntent from the Advisories or Observed tab once.
    LaunchedEffect(focusIntent, vizData, metrics) {
        val intent = focusIntent ?: return@LaunchedEffect
        if (intent.tab != BriefingTab.MAP) return@LaunchedEffect
        val preset = intent.preset
        if (!preset.isNullOrBlank()) {
            if (preset.lowercase(Locale.US) in OBSERVED_FOCUS_PRESETS) {
                if (session.radarPick.isEmpty()) session.radarPick = ObservedImageryRules.DEFAULT_SELECTION
                if (preset.equals("lightning", ignoreCase = true)) session.lightning = true else session.cells = true
            } else {
                val id = routeMetricForFocusPreset(preset)
                if (metrics?.metric(id) != null) {
                    session.metricId = id
                    altitudeFt = cruiseAltFt
                }
            }
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

    val routeLatLons = remember(vizData) {
        vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }.map { it.lat to it.lon }
    }
    val corridorNm = ObservedMapRules.corridorRadius(observedConditions)
    val cellsBox = remember(routeLatLons, corridorNm) { CellsOverlayRules.cellsBox(routeLatLons, corridorNm) }
    val lightningBox = remember(routeLatLons, corridorNm) { corridorNm?.let { ObservedMapRules.corridorBox(routeLatLons, it) } }
    val cellsModel = remember(briefingViewModel.repository) { RouteCellsModel(briefingViewModel.repository) }
    val lightningModel = remember(briefingViewModel.repository) { RouteLightningModel(briefingViewModel.repository) }
    val imageryModel = remember(briefingViewModel.repository) { RouteObservedImageryModel(briefingViewModel.repository) }
    val airportModel = remember(briefingViewModel.apiClient) { RouteAirportOverlayModel(briefingViewModel.apiClient) }
    val cellsState by cellsModel.state.collectAsState()
    val lightningState by lightningModel.state.collectAsState()
    val imageryState by imageryModel.state.collectAsState()
    val airportState by airportModel.state.collectAsState()

    LaunchedEffect(cellsModel, showsCells, cellsBox) {
        val box = cellsBox
        if (showsCells && box != null) cellsModel.poll(box)
    }
    LaunchedEffect(showsCells) {
        if (!showsCells && selection is RouteMapSelection.Cell) selection = null
    }
    LaunchedEffect(lightningModel, showsLightning, lightningBox) {
        val box = lightningBox
        if (!showsLightning || box == null) lightningModel.reset() else lightningModel.poll(box)
    }
    val imagerySources = remember(radarSource, showsSatellite) {
        setOfNotNull(radarSource.takeIf { it.isNotEmpty() }, ObservedImageryRules.SATELLITE_SOURCE.takeIf { showsSatellite })
    }
    LaunchedEffect(imageryModel, imagerySources) {
        if (imagerySources.isNotEmpty()) imageryModel.poll(imagerySources)
    }
    val departureIso = flight?.departureTime
    LaunchedEffect(airportModel, departureIso, session.airportsVisible) {
        if (session.airportsVisible) airportModel.load(departureIso)
    }

    // Imagery is recomposed with the listings and the clock of the last listing change.
    val imagery = remember(imageryState, radarSource, showsSatellite, session.radarOpacity) {
        ObservedImageryRules.compose(
            selection = radarSource,
            showSatellite = showsSatellite,
            radarOpacity = session.radarOpacity,
            frames = imageryState.frames,
            failed = imageryState.failed,
            nowMs = System.currentTimeMillis(),
        )
    }
    val observedChips = imagery.chips + listOfNotNull(
        cellsState.chip.takeIf { showsCells },
        lightningState.chip.takeIf { showsLightning },
    )
    val observedBadges = imagery.badges + listOfNotNull(
        cellsState.badge.takeIf { showsCells },
        lightningState.badge.takeIf { showsLightning },
    )

    // Airport overlay: only within the horizon, toggled on, and with data for the briefing's model.
    val airportSlot = remember(airportState, departureIso) { airportModel.slot(departureIso) }
    val airportModelSupported = airportSlot?.models?.contains(selectedModel) == true
    val airportMetric = session.airportMetric.takeIf { mapCatalog?.metrics?.containsKey(it) == true } ?: "flight_category"
    val airportPayload = airportSlot?.let { airportState.payloads[it.key] }
    val overlayAirports = if (session.airportsVisible && airportModelSupported) airportPayload?.airports.orEmpty() else emptyList()
    val airportMode = ForecastModelMode.Model(selectedModel)
    val airportFills = remember(overlayAirports, mapCatalog, airportMetric, selectedModel) {
        overlayAirports.map { AirportMapCatalog.color(mapCatalog, airportMetric, it, airportMode) }
    }

    val frontChains = vizData?.frontChains.orEmpty()
    val hasFronts = frontChains.isNotEmpty()

    val routeBounds: GeoBounds? = remember(vizData) {
        val pts = vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }
        if (pts.isEmpty()) return@remember null
        GeoBounds(
            minLat = pts.minOf { it.lat } - 0.35,
            maxLat = pts.maxOf { it.lat } + 0.35,
            minLon = pts.minOf { it.lon } - 0.55,
            maxLon = pts.maxOf { it.lon } + 0.55,
        )
    }
    val cameraState = rememberSlippyMapCameraState(
        initialCenterLat = routeBounds?.centerLat ?: 48.8,
        initialCenterLon = routeBounds?.centerLon ?: 2.4,
        initialZoom = 6.2f,
    )
    val density = LocalDensity.current
    val noDataColor = metrics?.noDataColor ?: AirportMapCatalog.MUTED

    val openForecastTarget: NavigationTarget.ForecastMap? = airportSlot?.let { slot ->
        NavigationTarget.ForecastMap(
            dayOffset = slot.day,
            hourUtc = slot.hour,
            model = selectedModel.takeIf { it in RouteAirportOverlayModel.INDIVIDUAL_MODELS },
            metric = airportMetric,
        )
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // 1. Top bar: altitude stepper (altitude metrics only) + Hide
        if (!session.hidden) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (metric?.usesAltitude == true) {
                    AltitudeStepper(
                        altitudeFt = altitudeFt,
                        cruiseFt = cruiseAltFt,
                        onStep = { dir -> altitudeFt = RouteMapMetrics.stepAltitude(altitudeFt, dir, ceilingFt) },
                        onReset = { altitudeFt = cruiseAltFt },
                    )
                }
                Spacer(Modifier.weight(1f))
                MapPill(
                    text = "Hide",
                    icon = { Icon(Icons.Default.VisibilityOff, contentDescription = null, modifier = Modifier.size(16.dp)) },
                    onClick = { session.hidden = true },
                    testTag = "map.hideControls",
                )
            }
        }

        // 2. The map, with its floating controls
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            SlippyMapCanvas(
                cameraState = cameraState,
                modifier = Modifier.fillMaxSize(),
                apiClient = briefingViewModel.apiClient,
                observedTileLayers = imagery.layers,
                defaultBounds = routeBounds,
                showFitButton = false,
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

                    // 1. A route point / waypoint
                    if (pts.isNotEmpty()) {
                        val closestIdx = pts.indices.minByOrNull { idx ->
                            val s = proj.latLonToScreen(pts[idx].lat, pts[idx].lon)
                            hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                        } ?: -1
                        if (closestIdx in pts.indices) {
                            val pt = pts[closestIdx]
                            val s = proj.latLonToScreen(pt.lat, pt.lon)
                            if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= hitThresholdPx * 1.25f) {
                                val obs = (liveLayer?.routeObservations ?: snapshot?.routeObservations)?.airports?.firstOrNull {
                                    it.icao.equals(pt.waypointIcao, ignoreCase = true)
                                }
                                selection = RouteMapSelection.RoutePoint(closestIdx, pt, obs)
                                return@SlippyMapCanvas
                            }
                        }
                    }

                    // 2. An airport-forecast marker: its full card on the forecast map
                    val target = openForecastTarget
                    if (target != null && onOpenForecastMap != null && overlayAirports.isNotEmpty()) {
                        val hit = overlayAirports.minByOrNull { ap ->
                            val s = proj.latLonToScreen(ap.lat, ap.lon)
                            hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                        }
                        if (hit != null) {
                            val s = proj.latLonToScreen(hit.lat, hit.lon)
                            if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= hitThresholdPx * 0.6f) {
                                onOpenForecastMap(target.copy(airport = hit.icao))
                                return@SlippyMapCanvas
                            }
                        }
                    }

                    selection = null
                },
            ) { proj ->
                val pts = vizData?.points.orEmpty().filter { it.lat != 0.0 || it.lon != 0.0 }

                // A. Airport-forecast markers, under the route
                val airportRadius = 5.dp.toPx()
                overlayAirports.forEachIndexed { i, ap ->
                    val c = proj.latLonToScreen(ap.lat, ap.lon)
                    if (c.x < -airportRadius || c.y < -airportRadius || c.x > size.width + airportRadius || c.y > size.height + airportRadius) {
                        return@forEachIndexed
                    }
                    drawCircle(color = airportFills[i].copy(alpha = 0.85f), radius = airportRadius, center = c)
                    drawCircle(color = Color(0xFF0F172A).copy(alpha = 0.6f), radius = airportRadius, center = c, style = Stroke(width = 1.dp.toPx()))
                }

                // B. Route segments: one metric drives colour and width; no value draws grey and thin
                if (pts.size >= 2) {
                    for (i in 0 until pts.size - 1) {
                        val p0 = pts[i]
                        val p1 = pts[i + 1]
                        val s0 = proj.latLonToScreen(p0.lat, p0.lon)
                        val s1 = proj.latLonToScreen(p1.lat, p1.lon)
                        val v = metric?.segmentValue(p0, p1, metricAltitude)
                        val widthPx = (if (v != null && metric != null) metric.widthDp(v) else RouteMapMetrics.MIN_WIDTH_DP.toFloat()).dp.toPx()
                        val color = if (v != null && metric != null) metric.color(v) else noDataColor
                        drawLine(
                            color = Color(0xFF0F172A).copy(alpha = 0.85f),
                            start = s0,
                            end = s1,
                            strokeWidth = widthPx + 3.dp.toPx(),
                            cap = StrokeCap.Round,
                        )
                        drawLine(color = color, start = s0, end = s1, strokeWidth = widthPx, cap = StrokeCap.Round)
                    }
                }

                // C. Fronts where the selected model's linked chains cross the route
                if (session.fronts) {
                    frontChains.forEach { chain ->
                        val (at, next) = frontPosition(chain, pts) ?: return@forEach
                        val s = proj.latLonToScreen(at.lat, at.lon)
                        val n = proj.latLonToScreen(next.lat, next.lon)
                        val bearing = Math.toDegrees(atan2((n.y - s.y).toDouble(), (n.x - s.x).toDouble()))
                        drawFrontSymbolOnMap(center = s, routeBearingDeg = bearing, frontType = chain.kind, lengthPx = 36.dp.toPx())
                    }
                }

                // D. The raw radar-cell feed, then lightning on top (iOS order)
                if (showsCells) cellsState.display?.let { drawCellsOverlayOnMap(it, proj) }
                if (showsLightning) drawLightningOnMap(lightningState.drawable, proj)

                // E. Departure, destination and named waypoints
                pts.forEachIndexed { idx, pt ->
                    val isTerminal = idx == 0 || idx == pts.lastIndex
                    val isNamed = !pt.waypointIcao.isNullOrBlank()
                    if (!isTerminal && !isNamed) return@forEachIndexed
                    val v = metric?.value(pt, metricAltitude)
                    drawAirportPinOnMap(
                        center = proj.latLonToScreen(pt.lat, pt.lon),
                        radiusPx = (if (isTerminal) 7.5f else 6f).dp.toPx(),
                        fillColor = if (v != null && metric != null) metric.color(v) else noDataColor,
                        isSelected = (selection as? RouteMapSelection.RoutePoint)?.index == idx,
                        label = pt.waypointIcao?.takeIf { it.isNotBlank() },
                    )
                }
            }

            if (session.hidden) {
                MapPill(
                    text = "Show",
                    icon = { Icon(Icons.Default.Visibility, contentDescription = null, modifier = Modifier.size(16.dp)) },
                    onClick = { session.hidden = false },
                    testTag = "map.showControls",
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(10.dp),
                )
            }

            // Each observed layer's age, tappable for the full lines and caveats. Stays when
            // hidden: an empty map must never read as "no echoes".
            ObservedLayersFooter(
                chips = observedChips,
                badges = observedBadges,
                onClick = { showObservedDetails = true },
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(10.dp),
            )

            if (!session.hidden) {
                Column(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(10.dp),
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (openForecastTarget != null && onOpenForecastMap != null) {
                        MapPill(
                            text = "Forecast map",
                            icon = { Icon(Icons.Default.Map, contentDescription = null, modifier = Modifier.size(16.dp)) },
                            onClick = { onOpenForecastMap(openForecastTarget) },
                            testTag = "map.openForecastMap",
                        )
                    }
                    MapPill(
                        text = "Fit route",
                        icon = { Icon(Icons.Default.FitScreen, contentDescription = null, modifier = Modifier.size(16.dp)) },
                        onClick = { routeBounds?.let { cameraState.fitBounds(it) } },
                        testTag = "map.fitRoute",
                    )
                }
            }
        }

        if (showObservedDetails) {
            ObservedLayersDetailsDialog(
                badges = observedBadges,
                showsCells = showsCells,
                showsLightning = showsLightning,
                onDismiss = { showObservedDetails = false },
            )
        }

        // 3. Bottom panel: SHOWING · MODEL · LAYERS, then the one legend area
        if (!session.hidden) {
            val hasAirports = airportSlot != null
            val hasLayers = hasAirports || showsObserved || hasFronts
            BottomPanel(
                legend = {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (metric != null) {
                            MapLegendBar(
                                metricTitle = metric.label,
                                swatches = metric.legend + MapLegendSwatch("No data", noDataColor),
                            )
                        }
                        if (overlayAirports.isNotEmpty()) {
                            val legend = AirportMapCatalog.legend(mapCatalog, airportMetric)
                            if (legend != null) {
                                MapLegendBar(
                                    metricTitle = "Airports ${validTimeShort(airportPayload?.forecastTime)}".trim(),
                                    swatches = legend.items.map { MapLegendSwatch(it.label, parseCatalogColor(it.color) ?: AirportMapCatalog.MUTED) },
                                )
                            }
                        }
                    }
                },
            ) {
                MenuButton(
                    text = metric?.label ?: "…",
                    caption = "Showing",
                    testTag = "map.metricMenu",
                    modifier = Modifier.weight(1.4f),
                    sections = metrics?.groups.orEmpty().map { g ->
                        MenuSection(g.label, g.metrics.map { m -> MenuChoice(m.label, m.id == metric?.id) { session.metricId = m.id } })
                    },
                )
                if (availableModels.size > 1) {
                    MenuButton(
                        text = selectedModel.shortModelName(),
                        caption = "Model",
                        testTag = "map.modelMenu",
                        modifier = Modifier.weight(1f),
                        sections = listOf(
                            MenuSection(
                                null,
                                availableModels.map { m ->
                                    MenuChoice(m.shortModelName(), m == selectedModel) { briefingViewModel.selectModel(m) }
                                },
                            ),
                        ),
                    )
                }
                if (hasLayers) {
                    CaptionButton(
                        caption = "Layers",
                        text = layersSummary(
                            airports = overlayAirports.isNotEmpty(),
                            radar = radarSource.isNotEmpty(),
                            satellite = showsSatellite,
                            lightning = showsLightning,
                            cells = showsCells,
                            fronts = hasFronts && session.fronts,
                        ),
                        onClick = { showLayers = true },
                        testTag = "map.layersMenu",
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }

        if (showLayers) {
            LayersDialog(
                mapCatalog = mapCatalog,
                hasAirports = airportSlot != null,
                airportModelSupported = airportModelSupported,
                selectedModel = selectedModel,
                airportMetric = airportMetric,
                showsObserved = showsObserved,
                radarOptions = radarOptions,
                radarSource = radarSource,
                lightningCollected = lightningCollected,
                hasFronts = hasFronts,
                onDismiss = { showLayers = false },
            )
        }

        // 4. Tapped route point / radar cell
        selection?.let { sel ->
            RouteMapSelectionCard(
                selection = sel,
                metric = metric,
                altitudeFt = metricAltitude,
                onDismiss = { selection = null },
                onOpenSkewT = { distNm, icao ->
                    briefingViewModel.applyFocusIntent(
                        FocusIntent(tab = BriefingTab.CROSS_SECTION, distanceNm = distNm, waypointIcao = icao),
                    )
                },
            )
        }
    }
}

/** The LAYERS button's one-line summary ("Airports · Radar · Sat"), "None" when nothing is on. */
internal fun layersSummary(
    airports: Boolean,
    radar: Boolean,
    satellite: Boolean,
    lightning: Boolean,
    cells: Boolean,
    fronts: Boolean,
): String {
    val on = listOfNotNull(
        "Airports".takeIf { airports },
        "Radar".takeIf { radar },
        "Sat".takeIf { satellite },
        "Lightning".takeIf { lightning },
        "Cells".takeIf { cells },
        "Fronts".takeIf { fronts },
    )
    return if (on.isEmpty()) "None" else on.joinToString(" · ")
}

/** "Wed 12Z" for a forecast valid time; empty when unreadable. */
private fun validTimeShort(iso: String?): String {
    val ms = aero.flyfun.weather.model.IsoTime.parseEpochMs(iso) ?: return ""
    val t = java.time.Instant.ofEpochMilli(ms).atZone(java.time.ZoneOffset.UTC)
    val day = t.dayOfWeek.getDisplayName(java.time.format.TextStyle.SHORT, Locale.US)
    return String.format(Locale.US, "%s %02dZ", day, t.hour)
}

/** `◀ FL080 ▶`: the level reads in monospace; tapping it resets to cruise. */
@Composable
private fun AltitudeStepper(
    altitudeFt: Double,
    cruiseFt: Double,
    onStep: (Int) -> Unit,
    onReset: () -> Unit,
) {
    Surface(
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 2.dp,
        shadowElevation = 2.dp,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { onStep(-1) }, modifier = Modifier.size(36.dp).testTag("map.altitudeDown")) {
                Icon(Icons.Default.ChevronLeft, contentDescription = "Lower")
            }
            val atCruise = abs(altitudeFt - cruiseFt) < 1.0
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier
                    .clickable(onClick = onReset)
                    .testTag("map.altitudeReset")
                    .padding(horizontal = 4.dp),
            ) {
                Text(
                    RouteMapMetrics.formatFlightLevel(altitudeFt),
                    style = MaterialTheme.typography.labelLarge,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    if (atCruise) "cruise" else "tap: cruise",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = { onStep(1) }, modifier = Modifier.size(36.dp).testTag("map.altitudeUp")) {
                Icon(Icons.Default.ChevronRight, contentDescription = "Higher")
            }
        }
    }
}

/** A labelled floating capsule (Hide, Show, Forecast map, Fit route). */
@Composable
private fun MapPill(
    text: String,
    icon: @Composable () -> Unit,
    onClick: () -> Unit,
    testTag: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier
            .clickable(onClick = onClick)
            .testTag(testTag),
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f),
        tonalElevation = 2.dp,
        shadowElevation = 2.dp,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            icon()
            Text(text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
        }
    }
}

/** A bottom-panel pick that opens a sheet rather than a menu: the same look as [MenuButton]. */
@Composable
private fun CaptionButton(
    caption: String,
    text: String,
    onClick: () -> Unit,
    testTag: String,
    modifier: Modifier = Modifier,
) {
    OutlinedButton(
        onClick = onClick,
        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
        shape = RoundedCornerShape(8.dp),
        modifier = modifier.testTag(testTag),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                caption.uppercase(Locale.US),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
            )
            Text(
                text,
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Icon(Icons.Default.ArrowDropDown, contentDescription = null, modifier = Modifier.size(18.dp))
    }
}

/** LAYERS: Airports / Observed / Forecast, each only when the briefing has that source. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LayersDialog(
    mapCatalog: MapMetricsCatalog?,
    hasAirports: Boolean,
    airportModelSupported: Boolean,
    selectedModel: String,
    airportMetric: String,
    showsObserved: Boolean,
    radarOptions: List<String>,
    radarSource: String,
    lightningCollected: Boolean,
    hasFronts: Boolean,
    onDismiss: () -> Unit,
) {
    val session = RouteMapSession
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } },
        title = { Text("Layers") },
        modifier = Modifier.testTag("map.layersSheet"),
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (hasAirports) {
                    SectionHeader("Airports")
                    SwitchRow("Show airports", session.airportsVisible, "map.layer.airports") { session.airportsVisible = it }
                    if (!airportModelSupported) {
                        Text(
                            "No ${selectedModel.shortModelName()} airport data for this day",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else if (session.airportsVisible) {
                        MenuButton(
                            text = AirportMapCatalog.label(mapCatalog, airportMetric),
                            caption = "Airports show",
                            testTag = "map.airportMetricMenu",
                            modifier = Modifier.fillMaxWidth(),
                            sections = AirportMapCatalog.metricSections.mapNotNull { section ->
                                val items = section.items
                                    .filter { mapCatalog?.metrics?.containsKey(it.metric) == true }
                                    .map { item -> MenuChoice(item.label, item.metric == airportMetric) { session.airportMetric = item.metric } }
                                if (items.isEmpty()) null else MenuSection(section.title, items)
                            },
                        )
                    }
                }
                if (showsObserved) {
                    if (hasAirports) HorizontalDivider()
                    SectionHeader("Observed")
                    (radarOptions + "").forEach { source ->
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { session.radarPick = source }
                                .testTag("map.layer.radar.${source.ifEmpty { "none" }}"),
                        ) {
                            RadioButton(selected = radarSource == source, onClick = { session.radarPick = source })
                            Text(if (source.isEmpty()) "No radar" else ObservedImageryRules.label(source), style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                    if (radarSource.isNotEmpty()) {
                        Text("Radar opacity", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            ObservedImageryRules.OPACITY_STEPS.forEach { step ->
                                FilterChip(
                                    selected = abs(session.radarOpacity - step) < 0.01f,
                                    onClick = { session.radarOpacity = step },
                                    label = { Text("${(step * 100).roundToInt()}%", style = MaterialTheme.typography.labelSmall) },
                                )
                            }
                        }
                    }
                    SwitchRow(ObservedImageryRules.label(ObservedImageryRules.SATELLITE_SOURCE), session.satellite, "map.layer.satellite") {
                        session.satellite = it
                    }
                    if (lightningCollected) {
                        SwitchRow("Lightning", session.lightning, "map.layer.lightning") { session.lightning = it }
                    }
                    SwitchRow("Radar cells (experimental)", session.cells, "map.layer.cells") { session.cells = it }
                }
                if (hasFronts) {
                    if (hasAirports || showsObserved) HorizontalDivider()
                    SectionHeader("Forecast")
                    SwitchRow("Fronts (experimental)", session.fronts, "map.layer.fronts") { session.fronts = it }
                }
            }
        },
    )
}

@Composable
private fun SectionHeader(text: String) {
    Text(
        text.uppercase(Locale.US),
        style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, testTag: String, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .testTag(testTag),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
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
                    Text(text = swatch.label, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RouteMapSelectionCard(
    selection: RouteMapSelection,
    metric: RouteMapMetric?,
    altitudeFt: Double?,
    onDismiss: () -> Unit,
    onOpenSkewT: (Double, String?) -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
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
                    val value = metric?.value(pt, altitudeFt)
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
                                text = pt.waypointIcao?.takeIf { it.isNotBlank() } ?: "Route point ${selection.index + 1}",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                            )
                            Text(
                                text = "${pt.distanceNm.roundToInt()} NM",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            if (metric != null) {
                                val color = if (value != null) metric.color(value) else MaterialTheme.colorScheme.onSurfaceVariant
                                Surface(shape = RoundedCornerShape(50), color = color.copy(alpha = 0.18f)) {
                                    Text(
                                        text = "${metric.label}: ${value?.let(metric::format) ?: "no data"}",
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = FontWeight.Bold,
                                        color = color,
                                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                                    )
                                }
                            }
                        }
                        IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                            Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                        }
                    }

                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val hw = pt.headwindKt
                        val spd = pt.windSpeedKt
                        if (hw != null && spd != null) {
                            val hwLabel = if (hw >= 0.0) "+${hw.roundToInt()}kt HW" else "${abs(hw).roundToInt()}kt TW"
                            Text(
                                text = String.format(
                                    Locale.US,
                                    "Wind %03d°/%dkt (%s, %dkt XW)",
                                    (pt.windDirectionDeg ?: 0.0).roundToInt(),
                                    spd.roundToInt(),
                                    hwLabel,
                                    abs(pt.crosswindKt ?: 0.0).roundToInt(),
                                ),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        pt.altitudeLines.freezingLevelFt?.let { fzl ->
                            Text(text = "0°C: ${fzl.roundToInt()} ft", style = MaterialTheme.typography.bodySmall)
                        }
                        pt.cloudCoverTotalPct?.let { total ->
                            Text(
                                text = "Clouds: ${total.roundToInt()}%" + (pt.cloudCoverLowPct?.let { " (low ${it.roundToInt()}%)" } ?: ""),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }

                    selection.observation?.metarRaw?.takeIf { it.isNotBlank() }?.let { raw ->
                        Text(
                            text = raw,
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        OutlinedButton(
                            onClick = { onOpenSkewT(pt.distanceNm, pt.waypointIcao) },
                            contentPadding = ButtonDefaults.TextButtonContentPadding,
                        ) {
                            Icon(imageVector = Icons.Default.ShowChart, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(6.dp))
                            Text(
                                text = "Open Skew-T & Cross-Section here ›",
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
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
            }
        }
    }
}
