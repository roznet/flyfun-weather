// SYNC: iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/ForecastMapView.swift (+Support)
//       iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/HistoricalMapView.swift
//       iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/ForecastMapKitView.swift (markers)
//       iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/ForecastAirportCard.swift
//       iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/HistoricalAirportCard.swift
package aero.flyfun.weather.ui.map

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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ChevronLeft
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.MapMetricsCatalog
import aero.flyfun.weather.model.ForecastCellData
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ForecastMapAirport
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.ForecastModelMode
import aero.flyfun.weather.model.HistoricalMapAirport
import aero.flyfun.weather.model.HistoricalMapResponse
import aero.flyfun.weather.model.HistoricalSource
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalTime
import aero.flyfun.weather.viewmodel.MapFocusRequest
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.hypot
import kotlin.math.ln
import kotlin.math.roundToInt
import kotlinx.coroutines.flow.first

enum class PanEuropeanMapMode(val label: String) {
    FORECAST("Forecast"),
    HISTORICAL("Historical"),
}

/**
 * Full-screen **pan-European weather map**, both tabs of the web maps page the app has:
 * 1. **Forecast** (`/api/maps/forecast`, #420): every watchlist airport for a day/hour, coloured by
 *    a pilot-question metric under worst / majority consensus or one model.
 * 2. **Historical** (`/api/maps/historical`, #629): a past 30-min instant, coloured by its METAR, its
 *    TAF read then, or a model run from before it.
 *
 * Both share one marker layer ([AirportMarkerMap]) and the served metric catalog, as on iOS.
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

    val europeBounds = remember { GeoBounds(minLat = 37.0, maxLat = 59.0, minLon = -3.0, maxLon = 23.0) }
    val cameraState = rememberSlippyMapCameraState(initialCenterLat = 48.0, initialCenterLon = 10.0, initialZoom = 4.6f)

    Scaffold(
        modifier = modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                title = {
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth(0.92f)) {
                        PanEuropeanMapMode.entries.forEachIndexed { idx, mode ->
                            SegmentedButton(
                                selected = activeMode == mode,
                                onClick = { activeMode = mode },
                                shape = SegmentedButtonDefaults.itemShape(index = idx, count = PanEuropeanMapMode.entries.size),
                            ) {
                                Text(text = mode.label, style = MaterialTheme.typography.labelMedium, maxLines = 1)
                            }
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(imageVector = Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { innerPadding ->
        val bodyModifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)
        when (activeMode) {
            PanEuropeanMapMode.FORECAST -> ForecastMapBody(forecastViewModel, cameraState, europeBounds, mapCatalog, bodyModifier)
            PanEuropeanMapMode.HISTORICAL -> HistoricalMapBody(historicalViewModel, cameraState, europeBounds, mapCatalog, bodyModifier)
        }
    }
}

// ============================================================================
// 1. Forecast map
// ============================================================================

@Composable
private fun ForecastMapBody(
    viewModel: ForecastMapViewModel,
    cameraState: SlippyMapCameraState,
    defaultBounds: GeoBounds,
    catalog: MapMetricsCatalog?,
    modifier: Modifier = Modifier,
) {
    val days by viewModel.days.collectAsState()
    val selectedDay by viewModel.selectedDay.collectAsState()
    val selectedHour by viewModel.selectedHour.collectAsState()
    val hours by viewModel.availableHours.collectAsState()
    val metric by viewModel.metric.collectAsState()
    val mode by viewModel.mode.collectAsState()
    val payload by viewModel.payload.collectAsState()
    val selectedIcao by viewModel.selectedIcao.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val loadError by viewModel.loadError.collectAsState()
    val didLoadOnce by viewModel.didLoadOnce.collectAsState()
    val linkedDateNotice by viewModel.linkedDateNotice.collectAsState()
    val focusRequest by viewModel.focusRequest.collectAsState()

    // A tap on a model that can't reach the day shows why instead of switching
    var unavailableModel by remember { mutableStateOf<String?>(null) }

    val selectedAirport = remember(payload, selectedIcao) { viewModel.selectedAirport() }
    val modelCount = viewModel.models(selectedDay).size

    Column(
        modifier = modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        // "When": title, day menu, hour stepper
        MapTitle(
            title = fullDayLabel(selectedDay, days),
            subtitle = String.format(Locale.US, "%02dZ · %s", selectedHour, modeLongLabel(mode, modelCount)),
        )
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            DayMenu(days = days, selectedDay = selectedDay, onSelect = viewModel::selectDay)
            StepperCapsule(
                label = String.format(Locale.US, "%02dZ", selectedHour),
                canBack = viewModel.canStepHour(-1),
                canForward = viewModel.canStepHour(1),
                onBack = { viewModel.stepHour(-1) },
                onForward = { viewModel.stepHour(1) },
                menuItems = hours.map { hr -> MenuChoice(String.format(Locale.US, "%02dZ", hr), hr == selectedHour) { viewModel.selectHour(hr) } },
            )
            if (isLoading && didLoadOnce) InlineSpinner()
        }
        // A linked `fc.date` the grid can't show (#840), until the pilot picks a day
        linkedDateNotice?.let { NoticeLine(it) }

        AirportMarkerMap(
            payload = payload,
            catalog = catalog,
            metric = metric,
            mode = mode,
            selectedIcao = selectedIcao,
            cameraState = cameraState,
            defaultBounds = defaultBounds,
            focusRequest = focusRequest,
            onFocusApplied = viewModel::focusApplied,
            onSelect = { icao -> if (icao == null) viewModel.deselect() else viewModel.select(icao) },
            onUserInteraction = viewModel::markUserInteracted,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            if (!didLoadOnce && payload == null) {
                MapPlaceholder(
                    loadingText = "Loading forecast map…",
                    error = loadError?.let { "Map unavailable: $it" },
                    isLoading = isLoading,
                    onRetry = viewModel::retry,
                )
            }
        }

        // "What": model, metric, legend
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            MenuButton(
                text = modeShortLabel(mode),
                testTag = "forecastModelMenu",
                sections = listOf(
                    MenuSection(
                        null,
                        listOf(
                            MenuChoice("Worst of models", mode == ForecastModelMode.Worst) { viewModel.selectMode(ForecastModelMode.Worst) },
                            MenuChoice("Majority of models", mode == ForecastModelMode.Majority) { viewModel.selectMode(ForecastModelMode.Majority) },
                        ),
                    ),
                    MenuSection(
                        "Individual model",
                        listOf("gfs", "icon", "ecmwf").map { m ->
                            val available = viewModel.modelAvailable(m)
                            // Greyed-but-tappable: an unreachable model explains itself on tap
                            MenuChoice(
                                m.uppercase(Locale.US) + if (available) "" else " (no data this day)",
                                mode == ForecastModelMode.Model(m),
                            ) {
                                if (available) viewModel.selectMode(ForecastModelMode.Model(m)) else unavailableModel = m.uppercase(Locale.US)
                            }
                        },
                    ),
                ),
            )
            MetricMenu(catalog = catalog, metric = metric, onSelect = viewModel::selectMetric)
        }
        CatalogLegend(catalog, metric)

        selectedAirport?.let { airport ->
            ForecastAirportCard(
                viewModel = viewModel,
                airport = airport,
                payload = payload,
                catalog = catalog,
                metric = metric,
                mode = mode,
                selectedHour = selectedHour,
                modifier = Modifier.heightIn(max = 360.dp),
            )
        }
    }

    unavailableModel?.let { model ->
        AlertDialog(
            onDismissRequest = { unavailableModel = null },
            confirmButton = { TextButton(onClick = { unavailableModel = null }) { Text("OK") } },
            title = { Text("No $model data") },
            text = {
                Text(
                    "This model doesn't reach ${shortDayLabel(selectedDay, days)}. Two- and three-model days differ by " +
                        "design — the consensus still shows what the models that do reach it agree on.",
                )
            },
        )
    }
}

/**
 * The tapped forecast airport (#420): verdict header, alternate-required row, a metric × model
 * matrix (a row tap recolours the map by that metric) and the model init times. Zero network.
 */
