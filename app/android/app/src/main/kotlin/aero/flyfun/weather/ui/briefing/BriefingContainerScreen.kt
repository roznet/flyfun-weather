package aero.flyfun.weather.ui.briefing

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.Verified
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.PrimaryScrollableTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.AdvisoriesResponse
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.SnapshotResponse
import aero.flyfun.weather.model.WaypointAnalysis
import aero.flyfun.weather.viewmodel.BriefingViewModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Top-level tabbed briefing container screen (mirrors `BriefingContainerView.swift`):
 * - TopAppBar with route title + departure subtitle, [BriefingPackToolbar], and refresh button.
 * - SSE refresh progress banner and offline download banner.
 * - Adaptive 5-tab navigation (`BriefingTab.tabs(showsObserved)`).
 * - [BackHandler] that clears an active [FocusIntent] or switches back to [BriefingTab.ADVISORY]
 *   before popping the screen.
 * - Switches between [AdvisoryTabScreen], [DiscussionTabScreen], [ObservedTabPreviewScreen],
 *   and Phase 1 preview screens for Cross-Section and Map.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BriefingContainerScreen(
    viewModel: BriefingViewModel,
    onBack: () -> Unit,
    showBackButton: Boolean = true,
    modifier: Modifier = Modifier,
) {
    val flight by viewModel.flight.collectAsState()
    val selectedTab by viewModel.selectedTab.collectAsState()
    val availableTabsState by viewModel.availableTabs.collectAsState()
    val advisories by viewModel.advisories.collectAsState()
    val snapshot by viewModel.snapshot.collectAsState()
    val liveLayer by viewModel.liveLayer.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()
    val refreshProgressText by viewModel.refreshProgressText.collectAsState()
    val isDownloadingPack by viewModel.isDownloadingPack.collectAsState()
    val focusIntent by viewModel.focusIntent.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()

    val showsObserved = remember(availableTabsState, liveLayer, snapshot) {
        BriefingTab.OBSERVED in availableTabsState ||
            liveLayer != null ||
            snapshot?.routeObservations != null ||
            snapshot?.routeSigmets != null ||
            snapshot?.observedConditions != null
    }
    val tabs = remember(availableTabsState, showsObserved) {
        availableTabsState.ifEmpty { BriefingTab.tabs(showsObserved = showsObserved) }
    }

    // BackHandler: first clear FocusIntent, then return to ADVISORY tab before popping screen
    BackHandler(enabled = focusIntent != null || selectedTab != BriefingTab.ADVISORY) {
        if (focusIntent != null) {
            viewModel.clearFocusIntent()
        }
        if (selectedTab != BriefingTab.ADVISORY) {
            viewModel.selectTab(BriefingTab.ADVISORY)
        }
    }

    val configuration = LocalConfiguration.current
    val isWideScreen = configuration.screenWidthDp >= 600

    Scaffold(
        modifier = modifier.fillMaxSize(),
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
                            Text(
                                text = flight?.shortTitle?.ifBlank { "Briefing" } ?: "Briefing",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            val subtitle = remember(flight) { buildFlightSubtitle(flight) }
                            if (subtitle.isNotBlank()) {
                                Text(
                                    text = subtitle,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    },
                    navigationIcon = {
                        if (showBackButton) {
                            IconButton(
                                onClick = {
                                    when {
                                        focusIntent != null -> {
                                            viewModel.clearFocusIntent()
                                            if (selectedTab != BriefingTab.ADVISORY) {
                                                viewModel.selectTab(BriefingTab.ADVISORY)
                                            }
                                        }
                                        selectedTab != BriefingTab.ADVISORY -> {
                                            viewModel.selectTab(BriefingTab.ADVISORY)
                                        }
                                        else -> onBack()
                                    }
                                },
                            ) {
                                Icon(
                                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                                    contentDescription = "Back",
                                )
                            }
                        }
                    },
                    actions = {
                        BriefingPackToolbar(
                            viewModel = viewModel,
                            modifier = Modifier.padding(end = 4.dp),
                        )
                        IconButton(
                            onClick = { viewModel.refreshBriefing() },
                            enabled = !isRefreshing,
                        ) {
                            if (isRefreshing) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    strokeWidth = 2.dp,
                                )
                            } else {
                                Icon(
                                    imageVector = Icons.Default.Refresh,
                                    contentDescription = "Refresh briefing",
                                )
                            }
                        }
                    },
                )

                // SSE Refresh Progress Banner
                if (isRefreshing) {
                    Surface(
                        color = MaterialTheme.colorScheme.surfaceVariant,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 6.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(14.dp),
                                    strokeWidth = 2.dp,
                                )
                                Text(
                                    text = refreshProgressText ?: "Refreshing briefing…",
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            }
                            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        }
                    }
                }

                // Pack Download Banner
                if (isDownloadingPack) {
                    Surface(
                        color = MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.6f),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.CloudDownload,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                            )
                            Text(
                                text = "Saving pack for offline use…",
                                style = MaterialTheme.typography.labelMedium,
                                modifier = Modifier.weight(1f),
                            )
                            CircularProgressIndicator(
                                modifier = Modifier.size(14.dp),
                                strokeWidth = 2.dp,
                            )
                        }
                    }
                }

                // Error Banner
                if (!errorMessage.isNullOrBlank()) {
                    Surface(
                        color = BriefingPalette.Red.copy(alpha = 0.12f),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.Warning,
                                contentDescription = null,
                                tint = BriefingPalette.Red,
                                modifier = Modifier.size(18.dp),
                            )
                            Text(
                                text = errorMessage.orEmpty(),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    }
                }

                // Top Tab Row on tablet / wide screens (mirrors iPad top TabView)
                if (isWideScreen) {
                    val selectedIdx = tabs.indexOf(selectedTab).coerceAtLeast(0)
                    PrimaryScrollableTabRow(
                        selectedTabIndex = selectedIdx,
                        edgePadding = 16.dp,
                    ) {
                        tabs.forEach { tab ->
                            Tab(
                                selected = tab == selectedTab,
                                onClick = { viewModel.selectTab(tab) },
                                text = { Text(tab.title) },
                                icon = {
                                    Icon(
                                        imageVector = tab.icon(),
                                        contentDescription = null,
                                        modifier = Modifier.size(18.dp),
                                    )
                                },
                            )
                        }
                    }
                }
            }
        },
        bottomBar = {
            if (!isWideScreen) {
                NavigationBar {
                    tabs.forEach { tab ->
                        NavigationBarItem(
                            selected = tab == selectedTab,
                            onClick = { viewModel.selectTab(tab) },
                            icon = {
                                Icon(
                                    imageVector = tab.icon(),
                                    contentDescription = tab.title,
                                )
                            },
                            label = {
                                Text(
                                    text = tab.title,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            },
                        )
                    }
                }
            }
        },
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            when (selectedTab) {
                BriefingTab.ADVISORY -> AdvisoryTabScreen(viewModel = viewModel)
                BriefingTab.DISCUSSION -> DiscussionTabScreen(viewModel = viewModel)
                BriefingTab.OBSERVED -> ObservedTabPreviewScreen(viewModel = viewModel)
                BriefingTab.CROSS_SECTION -> CrossSectionPreviewScreen(
                    flight = flight,
                    snapshot = snapshot,
                    advisories = advisories,
                    focusIntent = focusIntent,
                    onClearFocusIntent = { viewModel.clearFocusIntent() },
                )
                BriefingTab.MAP -> RouteMapPreviewScreen(
                    flight = flight,
                    snapshot = snapshot,
                    advisories = advisories,
                    focusIntent = focusIntent,
                    onClearFocusIntent = { viewModel.clearFocusIntent() },
                )
            }
        }
    }
}

