package aero.flyfun.weather.ui.observed

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.ui.platform.LocalContext
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.service.FlightTrackingService
import aero.flyfun.weather.ui.briefing.SectionSpyBar
import aero.flyfun.weather.ui.briefing.SpySection
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.viewmodel.BriefingViewModel
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.FlightTakeoff
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

private val TrackingActiveCyan = Color(0xFF0284C7)

private val ObservedSpySections = listOf(
    SpySection(id = "summary", title = "Summary"),
    SpySection(id = "changes", title = "Changes"),
    SpySection(id = "storms", title = "Storms"),
    SpySection(id = "airports", title = "Airports"),
    SpySection(id = "sigmets", title = "SIGMETs"),
)

/**
 * Flight-Day `Observed` Tab Screen combining:
 * - Top live status bar (`Live updated HH:MMZ · Refresh ↻`) + In-Flight GPS Tracking Bar
 *   (`"Start Flight Tracking"` / `"Tracking Active · Stop"` toggle showing live distance along route & groundspeed/altitude).
 * - Sticky [SectionSpyBar] (`Summary`, `Changes`, `Storms`, `Airports`, `SIGMETs`).
 * - [ObservedHighlightCard] → [ObservedNutshellCard] ([RouteRibbonCanvas]) → [LiveChangesSection] →
 *   [LiveStormsSection] → [RouteObservationsSection] (with [RunwayWindDialView]) → [RouteSigmetsSection].
 */
