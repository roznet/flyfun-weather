package aero.flyfun.weather.ui.flights

import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.PressInteraction
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
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimeInput
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.FlightEditRules
import aero.flyfun.weather.model.FlightFlexibility
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.viewmodel.AddFlightViewModel
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Modal bottom sheet for creating, editing or duplicating a flight, mirroring iOS
 * `AddFlightSheet`. While editing, a line above Save says what the change does to the
 * briefing ([FlightEditRules.impactLine]); a change of date or route endpoints turns Save into
 * "Continue…", which offers Move or Keep both ([StructuralChoiceSheet]).
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddFlightSheet(
    viewModel: AddFlightViewModel,
    onDismiss: () -> Unit,
    onFlightSaved: (FlightResponse) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val mode by viewModel.mode.collectAsState()
    val originalFlight by viewModel.originalFlight.collectAsState()
    val existingPackCount by viewModel.existingPackCount.collectAsState()
    val showStructuralChoice by viewModel.showStructuralChoice.collectAsState()
    val keepInTripOnMove by viewModel.keepInTripOnMove.collectAsState()
    val addDuplicateToTrip by viewModel.addDuplicateToTrip.collectAsState()
    val rawRouteText by viewModel.rawRouteText.collectAsState()
    val waypoints by viewModel.waypoints.collectAsState()
    val fplText by viewModel.fplText.collectAsState()
    val airportSuggestions by viewModel.airportSuggestions.collectAsState()
    val departureDate by viewModel.departureDate.collectAsState()
    val departureTime by viewModel.departureTime.collectAsState()
    val flexibility by viewModel.flexibility.collectAsState()
    val altDepartureTime by viewModel.altDepartureTime.collectAsState()
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
                    text = when (mode) {
                        AddFlightViewModel.Mode.EDIT -> "Edit Flight"
                        AddFlightViewModel.Mode.DUPLICATE -> "Duplicate Flight"
                        AddFlightViewModel.Mode.CREATE -> "New Flight"
                    },
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
                TextButton(
                    onClick = { viewModel.fetchAutorouterSuggestions() },
                    enabled = !isFetchingRoutes,
                ) {
                    Icon(Icons.Default.Route, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(if (isFetchingRoutes) "Fetching Routes..." else "Autorouter Routes")
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
                    text = "Recent Autorouter Routes",
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
                                text = option.title,
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(
                                text = option.subtitle,
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
                val today = LocalDate.now(ZoneOffset.UTC)
                listOf(
                    "Today" to today,
                    "Tomorrow" to today.plusDays(1),
                    "+2 Days" to today.plusDays(2),
                    "+3 Days" to today.plusDays(3),
                ).forEach { (label, date) ->
                    FilterChip(
                        selected = departureDate == date,
                        onClick = { viewModel.setDepartureDate(date) },
                        label = { Text(label) },
                    )
                }
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                NativeDatePickerField(
                    label = "Date",
                    date = departureDate,
                    onDateSelected = { viewModel.setDepartureDate(it) },
                    modifier = Modifier.weight(1f),
                )
                NativeTimePickerField(
                    label = "Time (UTC)",
                    time = departureTime,
                    onTimeSelected = { viewModel.setDepartureTime(it) },
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(modifier = Modifier.height(10.dp))

            // Flexibility: what the timing scan grades besides the planned departure.
            Text(
                text = "Flexibility",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                viewModel.flexibilityOptions.forEach { option ->
                    FilterChip(
                        selected = flexibility == option,
                        onClick = { viewModel.setFlexibility(option) },
                        label = { Text(option.label) },
                    )
                }
            }

            if (flexibility == FlightFlexibility.ALTERNATE) {
                Spacer(modifier = Modifier.height(8.dp))
                NativeTimePickerField(
                    label = "Alternate departure (UTC)",
                    time = altDepartureTime,
                    onTimeSelected = { viewModel.setAltDepartureTime(it) },
                    modifier = Modifier.fillMaxWidth(),
                )
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

            Spacer(modifier = Modifier.height(8.dp))

            // Flight Duration (iOS-style Hours + Quarter-Hour Minutes dropdowns + Slider)
            FlightDurationPickerSection(
                durationHours = durationHours,
                durationMinutes = durationMinutes,
                totalDurationHours = viewModel.totalDurationHours,
                onDurationChange = { h, m -> viewModel.setDuration(h, m) },
            )

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
                            .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
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
                            .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
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

            if (viewModel.showsTripPicker) {
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
                            .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
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
            }

            Spacer(modifier = Modifier.height(20.dp))

            val impact = viewModel.editImpact
            if (impact != null) {
                Text(
                    text = if (viewModel.hasChanges) FlightEditRules.impactLine(impact) else "No changes yet.",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (impact == FlightEditRules.Impact.STRUCTURAL) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }

            Button(
                onClick = {
                    viewModel.saveFlight { saved ->
                        onFlightSaved(saved)
                    }
                },
                enabled = !isSaving && viewModel.isValid && viewModel.hasChanges,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
            ) {
                if (isSaving) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text(
                        when {
                            mode == AddFlightViewModel.Mode.CREATE -> "Create Flight"
                            mode == AddFlightViewModel.Mode.DUPLICATE -> "Create Copy"
                            impact == FlightEditRules.Impact.STRUCTURAL -> "Continue…"
                            impact == FlightEditRules.Impact.REBRIEF -> "Save & Re-brief"
                            else -> "Save Changes"
                        },
                    )
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }

    val structuralSource = originalFlight
    if (showStructuralChoice && structuralSource != null) {
        StructuralChoiceSheet(
            tripName = structuralSource.tripName,
            moveConsequence = FlightEditRules.moveConsequence(existingPackCount, viewModel.isLive()),
            keepInTripOnMove = keepInTripOnMove,
            addDuplicateToTrip = addDuplicateToTrip,
            onKeepInTripOnMoveChange = viewModel::setKeepInTripOnMove,
            onAddDuplicateToTripChange = viewModel::setAddDuplicateToTrip,
            onMove = { viewModel.moveFlight { saved -> onFlightSaved(saved) } },
            onKeepBoth = { viewModel.duplicateEditedFlight { saved -> onFlightSaved(saved) } },
            onDismiss = viewModel::dismissStructuralChoice,
        )
    }
}

/**
 * The choice a structural edit (new UTC date, origin or destination) needs: the flight id is
 * built from those, so the edit is either a Move (replace, discarding the briefings) or a new
 * flight beside the original. Two large options rather than an AlertDialog, so each can say
 * what it costs and carry its own trip default (Move keeps the trip, a copy does not join it).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StructuralChoiceSheet(
    tripName: String?,
    moveConsequence: String,
    keepInTripOnMove: Boolean,
    addDuplicateToTrip: Boolean,
    onKeepInTripOnMoveChange: (Boolean) -> Unit,
    onAddDuplicateToTripChange: (Boolean) -> Unit,
    onMove: () -> Unit,
    onKeepBoth: () -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "This becomes a different flight",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = "The date or the route's departure / destination changed, and those identify a flight.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            StructuralOption(
                title = "Move this flight",
                detail = moveConsequence,
                destructive = true,
                onClick = onMove,
            ) {
                if (tripName != null) {
                    TripCheckboxRow(
                        label = "Keep in trip \u201c$tripName\u201d",
                        checked = keepInTripOnMove,
                        onCheckedChange = onKeepInTripOnMoveChange,
                    )
                }
            }

            StructuralOption(
                title = "Keep both",
                detail = "Creates a new flight; the original and its briefings stay as they are.",
                destructive = false,
                onClick = onKeepBoth,
            ) {
                if (tripName != null) {
                    TripCheckboxRow(
                        label = "Add the new flight to \u201c$tripName\u201d",
                        checked = addDuplicateToTrip,
                        onCheckedChange = onAddDuplicateToTripChange,
                    )
                }
            }

            TextButton(onClick = onDismiss, modifier = Modifier.align(Alignment.End)) {
                Text("Cancel")
            }
            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}

@Composable
private fun StructuralOption(
    title: String,
    detail: String,
    destructive: Boolean,
    onClick: () -> Unit,
    extra: @Composable () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (destructive) {
                MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.35f)
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
        ),
    ) {
        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                text = detail,
                style = MaterialTheme.typography.bodySmall,
            )
            extra()
            if (destructive) {
                Button(
                    onClick = onClick,
                    colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                        contentColor = MaterialTheme.colorScheme.onError,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(title) }
            } else {
                OutlinedButton(onClick = onClick, modifier = Modifier.fillMaxWidth()) { Text(title) }
            }
        }
    }
}

@Composable
private fun TripCheckboxRow(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onCheckedChange(!checked) },
    ) {
        Checkbox(checked = checked, onCheckedChange = onCheckedChange)
        Text(text = label, style = MaterialTheme.typography.bodySmall)
    }
}

private val DISPLAY_DATE_FORMATTER: DateTimeFormatter =
    DateTimeFormatter.ofPattern("EEE, d MMM yyyy", Locale.US)
private val DISPLAY_TIME_FORMATTER: DateTimeFormatter =
    DateTimeFormatter.ofPattern("HH:mm", Locale.US)

/**
 * Interaction source for a read-only text field that reports a tap anywhere on the field.
 */
@Composable
private fun rememberTapInteractionSource(onTap: () -> Unit): MutableInteractionSource {
    val interactionSource = remember { MutableInteractionSource() }
    val currentOnTap by rememberUpdatedState(onTap)
    LaunchedEffect(interactionSource) {
        interactionSource.interactions.collect { interaction ->
            if (interaction is PressInteraction.Release) {
                currentOnTap()
            }
        }
    }
    return interactionSource
}

/**
 * Read-only tappable field that opens a native Material 3 [DatePickerDialog] calendar widget.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NativeDatePickerField(
    label: String,
    date: LocalDate,
    onDateSelected: (LocalDate) -> Unit,
    modifier: Modifier = Modifier,
) {
    var showDialog by remember { mutableStateOf(false) }
    val interactionSource = rememberTapInteractionSource { showDialog = true }

    OutlinedTextField(
        value = date.format(DISPLAY_DATE_FORMATTER),
        onValueChange = {},
        readOnly = true,
        singleLine = true,
        label = { Text(label) },
        trailingIcon = {
            IconButton(onClick = { showDialog = true }) {
                Icon(Icons.Default.CalendarMonth, contentDescription = "Pick $label")
            }
        },
        interactionSource = interactionSource,
        modifier = modifier,
    )

    if (showDialog) {
        val initialMillis = remember(date) {
            date.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        }
        val datePickerState = rememberDatePickerState(initialSelectedDateMillis = initialMillis)

        DatePickerDialog(
            onDismissRequest = { showDialog = false },
            confirmButton = {
                TextButton(
                    onClick = {
                        datePickerState.selectedDateMillis?.let { millis ->
                            val picked = Instant.ofEpochMilli(millis)
                                .atZone(ZoneOffset.UTC)
                                .toLocalDate()
                            onDateSelected(picked)
                        }
                        showDialog = false
                    },
                ) {
                    Text("OK")
                }
            },
            dismissButton = {
                TextButton(onClick = { showDialog = false }) {
                    Text("Cancel")
                }
            },
        ) {
            DatePicker(state = datePickerState)
        }
    }
}

/**
 * Read-only tappable field that opens a native Material 3 24-hour [TimePicker] dialog
 * (with a clock-dial / keyboard toggle and quick quarter-hour chips).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NativeTimePickerField(
    label: String,
    time: LocalTime,
    onTimeSelected: (LocalTime) -> Unit,
    modifier: Modifier = Modifier,
) {
    var showDialog by remember { mutableStateOf(false) }
    var useKeyboardInput by remember { mutableStateOf(false) }
    val interactionSource = rememberTapInteractionSource { showDialog = true }

    OutlinedTextField(
        value = "${time.format(DISPLAY_TIME_FORMATTER)} UTC",
        onValueChange = {},
        readOnly = true,
        singleLine = true,
        label = { Text(label) },
        trailingIcon = {
            IconButton(onClick = { showDialog = true }) {
                Icon(Icons.Default.Schedule, contentDescription = "Pick $label")
            }
        },
        interactionSource = interactionSource,
        modifier = modifier,
    )

    if (showDialog) {
        val timePickerState = rememberTimePickerState(
            initialHour = time.hour,
            initialMinute = time.minute,
            is24Hour = true,
        )

        AlertDialog(
            onDismissRequest = { showDialog = false },
            title = {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.titleMedium,
                    )
                    IconButton(onClick = { useKeyboardInput = !useKeyboardInput }) {
                        Icon(
                            imageVector = if (useKeyboardInput) Icons.Default.Schedule else Icons.Default.Keyboard,
                            contentDescription = if (useKeyboardInput) "Switch to clock dial" else "Switch to keyboard input",
                        )
                    }
                }
            },
            text = {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (useKeyboardInput) {
                        TimeInput(state = timePickerState)
                    } else {
                        TimePicker(state = timePickerState)
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    // Quick quarter-hour chips for fast aviation time entry
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        FlightDuration.minuteOptions.forEach { minOpt ->
                            FilterChip(
                                selected = timePickerState.minute == minOpt,
                                onClick = {
                                    timePickerState.minute = minOpt
                                },
                                label = { Text(":%02d".format(minOpt)) },
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        onTimeSelected(LocalTime.of(timePickerState.hour, timePickerState.minute))
                        showDialog = false
                    },
                ) {
                    Text("OK")
                }
            },
            dismissButton = {
                TextButton(onClick = { showDialog = false }) {
                    Text("Cancel")
                }
            },
        )
    }
}

/**
 * Flight duration section combining iOS-style Hours (`0..12 h`) + Quarter-Hour Minutes
 * (`00, 15, 30, 45 m`) dropdown menus with a synchronized quarter-hour slider.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FlightDurationPickerSection(
    durationHours: Int,
    durationMinutes: Int,
    totalDurationHours: Double,
    onDurationChange: (hours: Int, minutes: Int) -> Unit,
) {
    var hoursExpanded by remember { mutableStateOf(false) }
    var minutesExpanded by remember { mutableStateOf(false) }
    val snappedMinutes = remember(durationMinutes) {
        FlightDuration.minuteOptions.minByOrNull { kotlin.math.abs(it - durationMinutes) } ?: 0
    }

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                text = "Duration: ${FlightDuration.label(totalDurationHours)}",
                style = MaterialTheme.typography.labelLarge,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )

            // Hours dropdown (0h .. 12h)
            ExposedDropdownMenuBox(
                expanded = hoursExpanded,
                onExpandedChange = { hoursExpanded = it },
                modifier = Modifier.width(104.dp),
            ) {
                OutlinedTextField(
                    value = "${durationHours}h",
                    onValueChange = {},
                    readOnly = true,
                    singleLine = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = hoursExpanded) },
                    modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable),
                )
                ExposedDropdownMenu(
                    expanded = hoursExpanded,
                    onDismissRequest = { hoursExpanded = false },
                ) {
                    (0..FlightDuration.maxHours).forEach { h ->
                        DropdownMenuItem(
                            text = { Text("${h} h") },
                            onClick = {
                                val nextMin = if (h == 0 && snappedMinutes == 0) 15 else snappedMinutes
                                onDurationChange(h, nextMin)
                                hoursExpanded = false
                            },
                        )
                    }
                }
            }

            // Quarter-hour minutes dropdown (00m, 15m, 30m, 45m)
            ExposedDropdownMenuBox(
                expanded = minutesExpanded,
                onExpandedChange = { minutesExpanded = it },
                modifier = Modifier.width(108.dp),
            ) {
                OutlinedTextField(
                    value = "%02dm".format(snappedMinutes),
                    onValueChange = {},
                    readOnly = true,
                    singleLine = true,
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = minutesExpanded) },
                    modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable),
                )
                ExposedDropdownMenu(
                    expanded = minutesExpanded,
                    onDismissRequest = { minutesExpanded = false },
                ) {
                    FlightDuration.minuteOptions.forEach { m ->
                        val enabled = !(durationHours == 0 && m == 0)
                        DropdownMenuItem(
                            text = { Text("%02d min".format(m)) },
                            enabled = enabled,
                            onClick = {
                                onDurationChange(durationHours, m)
                                minutesExpanded = false
                            },
                        )
                    }
                }
            }
        }

        // Quarter-hour slider (0h 15m .. 12h 00m in 15-minute increments)
        val totalQuarterHours = (durationHours * 4 + snappedMinutes / 15).coerceIn(1, FlightDuration.maxHours * 4)
        Slider(
            value = totalQuarterHours.toFloat(),
            onValueChange = { rawQuarters ->
                val q = rawQuarters.roundToInt().coerceIn(1, FlightDuration.maxHours * 4)
                onDurationChange(q / 4, (q % 4) * 15)
            },
            valueRange = 1f..(FlightDuration.maxHours * 4).toFloat(),
        )
    }
}