private fun BriefingTab.icon(): ImageVector = when (this) {
    BriefingTab.ADVISORY -> Icons.Default.Verified
    BriefingTab.OBSERVED -> Icons.Default.Sensors
    BriefingTab.DISCUSSION -> Icons.Default.Description
    BriefingTab.CROSS_SECTION -> Icons.Default.ShowChart
    BriefingTab.MAP -> Icons.Default.Map
}

private fun buildFlightSubtitle(flight: FlightResponse?): String {
    if (flight == null) return ""
    val parts = mutableListOf<String>()
    if (flight.departureTime.isNotBlank()) {
        parts.add(formatUtcTimestamp(flight.departureTime))
    }
    if (flight.cruiseAltitudeFt > 0) {
        parts.add(String.format(Locale.US, "FL%03d", flight.cruiseAltitudeFt / 100))
    }
    if (flight.flightDurationHours > 0) {
        parts.add(flight.durationLabel)
    }
    return parts.joinToString(" · ")
}

// ============================================================================
// Phase 1 Preview Cards for Cross-Section and Map
// ============================================================================

@Composable
internal fun FocusIntentBanner(
    focusIntent: FocusIntent,
    onClear: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.primaryContainer,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            val details = listOfNotNull(
                focusIntent.preset?.let { "Lens: $it" },
                focusIntent.waypointIcao?.let { "Waypoint: $it" },
                focusIntent.distanceNm?.let { "${it.roundToInt()} NM" },
            ).joinToString(" · ").ifBlank { "Focused view" }
            Text(
                text = details,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
                modifier = Modifier.weight(1f),
            )
            IconButton(
                onClick = onClear,
                modifier = Modifier.size(28.dp),
            ) {
                Icon(
                    imageVector = Icons.Default.Close,
                    contentDescription = "Clear focus lens",
                    tint = MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.size(16.dp),
                )
            }
        }
    }
}