@Composable
private fun ForecastAirportCard(
    viewModel: ForecastMapViewModel,
    airport: ForecastMapAirport,
    payload: ForecastMapResponse?,
    catalog: MapMetricsCatalog?,
    metric: String,
    mode: ForecastModelMode,
    selectedHour: Int,
    modifier: Modifier = Modifier,
) {
    // The consensus column mirrors the map mode, falling back to worst for a model
    val consensusMode = mode.consensusMode
    val consensus = airport.consensus(consensusMode)
    val modeLabel = if (consensusMode == ForecastModelMode.Majority) "Majority" else "Worst"
    val presentModels = listOf("gfs", "icon", "ecmwf").filter { airport.models[it] != null }

    AirportCardFrame(onDismiss = viewModel::deselect, modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            CategoryBadge(
                text = consensus.flightCategory ?: "—",
                color = AirportMapCatalog.color(catalog, "flight_category", airport, consensusMode),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(airport.icao, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(
                    validTimeLabel(payload?.forecastTime),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                agreementChip(consensus.agreement(forMetric = "flight_category"))?.let { (text, label) ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Box(
                            Modifier
                                .size(7.dp)
                                .background(AirportMapCatalog.agreementColor(catalog, label), CircleShape),
                        )
                        Text(text, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            StepperCapsule(
                label = String.format(Locale.US, "%02dZ", selectedHour),
                canBack = viewModel.canStepHour(-1),
                canForward = viewModel.canStepHour(1),
                onBack = { viewModel.stepHour(-1) },
                onForward = { viewModel.stepHour(1) },
            )
        }

        // Alternate required, under the card's consensus mode
        val alt = airport.aggregatedAltRequired(consensusMode)
        val required = alt?.faa == true || alt?.easa == true
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Alternate required", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
            Text(
                AirportMapCatalog.altLabel(alt),
                style = MaterialTheme.typography.bodySmall,
                fontWeight = FontWeight.SemiBold,
                color = if (required) Color(0xFFEF4444) else Color(0xFF22C55E),
            )
            Spacer(Modifier.width(6.dp))
            ModeTag(modeLabel)
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        // Metric × model matrix
        MatrixHeader(columns = presentModels.map { it.uppercase(Locale.US) to false } + (modeLabel to false))
        FORECAST_MATRIX_ROWS.forEach { row ->
            val diverging = consensus.agreement(forMetric = row.metric) == "divergent"
            MatrixRow(
                label = rowLabel(row, presentModels.mapNotNull { airport.models[it]?.bestRunwayId }.firstOrNull()),
                isActive = metric == row.metric,
                diverging = diverging,
                onClick = { viewModel.selectMetric(row.metric) },
            ) {
                presentModels.forEach { m ->
                    val cell = airport.models[m]
                    MatrixCell(
                        text = AirportMapCatalog.compactCell(cell, row.metric),
                        color = AirportMapCatalog.color(catalog, row.metric, cell, airport, ForecastModelMode.Model(m)),
                    )
                }
                MatrixCell(
                    text = AirportMapCatalog.compactCell(consensus, row.metric),
                    color = AirportMapCatalog.color(catalog, row.metric, consensus, airport, consensusMode),
                )
            }
        }
        // Temp: plain per-model values, not a colour metric
        if (presentModels.any { airport.models[it]?.temperatureC != null }) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                Text("Temp", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                presentModels.forEach { m ->
                    Text(
                        airport.models[m]?.temperatureC?.let { "${it.roundToInt()}°" } ?: "—",
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.width(MATRIX_CELL_WIDTH),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    )
                }
                Text("—", style = MaterialTheme.typography.labelSmall, modifier = Modifier.width(MATRIX_CELL_WIDTH), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
        }
        initTimesLabel(payload, presentModels)?.let {
            Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ============================================================================
// 2. Historical map
// ============================================================================

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HistoricalMapBody(
    viewModel: HistoricalMapViewModel,
    cameraState: SlippyMapCameraState,
    defaultBounds: GeoBounds,
    catalog: MapMetricsCatalog?,
    modifier: Modifier = Modifier,
) {
    val range by viewModel.range.collectAsState()
    val selectedInstant by viewModel.selectedInstant.collectAsState()
    val lead by viewModel.lead.collectAsState()
    val source by viewModel.source.collectAsState()
    val metric by viewModel.metric.collectAsState()
    val payload by viewModel.payload.collectAsState()
    val mapPayload by viewModel.mapPayload.collectAsState()
    val selectedIcao by viewModel.selectedIcao.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val loadError by viewModel.loadError.collectAsState()
    val didLoadOnce by viewModel.didLoadOnce.collectAsState()
    val notice by viewModel.notice.collectAsState()
    val focusRequest by viewModel.focusRequest.collectAsState()

    var showDatePicker by remember { mutableStateOf(false) }
    val selectedAirport = remember(payload, selectedIcao) { viewModel.selectedAirport() }
    // Re-read on every relevant change (the VM derives it from several flows)
    val statusLine = remember(payload, mapPayload, loadError, isLoading, selectedInstant, source) { viewModel.statusLine() }

    Column(
        modifier = modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        val at = selectedInstant
        val subtitle = buildList {
            add("Historical")
            add(source.label)
            if (!source.isObserved) add(if (lead == 0) "latest runs" else "runs from D-$lead")
            if (at != null && range != null && viewModel.isObservationOnly(at)) add("METAR/TAF only")
        }.joinToString(" · ")
        MapTitle(
            title = at?.let { "${HistoricalTime.shortDateLabel(it)} · ${HistoricalTime.timeLabel(it)}" } ?: "Historical",
            subtitle = subtitle,
        )
        Row(
            modifier = Modifier.horizontalScroll(rememberScrollState()),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                onClick = { showDatePicker = true },
                enabled = at != null,
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                modifier = Modifier.testTag("historicalDatePicker"),
            ) {
                Icon(Icons.Default.DateRange, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text(at?.let(HistoricalTime::shortDateLabel) ?: "Date", style = MaterialTheme.typography.labelMedium)
            }
            StepperCapsule(
                label = at?.let(HistoricalTime::timeLabel) ?: "--:--Z",
                canBack = viewModel.canStep(-1),
                canForward = viewModel.canStep(1),
                onBack = { viewModel.step(-1) },
                onForward = { viewModel.step(1) },
                menuItems = viewModel.timeSlots().map { slot ->
                    MenuChoice(HistoricalTime.timeLabel(slot), slot == at, enabled = viewModel.isSelectable(slot)) {
                        viewModel.selectInstant(slot)
                    }
                },
            )
            MenuButton(
                text = if (lead == 0) "Latest" else "D-$lead",
                sections = listOf(
                    MenuSection(
                        "Model runs",
                        HistoricalMapViewModel.LEADS.map { n ->
                            val base = if (n == 0) "Latest run before this time" else "Run from $n day${if (n == 1) "" else "s"} before"
                            val models = viewModel.models(n)
                            // Say which models a long lead still carries (ICON stops early)
                            val label = if (models.size < 3) base + " · " + models.joinToString(", ") { it.uppercase(Locale.US) } else base
                            MenuChoice(label, n == lead) { viewModel.selectLead(n) }
                        },
                    ),
                ),
            )
            if (isLoading && didLoadOnce) InlineSpinner()
        }
        if (didLoadOnce) {
            statusLine?.let { line ->
                NoticeLine(
                    text = line,
                    // A failed reload keeps the pickers; this re-fetches the same instant
                    action = if (loadError != null && !isLoading) ("Retry" to viewModel::retryReload) else null,
                )
            }
        }

        AirportMarkerMap(
            payload = mapPayload,
            catalog = catalog,
            metric = metric,
            mode = source.mapMode,
            selectedIcao = selectedIcao,
            cameraState = cameraState,
            defaultBounds = defaultBounds,
            focusRequest = focusRequest,
            onFocusApplied = viewModel::focusApplied,
            onSelect = { icao -> if (icao == null) viewModel.deselect() else viewModel.select(icao) },
            onUserInteraction = {},
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            if (!didLoadOnce) {
                MapPlaceholder(
                    loadingText = "Loading historical map…",
                    error = loadError?.let { "Historical map unavailable: $it" },
                    isLoading = isLoading,
                    onRetry = viewModel::retry,
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            // Unavailable sources stay tappable so they can explain themselves
            fun choice(s: HistoricalSource, title: String) = MenuChoice(
                title + if (viewModel.sourceAvailable(s)) "" else " (none at this time)",
                s == source,
            ) { viewModel.selectSource(s) }
            MenuButton(
                text = source.label,
                testTag = "historicalSourceMenu",
                sections = listOf(
                    MenuSection("Observed", HistoricalSource.observed.map { choice(it, it.label) }),
                    MenuSection(
                        "Models",
                        listOf(choice(HistoricalSource.WORST, "Worst of models"), choice(HistoricalSource.MAJORITY, "Majority of models")) +
                            HistoricalSource.nwpModels.map { choice(it, it.label) },
                    ),
                ),
            )
            MetricMenu(catalog = catalog, metric = metric, onSelect = viewModel::selectMetric)
        }
        CatalogLegend(catalog, metric)

        selectedAirport?.let { airport ->
            HistoricalAirportCard(
                viewModel = viewModel,
                airport = airport,
                payload = payload,
                catalog = catalog,
                metric = metric,
                source = source,
                selectedInstant = selectedInstant,
                modifier = Modifier.heightIn(max = 360.dp),
            )
        }
    }

    notice?.let { text ->
        AlertDialog(
            onDismissRequest = viewModel::dismissNotice,
            confirmButton = { TextButton(onClick = viewModel::dismissNotice) { Text("OK") } },
            title = { Text("Nothing to show") },
            text = { Text(text) },
        )
    }

    if (showDatePicker) {
        val at = selectedInstant ?: viewModel.latest
        val earliestDay = HistoricalTime.startOfUtcDay(viewModel.earliest).toEpochMilli()
        val latestMs = viewModel.latest.toEpochMilli()
        val pickerState = rememberDatePickerState(
            initialSelectedDateMillis = HistoricalTime.startOfUtcDay(at).toEpochMilli(),
            selectableDates = object : SelectableDates {
                override fun isSelectableDate(utcTimeMillis: Long) = utcTimeMillis in earliestDay..latestMs
            },
        )
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(onClick = {
                    pickerState.selectedDateMillis?.let { ms ->
                        viewModel.selectDay(Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC).toLocalDate())
                    }
                    showDatePicker = false
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { showDatePicker = false }) { Text("Cancel") } },
        ) {
            DatePicker(state = pickerState)
        }
    }
}

/**
 * The tapped historical airport (#629): METAR, TAF and the three models side by side at the
 * instant, each cell coloured by the catalog, then per-source provenance and the raw reports.
 */
@Composable
private fun HistoricalAirportCard(
    viewModel: HistoricalMapViewModel,
    airport: HistoricalMapAirport,
    payload: HistoricalMapResponse?,
    catalog: MapMetricsCatalog?,
    metric: String,
    source: HistoricalSource,
    selectedInstant: Instant?,
    modifier: Modifier = Modifier,
) {
    // Every source keyed by its token, so each cell colours via Model(token)
    val combined = remember(airport) { airport.allSourcesAirport }
    val columns = HISTORICAL_COLUMNS

    AirportCardFrame(onDismiss = viewModel::deselect, modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            // The active source's category (the map's colour for this airport)
            val cell: ForecastCellData? = if (source.isConsensus) airport.consensus(source) else airport.entry(source)
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                CategoryBadge(
                    text = cell?.categoryField("flight_category") ?: "—",
                    color = AirportMapCatalog.color(catalog, "flight_category", cell, combined, source.mapMode),
                )
                Text(source.label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(airport.icao, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(instantLabel(payload), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            StepperCapsule(
                label = selectedInstant?.let(HistoricalTime::timeLabel) ?: "",
                canBack = viewModel.canStep(-1),
                canForward = viewModel.canStep(1),
                onBack = { viewModel.step(-1) },
                onForward = { viewModel.step(1) },
            )
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        MatrixHeader(columns = columns.map { it.label to (it == source) })
        HISTORICAL_MATRIX_ROWS.forEach { row ->
            MatrixRow(
                label = rowLabel(row, columns.mapNotNull { airport.entry(it)?.bestRunwayId }.firstOrNull(), crosswindOnly = true),
                isActive = metric == row.metric,
                diverging = false,
                onClick = { viewModel.selectMetric(row.metric) },
            ) {
                columns.forEach { s ->
                    val entry = airport.entry(s)
                    MatrixCell(
                        text = if (entry == null) {
                            "—"
                        } else if (row.metric == "alternate_needed") {
                            AirportMapCatalog.altLabel(entry.altRequired)
                        } else {
                            AirportMapCatalog.compactCell(entry, row.metric)
                        },
                        color = if (entry == null) null else AirportMapCatalog.color(catalog, row.metric, entry, combined, s.mapMode),
                    )
                }
            }
        }

        // Provenance per source
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            columns.forEach { s ->
                Row {
                    Text(
                        s.label,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.width(52.dp),
                    )
                    Text(sourceNote(viewModel, airport, payload, s), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }

        airport.metar?.raw?.let { RawReport("METAR", it) }
        airport.taf?.let { taf ->
            taf.raw?.let { raw ->
                val issued = taf.issueTime?.let { " (issued ${HistoricalTime.runLabel(it)})" } ?: ""
                RawReport("TAF$issued", raw)
            }
        }
    }
}

private fun sourceNote(viewModel: HistoricalMapViewModel, airport: HistoricalMapAirport, payload: HistoricalMapResponse?, source: HistoricalSource): String =
    when (source) {
        HistoricalSource.METAR -> airport.metar?.let { metar ->
            var text = HistoricalTime.timeLabel(metar.observationTime)
            if (metar.reportType == "SPECI") text += " SPECI"
            metar.ageMin?.let { text += " · $it min old" }
            text
        } ?: "No report in the ${payload?.sources?.get("metar")?.maxAgeMin ?: 90} min before"
        HistoricalSource.TAF -> airport.taf?.let { taf ->
            taf.temporaryType?.let { type -> "$type ${taf.temporaryCategory ?: ""} over prevailing ${taf.prevailingCategory ?: "—"}" }
                ?: "Prevailing conditions"
        } ?: "No TAF valid at this time"
        HistoricalSource.WORST, HistoricalSource.MAJORITY -> ""
        HistoricalSource.GFS, HistoricalSource.ICON, HistoricalSource.ECMWF -> {
            val entry = airport.models[source.token]
            when {
                entry != null -> "Run ${HistoricalTime.runLabel(entry.modelInitTime)} → valid ${HistoricalTime.timeLabel(entry.validTime)}"
                payload?.sources?.get(source.token)?.available == true -> "No data for this airport"
                else -> viewModel.unavailableText(source)
            }
        }
    }

private fun instantLabel(payload: HistoricalMapResponse?): String {
    val at = payload?.at?.let(HistoricalTime::parse) ?: return ""
    val lead = if (payload.leadDays == 0) "latest runs" else "runs from D-${payload.leadDays}"
    return "${HistoricalTime.dateLabel(at)} ${HistoricalTime.timeLabel(at)} · $lead"
}

// ============================================================================
// 3. Shared marker layer (iOS ForecastMapKitView)
// ============================================================================

/**
 * Every airport always shows: no clustering and no decluttering, since a cluster can't be both "the
 * best airport here" and "the worst" (#420). Fill = active metric colour; the ring is the
 * per-metric model agreement in a consensus mode, amber when selected. Markers grow with zoom.
 */
@Composable
private fun AirportMarkerMap(
    payload: ForecastMapResponse?,
    catalog: MapMetricsCatalog?,
    metric: String,
    mode: ForecastModelMode,
    selectedIcao: String?,
    cameraState: SlippyMapCameraState,
    defaultBounds: GeoBounds,
    focusRequest: MapFocusRequest?,
    onFocusApplied: (MapFocusRequest) -> Unit,
    onSelect: (String?) -> Unit,
    onUserInteraction: () -> Unit,
    modifier: Modifier = Modifier,
    overlay: @Composable () -> Unit = {},
) {
    val density = LocalDensity.current.density
    val log2Density = (ln(density.toDouble()) / ln(2.0)).toFloat()
    val airports = payload?.airports.orEmpty()
    val currentOnInteraction by rememberUpdatedState(onUserInteraction)

    // A one-shot camera move, once the viewport has a size (the canvas fits its default first)
    LaunchedEffect(focusRequest) {
        val req = focusRequest ?: return@LaunchedEffect
        snapshotFlow { cameraState.viewportWidthPx }.first { it > 1f }
        cameraState.centerLat = req.lat.coerceIn(-80.0, 80.0)
        cameraState.centerLon = req.lon.coerceIn(-180.0, 180.0)
        req.webZoom?.let {
            cameraState.zoom = (it.toFloat() + log2Density).coerceIn(SlippyMapCameraState.MIN_ZOOM, SlippyMapCameraState.MAX_ZOOM)
        }
        onFocusApplied(req)
    }

    // Colours are a pure function of (airport, metric, mode): computed once per change, not per frame
    val fills = remember(payload, catalog, metric, mode) {
        airports.map { AirportMapCatalog.color(catalog, metric, it, mode) }
    }
    val rings = remember(payload, catalog, metric, mode) {
        airports.mapIndexed { i, apt ->
            if (mode.isConsensus) {
                AirportMapCatalog.agreementColor(catalog, apt.consensus(mode).agreement(forMetric = metric))
            } else {
                fills[i]
            }
        }
    }

    Box(
        modifier = modifier.pointerInput(Unit) {
            // Observe (never consume) a press: the pilot moved the map, so no late recentring
            awaitPointerEventScope {
                while (true) {
                    val event = awaitPointerEvent(PointerEventPass.Initial)
                    if (event.changes.any { it.pressed }) currentOnInteraction()
                }
            }
        },
    ) {
        SlippyMapCanvas(
            cameraState = cameraState,
            modifier = Modifier
                .fillMaxSize()
                .testTag("airportMarkerMap"),
            defaultBounds = defaultBounds,
            onTap = { tap, _, proj ->
                val radiusPx = markerRadius(cameraState.zoom - log2Density).value * density
                val threshold = maxOf(radiusPx + 10f * density, 22f * density)
                val hit = airports.minByOrNull { ap ->
                    val s = proj.latLonToScreen(ap.lat, ap.lon)
                    hypot(s.x - tap.x, s.y - tap.y)
                }?.takeIf { ap ->
                    val s = proj.latLonToScreen(ap.lat, ap.lon)
                    hypot(s.x - tap.x, s.y - tap.y) <= threshold
                }
                onSelect(hit?.icao)
            },
        ) { proj ->
            val radius = markerRadius(cameraState.zoom - log2Density).toPx()
            var selectedIndex = -1
            airports.forEachIndexed { i, ap ->
                if (ap.icao == selectedIcao) {
                    selectedIndex = i
                    return@forEachIndexed
                }
                val center = proj.latLonToScreen(ap.lat, ap.lon)
                if (center.x < -radius || center.y < -radius || center.x > size.width + radius || center.y > size.height + radius) return@forEachIndexed
                drawMarker(center, radius, fills[i], rings[i], if (mode.isConsensus) 2.dp.toPx() else 1.dp.toPx())
            }
            // The selected marker last, on top, with the amber ring
            if (selectedIndex >= 0) {
                val ap = airports[selectedIndex]
                drawMarker(proj.latLonToScreen(ap.lat, ap.lon), radius, fills[selectedIndex], Color(0xFFFBBF24), 4.dp.toPx())
            }
        }
        overlay()
    }
}

private fun DrawScope.drawMarker(center: androidx.compose.ui.geometry.Offset, radius: Float, fill: Color, ring: Color, ringWidth: Float) {
    drawCircle(color = fill.copy(alpha = 0.85f), radius = radius, center = center)
    drawCircle(color = ring, radius = (radius - ringWidth / 2f).coerceAtLeast(1f), center = center, style = Stroke(width = ringWidth))
}

/**
 * Marker radius from the Leaflet-equivalent zoom (web `markerRadius`: 5 at z ≤ 4 → 11 at z ≥ 8,
 * same steps as iOS `AirportMarkerSizing`).
 */
private fun markerRadius(webZoom: Float): Dp = when {
    webZoom < 4.5f -> 5.dp
    webZoom < 5.5f -> 6.dp
    webZoom < 6.5f -> 7.dp
    webZoom < 7.5f -> 9.dp
    else -> 11.dp
}

// ============================================================================
// 4. Shared chrome
// ============================================================================

private data class MenuChoice(
    val label: String,
    val selected: Boolean,
    val enabled: Boolean = true,
    val onClick: () -> Unit,
)

private data class MenuSection(val title: String?, val items: List<MenuChoice>)

@Composable
private fun MapTitle(title: String, subtitle: String) {
    Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Text(subtitle, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun DayMenu(days: List<ForecastDayEntry>, selectedDay: Int, onSelect: (Int) -> Unit) {
    MenuButton(
        text = shortDayLabel(selectedDay, days),
        leadingIcon = { Icon(Icons.Default.DateRange, contentDescription = null, modifier = Modifier.size(16.dp)) },
        sections = listOf(
            MenuSection(
                null,
                days.map { d -> MenuChoice(dayMenuLabel(d, days), d.day == selectedDay, enabled = d.available) { onSelect(d.day) } },
            ),
        ),
    )
}

@Composable
private fun MenuButton(
    text: String,
    sections: List<MenuSection>,
    testTag: String? = null,
    leadingIcon: (@Composable () -> Unit)? = null,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(
            onClick = { expanded = true },
            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
            modifier = if (testTag != null) Modifier.testTag(testTag) else Modifier,
        ) {
            leadingIcon?.let {
                it()
                Spacer(Modifier.width(4.dp))
            }
            Text(text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold, maxLines = 1)
            Icon(Icons.Default.ArrowDropDown, contentDescription = null, modifier = Modifier.size(18.dp))
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            sections.forEachIndexed { idx, section ->
                if (idx > 0) HorizontalDivider()
                section.title?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                    )
                }
                section.items.forEach { item ->
                    DropdownMenuItem(
                        text = { Text(item.label, fontWeight = if (item.selected) FontWeight.Bold else FontWeight.Normal) },
                        leadingIcon = if (item.selected) ({ Icon(Icons.Default.Check, contentDescription = null) }) else null,
                        enabled = item.enabled,
                        onClick = {
                            expanded = false
                            item.onClick()
                        },
                    )
                }
            }
        }
    }
}

/** The metric menu: pilot questions, only the metrics the catalog defines. */
@Composable
private fun MetricMenu(catalog: MapMetricsCatalog?, metric: String, onSelect: (String) -> Unit) {
    MenuButton(
        text = AirportMapCatalog.label(catalog, metric),
        testTag = "mapMetricMenu",
        leadingIcon = { Icon(Icons.Default.Map, contentDescription = null, modifier = Modifier.size(16.dp)) },
        sections = AirportMapCatalog.metricSections.mapNotNull { section ->
            val items = section.items
                .filter { catalog?.metrics?.containsKey(it.metric) == true }
                .map { item -> MenuChoice(item.label, item.metric == metric) { onSelect(item.metric) } }
            if (items.isEmpty()) null else MenuSection(section.title, items)
        },
    )
}

/** ‹ label › with an optional menu of every value on the label. */
@Composable
private fun StepperCapsule(
    label: String,
    canBack: Boolean,
    canForward: Boolean,
    onBack: () -> Unit,
    onForward: () -> Unit,
    menuItems: List<MenuChoice> = emptyList(),
) {
    var expanded by remember { mutableStateOf(false) }
    Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack, enabled = canBack, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.ChevronLeft, contentDescription = "Earlier")
            }
            Box {
                Text(
                    label,
                    style = MaterialTheme.typography.labelLarge,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier
                        .clickable(enabled = menuItems.isNotEmpty()) { expanded = true }
                        .padding(horizontal = 4.dp),
                )
                if (menuItems.isNotEmpty()) {
                    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }, modifier = Modifier.heightIn(max = 420.dp)) {
                        menuItems.forEach { item ->
                            DropdownMenuItem(
                                text = { Text(item.label, fontWeight = if (item.selected) FontWeight.Bold else FontWeight.Normal) },
                                leadingIcon = if (item.selected) ({ Icon(Icons.Default.Check, contentDescription = null) }) else null,
                                enabled = item.enabled,
                                onClick = {
                                    expanded = false
                                    item.onClick()
                                },
                            )
                        }
                    }
                }
            }
            IconButton(onClick = onForward, enabled = canForward, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.ChevronRight, contentDescription = "Later")
            }
        }
    }
}

@Composable
private fun InlineSpinner() {
    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
}

@Composable
private fun NoticeLine(text: String, action: Pair<String, () -> Unit>? = null) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
    ) {
        Row(modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            action?.let { (title, onClick) -> TextButton(onClick = onClick) { Text(title) } }
        }
    }
}

@Composable
private fun MapPlaceholder(loadingText: String, error: String?, isLoading: Boolean, onRetry: () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.7f)),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (error != null && !isLoading) {
                Icon(Icons.Default.Warning, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 24.dp))
                OutlinedButton(onClick = onRetry) { Text("Retry") }
            } else {
                CircularProgressIndicator()
                Text(loadingText, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun CatalogLegend(catalog: MapMetricsCatalog?, metric: String) {
    val legend = AirportMapCatalog.legend(catalog, metric) ?: return
    MapLegendBar(
        metricTitle = legend.title,
        swatches = legend.items.map { MapLegendSwatch(it.label, parseCatalogColor(it.color) ?: AirportMapCatalog.MUTED) },
    )
}

@Composable
private fun AirportCardFrame(onDismiss: () -> Unit, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Card(
        modifier = modifier
            .fillMaxWidth()
            .testTag("mapAirportCard"),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp),
    ) {
        Box {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                content()
            }
            IconButton(onClick = onDismiss, modifier = Modifier.align(Alignment.TopEnd).size(28.dp)) {
                Icon(Icons.Default.Close, contentDescription = "Close", modifier = Modifier.size(16.dp))
            }
        }
    }
}

@Composable
private fun CategoryBadge(text: String, color: Color) {
    Text(
        text,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold,
        color = color.readableText(),
        modifier = Modifier
            .background(color, RoundedCornerShape(10.dp))
            .padding(horizontal = 12.dp, vertical = 8.dp),
    )
}

@Composable
private fun ModeTag(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(50))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

private val MATRIX_CELL_WIDTH = 52.dp

private data class MatrixRowSpec(val metric: String, val label: String, val unit: String?)

private val FORECAST_MATRIX_ROWS = listOf(
    MatrixRowSpec("flight_category", "Category", null),
    MatrixRowSpec("ceiling_ft", "Ceiling", "ft"),
    MatrixRowSpec("visibility_m", "Visibility", null),
    MatrixRowSpec("wind_speed_kt", "Wind", "kt"),
    MatrixRowSpec("crosswind_kt", "Xwind", "kt"),
    MatrixRowSpec("headwind_kt", "Headwind", "kt"),
    MatrixRowSpec("convective_risk", "Convective", null),
    MatrixRowSpec("cape_jkg", "CAPE", "J/kg"),
    MatrixRowSpec("cloud_cover_pct", "Cloud cover", null),
)

/** The web panel's rows: what a pilot compares observation vs forecast on. */
private val HISTORICAL_MATRIX_ROWS = listOf(
    MatrixRowSpec("flight_category", "Category", null),
    MatrixRowSpec("ceiling_ft", "Ceiling", "ft"),
    MatrixRowSpec("visibility_m", "Visibility", null),
    MatrixRowSpec("wind_speed_kt", "Wind", "kt"),
    MatrixRowSpec("crosswind_kt", "Xwind", "kt"),
    MatrixRowSpec("alternate_needed", "Alt req", null),
)

private val HISTORICAL_COLUMNS = listOf(
    HistoricalSource.METAR,
    HistoricalSource.TAF,
    HistoricalSource.GFS,
    HistoricalSource.ICON,
    HistoricalSource.ECMWF,
)

/** Row label with its unit, naming the best runway on the wind-component rows. */
private fun rowLabel(row: MatrixRowSpec, runway: String?, crosswindOnly: Boolean = false): String {
    var label = row.label
    row.unit?.let { label += " ($it)" }
    val componentRow = row.metric == "crosswind_kt" || (!crosswindOnly && row.metric == "headwind_kt")
    if (componentRow && runway != null) label += " · RWY $runway"
    return label
}

@Composable
private fun MatrixHeader(columns: List<Pair<String, Boolean>>) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Spacer(Modifier.weight(1f))
        columns.forEach { (title, highlighted) ->
            Text(
                title,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = if (highlighted) FontWeight.Bold else FontWeight.SemiBold,
                color = if (highlighted) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                modifier = Modifier.width(MATRIX_CELL_WIDTH),
            )
        }
    }
}

