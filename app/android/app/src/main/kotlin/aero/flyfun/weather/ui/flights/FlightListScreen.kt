package aero.flyfun.weather.ui.flights

import android.content.Intent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AirplanemodeActive
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.FolderSpecial
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.SwapVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.BottomAppBar
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.FlightGroup
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FlightRole
import aero.flyfun.weather.model.FlightSection
import aero.flyfun.weather.ui.theme.color
import aero.flyfun.weather.viewmodel.FlightListViewModel
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

private val FLIGHT_DATE_FMT: DateTimeFormatter =
    DateTimeFormatter.ofPattern("EEE dd MMM HH:mm 'Z'").withZone(ZoneOffset.UTC)

/**
 * Flight list screen mirroring iOS `FlightListView`.
 * Features:
 * - Grouped sections: Upcoming (`FUTURE`), Recently Completed (`RECENT`), Past (`PAST`, collapsible)
 * - Assessment status dot (GREEN / AMBER / RED / UNAVAILABLE)
 * - Trip badge & click-to-open Trip detail
 * - Long-press multi-select mode with bulk Delete, Move to Trip, or New Trip from selected
 * - Search bar and sort order toggle
 * - Quick actions: Add Flight, Add Next Leg, Edit Flight, Share Flight
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FlightListScreen(
    viewModel: FlightListViewModel,
    selectedFlightId: String? = null,
    unseenWhatsNewCount: Int = 0,
    onFlightClick: (FlightResponse) -> Unit,
    onAddFlightClick: () -> Unit,
    onEditFlightClick: (FlightResponse) -> Unit = {},
    onAddNextLegClick: (FlightResponse) -> Unit = {},
    onOpenTripClick: (String) -> Unit = {},
    onOpenMapsClick: () -> Unit = {},
    onOpenAircraftClick: () -> Unit = {},
    onOpenWhatsNewClick: () -> Unit = {},
    onOpenSettingsClick: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val groups by viewModel.groups.collectAsState()
    val trips by viewModel.trips.collectAsState()
    val order by viewModel.order.collectAsState()
    val searchQuery by viewModel.searchQuery.collectAsState()
    val collapsedSections by viewModel.collapsedSections.collectAsState()
    val isSelectionMode by viewModel.isSelectionMode.collectAsState()
    val selectedIds by viewModel.selectedFlightIds.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()

    var showSearch by rememberSaveable { mutableStateOf(false) }
    var showMoveTripDialog by remember { mutableStateOf(false) }
    var showCreateTripDialog by remember { mutableStateOf(false) }
    var newTripNameInput by remember { mutableStateOf("") }

    Scaffold(
        modifier = modifier,
        topBar = {
            if (isSelectionMode) {
                TopAppBar(
                    title = { Text("${selectedIds.size} selected") },
                    navigationIcon = {
                        IconButton(onClick = { viewModel.exitSelectionMode() }) {
                            Icon(Icons.Default.Close, contentDescription = "Exit selection")
                        }
                    },
                    actions = {
                        TextButton(onClick = { viewModel.selectAllFlights() }) {
                            Text("Select All")
                        }
                    },
                )
            } else {
                TopAppBar(
                    title = {
                        Text(
                            text = "Flights",
                            fontWeight = FontWeight.Bold,
                        )
                    },
                    actions = {
                        IconButton(onClick = { showSearch = !showSearch }) {
                            Icon(Icons.Default.Search, contentDescription = "Search flights")
                        }
                        IconButton(onClick = { viewModel.toggleOrder() }) {
                            Icon(
                                imageVector = Icons.Default.SwapVert,
                                contentDescription = "Sort (${order.title})",
                            )
                        }
                        IconButton(onClick = { viewModel.loadFlights() }) {
                            Icon(Icons.Default.Refresh, contentDescription = "Refresh flights")
                        }
                        IconButton(onClick = onOpenMapsClick) {
                            Icon(Icons.Default.Map, contentDescription = "Explore Maps")
                        }
                        IconButton(onClick = onOpenAircraftClick) {
                            Icon(Icons.Default.AirplanemodeActive, contentDescription = "Aircraft")
                        }
                        IconButton(onClick = onOpenWhatsNewClick) {
                            BadgedBox(
                                badge = {
                                    if (unseenWhatsNewCount > 0) {
                                        Badge { Text(unseenWhatsNewCount.toString()) }
                                    }
                                },
                            ) {
                                Icon(Icons.Default.NewReleases, contentDescription = "What's New")
                            }
                        }
                        IconButton(onClick = onOpenSettingsClick) {
                            Icon(Icons.Default.Settings, contentDescription = "Settings")
                        }
                    },
                )
            }
        },
        bottomBar = {
            if (isSelectionMode) {
                BottomAppBar {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TextButton(
                            onClick = { showCreateTripDialog = true },
                            enabled = selectedIds.isNotEmpty(),
                        ) {
                            Icon(Icons.Default.FolderSpecial, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(modifier = Modifier.width(6.dp))
                            Text("New Trip")
                        }
                        TextButton(
                            onClick = { showMoveTripDialog = true },
                            enabled = selectedIds.isNotEmpty() && trips.isNotEmpty(),
                        ) {
                            Text("Move to Trip")
                        }
                        TextButton(
                            onClick = { viewModel.deleteSelectedFlights() },
                            enabled = selectedIds.isNotEmpty(),
                        ) {
                            Icon(
                                imageVector = Icons.Default.Delete,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("Delete", color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        },
        floatingActionButton = {
            if (!isSelectionMode) {
                ExtendedFloatingActionButton(
                    onClick = onAddFlightClick,
                    icon = { Icon(Icons.Default.Add, contentDescription = null) },
                    text = { Text("New Flight") },
                )
            }
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            AnimatedVisibility(visible = showSearch) {
                OutlinedTextField(
                    value = searchQuery,
                    onValueChange = { viewModel.setSearchQuery(it) },
                    placeholder = { Text("Filter by ICAO, route, or trip...") },
                    singleLine = true,
                    leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                    trailingIcon = {
                        if (searchQuery.isNotEmpty()) {
                            IconButton(onClick = { viewModel.setSearchQuery("") }) {
                                Icon(Icons.Default.Close, contentDescription = "Clear")
                            }
                        }
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }

            if (errorMessage != null) {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 6.dp),
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = errorMessage ?: "",
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = { viewModel.clearError() }) {
                            Text("Dismiss")
                        }
                    }
                }
            }

            if (trips.isNotEmpty() && !isSelectionMode) {
                TripsQuickStrip(
                    trips = trips.map { it.id to "${it.name} (${it.flightCount})" },
                    onTripClick = onOpenTripClick,
                )
            }

            if (isLoading && groups.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else if (groups.isEmpty()) {
                EmptyFlightsPlaceholder(onAddFlightClick = onAddFlightClick)
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(bottom = 88.dp, top = 4.dp),
                ) {
                    groups.forEach { group ->
                        val isCollapsed = group.section in collapsedSections
                        item(key = "header_${group.section.name}") {
                            FlightSectionHeader(
                                group = group,
                                isCollapsed = isCollapsed,
                                onToggleCollapse = {
                                    if (group.section.startsCollapsed || group.section == FlightSection.PAST) {
                                        viewModel.toggleSectionCollapsed(group.section)
                                    }
                                },
                            )
                        }
                        if (!isCollapsed) {
                            items(
                                items = group.flights,
                                key = { it.id },
                            ) { flight ->
                                FlightRowCard(
                                    flight = flight,
                                    isSelectedInDetail = flight.id == selectedFlightId,
                                    isSelectionMode = isSelectionMode,
                                    isChecked = flight.id in selectedIds,
                                    onClick = {
                                        if (isSelectionMode) {
                                            viewModel.toggleFlightSelection(flight.id)
                                        } else {
                                            onFlightClick(flight)
                                        }
                                    },
                                    onLongClick = {
                                        if (!isSelectionMode) {
                                            viewModel.enterSelectionMode(flight.id)
                                        }
                                    },
                                    onEditClick = { onEditFlightClick(flight) },
                                    onAddNextLegClick = { onAddNextLegClick(flight) },
                                    onShareClick = {
                                        viewModel.createShareLink(flight.id) { shareUrl ->
                                            runCatching {
                                                val sendIntent = Intent(Intent.ACTION_SEND).apply {
                                                    putExtra(Intent.EXTRA_TEXT, "FlyFun Weather Briefing (${flight.routeName}): $shareUrl")
                                                    type = "text/plain"
                                                }
                                                context.startActivity(Intent.createChooser(sendIntent, "Share Flight Briefing"))
                                            }
                                        }
                                    },
                                    onDeleteClick = { viewModel.deleteFlight(flight.id) },
                                    onTripBadgeClick = {
                                        flight.tripId?.let(onOpenTripClick)
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    if (showCreateTripDialog) {
        AlertDialog(
            onDismissRequest = { showCreateTripDialog = false },
            title = { Text("Create Trip from Selected Flights") },
            text = {
                OutlinedTextField(
                    value = newTripNameInput,
                    onValueChange = { newTripNameInput = it },
                    label = { Text("Trip Name") },
                    placeholder = { Text("Summer Alps Tour") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val name = newTripNameInput
                        newTripNameInput = ""
                        showCreateTripDialog = false
                        viewModel.createTripFromSelectedFlights(name) { createdTripId ->
                            onOpenTripClick(createdTripId)
                        }
                    },
                    enabled = newTripNameInput.isNotBlank(),
                ) {
                    Text("Create")
                }
            },
            dismissButton = {
                TextButton(onClick = { showCreateTripDialog = false }) {
                    Text("Cancel")
                }
            },
        )
    }

    if (showMoveTripDialog) {
        AlertDialog(
            onDismissRequest = { showMoveTripDialog = false },
            title = { Text("Move Flights to Trip") },
            text = {
                Column {
                    trips.forEach { trip ->
                        TextButton(
                            onClick = {
                                showMoveTripDialog = false
                                viewModel.moveSelectedFlightsToTrip(trip.id)
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("${trip.name} (${trip.flightCount} legs)")
                        }
                    }
                    TextButton(
                        onClick = {
                            showMoveTripDialog = false
                            viewModel.moveSelectedFlightsToTrip(null)
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Remove from Trip")
                    }
                }
            },
            confirmButton = {},
            dismissButton = {
                TextButton(onClick = { showMoveTripDialog = false }) {
                    Text("Cancel")
                }
            },
        )
    }
}

@Composable
private fun TripsQuickStrip(
    trips: List<Pair<String, String>>,
    onTripClick: (String) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = Icons.Default.FolderSpecial,
            contentDescription = "Trips",
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(18.dp),
        )
        trips.take(3).forEach { (id, label) ->
            Surface(
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.secondaryContainer,
                modifier = Modifier.clickable { onTripClick(id) },
            ) {
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSecondaryContainer,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun FlightSectionHeader(
    group: FlightGroup,
    isCollapsed: Boolean,
    onToggleCollapse: () -> Unit,
) {
    val canCollapse = group.section.startsCollapsed || group.section == FlightSection.PAST
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = canCollapse, onClick = onToggleCollapse)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "${group.section.title.uppercase()} (${group.flights.size})",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        if (canCollapse) {
            Icon(
                imageVector = if (isCollapsed) Icons.Default.ExpandMore else Icons.Default.ExpandLess,
                contentDescription = if (isCollapsed) "Expand" else "Collapse",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun FlightRowCard(
    flight: FlightResponse,
    isSelectedInDetail: Boolean,
    isSelectionMode: Boolean,
    isChecked: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
    onEditClick: () -> Unit,
    onAddNextLegClick: () -> Unit,
    onShareClick: () -> Unit,
    onDeleteClick: () -> Unit,
    onTripBadgeClick: () -> Unit,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    val assessment = Assessment.fromWire(flight.latestAssessment)
    val depFormatted = remember(flight.departureTime) {
        runCatching { FLIGHT_DATE_FMT.format(Instant.parse(flight.departureTime)) }
            .getOrDefault(flight.departureTime)
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 5.dp)
            .combinedClickable(
                onClick = onClick,
                onLongClick = onLongClick,
            ),
        colors = CardDefaults.cardColors(
            containerColor = if (isSelectedInDetail) {
                MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.45f)
            } else {
                MaterialTheme.colorScheme.surface
            },
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (isSelectionMode) {
                Icon(
                    imageVector = if (isChecked) Icons.Default.CheckCircle else Icons.Default.RadioButtonUnchecked,
                    contentDescription = null,
                    tint = if (isChecked) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .padding(end = 12.dp)
                        .size(22.dp),
                )
            } else {
                // Assessment dot
                Box(
                    modifier = Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(assessment.color()),
                )
                Spacer(modifier = Modifier.width(12.dp))
            }

            Column(modifier = Modifier.weight(1f)) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = flight.waypoints.takeIf { it.isNotEmpty() }?.joinToString(" → ") ?: flight.routeName,
                        style = MaterialTheme.typography.titleMedium,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (flight.role != null && flight.role != FlightRole.OWNER) {
                        Surface(
                            shape = RoundedCornerShape(4.dp),
                            color = MaterialTheme.colorScheme.tertiaryContainer,
                        ) {
                            Text(
                                text = flight.role.name,
                                style = MaterialTheme.typography.labelSmall,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(4.dp))

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        text = depFormatted,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "• ${FlightDuration.label(flight.flightDurationHours)}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "• ${flight.cruiseAltitudeFt} ft",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                if (flight.tripName != null || flight.aircraft != null) {
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        flight.tripName?.let { tripName ->
                            Surface(
                                shape = RoundedCornerShape(6.dp),
                                color = MaterialTheme.colorScheme.secondaryContainer,
                                modifier = Modifier.clickable(onClick = onTripBadgeClick),
                            ) {
                                Text(
                                    text = tripName,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSecondaryContainer,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                )
                            }
                        }
                        flight.aircraft?.let { ac ->
                            Text(
                                text = ac.displayLabel,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            if (!isSelectionMode) {
                Box {
                    IconButton(onClick = { menuExpanded = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "Flight actions")
                    }
                    DropdownMenu(
                        expanded = menuExpanded,
                        onDismissRequest = { menuExpanded = false },
                    ) {
                        DropdownMenuItem(
                            text = { Text("Edit Flight") },
                            onClick = {
                                menuExpanded = false
                                onEditClick()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Add Next Leg") },
                            onClick = {
                                menuExpanded = false
                                onAddNextLegClick()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Share Flight") },
                            leadingIcon = { Icon(Icons.Default.Share, contentDescription = null) },
                            onClick = {
                                menuExpanded = false
                                onShareClick()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Select Multiple") },
                            onClick = {
                                menuExpanded = false
                                onLongClick()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Delete", color = MaterialTheme.colorScheme.error) },
                            leadingIcon = {
                                Icon(
                                    Icons.Default.Delete,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.error,
                                )
                            },
                            onClick = {
                                menuExpanded = false
                                onDeleteClick()
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun EmptyFlightsPlaceholder(
    onAddFlightClick: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = Icons.Default.AirplanemodeActive,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(56.dp),
        )
        Spacer(modifier = Modifier.height(12.dp))
        Text(
            text = "No Flights Yet",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = "Create a flight route to generate multi-model weather briefings, vertical cross-sections, and route advisories.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(20.dp))
        ExtendedFloatingActionButton(
            onClick = onAddFlightClick,
            icon = { Icon(Icons.Default.Add, contentDescription = null) },
            text = { Text("Create First Flight") },
        )
    }
}
