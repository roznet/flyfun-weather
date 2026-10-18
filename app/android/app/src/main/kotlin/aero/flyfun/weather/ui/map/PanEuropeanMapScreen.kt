package aero.flyfun.weather.ui.map

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.MapMetricsCatalog
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ForecastMapAirport
import aero.flyfun.weather.model.HistoricalMapAirport
import aero.flyfun.weather.ui.briefing.FlightCategoryPillBadge
import aero.flyfun.weather.ui.briefing.shortModelName
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalMapViewModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.roundToInt

enum class PanEuropeanMapMode(val label: String) {
    FORECAST("Forecast Map"),
    HISTORICAL("Historical Replay"),
}

/**
 * Full-screen **Pan-European Weather Overview Map** supporting both:
 * 1. **Forecast Map** (`/api/maps/forecast`) — multi-day (`D0`..`D3`), multi-hour (`06Z`..`18Z`),
 *    multi-model (`ECMWF`, `GFS`, `ICON-EU`, `Worst`), 10+1 airport metrics, and multi-model
 *    comparison inspector card (`ForecastAirportCard`).
 * 2. **Historical Replay** (`/api/maps/historical`) — date stepper, hour picker, `METAR` vs `TAF`
 *    vs `Worst` mode, and side-by-side METAR/TAF verification card (`HistoricalAirportCard`).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PanEuropeanMapScreen(
    forecastViewModel: ForecastMapViewModel,
    historicalViewModel: HistoricalMapViewModel,
    initialMode: PanEuropeanMapMode = PanEuropeanMapMode.FORECAST,
    mapCatalog: MapMetricsCatalog? = null,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var activeMode by remember(initialMode) { mutableStateOf(initialMode) }

    val europeBounds = remember {
        GeoBounds(
            minLat = 39.5,
            maxLat = 54.0,
            minLon = -4.5,
            maxLon = 17.5,
        )
    }
    val cameraState = rememberSlippyMapCameraState(
        initialCenterLat = 47.2,
        initialCenterLon = 6.5,
        initialZoom = 4.6f,
    )

    Scaffold(
        modifier = modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                title = {
                    SingleChoiceSegmentedButtonRow(
                        modifier = Modifier.fillMaxWidth(0.92f),
                    ) {
                        PanEuropeanMapMode.entries.forEachIndexed { idx, mode ->
                            SegmentedButton(
                                selected = activeMode == mode,
                                onClick = { activeMode = mode },
                                shape = SegmentedButtonDefaults.itemShape(
                                    index = idx,
                                    count = PanEuropeanMapMode.entries.size,
                                ),
                            ) {
                                Text(
                                    text = mode.label,
                                    style = MaterialTheme.typography.labelMedium,
                                    maxLines = 1,
                                )
                            }
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
            )
        },
    ) { innerPadding ->
        when (activeMode) {
            PanEuropeanMapMode.FORECAST -> ForecastMapBody(
                viewModel = forecastViewModel,
                cameraState = cameraState,
                defaultBounds = europeBounds,
                mapCatalog = mapCatalog,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            )
            PanEuropeanMapMode.HISTORICAL -> HistoricalMapBody(
                viewModel = historicalViewModel,
                cameraState = cameraState,
                defaultBounds = europeBounds,
                mapCatalog = mapCatalog,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            )
        }
    }
}

// ============================================================================
// 1. Forecast Map Body & Multi-Model Airport Inspector Card
// ============================================================================

@Composable
private fun ForecastMapBody(
    viewModel: ForecastMapViewModel,
    cameraState: SlippyMapCameraState,
    defaultBounds: GeoBounds,
    mapCatalog: MapMetricsCatalog?,
    modifier: Modifier = Modifier,
) {
    val days by viewModel.days.collectAsState()
    val selectedDay by viewModel.selectedDay.collectAsState()
    val selectedHour by viewModel.selectedHour.collectAsState()
    val availableHours by viewModel.availableHours.collectAsState()
    val selectedModel by viewModel.selectedModel.collectAsState()
    val availableModels by viewModel.availableModels.collectAsState()
    val selectedMetric by viewModel.selectedMetric.collectAsState()
    val worstOfModels by viewModel.worstOfModels.collectAsState()
    val mapData by viewModel.mapData.collectAsState()
    val selectedAirportIcao by viewModel.selectedAirportIcao.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()

    var metricMenuExpanded by remember { mutableStateOf(false) }
    val density = LocalDensity.current

    val airports = mapData?.airports.orEmpty()
    val selectedAirport = remember(airports, selectedAirportIcao) {
        airports.firstOrNull { it.icao.equals(selectedAirportIcao, ignoreCase = true) }
    }

    Column(
        modifier = modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // A. Day Selector Strip (D0..D3)
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            days.forEach { dayEntry ->
                ForecastDayChipCard(
                    entry = dayEntry,
                    isSelected = dayEntry.day == selectedDay,
                    onClick = { viewModel.selectDay(dayEntry.day) },
                )
            }
        }

        // B. Hour Chips + Model Chips + Metric Dropdown
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                // Row 1: UTC Hours + Metric Selector
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        modifier = Modifier
                            .weight(1f)
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        availableHours.forEach { hr ->
                            val selected = hr == selectedHour
                            FilterChip(
                                selected = selected,
                                onClick = { viewModel.selectHour(hr) },
                                label = {
                                    Text(
                                        text = String.format(Locale.US, "%02dZ", hr),
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    )
                                },
                            )
                        }
                    }

                    Spacer(modifier = Modifier.width(8.dp))

                    Box {
                        OutlinedButton(
                            onClick = { metricMenuExpanded = true },
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                        ) {
                            Text(
                                text = selectedMetric.shortLabel,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Icon(
                                imageVector = Icons.Default.ArrowDropDown,
                                contentDescription = "Select metric",
                                modifier = Modifier.size(18.dp),
                            )
                        }
                        DropdownMenu(
                            expanded = metricMenuExpanded,
                            onDismissRequest = { metricMenuExpanded = false },
                        ) {
                            AirportMapMetric.forecastMetrics.forEach { metric ->
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            text = metric.label,
                                            fontWeight = if (metric == selectedMetric) FontWeight.Bold else FontWeight.Normal,
                                        )
                                    },
                                    onClick = {
                                        viewModel.selectMetric(metric)
                                        metricMenuExpanded = false
                                    },
                                )
                            }
                        }
                    }
                }

                // Row 2: Model Selector Chips + "Worst" chip
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    availableModels.forEach { model ->
                        val selected = !worstOfModels && model == selectedModel
                        FilterChip(
                            selected = selected,
                            onClick = { viewModel.selectModel(model) },
                            label = {
                                Text(
                                    text = model.shortModelName(),
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                )
                            },
                        )
                    }
                    FilterChip(
                        selected = worstOfModels,
                        onClick = { viewModel.setWorstOfModels(!worstOfModels) },
                        label = {
                            Text(
                                text = "Worst of Models",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = if (worstOfModels) FontWeight.Bold else FontWeight.Normal,
                            )
                        },
                    )
                }
            }
        }

        // C. Interactive Map Canvas
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            SlippyMapCanvas(
                cameraState = cameraState,
                modifier = Modifier.fillMaxSize(),
                defaultBounds = defaultBounds,
                onTap = { tapOffset, _, proj ->
                    val thresholdPx = with(density) { 28.dp.toPx() }
                    val hit = airports.minByOrNull { ap ->
                        val s = proj.latLonToScreen(ap.lat, ap.lon)
                        hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                    }
                    if (hit != null) {
                        val s = proj.latLonToScreen(hit.lat, hit.lon)
                        if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= thresholdPx) {
                            viewModel.selectAirport(hit.icao)
                            return@SlippyMapCanvas
                        }
                    }
                    viewModel.selectAirport(null)
                },
            ) { proj ->
                airports.forEach { ap ->
                    val screen = proj.latLonToScreen(ap.lat, ap.lon)
                    val rawVal = selectedMetric.extractForecastRawValue(
                        airport = ap,
                        selectedModel = selectedModel,
                        worstOfModels = worstOfModels,
                    )
                    val color = selectedMetric.resolveColor(rawVal, mapCatalog)
                    val radiusDp = selectedMetric.resolveRadiusDp(rawVal)
                    val isSelected = ap.icao.equals(selectedAirportIcao, ignoreCase = true)

                    // Draw wind barb at airport if wind speed is available
                    val modelEntry = ap.models[selectedModel] ?: ap.models.values.firstOrNull()
                    val windSpd = modelEntry?.windSpeedKt
                    val windDir = modelEntry?.windDir
                    if (windSpd != null && windDir != null) {
                        drawWindBarbOnMap(
                            center = screen,
                            windSpeedKt = windSpd,
                            windDirDeg = windDir,
                            color = Color(0xFFE2E8F0),
                            staffLengthPx = 22.dp.toPx(),
                        )
                    }

                    drawAirportPinOnMap(
                        center = screen,
                        radiusPx = radiusDp.dp.toPx(),
                        fillColor = color,
                        isSelected = isSelected,
                        label = ap.icao,
                    )
                }
            }

            if (isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(12.dp)
                        .size(22.dp),
                    strokeWidth = 2.dp,
                )
            }
        }

        // D. Legend Bar
        MapLegendBar(
            metricTitle = selectedMetric.shortLabel,
            swatches = selectedMetric.legendSwatches(mapCatalog),
        )

        // E. Selected Airport Multi-Model Comparison Card
        selectedAirport?.let { airport ->
            ForecastAirportCard(
                airport = airport,
                selectedModel = selectedModel,
                selectedMetric = selectedMetric,
                onDismiss = { viewModel.selectAirport(null) },
            )
        }
    }
}

@Composable
private fun ForecastDayChipCard(
    entry: ForecastDayEntry,
    isSelected: Boolean,
    onClick: () -> Unit,
) {
    val dayLabel = when (entry.day) {
        0 -> "Today"
        1 -> "Tomorrow"
        else -> "D+${entry.day}"
    }
    Surface(
        modifier = Modifier
            .width(104.dp)
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(10.dp),
        color = if (isSelected) {
            MaterialTheme.colorScheme.primaryContainer
        } else {
            MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)
        },
        border = BorderStroke(
            width = if (isSelected) 1.5.dp else 0.5.dp,
            color = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant,
        ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = dayLabel,
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = entry.date.takeLast(5),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
fun ForecastAirportCard(
    airport: ForecastMapAirport,
    selectedModel: String,
    selectedMetric: AirportMapMetric,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val modelsOrder = remember(airport.models) {
        airport.models.keys.toList()
    }
    val consensusCat = airport.consensus?.get("category")
        ?: airport.models[selectedModel]?.category
    val overallAgreement = airport.agreement?.get(selectedModel)?.level
        ?: airport.agreement?.values?.firstOrNull()?.level

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.35f)),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = airport.icao,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = airport.name,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    consensusCat?.let { FlightCategoryPillBadge(it) }
                    overallAgreement?.takeIf { it.isNotBlank() }?.let { agr ->
                        val agrColor = AirportMapMetric.AGREEMENT.resolveColor(agr)
                        Surface(
                            shape = RoundedCornerShape(50),
                            color = agrColor.copy(alpha = 0.16f),
                        ) {
                            Text(
                                text = agr.replaceFirstChar { it.uppercase() },
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = agrColor,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
                IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                    Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                }
            }

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

            // Side-by-side multi-model comparison rows
            Row(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = "Model",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(68.dp),
                )
                Text(
                    text = "Cat",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(54.dp),
                )
                Text(
                    text = "Wind",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = "Runway / XW",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = "Ceil / Vis",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
            }

            modelsOrder.forEach { modelKey ->
                val entry = airport.models[modelKey] ?: return@forEach
                val isCurrent = modelKey == selectedModel
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            if (isCurrent) MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.25f)
                            else Color.Transparent,
                            shape = RoundedCornerShape(6.dp),
                        )
                        .padding(vertical = 3.dp, horizontal = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = modelKey.shortModelName(),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = if (isCurrent) FontWeight.Bold else FontWeight.Medium,
                        modifier = Modifier.width(68.dp),
                    )
                    Box(modifier = Modifier.width(54.dp)) {
                        FlightCategoryPillBadge(entry.category ?: "VFR")
                    }
                    val windStr = buildString {
                        entry.windDir?.let { append(String.format(Locale.US, "%03d°/", it.roundToInt())) }
                        append("${entry.windSpeedKt?.roundToInt() ?: 0}")
                        entry.windGustKt?.let { append("G${it.roundToInt()}") }
                        append("kt")
                    }
                    Text(
                        text = windStr,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.weight(1f),
                    )
                    val rwyStr = buildString {
                        append(entry.bestRunway ?: "—")
                        entry.crosswindKt?.let { append(" (${it.roundToInt()}kt XW)") }
                    }
                    Text(
                        text = rwyStr,
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.weight(1f),
                    )
                    val ceilStr = entry.ceilingFt?.let {
                        if (it >= 9000.0) "Unlim" else "${it.roundToInt()}ft"
                    } ?: "Unlim"
                    val visStr = entry.visibilityM?.let {
                        if (it >= 9999.0) "10km+" else String.format(Locale.US, "%.1fkm", it / 1000.0)
                    } ?: "10km+"
                    Text(
                        text = "$ceilStr · $visStr",
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

// ============================================================================
// 2. Historical Replay Map Body & METAR vs TAF Verification Card
// ============================================================================

@Composable
private fun HistoricalMapBody(
    viewModel: HistoricalMapViewModel,
    cameraState: SlippyMapCameraState,
    defaultBounds: GeoBounds,
    mapCatalog: MapMetricsCatalog?,
    modifier: Modifier = Modifier,
) {
    val selectedDate by viewModel.selectedDate.collectAsState()
    val selectedHour by viewModel.selectedHour.collectAsState()
    val availableHours by viewModel.availableHours.collectAsState()
    val selectedMetric by viewModel.selectedMetric.collectAsState()
    val viewMode by viewModel.viewMode.collectAsState()
    val mapData by viewModel.mapData.collectAsState()
    val selectedAirportIcao by viewModel.selectedAirportIcao.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()

    var metricMenuExpanded by remember { mutableStateOf(false) }
    val density = LocalDensity.current

    val airports = mapData?.airports.orEmpty()
    val selectedAirport = remember(airports, selectedAirportIcao) {
        airports.firstOrNull { it.icao.equals(selectedAirportIcao, ignoreCase = true) }
    }

    Column(
        modifier = modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        // A. Date Stepper + View Mode (METAR / TAF / Worst) + Hour & Metric Controls
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                // Row 1: Date Stepper + View Mode Chips
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        IconButton(
                            onClick = { viewModel.stepDay(-1) },
                            modifier = Modifier.size(28.dp),
                        ) {
                            Icon(Icons.Default.ChevronLeft, contentDescription = "Previous day")
                        }
                        Text(
                            text = selectedDate,
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                        )
                        IconButton(
                            onClick = { viewModel.stepDay(1) },
                            modifier = Modifier.size(28.dp),
                        ) {
                            Icon(Icons.Default.ChevronRight, contentDescription = "Next day")
                        }
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        listOf(
                            "metar" to "METAR",
                            "taf" to "TAF",
                            "worst" to "Worst",
                        ).forEach { (modeId, label) ->
                            val selected = viewMode == modeId
                            FilterChip(
                                selected = selected,
                                onClick = { viewModel.selectViewMode(modeId) },
                                label = {
                                    Text(
                                        text = label,
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    )
                                },
                            )
                        }
                    }
                }

                // Row 2: Hour Chips + Metric Dropdown
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        modifier = Modifier
                            .weight(1f)
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        availableHours.forEach { hr ->
                            val selected = hr == selectedHour
                            FilterChip(
                                selected = selected,
                                onClick = { viewModel.selectHour(hr) },
                                label = {
                                    Text(
                                        text = String.format(Locale.US, "%02dZ", hr),
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    )
                                },
                            )
                        }
                    }

                    Spacer(modifier = Modifier.width(8.dp))

                    Box {
                        OutlinedButton(
                            onClick = { metricMenuExpanded = true },
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                        ) {
                            Text(
                                text = selectedMetric.shortLabel,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Icon(
                                imageVector = Icons.Default.ArrowDropDown,
                                contentDescription = "Select metric",
                                modifier = Modifier.size(18.dp),
                            )
                        }
                        DropdownMenu(
                            expanded = metricMenuExpanded,
                            onDismissRequest = { metricMenuExpanded = false },
                        ) {
                            AirportMapMetric.historicalMetrics.forEach { metric ->
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            text = metric.label,
                                            fontWeight = if (metric == selectedMetric) FontWeight.Bold else FontWeight.Normal,
                                        )
                                    },
                                    onClick = {
                                        viewModel.selectMetric(metric)
                                        metricMenuExpanded = false
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }

        // B. Interactive Map Canvas
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            SlippyMapCanvas(
                cameraState = cameraState,
                modifier = Modifier.fillMaxSize(),
                defaultBounds = defaultBounds,
                onTap = { tapOffset, _, proj ->
                    val thresholdPx = with(density) { 28.dp.toPx() }
                    val hit = airports.minByOrNull { ap ->
                        val s = proj.latLonToScreen(ap.lat, ap.lon)
                        hypot(s.x - tapOffset.x, s.y - tapOffset.y)
                    }
                    if (hit != null) {
                        val s = proj.latLonToScreen(hit.lat, hit.lon)
                        if (hypot(s.x - tapOffset.x, s.y - tapOffset.y) <= thresholdPx) {
                            viewModel.selectAirport(hit.icao)
                            return@SlippyMapCanvas
                        }
                    }
                    viewModel.selectAirport(null)
                },
            ) { proj ->
                airports.forEach { ap ->
                    val screen = proj.latLonToScreen(ap.lat, ap.lon)
                    val rawVal = selectedMetric.extractHistoricalRawValue(ap, viewMode)
                    val color = selectedMetric.resolveColor(rawVal, mapCatalog)
                    val radiusDp = selectedMetric.resolveRadiusDp(rawVal)
                    val isSelected = ap.icao.equals(selectedAirportIcao, ignoreCase = true)

                    val windSpd = ap.metar?.windSpeedKt
                    val windDir = ap.metar?.windDir
                    if (windSpd != null && windDir != null) {
                        drawWindBarbOnMap(
                            center = screen,
                            windSpeedKt = windSpd,
                            windDirDeg = windDir,
                            color = Color(0xFFE2E8F0),
                            staffLengthPx = 22.dp.toPx(),
                        )
                    }

                    drawAirportPinOnMap(
                        center = screen,
                        radiusPx = radiusDp.dp.toPx(),
                        fillColor = color,
                        isSelected = isSelected,
                        label = ap.icao,
                    )
                }
            }

            if (isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(12.dp)
                        .size(22.dp),
                    strokeWidth = 2.dp,
                )
            }
        }

        // C. Legend Bar
        MapLegendBar(
            metricTitle = selectedMetric.shortLabel,
            swatches = selectedMetric.legendSwatches(mapCatalog),
        )

        // D. Selected Historical Airport Card (METAR vs TAF Verification)
        selectedAirport?.let { airport ->
            HistoricalAirportCard(
                airport = airport,
                onDismiss = { viewModel.selectAirport(null) },
            )
        }
    }
}

@Composable
fun HistoricalAirportCard(
    airport: HistoricalMapAirport,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val m = airport.metar
    val t = airport.taf
    val categoriesMatch = airport.categoryMatch ?: (
        t?.category != null && t.category.equals(m?.category, ignoreCase = true)
    )

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.35f)),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = airport.icao,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = airport.name,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    m?.category?.let { FlightCategoryPillBadge(it) }
                    if (t?.category != null) {
                        val badgeColor = if (categoriesMatch) Color(0xFF22C55E) else Color(0xFFF59E0B)
                        Surface(
                            shape = RoundedCornerShape(50),
                            color = badgeColor.copy(alpha = 0.16f),
                        ) {
                            Text(
                                text = if (categoriesMatch) "TAF Match" else "TAF was ${t.category}",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = badgeColor,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
                IconButton(onClick = onDismiss, modifier = Modifier.size(24.dp)) {
                    Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
                }
            }

            if (m != null) {
                val metarMetrics = listOfNotNull(
                    m.windSpeedKt?.let { spd ->
                        val dir = m.windDir?.let { String.format(Locale.US, "%03d°/", it.roundToInt()) } ?: ""
                        val gst = m.windGustKt?.let { "G${it.roundToInt()}" } ?: ""
                        "Wind $dir${spd.roundToInt()}${gst}kt"
                    },
                    m.bestRunway?.let { rwy ->
                        val xw = m.crosswindKt?.let { " (${abs(it).roundToInt()}kt XW)" } ?: ""
                        "Rwy $rwy$xw"
                    },
                    m.ceilingFt?.let { "Ceil ${it.roundToInt()}ft" },
                    m.visibilityM?.let { "Vis ${it.roundToInt()}m" },
                    m.pressureHpa?.let { "QNH ${it.roundToInt()}" },
                ).joinToString(" · ")

                if (metarMetrics.isNotBlank()) {
                    Text(
                        text = metarMetrics,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }

                if (!m.raw.isNullOrBlank()) {
                    Text(
                        text = m.raw,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            if (t != null && !t.raw.isNullOrBlank()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Text(
                    text = t.raw,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
