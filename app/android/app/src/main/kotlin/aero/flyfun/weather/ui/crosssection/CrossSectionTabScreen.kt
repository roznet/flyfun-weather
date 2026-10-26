package aero.flyfun.weather.ui.crosssection

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import aero.flyfun.weather.model.SoundingProfileResponse
import aero.flyfun.weather.model.viz.CrossSectionPresets
import aero.flyfun.weather.ui.briefing.shortModelName
import aero.flyfun.weather.ui.skewt.SkewTDetailSection
import aero.flyfun.weather.viewmodel.BriefingViewModel
import aero.flyfun.weather.viewmodel.CrossSectionViewModel
import kotlinx.coroutines.launch

/**
 * Full Cross-Section & Skew-T tab screen inside the 5-tab Briefing container:
 * - Syncs [BriefingViewModel] state (`routeAnalyses`, `elevation`, `snapshot`, `advisories`, `flight`,
 *   `selectedModel`, `focusIntent`) into [CrossSectionViewModel].
 * - Loads [SoundingProfileResponse] via [BriefingViewModel.loadSoundingProfile] when `selectedPointIndex`
 *   or `selectedModel` changes.
 * - Renders Model selector chips (`ECMWF`, `GFS`, `ICON-EU`, etc.), adaptive [LayerBarView],
 *   [CrossSectionReadoutBar], [CrossSectionCanvas], [RouteGraphSection], and [SkewTDetailSection]
 *   with bidirectional hover altitude sync between Cross-Section and Skew-T.
 */