/** One metric row; a tap colours the map by that metric. */
@Composable
private fun MatrixRow(
    label: String,
    isActive: Boolean,
    diverging: Boolean,
    onClick: () -> Unit,
    cells: @Composable () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .background(if (isActive) MaterialTheme.colorScheme.primary.copy(alpha = 0.08f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(vertical = 2.dp),
    ) {
        Row(modifier = Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(3.dp)) {
            if (isActive) Icon(Icons.Default.Map, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(10.dp))
            if (diverging) Icon(Icons.Default.Warning, contentDescription = "Models diverge", tint = Color(0xFFF59E0B), modifier = Modifier.size(10.dp))
            Text(label, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        cells()
    }
}

/** A coloured value cell; [color] null leaves it uncoloured (no data for that source). */
@Composable
private fun MatrixCell(text: String, color: Color?) {
    Box(
        modifier = Modifier
            .width(MATRIX_CELL_WIDTH)
            .height(22.dp)
            .background(color ?: Color.Transparent),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = color?.readableText() ?: MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Clip,
        )
    }
}

@Composable
private fun RawReport(title: String, text: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)
        SelectionContainer {
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f), RoundedCornerShape(8.dp))
                    .padding(8.dp),
            )
        }
    }
}

// ============================================================================
// 5. Labels
// ============================================================================

