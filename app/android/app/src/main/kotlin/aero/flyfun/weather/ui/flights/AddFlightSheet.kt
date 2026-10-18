package aero.flyfun.weather.ui.flights

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoFixHigh
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Route
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.FlexibilityMode
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.viewmodel.AddFlightViewModel
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

/**
 * Modal bottom sheet for creating or editing a flight, mirroring iOS `AddFlightSheet`.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddFlightSheet(
    viewModel: AddFlightViewModel,
    onDismiss: () -> Unit,
    onFlightSaved: (FlightResponse) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val editingId by viewModel.editingFlightId.collectAsState()
    val rawRouteText by viewModel.rawRouteText.collectAsState()
    val waypoints by viewModel.waypoints.collectAsState()
    val fplText by viewModel.fplText.collectAsState()
    val airportSuggestions by viewModel.airportSuggestions.collectAsState()
    val departureDate by viewModel.departureDate.collectAsState()
    val departureTime by viewModel.departureTime.collectAsState()
    val flexibilityMode by viewModel.flexibilityMode.collectAsState()
    val windowEndTime by viewModel.windowEndTime.collectAsState()
    val windowEndDate by viewModel.windowEndDate.collectAsState()
    val cruiseAltitudeFt by viewModel.cruiseAltitudeFt.collectAsState()
    val flightCeilingFt by viewModel.flightCeilingFt.collectAsState()
    val durationHours by viewModel.durationHours.collectAsState()
    val durationMinutes by viewModel.durationMinutes.collectAsState()
    val distanceNm by viewModel.distanceNm.collectAsState()
    val aircraftList by viewModel.aircraftList.collectAsState()
    val selectedAircraftId by viewModel.selectedAircraftId.collectAsState()
    val profiles by viewModel.profiles.collectAsState()
    val selectedProfileId by viewModel.selectedProfileId.collectAsState()
    val trips by viewModel.trips.collectAsState()
    val selectedTripId by viewModel.selectedTripId.collectAsState()
    val newTripName by viewModel.newTripName.collectAsState()
    val autorouterRoutes by viewModel.autorouterRoutes.collectAsState()
    val isFetchingRoutes by viewModel.isFetchingRoutes.collectAsState()
    val isSaving by viewModel.isSaving.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()

    var showFplBox by remember { mutableStateOf(false) }
    var dateInputText by remember(departureDate) { mutableStateOf(departureDate.toString()) }
    var timeInputText by remember(departureTime) {
        mutableStateOf(departureTime.format(DateTimeFormatter.ofPattern("HH:mm")))
    }

    LaunchedEffect(Unit) {
        viewModel.loadFormMetadata()
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = if (editingId != null) "Edit Flight" else "New Flight",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }

            if (errorMessage != null) {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 6.dp),
                ) {
                    Text(
                        text = errorMessage ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        modifier = Modifier.padding(10.dp),
                    )
                }
            }

            // Route Input
            OutlinedTextField(
                value = rawRouteText,
                onValueChange = { viewModel.updateRouteText(it) },
                label = { Text("Route Waypoints (ICAO / Navaids)") },
                placeholder = { Text("EGTK LFPB LFMD") },
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Characters,
                ),
                trailingIcon = {
                    IconButton(onClick = { viewModel.interpretRouteFromServer() }) {
                        Icon(Icons.Default.AutoFixHigh, contentDescription = "Interpret Route")
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )

            // Airport autocomplete suggestions
            if (airportSuggestions.isNotEmpty()) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 4.dp),
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        airportSuggestions.forEach { airport ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { viewModel.selectAirportSuggestion(airport) }
                                    .padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = airport.icao,
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Bold,
                                    style = MaterialTheme.typography.bodyMedium,
                                )
                                Spacer(modifier = Modifier.width(10.dp))
                                Text(
                                    text = "${airport.name} (${airport.municipality})",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }

            // Parsed waypoint chips + distance summary
            if (waypoints.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    waypoints.forEach { wp ->
                        AssistChip(
                            onClick = {},
                            label = {
                                Text(
                                    text = wp,
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            },
                        )
                    }
                    distanceNm?.let { nm ->
                        AssistChip(
                            onClick = {},
                            label = { Text("${nm.roundToInt()} nm") },
                        )
                    }
                }
            }

            // ICAO FPL paste & Autorouter route buttons
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(onClick = { showFplBox = !showFplBox }) {
                    Text(if (showFplBox) "Hide ICAO FPL" else "Paste ICAO FPL")
                }
                if (waypoints.size >= 2) {
                    TextButton(
                        onClick = { viewModel.fetchAutorouterSuggestions() },
                        enabled = !isFetchingRoutes,
                    ) {
                        Icon(Icons.Default.Route, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(if (isFetchingRoutes) "Fetching Routes..." else "Autorouter Routes")
                    }
                }
            }

            if (showFplBox) {
                OutlinedTextField(
                    value = fplText,
                    onValueChange = { viewModel.updateFplText(it) },
                    label = { Text("Paste ICAO FPL String") },
                    placeholder = { Text("(FPL-N123AB-VG-SR22/L-S/C-EGTK0900-N0160F085 ...)") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedButton(
                    onClick = {
                        viewModel.parseIcaoFpl()
                        showFplBox = false
                    },
                    modifier = Modifier.padding(top = 6.dp),
                ) {
                    Text("Apply FPL")
                }
            }

            if (autorouterRoutes.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = "Autorouter Suggestions",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
                autorouterRoutes.forEach { option ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp)
                            .clickable { viewModel.applyAutorouterRoute(option) },
                    ) {
                        Column(modifier = Modifier.padding(10.dp)) {
                            Text(
                                text = option.routeText,
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(
                                text = "${option.distanceNm?.roundToInt() ?: 0} nm • ${
                                    option.durationHours?.let { FlightDuration.label(it) } ?: ""
                                }",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(12.dp))
            HorizontalDivider()
            Spacer(modifier = Modifier.height(12.dp))

            // Date & UTC Time
            Text(
                text = "Departure Date & Time (UTC)",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(modifier = Modifier.height(8.dp))

            // Quick date chips
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                val today = LocalDate.now()
                listOf(
                    "Today" to today,
                    "Tomorrow" to today.plusDays(1),
                    "+2 Days" to today.plusDays(2),
                    "+3 Days" to today.plusDays(3),
                ).forEach { (label, date) ->
                    FilterChip(
                        selected = departureDate == date,
                        onClick = {
                            viewModel.setDepartureDate(date)
                            dateInputText = date.toString()
                        },
                        label = { Text(label) },
                    )
                }
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                OutlinedTextField(
                    value = dateInputText,
                    onValueChange = { str ->
                        dateInputText = str
                        runCatching { LocalDate.parse(str) }.onSuccess { viewModel.setDepartureDate(it) }
                    },
                    label = { Text("Date (YYYY-MM-DD)") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = timeInputText,
                    onValueChange = { str ->
                        timeInputText = str
                        runCatching { LocalTime.parse(str) }.onSuccess { viewModel.setDepartureTime(it) }
                    },
                    label = { Text("Time UTC (HH:MM)") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(modifier = Modifier.height(10.dp))

            // Flexibility Mode
            SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                FlexibilityMode.entries.forEachIndexed { idx, mode ->
                    SegmentedButton(
                        selected = flexibilityMode == mode,
                        onClick = { viewModel.setFlexibilityMode(mode) },
                        shape = SegmentedButtonDefaults.itemShape(index = idx, count = FlexibilityMode.entries.size),
                    ) {
                        Text(mode.title)
                    }
                }
            }

            if (flexibilityMode != FlexibilityMode.FIXED) {
                Spacer(modifier = Modifier.height(8.dp))
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (flexibilityMode == FlexibilityMode.MULTI_DAY) {
                        var endDateStr by remember(windowEndDate) { mutableStateOf(windowEndDate.toString()) }
                        OutlinedTextField(
                            value = endDateStr,
                            onValueChange = { str ->
                                endDateStr = str
                                runCatching { LocalDate.parse(str) }.onSuccess { viewModel.setWindowEndDate(it) }
                            },
                            label = { Text("End Date (YYYY-MM-DD)") },
                            singleLine = true,
                            modifier = Modifier.weight(1f),
                        )
                    }
                    var endTimeStr by remember(windowEndTime) {
                        mutableStateOf(windowEndTime.format(DateTimeFormatter.ofPattern("HH:mm")))
                    }
                    OutlinedTextField(
                        value = endTimeStr,
                        onValueChange = { str ->
                            endTimeStr = str
                            runCatching { LocalTime.parse(str) }.onSuccess { viewModel.setWindowEndTime(it) }
                        },
                        label = { Text("Window End UTC (HH:MM)") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            Spacer(modifier = Modifier.height(14.dp))
            HorizontalDivider()
            Spacer(modifier = Modifier.height(12.dp))

            // Altitude & Ceiling Sliders
            Text(
                text = "Cruise Altitude: $cruiseAltitudeFt ft  •  Ceiling: $flightCeilingFt ft",
                style = MaterialTheme.typography.labelLarge,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold,
            )
            Slider(
                value = cruiseAltitudeFt.toFloat(),
                onValueChange = {
                    val stepped = ((it / 500f).roundToInt() * 500).coerceIn(1000, 30000)
                    viewModel.setCruiseAltitudeFt(stepped)
                },
                valueRange = 1000f..25000f,
            )
            Slider(
                value = flightCeilingFt.toFloat(),
                onValueChange = {
                    val stepped = ((it / 500f).roundToInt() * 500).coerceIn(cruiseAltitudeFt, 35000)
                    viewModel.setFlightCeilingFt(stepped)
                },
                valueRange = 2000f..30000f,
            )

            // Flight Duration
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "Duration: ${FlightDuration.label(viewModel.totalDurationHours)}",
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.weight(1f),
                )
                OutlinedButton(
                    onClick = {
                        val totalMins = (durationHours * 60 + durationMinutes - 15).coerceAtLeast(15)
                        viewModel.setDuration(totalMins / 60, totalMins % 60)
                    },
                ) { Text("-15m") }
                OutlinedButton(
                    onClick = {
                        val totalMins = (durationHours * 60 + durationMinutes + 15).coerceAtMost(12 * 60)
                        viewModel.setDuration(totalMins / 60, totalMins % 60)
                    },
                ) { Text("+15m") }
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Aircraft Selector
            if (aircraftList.isNotEmpty()) {
                var acExpanded by remember { mutableStateOf(false) }
                val selectedAc = aircraftList.firstOrNull { it.id == selectedAircraftId }
                ExposedDropdownMenuBox(
                    expanded = acExpanded,
                    onExpandedChange = { acExpanded = it },
                ) {
                    OutlinedTextField(
                        value = selectedAc?.displayLabel ?: "None",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Aircraft") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = acExpanded) },
                        modifier = Modifier
                            .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                            .fillMaxWidth(),
                    )
                    ExposedDropdownMenu(
                        expanded = acExpanded,
                        onDismissRequest = { acExpanded = false },
                    ) {
                        DropdownMenuItem(
                            text = { Text("No Aircraft") },
                            onClick = {
                                viewModel.selectAircraft(null)
                                acExpanded = false
                            },
                        )
                        aircraftList.forEach { ac ->
                            DropdownMenuItem(
                                text = { Text(ac.displayLabel) },
                                onClick = {
                                    viewModel.selectAircraft(ac.id)
                                    acExpanded = false
                                },
                            )
                        }
                    }
                }
                Spacer(modifier = Modifier.height(10.dp))
            }

            // Profile Selector
            if (profiles.isNotEmpty()) {
                var profExpanded by remember { mutableStateOf(false) }
                val selectedProf = profiles.firstOrNull { it.id == selectedProfileId }
                ExposedDropdownMenuBox(
                    expanded = profExpanded,
                    onExpandedChange = { profExpanded = it },
                ) {
                    OutlinedTextField(
                        value = selectedProf?.name ?: "Default Profile",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Briefing Profile") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = profExpanded) },
                        modifier = Modifier
                            .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                            .fillMaxWidth(),
                    )
                    ExposedDropdownMenu(
                        expanded = profExpanded,
                        onDismissRequest = { profExpanded = false },
                    ) {
                        profiles.forEach { prof ->
                            DropdownMenuItem(
                                text = { Text(prof.name) },
                                onClick = {
                                    viewModel.selectProfile(prof.id)
                                    profExpanded = false
                                },
                            )
                        }
                    }
                }
                Spacer(modifier = Modifier.height(10.dp))
            }

            // Trip Assignment
            var tripExpanded by remember { mutableStateOf(false) }
            val currentTripName = trips.firstOrNull { it.id == selectedTripId }?.name ?: "No Trip"
            ExposedDropdownMenuBox(
                expanded = tripExpanded,
                onExpandedChange = { tripExpanded = it },
            ) {
                OutlinedTextField(
                    value = currentTripName,
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("Assign to Trip (Optional)") },
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = tripExpanded) },
                    modifier = Modifier
                        .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                        .fillMaxWidth(),
                )
                ExposedDropdownMenu(
                    expanded = tripExpanded,
                    onDismissRequest = { tripExpanded = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("No Trip") },
                        onClick = {
                            viewModel.selectTrip(null)
                            tripExpanded = false
                        },
                    )
                    trips.forEach { trip ->
                        DropdownMenuItem(
                            text = { Text(trip.name) },
                            onClick = {
                                viewModel.selectTrip(trip.id)
                                tripExpanded = false
                            },
                        )
                    }
                }
            }

            if (selectedTripId == null) {
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = newTripName,
                    onValueChange = { viewModel.setNewTripName(it) },
                    label = { Text("Or create new trip name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            Spacer(modifier = Modifier.height(20.dp))

            Button(
                onClick = {
                    viewModel.saveFlight { saved ->
                        onFlightSaved(saved)
                    }
                },
                enabled = !isSaving && viewModel.isValid,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
            ) {
                if (isSaving) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text(if (editingId != null) "Save Changes" else "Create Flight")
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}
