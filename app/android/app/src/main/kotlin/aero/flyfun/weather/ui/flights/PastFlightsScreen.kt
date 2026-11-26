package aero.flyfun.weather.ui.flights

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.PastArchiveRow
import aero.flyfun.weather.model.PastChips
import aero.flyfun.weather.model.PastFilter
import aero.flyfun.weather.ui.theme.color
import aero.flyfun.weather.viewmodel.PastFlightsViewModel
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

private val PAST_DATE_FMT: DateTimeFormatter =
    DateTimeFormatter.ofPattern("EEE dd MMM yyyy").withZone(ZoneOffset.UTC)

/**
 * The Past flights screen (#1032): search, hazard and outcome chips with the server's counts,
 * the list grouped by month while a filter is on, paged from `GET /api/flights/archive`.
 * Owned flights only; subscribed flights never appear. Mirrors iOS `PastFlightsView`.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PastFlightsScreen(
    viewModel: PastFlightsViewModel,
    onBack: () -> Unit,
    onOpenFlight: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val rows by viewModel.rows.collectAsState()
    val total by viewModel.total.collectAsState()
    val facets by viewModel.facets.collectAsState()
    val filter by viewModel.filter.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val error by viewModel.error.collectAsState()
    var searchText by rememberSaveable { mutableStateOf(filter.query) }

    LaunchedEffect(Unit) {
        if (rows.isEmpty()) viewModel.reload()
    }

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text("Past flights", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize()) {
            OutlinedTextField(
                value = searchText,
                onValueChange = {
                    searchText = it
                    viewModel.setQuery(it)
                },
                placeholder = { Text("ICAO, description or trip") },
                singleLine = true,
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = {
                    if (searchText.isNotEmpty()) {
                        IconButton(onClick = {
                            searchText = ""
                            viewModel.setQuery("", debounceMs = 0)
                        }) {
                            Icon(Icons.Default.Close, contentDescription = "Clear")
                        }
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp)
                    .testTag("pastSearch"),
            )
            // Hazards (+ Reported), then outcomes (+ Hide): counts are the server's facets.
            Row(
                modifier = Modifier
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                PastChips.hazards.forEach { chip ->
                    PastFilterChip(
                        label = chip.label,
                        count = facets?.tags?.get(chip.id),
                        selected = chip.id in filter.tags,
                        onClick = { viewModel.toggleTag(chip.id) },
                        tag = "pastChip-${chip.id}",
                    )
                }
                PastFilterChip(
                    label = "Reported",
                    count = null,
                    selected = filter.reported,
                    onClick = { viewModel.update { it.copy(reported = !it.reported) } },
                    tag = "pastChip-reported",
                )
            }
            Row(
                modifier = Modifier
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                PastChips.outcomes.forEach { chip ->
                    PastFilterChip(
                        label = chip.label,
                        count = facets?.outcomes?.get(chip.id),
                        selected = chip.id in filter.outcomes,
                        onClick = { viewModel.toggleOutcome(chip.id) },
                        tag = "pastChip-${chip.id}",
                    )
                }
                PastFilterChip(
                    label = "Hide cancelled and monitor only",
                    count = null,
                    selected = filter.hideInactive,
                    onClick = { viewModel.update { it.copy(hideInactive = !it.hideInactive) } },
                    tag = "pastChip-hide",
                )
            }

            if (rows.isEmpty() && !isLoading) {
                PastEmptyState(
                    error = error,
                    filterActive = filter.isActive,
                    onClear = {
                        searchText = ""
                        viewModel.update { PastFilter() }
                    },
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize().testTag("pastFlightsList"),
                    contentPadding = PaddingValues(bottom = 24.dp, top = 4.dp),
                ) {
                    item(key = "total") {
                        Text(
                            text = "$total flight${if (total == 1) "" else "s"}",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                        )
                    }
                    val sections = if (filter.isActive) {
                        PastFlightsViewModel.monthSections(rows)
                    } else {
                        listOf(PastFlightsViewModel.MonthSection("", rows))
                    }
                    sections.forEach { section ->
                        if (section.title.isNotEmpty()) {
                            item(key = "month_${section.title}") {
                                Text(
                                    text = section.title.uppercase(),
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                                )
                            }
                        }
                        items(items = section.rows, key = { it.id }) { row ->
                            LaunchedEffect(row.id, rows.size) { viewModel.loadMoreIfNeeded(row) }
                            PastFlightRow(row = row, onClick = { onOpenFlight(row.id) })
                            HorizontalDivider(modifier = Modifier.padding(horizontal = 16.dp))
                        }
                    }
                    if (isLoading) {
                        item(key = "loading") {
                            Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                                CircularProgressIndicator()
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PastFilterChip(
    label: String,
    count: Int?,
    selected: Boolean,
    onClick: () -> Unit,
    tag: String,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(if (count != null) "$label  $count" else label) },
        modifier = Modifier.testTag(tag),
    )
}

@Composable
private fun PastEmptyState(error: String?, filterActive: Boolean, onClear: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        val text = when {
            error != null -> "Couldn't load past flights: $error"
            filterActive -> "No past flights match these filters."
            else -> "No past flights"
        }
        Text(text, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (error == null && filterActive) {
            TextButton(onClick = onClear) { Text("Clear filters") }
        }
    }
}

/** One archive row: route, date, outcome, hazard dots, reported tags. */
@Composable
private fun PastFlightRow(row: PastArchiveRow, onClick: () -> Unit) {
    val date = remember(row.departureTime) {
        row.departureEpochMs?.let { PAST_DATE_FMT.format(Instant.ofEpochMilli(it)) } ?: ""
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp)
            .testTag("pastRow-${row.id}"),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(row.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Text(date, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            row.trip?.let {
                Text(it.name, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            row.decision?.let {
                Text(
                    PastChips.labelForOutcome(it),
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier
                        .background(MaterialTheme.colorScheme.surfaceVariant, CircleShape)
                        .padding(horizontal = 6.dp, vertical = 1.dp),
                )
            }
            if (row.differsFromForecast) {
                Text("Differed from forecast", style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        when {
            !row.tagsRecorded -> Text("Hazards not recorded", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            row.forecastTags.isEmpty() -> Text("No amber or red hazard in the last briefing",
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            else -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                PastChips.hazards.filter { it.id in row.forecastTags }.forEach { chip ->
                    val red = row.forecastTags[chip.id] == "red"
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(8.dp).background((if (red) Assessment.RED else Assessment.AMBER).color(), CircleShape))
                        Spacer(Modifier.width(4.dp))
                        Text(chip.label, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
        if (row.reportedTags.isNotEmpty()) {
            Text(
                "Reported: " + row.reportedTags.joinToString(", ") { PastChips.labelForTag(it) },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