private fun agreementChip(bucket: String?): Pair<String, String>? = when (bucket) {
    "divergent" -> "Models split" to bucket
    "mixed" -> "Some disagreement" to bucket
    "consistent" -> "Models agree" to bucket
    else -> null
}

private fun modeShortLabel(mode: ForecastModelMode): String = when (mode) {
    ForecastModelMode.Worst -> "Worst"
    ForecastModelMode.Majority -> "Majority"
    is ForecastModelMode.Model -> mode.name.uppercase(Locale.US)
}

private fun modeLongLabel(mode: ForecastModelMode, modelCount: Int): String {
    val models = "$modelCount model${if (modelCount == 1) "" else "s"}"
    return when (mode) {
        ForecastModelMode.Worst -> "Worst of $models"
        ForecastModelMode.Majority -> "Majority of $models"
        is ForecastModelMode.Model -> mode.name.uppercase(Locale.US)
    }
}

private val utcFormatterCache = HashMap<String, DateTimeFormatter>()

private fun utcFormat(pattern: String, date: LocalDate): String =
    utcFormatterCache.getOrPut(pattern) { DateTimeFormatter.ofPattern(pattern, Locale.US) }.format(date)

/** The grid's UTC date for a day (today + offset when the grid doesn't say). Never D-N (#419 W1). */
private fun dateFor(day: Int, days: List<ForecastDayEntry>): LocalDate =
    days.firstOrNull { it.day == day }?.date?.takeIf { it.isNotEmpty() }?.let { runCatching { LocalDate.parse(it) }.getOrNull() }
        ?: LocalDate.now(ZoneOffset.UTC).plusDays(day.toLong())