@Composable
internal fun CrossSectionPreviewScreen(
    flight: FlightResponse?,
    snapshot: SnapshotResponse?,
    advisories: AdvisoriesResponse?,
    focusIntent: FocusIntent?,
    onClearFocusIntent: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val route = snapshot?.route
    val analyses = snapshot?.analyses.orEmpty()
    val cruiseFt = route?.cruiseAltitudeFt ?: advisories?.cruiseAltitudeFt ?: flight?.cruiseAltitudeFt ?: 0
    val ceilingFt = route?.flightCeilingFt ?: advisories?.flightCeilingFt ?: flight?.flightCeilingFt ?: 0
    val distanceNm = advisories?.totalDistanceNm ?: 0.0

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (focusIntent != null) {
            item(key = "focus_banner") {
                FocusIntentBanner(
                    focusIntent = focusIntent,
                    onClear = onClearFocusIntent,
                )
            }
        }

        item(key = "header") {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                ),
                border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        text = "Vertical Cross-Section & Sounding Summary",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = listOfNotNull(
                            route?.name?.takeIf { it.isNotBlank() } ?: flight?.shortTitle,
                            cruiseFt.takeIf { it > 0 }?.let { "Cruise ${it} ft" },
                            ceilingFt.takeIf { it > 0 }?.let { "Ceiling ${it} ft" },
                            distanceNm.takeIf { it > 0 }?.let { "${it.roundToInt()} NM" },
                        ).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        if (analyses.isNotEmpty()) {
            items(analyses, key = { "${it.waypoint.icao}_${it.targetTime}" }) { wpAnalysis ->
                WaypointSoundingCard(analysis = wpAnalysis)
            }
        } else {
            val waypoints = route?.waypoints.orEmpty()
            items(waypoints, key = { it.icao }) { wp ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = "${wp.icao} ${wp.name}".trim(),
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = String.format(Locale.US, "%.2f°, %.2f°", wp.lat, wp.lon),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun WaypointSoundingCard(
    analysis: WaypointAnalysis,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
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
                        text = analysis.waypoint.icao,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    if (analysis.waypoint.name.isNotBlank()) {
                        Text(
                            text = analysis.waypoint.name,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                if (analysis.targetTime.isNotBlank()) {
                    Text(
                        text = formatUtcTimestamp(analysis.targetTime),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Wind components per model
            if (analysis.windComponents.isNotEmpty()) {
                analysis.windComponents.entries.sortedBy { it.key }.forEach { (model, wind) ->
                    val hwLabel = if (wind.headwindKt >= 0) {
                        "${wind.headwindKt.roundToInt()}kt HW"
                    } else {
                        "${abs(wind.headwindKt).roundToInt()}kt TW"
                    }
                    Text(
                        text = "${model.shortModelName()}: ${wind.windDirectionDeg.roundToInt()}°/${wind.windSpeedKt.roundToInt()}kt ($hwLabel, ${abs(wind.crosswindKt).roundToInt()}kt XW)",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            // Sounding indices per model
            val soundings = analysis.sounding.orEmpty()
            if (soundings.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                soundings.entries.sortedBy { it.key }.forEach { (model, s) ->
                    val metrics = listOfNotNull(
                        s.indices?.freezingLevelFt?.let { "FZLVL ${it.roundToInt()} ft" },
                        s.indices?.capeSurfaceJkg?.takeIf { it > 0 }?.let { "CAPE ${it.roundToInt()} J/kg" },
                        (s.indices?.nwpCeilingFt ?: s.indices?.soundingCeilingFt)?.let { "Cig ${it.roundToInt()} ft" },
                        s.cloudCoverLowPct?.let { "Clouds L/M/H ${it.roundToInt()}/${(s.cloudCoverMidPct ?: 0.0).roundToInt()}/${(s.cloudCoverHighPct ?: 0.0).roundToInt()}%" },
                    ).joinToString(" · ")
                    if (metrics.isNotBlank()) {
                        Text(
                            text = "${model.shortModelName()} Sounding: $metrics",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

@Composable
internal fun RouteMapPreviewScreen(
    flight: FlightResponse?,
    snapshot: SnapshotResponse?,
    advisories: AdvisoriesResponse?,
    focusIntent: FocusIntent?,
    onClearFocusIntent: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val route = snapshot?.route
    val waypoints = route?.waypoints.orEmpty()
    val corridorNm = snapshot?.routeObservations?.corridorNm ?: snapshot?.observedConditions?.corridorNm ?: 15.0
    val distanceNm = advisories?.totalDistanceNm ?: 0.0

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (focusIntent != null) {
            item(key = "focus_banner") {
                FocusIntentBanner(
                    focusIntent = focusIntent,
                    onClear = onClearFocusIntent,
                )
            }
        }

        item(key = "corridor_card") {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                ),
                border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        text = "Route Corridor & Waypoints",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = listOfNotNull(
                            route?.name?.takeIf { it.isNotBlank() } ?: flight?.shortTitle,
                            distanceNm.takeIf { it > 0 }?.let { "${it.roundToInt()} NM total" },
                            "Corridor ±${corridorNm.roundToInt()} NM",
                            flight?.waypoints?.takeIf { it.isNotEmpty() }?.joinToString(" → "),
                        ).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        if (waypoints.isNotEmpty()) {
            items(waypoints, key = { "${it.icao}_${it.lat}_${it.lon}" }) { wp ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(
                                text = wp.icao,
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold,
                            )
                            if (wp.name.isNotBlank()) {
                                Text(
                                    text = wp.name,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        Text(
                            text = String.format(Locale.US, "%.3f°N, %.3f°E", wp.lat, wp.lon),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}