@Composable
fun CrossSectionTabScreen(
    briefingViewModel: BriefingViewModel,
    crossSectionViewModel: CrossSectionViewModel = viewModel(),
    modifier: Modifier = Modifier,
) {
    val flight by briefingViewModel.flight.collectAsState()
    val selectedPack by briefingViewModel.selectedPack.collectAsState()
    val routeAnalyses by briefingViewModel.routeAnalyses.collectAsState()
    val elevation by briefingViewModel.elevation.collectAsState()
    val routeFronts by briefingViewModel.routeFronts.collectAsState()
    val snapshot by briefingViewModel.snapshot.collectAsState()
    val advisories by briefingViewModel.advisories.collectAsState()
    val briefingModel by briefingViewModel.selectedModel.collectAsState()
    val briefingModels by briefingViewModel.availableModels.collectAsState()
    val focusIntent by briefingViewModel.focusIntent.collectAsState()
    val isLoading by briefingViewModel.isLoading.collectAsState()

    val vizData by crossSectionViewModel.vizRouteData.collectAsState()
    val selectedModel by crossSectionViewModel.selectedModel.collectAsState()
    val csModels by crossSectionViewModel.availableModels.collectAsState()
    val enabledLayers by crossSectionViewModel.enabledLayers.collectAsState()
    val effectiveLayers by crossSectionViewModel.effectiveLayers.collectAsState()
    val unavailableLayers by crossSectionViewModel.unavailableLayers.collectAsState()
    val substitutedLayers by crossSectionViewModel.substitutedLayers.collectAsState()
    val cloudStyle by crossSectionViewModel.cloudStyle.collectAsState()
    val advisoryHighlightVisible by crossSectionViewModel.advisoryHighlightVisible.collectAsState()
    val effectiveTheme by crossSectionViewModel.selectedTheme.collectAsState()
    val activePresetId by crossSectionViewModel.activePresetId.collectAsState()
    val activeEmulationId by crossSectionViewModel.activeEmulationId.collectAsState()
    val expandedFamily by crossSectionViewModel.expandedFamily.collectAsState()
    val selectedPointIndex by crossSectionViewModel.selectedPointIndex.collectAsState()
    val routeGraphVisible by crossSectionViewModel.isRouteGraphVisible.collectAsState()
    val leftMetricId by crossSectionViewModel.leftMetricId.collectAsState()
    val rightMetricId by crossSectionViewModel.rightMetricId.collectAsState()

    var scrubDistanceNm by remember { mutableStateOf<Double?>(null) }
    var hoverAltitudeFt by remember { mutableStateOf<Double?>(null) }
    var soundingProfile by remember { mutableStateOf<SoundingProfileResponse?>(null) }
    var compareProfiles by remember { mutableStateOf<Map<String, SoundingProfileResponse>>(emptyMap()) }

    // 1. Sync briefing data into CrossSectionViewModel
    LaunchedEffect(routeAnalyses, elevation, selectedPack, flight, snapshot, advisories, routeFronts) {
        crossSectionViewModel.updateBriefingData(
            analyses = routeAnalyses,
            elevationProfile = elevation,
            briefingPack = selectedPack,
            flight = flight,
            snapshot = snapshot,
            advisories = advisories,
            routeFronts = routeFronts,
        )
    }

    // 2. Sync model selection from BriefingViewModel -> CrossSectionViewModel
    LaunchedEffect(briefingModel) {
        if (briefingModel.isNotBlank() && briefingModel != selectedModel) {
            crossSectionViewModel.selectModel(briefingModel)
        }
    }

    // 3. Apply any pending FocusIntent from Advisory / Observed / Digest navigation
    LaunchedEffect(focusIntent, vizData) {
        val intent = focusIntent
        if (intent != null && vizData != null) {
            crossSectionViewModel.applyFocusIntent(intent)
            briefingViewModel.clearFocusIntent()
        }
    }

    val currentVizData = vizData
    val availableModels = csModels.ifEmpty { briefingModels }
    val activeModel = selectedModel.ifBlank { briefingModel.ifBlank { availableModels.firstOrNull().orEmpty() } }
    val pointIndex = (selectedPointIndex ?: 0).coerceIn(
        0,
        ((currentVizData?.points?.size ?: 1) - 1).coerceAtLeast(0),
    )

    // 4. Load SoundingProfileResponse for the selected route point and model(s)
    LaunchedEffect(selectedPack, pointIndex, activeModel, availableModels) {
        if (activeModel.isBlank() || currentVizData == null || currentVizData.points.isEmpty()) {
            soundingProfile = null
            compareProfiles = emptyMap()
            return@LaunchedEffect
        }
        val rawPointIdx = currentVizData.points.getOrNull(pointIndex)?.pointIndex ?: pointIndex
        soundingProfile = briefingViewModel.loadSoundingProfile(rawPointIdx, activeModel)

        val compareMap = mutableMapOf<String, SoundingProfileResponse>()
        soundingProfile?.let { compareMap[activeModel] = it }
        for (m in availableModels) {
            if (m != activeModel) {
                briefingViewModel.loadSoundingProfile(rawPointIdx, m)?.let { prof ->
                    compareMap[m] = prof
                }
            }
        }
        compareProfiles = compareMap
    }

    if (currentVizData == null || currentVizData.points.isEmpty()) {
        Box(
            modifier = modifier
                .fillMaxSize()
                .padding(24.dp),
            contentAlignment = Alignment.Center,
        ) {
            if (isLoading) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    CircularProgressIndicator()
                    Text(
                        text = "Loading route cross-section…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Column(
                        modifier = Modifier.padding(20.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.ShowChart,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(32.dp),
                        )
                        Text(
                            text = "No Cross-Section Data Available",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            text = "Refresh the briefing pack to generate vertical atmospheric cross-sections and Skew-T soundings.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
        return
    }

    val activePresetInterpretation = CrossSectionPresets.ADVISORY_PRESETS
        .firstOrNull { it.id == activePresetId }
        ?.interpretation

    val waypointLabels = remember(currentVizData.points, currentVizData.waypoints) {
        currentVizData.points.map { pt ->
            pt.waypointIcao?.takeIf { it.isNotBlank() }
                ?: currentVizData.waypoints
                    .firstOrNull { kotlin.math.abs(it.distanceNm - pt.distanceNm) <= 1.5 }
                    ?.icao
                ?: ""
        }
    }

    val scrollState = rememberScrollState()
    val coroutineScope = androidx.compose.runtime.rememberCoroutineScope()

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        // 1. Unified Layer Bar (Model pills + Focus/Emulate/Theme + Pinned Options button + Wrapping 7-Family Grid)
        LayerBarView(
            enabledLayers = enabledLayers,
            effectiveLayers = effectiveLayers,
            unavailableLayers = unavailableLayers,
            substitutedLayers = substitutedLayers,
            cloudStyle = cloudStyle,
            userThemeId = effectiveTheme.id,
            activeEmulationId = activeEmulationId,
            activePresetId = activePresetId,
            expandedFamily = expandedFamily,
            availableModels = availableModels,
            selectedModel = activeModel,
            onSelectModel = { model ->
                crossSectionViewModel.selectModel(model)
                briefingViewModel.selectModel(model)
            },
            onApplyAdvisoryPreset = { presetId ->
                val preset = CrossSectionPresets.ADVISORY_PRESETS.firstOrNull { it.id == presetId }
                if (preset != null) {
                    crossSectionViewModel.applyAdvisoryPreset(preset)
                }
            },
            onApplyEmulation = { emulationId ->
                crossSectionViewModel.applyEmulationById(emulationId)
            },
            onSetUserTheme = { themeId ->
                crossSectionViewModel.setThemeById(themeId)
            },
            onToggleExpandedFamily = { family ->
                crossSectionViewModel.toggleExpandedFamily(family)
            },
            onSelectFamilyMethod = { family, methodOrLayer ->
                crossSectionViewModel.selectFamilyMethod(family, methodOrLayer)
            },
            onSetCloudStyle = { style ->
                crossSectionViewModel.setCloudStyle(style)
            },
            onToggleLayer = { layerId ->
                crossSectionViewModel.toggleLayer(layerId)
            },
            hasAdvisoryHighlight = currentVizData.advisoryHighlights != null,
            advisoryHighlightOn = advisoryHighlightVisible,
            onToggleAdvisoryHighlight = { crossSectionViewModel.toggleAdvisoryHighlightVisible() },
        )

        // 2. Cockpit Readout Strip (with Sounding › quick-jump pill)
        CrossSectionReadoutBar(
            vizData = currentVizData,
            selectedPointIndex = pointIndex,
            scrubDistanceNm = scrubDistanceNm,
            hoverAltitudeFt = hoverAltitudeFt,
            effectiveLayers = effectiveLayers,
            onOpenSounding = {
                coroutineScope.launch {
                    scrollState.animateScrollTo(scrollState.maxValue)
                }
            },
        )

        // 4. Interactive Cross-Section Canvas
        CrossSectionCanvas(
            vizData = currentVizData,
            effectiveLayers = effectiveLayers,
            cloudStyle = cloudStyle,
            theme = effectiveTheme,
            selectedModel = activeModel,
            scrubDistanceNm = scrubDistanceNm,
            selectedPointIndex = pointIndex,
            hoverAltitudeFt = hoverAltitudeFt,
            onScrubDistanceChanged = { distNm ->
                scrubDistanceNm = distNm
            },
            onSelectPointIndex = { idx ->
                crossSectionViewModel.selectPoint(idx)
                briefingViewModel.setActivePointIndex(idx)
            },
            onHoverAltitudeChanged = { altFt ->
                hoverAltitudeFt = altFt
            },
            showAdvisoryHighlight = advisoryHighlightVisible,
        )

        // 5. Pixel-Aligned Route Graph Canvas + Controls
        RouteGraphSection(
            vizData = currentVizData,
            visible = routeGraphVisible,
            leftMetricId = leftMetricId,
            rightMetricId = rightMetricId,
            scrubDistanceNm = scrubDistanceNm,
            selectedPointIndex = pointIndex,
            theme = effectiveTheme,
            onToggleVisible = { visible ->
                crossSectionViewModel.setRouteGraphVisible(visible)
            },
            onSelectMetrics = { leftId, rightId ->
                if (leftId != null) crossSectionViewModel.setLeftMetric(leftId)
                if (rightId != null) crossSectionViewModel.setRightMetric(rightId)
            },
            onScrubDistanceChanged = { distNm ->
                scrubDistanceNm = distNm
            },
            onSelectPointIndex = { idx ->
                crossSectionViewModel.selectPoint(idx)
                briefingViewModel.setActivePointIndex(idx)
            },
        )

        // 6. Interactive Skew-T Log-P Detail Section (with bidirectional hover altitude sync)
        SkewTDetailSection(
            profile = soundingProfile,
            compareProfiles = compareProfiles,
            waypoints = waypointLabels,
            selectedPointIndex = pointIndex,
            totalPoints = currentVizData.points.size,
            selectedModel = activeModel,
            availableModels = availableModels,
            cruiseAltitudeFt = currentVizData.cruiseAltitudeFt.toInt(),
            externalHoverAltitudeFt = hoverAltitudeFt,
            activePresetInterpretation = activePresetInterpretation,
            onSelectPointIndex = { idx ->
                crossSectionViewModel.selectPoint(idx)
                briefingViewModel.setActivePointIndex(idx)
            },
            onSelectModel = { model ->
                crossSectionViewModel.selectModel(model)
                briefingViewModel.selectModel(model)
            },
            onHoverAltitudeChanged = { altFt ->
                hoverAltitudeFt = altFt
            },
        )

        Spacer(modifier = Modifier.height(16.dp))
    }
}