/** "Today" for D+0, else "Sat 18". */
private fun shortDayLabel(day: Int, days: List<ForecastDayEntry>): String =
    if (day == 0) "Today" else utcFormat("EEE d", dateFor(day, days))

/** "Sat 18 Jul 2026", D+0 prefixed "Today · ". */
private fun fullDayLabel(day: Int, days: List<ForecastDayEntry>): String {
    val full = utcFormat("EEE d MMM yyyy", dateFor(day, days))
    return if (day == 0) "Today · $full" else full
}

/** "Today · Sat 18" / "Sat 18 Jul" (unavailable days stay listed, disabled). */
private fun dayMenuLabel(entry: ForecastDayEntry, days: List<ForecastDayEntry>): String {
    val short = utcFormat("EEE d MMM", dateFor(entry.day, days))
    return if (entry.day == 0) "Today · $short" else short
}

private fun validTimeLabel(iso: String?): String =
    HistoricalTime.parse(iso)?.let { DateTimeFormatter.ofPattern("EEE HH:mm'Z'", Locale.US).withZone(ZoneOffset.UTC).format(it) } ?: ""

private fun initTimesLabel(payload: ForecastMapResponse?, presentModels: List<String>): String? {
    val inits = payload?.modelInitTimes ?: return null
    val parts = presentModels.mapNotNull { m ->
        HistoricalTime.parse(inits[m])?.let { "${m.uppercase(Locale.US)} ${DateTimeFormatter.ofPattern("HH", Locale.US).withZone(ZoneOffset.UTC).format(it)}Z" }
    }
    return if (parts.isEmpty()) null else "Init: " + parts.joinToString(" · ")
}