@Composable
fun ObservedTabScreen(
    viewModel: BriefingViewModel,
    onOpenMapTab: () -> Unit = { viewModel.selectTab(BriefingTab.MAP) },
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val flight by viewModel.flight.collectAsState()
    val selectedPack by viewModel.selectedPack.collectAsState()
    val advisories by viewModel.advisories.collectAsState()
    val snapshot by viewModel.snapshot.collectAsState()
    val elevation by viewModel.elevation.collectAsState()
    val routeAnalyses by viewModel.routeAnalyses.collectAsState()
    val liveLayer by viewModel.liveLayer.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()

    val serviceTracking by FlightTrackingService.isTracking.collectAsState()
    val serviceFlightId by FlightTrackingService.activeFlightId.collectAsState()
    val serviceProjectedPos by FlightTrackingService.currentPosition.collectAsState()

    val isDemoMode = viewModel.repository is FixtureBriefingRepository
    val currentFlightId = flight?.id ?: viewModel.flightId.orEmpty()

    val listState = rememberLazyListState()

    val totalDistanceNm = remember(liveLayer, elevation, routeAnalyses) {
        liveLayer?.ribbon?.totalDistanceNm
            ?: elevation?.totalDistanceNm?.takeIf { it > 0.0 }
            ?: routeAnalyses?.totalDistanceNm?.takeIf { it > 0.0 }
            ?: 180.0
    }

    val waypoints = remember(flight, snapshot, liveLayer) {
        val fromFlight = flight?.waypoints.orEmpty()
        val fromSnapshot = snapshot?.route?.waypoints?.map { it.icao }.orEmpty()
        when {
            fromFlight.isNotEmpty() -> fromFlight
            fromSnapshot.isNotEmpty() -> fromSnapshot
            else -> liveLayer?.ribbon?.waypoints?.map { it.icao }.orEmpty()
        }
    }

    val routeCoordinates = remember(snapshot, elevation) {
        val wpCoords = snapshot?.route?.waypoints
            ?.filter { it.lat != 0.0 || it.lon != 0.0 }
            ?.map { it.lat to it.lon }
            .orEmpty()
        if (wpCoords.size >= 2) {
            wpCoords
        } else {
            elevation?.points
                ?.filter { it.lat != 0.0 || it.lon != 0.0 }
                ?.map { it.lat to it.lon }
                .orEmpty()
        }
    }

    // Demo-mode simulation state (used only when repository is FixtureBriefingRepository)
    var demoTrackingActive by rememberSaveable { mutableStateOf(false) }
    var demoTrackedFraction by rememberSaveable { mutableFloatStateOf(0.28f) }

    val isTrackingThisFlight = if (isDemoMode) {
        demoTrackingActive
    } else {
        serviceTracking && serviceFlightId == currentFlightId
    }

    val locationPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val granted = grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true ||
            FlightTrackingService.hasLocationPermission(context)
        if (granted && currentFlightId.isNotBlank()) {
            FlightTrackingService.startTracking(
                context = context,
                flightId = currentFlightId,
                waypoints = routeCoordinates,
                apiClient = viewModel.apiClient,
                endEpochMs = flight?.trackingWindowEndEpochMs(started = true),
            )
        }
    }

    val toggleTracking: () -> Unit = {
        if (isDemoMode) {
            demoTrackingActive = !demoTrackingActive
        } else if (isTrackingThisFlight) {
            FlightTrackingService.stopTracking(context)
        } else if (currentFlightId.isNotBlank()) {
            if (FlightTrackingService.hasLocationPermission(context)) {
                FlightTrackingService.startTracking(
                    context = context,
                    flightId = currentFlightId,
                    waypoints = routeCoordinates,
                    apiClient = viewModel.apiClient,
                    // Until the landing, or the latest a late take-off could still be flying (#874).
                    endEpochMs = flight?.trackingWindowEndEpochMs(started = true),
                )
            } else {
                val permissions = buildList {
                    add(Manifest.permission.ACCESS_FINE_LOCATION)
                    add(Manifest.permission.ACCESS_COARSE_LOCATION)
                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                        add(Manifest.permission.POST_NOTIFICATIONS)
                    }
                }.toTypedArray()
                locationPermissionLauncher.launch(permissions)
            }
        }
    }

    val flightProgress = remember(
        isDemoMode,
        isTrackingThisFlight,
        demoTrackedFraction,
        serviceProjectedPos,
        totalDistanceNm,
        flight,
    ) {
        if (!isTrackingThisFlight) {
            null
        } else if (isDemoMode) {
            val flownNm = totalDistanceNm * demoTrackedFraction.toDouble()
            val cruiseFt = (flight?.cruiseAltitudeFt ?: 6500).toDouble()
            FlightProgress(
                flownFraction = demoTrackedFraction.toDouble(),
                flownNm = flownNm,
                totalNm = totalDistanceNm,
                groundSpeedKt = 122.0,
                altitudeFt = cruiseFt,
                phase = when {
                    demoTrackedFraction < 0.12f -> "Climb"
                    demoTrackedFraction > 0.88f -> "Descent"
                    else -> "En route"
                },
                isTrackingActive = true,
            )
        } else {
            val pos = serviceProjectedPos
            if (pos != null) {
                FlightProgress(
                    flownFraction = pos.routeFraction.coerceIn(0.0, 1.0),
                    flownNm = pos.distanceAlongRouteNm,
                    totalNm = totalDistanceNm,
                    groundSpeedKt = pos.groundSpeedKt,
                    altitudeFt = pos.altitudeFt,
                    phase = when {
                        pos.routeFraction < 0.12 -> "Climb"
                        pos.routeFraction > 0.88 -> "Descent"
                        else -> "En route"
                    },
                    isTrackingActive = true,
                )
            } else {
                FlightProgress(
                    flownFraction = 0.0,
                    flownNm = 0.0,
                    totalNm = totalDistanceNm,
                    groundSpeedKt = null,
                    altitudeFt = null,
                    phase = "Acquiring GPS…",
                    isTrackingActive = true,
                )
            }
        }
    }

    val handleFocus: (LiveFocus) -> Unit = { focus ->
        viewModel.applyFocusIntent(
            FocusIntent(
                tab = BriefingTab.MAP,
                preset = focus.layer,
                distanceNm = focus.distanceNm,
                waypointIcao = focus.stationIcao,
            ),
        )
        onOpenMapTab()
    }

    // LazyColumn item indices for SectionSpyBar
    // 0 = Highlight + Nutshell (Summary)
    // 1 = LiveChangesSection (Changes)
    // 2 = LiveStormsSection (Storms)
    // 3 = RouteObservationsSection (Airports)
    // 4 = RouteSigmetsSection (SIGMETs)
    val sectionIndices = remember {
        mapOf(
            "summary" to 0,
            "changes" to 1,
            "storms" to 2,
            "airports" to 3,
            "sigmets" to 4,
        )
    }

    Column(modifier = modifier.fillMaxSize()) {
        // 1. Top Live Status Bar + In-Flight GPS Tracking Bar
        ObservedTopStatusAndTrackingBar(
            liveUpdatedAt = liveLayer?.liveUpdatedAt,
            packTimestamp = liveLayer?.packTimestamp ?: selectedPack?.fetchTimestamp,
            isLoading = isLoading || isRefreshing,
            isTrackingActive = isTrackingThisFlight,
            progress = flightProgress,
            totalDistanceNm = totalDistanceNm,
            showScrubSlider = isDemoMode,
            onRefreshLive = { viewModel.loadInitialBriefing(selectedPack?.fetchTimestamp) },
            onToggleTracking = toggleTracking,
            onScrubFraction = { demoTrackedFraction = it },
        )

        // 2. Sticky SectionSpyBar
        SectionSpyBar(
            sections = ObservedSpySections,
            listState = listState,
            sectionIndices = sectionIndices,
        )

        // 3. Scrollable sections
        if (liveLayer == null && isLoading) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(32.dp),
                contentAlignment = Alignment.Center,
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    CircularProgressIndicator()
                    Text(
                        text = "Loading live METAR, TAF, radar, lightning & SIGMET layer…",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                // Item 0: Summary (Highlight Card + Nutshell Card)
                item(key = "summary") {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        val glance = liveLayer?.glance
                        if (glance != null) {
                            ObservedHighlightCard(
                                glance = glance,
                                updatedAt = liveLayer?.liveUpdatedAt,
                                onRateHighlight = { thumbsUp ->
                                    viewModel.submitDigestFeedback(thumbsUp = thumbsUp)
                                },
                                onSelectFocus = handleFocus,
                            )
                        }
                        ObservedNutshellCard(
                            glance = glance,
                            ribbon = liveLayer?.ribbon,
                            progress = flightProgress,
                            waypoints = waypoints,
                            onFocusMap = onOpenMapTab,
                            onSelectFocus = handleFocus,
                        )
                    }
                }

                // Item 1: Since this briefing (LiveChangesSection)
                item(key = "changes") {
                    LiveChangesSection(
                        liveChanges = liveLayer?.changes,
                        onSelectFocus = handleFocus,
                    )
                }

                // Item 2: Radar Storm Lineage (LiveStormsSection)
                item(key = "storms") {
                    LiveStormsSection(
                        storms = liveLayer?.storms,
                        onSelectFocus = handleFocus,
                    )
                }

                // Item 3: Airport METAR/TAF + Runway Wind Dials (RouteObservationsSection)
                item(key = "airports") {
                    RouteObservationsSection(
                        observations = liveLayer?.routeObservations ?: snapshot?.routeObservations,
                        waypoints = waypoints,
                        airportConditions = advisories?.airportConditions,
                        onSelectFocus = handleFocus,
                    )
                }

                // Item 4: Route SIGMETs (RouteSigmetsSection)
                item(key = "sigmets") {
                    RouteSigmetsSection(
                        routeSigmets = liveLayer?.routeSigmets ?: snapshot?.routeSigmets,
                        onSelectFocus = handleFocus,
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ObservedTopStatusAndTrackingBar(
    liveUpdatedAt: String?,
    packTimestamp: String?,
    isLoading: Boolean,
    isTrackingActive: Boolean,
    progress: FlightProgress?,
    totalDistanceNm: Double,
    showScrubSlider: Boolean = false,
    onRefreshLive: () -> Unit,
    onToggleTracking: () -> Unit,
    onScrubFraction: (Float) -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 1.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // Top bar: Live updated timestamp + Refresh button + Flight Tracking toggle
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .background(
                                if (liveUpdatedAt != null) AssessmentGreen else AssessmentAmber,
                                CircleShape,
                            ),
                    )
                    Column {
                        val updatedLabel = liveUpdatedAt?.let {
                            "Live updated ${formatUtcTimestampShort(it)}"
                        } ?: "Live layer ready to refresh"
                        Text(
                            text = updatedLabel,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                            fontFamily = FontFamily.Monospace,
                        )
                        if (!packTimestamp.isNullOrBlank()) {
                            Text(
                                text = "Briefing pack ${formatUtcTimestampShort(packTimestamp)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TextButton(
                        onClick = onRefreshLive,
                        enabled = !isLoading,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "Refresh live observations",
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = "Refresh ↻",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                        )
                    }

                    if (isTrackingActive) {
                        FilledTonalButton(
                            onClick = onToggleTracking,
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                            colors = ButtonDefaults.filledTonalButtonColors(
                                containerColor = TrackingActiveCyan.copy(alpha = 0.16f),
                                contentColor = TrackingActiveCyan,
                            ),
                        ) {
                            Icon(
                                imageVector = Icons.Default.GpsFixed,
                                contentDescription = null,
                                modifier = Modifier.size(15.dp),
                            )
                            Spacer(modifier = Modifier.width(5.dp))
                            Text(
                                text = "Tracking Active · Stop",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    } else {
                        OutlinedButton(
                            onClick = onToggleTracking,
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.FlightTakeoff,
                                contentDescription = null,
                                modifier = Modifier.size(15.dp),
                            )
                            Spacer(modifier = Modifier.width(5.dp))
                            Text(
                                text = "Start Flight Tracking",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
            }

            // Expanded In-Flight GPS Telemetry Bar when tracking is active
            AnimatedVisibility(visible = isTrackingActive && progress != null) {
                if (progress != null) {
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = TrackingActiveCyan.copy(alpha = 0.08f),
                        border = BorderStroke(1.dp, TrackingActiveCyan.copy(alpha = 0.35f)),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            FlowRow(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalArrangement = Arrangement.spacedBy(4.dp),
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.GpsFixed,
                                        contentDescription = null,
                                        tint = TrackingActiveCyan,
                                        modifier = Modifier.size(14.dp),
                                    )
                                    Text(
                                        text = "${progress.flownNm.roundToInt()} / ${progress.totalNm.roundToInt()} NM (${(progress.flownFraction * 100).roundToInt()}%)",
                                        style = MaterialTheme.typography.labelMedium,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace,
                                        color = TrackingActiveCyan,
                                    )
                                }

                                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    progress.groundSpeedKt?.let { gs ->
                                        Text(
                                            text = "GS ${gs.roundToInt()} kt",
                                            style = MaterialTheme.typography.labelSmall,
                                            fontFamily = FontFamily.Monospace,
                                        )
                                    }
                                    progress.altitudeFt?.let { alt ->
                                        Text(
                                            text = "ALT ${alt.roundToInt()} ft",
                                            style = MaterialTheme.typography.labelSmall,
                                            fontFamily = FontFamily.Monospace,
                                        )
                                    }
                                    progress.phase?.let { ph ->
                                        Text(
                                            text = ph,
                                            style = MaterialTheme.typography.labelSmall,
                                            fontWeight = FontWeight.SemiBold,
                                            color = TrackingActiveCyan,
                                        )
                                    }
                                }
                            }

                            if (showScrubSlider) {
                                Slider(
                                    value = progress.flownFraction.toFloat(),
                                    onValueChange = onScrubFraction,
                                    valueRange = 0f..1f,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(24.dp),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
