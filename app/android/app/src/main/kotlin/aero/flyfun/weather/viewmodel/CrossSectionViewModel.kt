package aero.flyfun.weather.viewmodel

import aero.flyfun.weather.model.AdvisoriesResponse
import aero.flyfun.weather.model.ElevationResponse
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.model.RouteAnalysesResponse
import aero.flyfun.weather.model.RoutePointAnalysis
import aero.flyfun.weather.model.SnapshotResponse
import aero.flyfun.weather.model.viz.AdvisoryPreset
import aero.flyfun.weather.model.viz.CloudStyle
import aero.flyfun.weather.model.viz.CrossSectionPresets
import aero.flyfun.weather.model.viz.CrossSectionTheme
import aero.flyfun.weather.model.viz.CrossSectionThemes
import aero.flyfun.weather.model.viz.EmulationPreset
import aero.flyfun.weather.model.viz.LayerFamily
import aero.flyfun.weather.model.viz.NwpFallback
import aero.flyfun.weather.model.viz.VizDataExtractor
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import androidx.lifecycle.ViewModel
import kotlin.math.abs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class CrossSectionViewModel : ViewModel() {

    private var cachedAnalyses: RouteAnalysesResponse? = null
    private var cachedElevation: ElevationResponse? = null
    private var cachedSnapshot: SnapshotResponse? = null
    private var cachedAdvisories: AdvisoriesResponse? = null
    private var cachedPack: PackMetaResponse? = null
    private var cachedFlight: FlightResponse? = null
    private var activeAdvisoryHighlightId: String? = null

    private val _selectedModel = MutableStateFlow("")
    val selectedModel: StateFlow<String> = _selectedModel.asStateFlow()

    private val _availableModels = MutableStateFlow<List<String>>(emptyList())
    val availableModels: StateFlow<List<String>> = _availableModels.asStateFlow()

    private val _enabledLayers = MutableStateFlow(CrossSectionPresets.defaultEnabledLayers())
    val enabledLayers: StateFlow<Map<String, Boolean>> = _enabledLayers.asStateFlow()

    private val _effectiveLayers = MutableStateFlow(CrossSectionPresets.defaultEnabledLayers())
    val effectiveLayers: StateFlow<Map<String, Boolean>> = _effectiveLayers.asStateFlow()

    private val _unavailableLayers = MutableStateFlow<Set<String>>(emptySet())
    val unavailableLayers: StateFlow<Set<String>> = _unavailableLayers.asStateFlow()

    private val _substitutedLayers = MutableStateFlow<Set<String>>(emptySet())
    val substitutedLayers: StateFlow<Set<String>> = _substitutedLayers.asStateFlow()

    private val _selectedPointIndex = MutableStateFlow<Int?>(null)
    val selectedPointIndex: StateFlow<Int?> = _selectedPointIndex.asStateFlow()

    private val _selectedPoint = MutableStateFlow<VizPoint?>(null)
    val selectedPoint: StateFlow<VizPoint?> = _selectedPoint.asStateFlow()

    private val _selectedRoutePointAnalysis = MutableStateFlow<RoutePointAnalysis?>(null)
    val selectedRoutePointAnalysis: StateFlow<RoutePointAnalysis?> = _selectedRoutePointAnalysis.asStateFlow()

    private val _vizRouteData = MutableStateFlow<VizRouteData?>(null)
    val vizRouteData: StateFlow<VizRouteData?> = _vizRouteData.asStateFlow()

    private val _selectedTheme = MutableStateFlow(CrossSectionThemes.STANDARD)
    val selectedTheme: StateFlow<CrossSectionTheme> = _selectedTheme.asStateFlow()

    private val _cloudStyle = MutableStateFlow(CloudStyle.SOFT)
    val cloudStyle: StateFlow<CloudStyle> = _cloudStyle.asStateFlow()

    private val _activePresetId = MutableStateFlow<String?>("basic")
    val activePresetId: StateFlow<String?> = _activePresetId.asStateFlow()

    private val _activeEmulationId = MutableStateFlow<String?>(null)
    val activeEmulationId: StateFlow<String?> = _activeEmulationId.asStateFlow()

    private val _expandedFamily = MutableStateFlow<LayerFamily?>(null)
    val expandedFamily: StateFlow<LayerFamily?> = _expandedFamily.asStateFlow()

    private val _isRouteGraphVisible = MutableStateFlow(true)
    val isRouteGraphVisible: StateFlow<Boolean> = _isRouteGraphVisible.asStateFlow()

    private val _leftMetricId = MutableStateFlow("headwind")
    val leftMetricId: StateFlow<String> = _leftMetricId.asStateFlow()

    private val _rightMetricId = MutableStateFlow("crosswind")
    val rightMetricId: StateFlow<String> = _rightMetricId.asStateFlow()

    private val _showDetailSheet = MutableStateFlow(false)
    val showDetailSheet: StateFlow<Boolean> = _showDetailSheet.asStateFlow()

    private val _showSkewT = MutableStateFlow(false)
    val showSkewT: StateFlow<Boolean> = _showSkewT.asStateFlow()

    private val _skewTCompareMode = MutableStateFlow(false)
    val skewTCompareMode: StateFlow<Boolean> = _skewTCompareMode.asStateFlow()

    fun updateBriefingData(
        analyses: RouteAnalysesResponse?,
        elevationProfile: ElevationResponse?,
        briefingPack: PackMetaResponse? = null,
        flight: FlightResponse? = null,
        snapshot: SnapshotResponse? = null,
        advisories: AdvisoriesResponse? = null,
    ) {
        cachedAnalyses = analyses
        cachedElevation = elevationProfile
        cachedPack = briefingPack
        cachedFlight = flight
        cachedSnapshot = snapshot
        cachedAdvisories = advisories

        val models = analyses?.models ?: emptyList()
        _availableModels.value = models
        if (models.isNotEmpty() && (_selectedModel.value.isBlank() || _selectedModel.value !in models)) {
            _selectedModel.value = models.first()
        }
        rebuildVizData()
    }

    fun selectModel(model: String) {
        if (_selectedModel.value == model) return
        _selectedModel.value = model
        rebuildVizData()
    }

    fun toggleLayer(layerId: String) {
        val canonicalId = CrossSectionPresets.normalizeLayerId(layerId)
        val current = _enabledLayers.value.toMutableMap()
        val currentlyOn = current[canonicalId] == true || current[layerId] == true
        val nextState = !currentlyOn

        if (nextState) {
            for ((_, groupSet) in CrossSectionPresets.MUTUALLY_EXCLUSIVE_GROUPS) {
                if (canonicalId in groupSet || layerId in groupSet) {
                    for (sibling in groupSet) {
                        current[sibling] = false
                    }
                }
            }
        }
        current[canonicalId] = nextState
        for (alias in CrossSectionPresets.CANONICAL_TO_ALIAS[canonicalId].orEmpty()) {
            current[alias] = nextState
        }
        _enabledLayers.value = CrossSectionPresets.syncLayerAliases(current)
        _activePresetId.value = null
        recomputeEffectiveLayers()
    }

    fun toggleLayerFamily(family: LayerFamily) {
        val preferred = CrossSectionPresets.deriveGradedMethods(_enabledLayers.value)
        _enabledLayers.value = CrossSectionPresets.getCompactLayerOverrides(
            family = family,
            currentlyEnabled = _enabledLayers.value,
            preferredMethods = preferred,
        )
        _activePresetId.value = null
        recomputeEffectiveLayers()
    }

    fun toggleExpandedFamily(family: LayerFamily) {
        _expandedFamily.value = if (_expandedFamily.value == family) null else family
    }

    fun selectFamilyMethod(family: LayerFamily, methodOrLayer: String) {
        val current = _enabledLayers.value.toMutableMap()
        when (family) {
            LayerFamily.CLOUDS -> {
                for (id in CrossSectionPresets.MUTUALLY_EXCLUSIVE_GROUPS["clouds"].orEmpty()) {
                    current[id] = false
                }
                when (methodOrLayer.lowercase()) {
                    "nwp" -> current["clouds-nwp"] = true
                    "dd" -> current["clouds"] = true
                    "off" -> Unit
                    else -> current[CrossSectionPresets.normalizeLayerId(methodOrLayer)] = true
                }
            }
            LayerFamily.ICING -> {
                for (id in CrossSectionPresets.MUTUALLY_EXCLUSIVE_GROUPS["icing"].orEmpty()) {
                    current[id] = false
                }
                if (methodOrLayer.equals("off", ignoreCase = true)) {
                    current["sld"] = false
                } else {
                    current[CrossSectionPresets.normalizeLayerId(methodOrLayer)] = true
                }
            }
            LayerFamily.TURBULENCE -> {
                if (methodOrLayer.equals("off", ignoreCase = true)) {
                    current["cat"] = false
                    current["vertical-motion"] = false
                    current["inversions"] = false
                } else {
                    val canonical = CrossSectionPresets.normalizeLayerId(methodOrLayer)
                    current[canonical] = !(current[canonical] == true)
                }
            }
            LayerFamily.CONVECTION -> {
                for (id in CrossSectionPresets.MUTUALLY_EXCLUSIVE_GROUPS["convection"].orEmpty()) {
                    current[id] = false
                }
                if (!methodOrLayer.equals("off", ignoreCase = true)) {
                    current[CrossSectionPresets.normalizeLayerId(methodOrLayer)] = true
                }
            }
            LayerFamily.LINES -> {
                toggleLayer(methodOrLayer)
                return
            }
        }
        _enabledLayers.value = CrossSectionPresets.syncLayerAliases(current)
        _activePresetId.value = null
        recomputeEffectiveLayers()
    }

    fun applyEmulationPreset(preset: EmulationPreset) {
        _selectedTheme.value = CrossSectionThemes.byId(preset.themeId)
        _cloudStyle.value = preset.cloudStyle
        _enabledLayers.value = CrossSectionPresets.syncLayerAliases(preset.enabledLayers)
        _activePresetId.value = preset.id
        _activeEmulationId.value = preset.id
        recomputeEffectiveLayers()
    }

    fun applyEmulationById(emulationId: String?) {
        if (emulationId.isNullOrBlank() || emulationId.equals("flyfun", ignoreCase = true)) {
            _activeEmulationId.value = null
            _selectedTheme.value = CrossSectionThemes.STANDARD
            _cloudStyle.value = CloudStyle.SOFT
            applyAdvisoryPreset(CrossSectionPresets.BASIC)
            return
        }
        val preset = CrossSectionPresets.EMULATION_PRESETS.firstOrNull {
            it.id.equals(emulationId, ignoreCase = true)
        } ?: return
        applyEmulationPreset(preset)
    }

    fun applyAdvisoryPreset(preset: AdvisoryPreset) {
        val preferred = CrossSectionPresets.deriveGradedMethods(_enabledLayers.value)
        _enabledLayers.value = CrossSectionPresets.resolveAdvisoryPreset(preset.id, preferred)
        _activePresetId.value = preset.id
        preset.routeGraphLeft?.let { _leftMetricId.value = it }
        preset.routeGraphRight?.let { _rightMetricId.value = it }
        recomputeEffectiveLayers()
    }

    fun applyAdvisoryFocus(advisoryId: String) {
        activeAdvisoryHighlightId = advisoryId
        val mappedId = CrossSectionPresets.ADVISORY_TO_PRESET[advisoryId.lowercase()]
            ?: advisoryId.lowercase()
        val preset = CrossSectionPresets.ADVISORY_PRESETS.firstOrNull { it.id == mappedId }
            ?: CrossSectionPresets.BASIC
        applyAdvisoryPreset(preset)
        rebuildVizData()
    }

    fun applyFocusIntent(intent: FocusIntent) {
        intent.preset?.let { applyAdvisoryFocus(it) }
        intent.waypointIcao?.let { icao ->
            val points = _vizRouteData.value?.points.orEmpty()
            val idx = points.indexOfFirst { it.waypointIcao.equals(icao, ignoreCase = true) }
            if (idx >= 0) {
                selectPoint(idx, openDetail = false)
            }
        }
        intent.distanceNm?.let { dist ->
            selectNearestPointByDistance(dist, openDetail = false)
        }
    }

    fun selectPoint(index: Int?, openDetail: Boolean = false) {
        val points = _vizRouteData.value?.points.orEmpty()
        if (index == null || index !in points.indices) {
            _selectedPointIndex.value = null
            _selectedPoint.value = null
            _selectedRoutePointAnalysis.value = null
            if (!openDetail) _showDetailSheet.value = false
            return
        }
        _selectedPointIndex.value = index
        val pt = points[index]
        _selectedPoint.value = pt
        _selectedRoutePointAnalysis.value = cachedAnalyses?.analyses?.getOrNull(pt.pointIndex)
        if (openDetail) {
            _showDetailSheet.value = true
        }
    }

    fun selectNearestPointByDistance(distanceNm: Double, openDetail: Boolean = false) {
        val points = _vizRouteData.value?.points.orEmpty()
        if (points.isEmpty()) return
        val nearestIdx = points.indices.minByOrNull { abs(points[it].distanceNm - distanceNm) }
        selectPoint(nearestIdx, openDetail)
    }

    fun setTheme(theme: CrossSectionTheme) {
        _selectedTheme.value = theme
    }

    fun setThemeById(themeId: String) {
        _selectedTheme.value = CrossSectionThemes.byId(themeId)
    }

    fun setCloudStyle(style: CloudStyle) {
        _cloudStyle.value = style
    }

    fun setRouteGraphVisible(visible: Boolean) {
        _isRouteGraphVisible.value = visible
    }

    fun setLeftMetric(metricId: String) {
        _leftMetricId.value = metricId
    }

    fun setRightMetric(metricId: String) {
        _rightMetricId.value = metricId
    }

    fun setShowDetailSheet(show: Boolean) {
        _showDetailSheet.value = show
    }

    fun setShowSkewT(show: Boolean, compareMode: Boolean = false) {
        _showSkewT.value = show
        _skewTCompareMode.value = compareMode
    }

    fun setSkewTCompareMode(compare: Boolean) {
        _skewTCompareMode.value = compare
    }

    private fun rebuildVizData() {
        val manifest = cachedAnalyses
        val model = _selectedModel.value
        if (manifest == null || model.isBlank()) {
            _vizRouteData.value = null
            _unavailableLayers.value = emptySet()
            _effectiveLayers.value = _enabledLayers.value
            _substitutedLayers.value = emptySet()
            selectPoint(null)
            return
        }

        val data = VizDataExtractor.extractVizData(
            routeAnalyses = manifest,
            elevation = cachedElevation,
            snapshot = cachedSnapshot,
            advisories = cachedAdvisories,
            selectedModel = model,
            flight = cachedFlight,
            activeAdvisoryHighlightId = activeAdvisoryHighlightId,
        )
        _vizRouteData.value = data
        recomputeEffectiveLayers()

        val currentIdx = _selectedPointIndex.value
        if (data != null && currentIdx != null && currentIdx in data.points.indices) {
            selectPoint(currentIdx, openDetail = _showDetailSheet.value)
        } else {
            selectPoint(null)
        }
    }

    private fun recomputeEffectiveLayers() {
        val data = _vizRouteData.value
        val unavailable = if (data != null) {
            NwpFallback.getUnavailableLayers(data)
        } else {
            emptySet()
        }
        val stored = _enabledLayers.value
        val effective = NwpFallback.applyNwpFallback(stored, unavailable)
        val substituted = NwpFallback.getSubstitutedLayers(stored, effective)

        _unavailableLayers.value = unavailable
        _effectiveLayers.value = effective
        _substitutedLayers.value = substituted
    }
}
