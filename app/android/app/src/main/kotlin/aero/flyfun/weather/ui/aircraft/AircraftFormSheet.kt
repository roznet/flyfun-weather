package aero.flyfun.weather.ui.aircraft

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.AircraftResponse
import aero.flyfun.weather.model.rules.AircraftPerformanceForm
import aero.flyfun.weather.model.rules.PerformanceFormState
import aero.flyfun.weather.viewmodel.AircraftListViewModel
import aero.flyfun.weather.viewmodel.AircraftPerformanceFormController

/**
 * Modal bottom sheet for creating or editing an aircraft, with ICAO type search autocomplete.
 * Mirrors iOS `AircraftFormSheet`.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AircraftFormSheet(
    viewModel: AircraftListViewModel,
    existingAircraft: AircraftResponse? = null,
    onDismiss: () -> Unit,
    onSaved: (AircraftResponse) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val typeResults by viewModel.typeResults.collectAsState()
    val isSaving by viewModel.isSaving.collectAsState()
    val errorMessage by viewModel.errorMessage.collectAsState()
    val performanceForm = viewModel.performanceForm
    val performanceState by performanceForm.state.collectAsState()

    var icaoType by rememberSaveable { mutableStateOf(existingAircraft?.icaoType ?: "") }
    var tailNumber by rememberSaveable { mutableStateOf(existingAircraft?.tailNumber ?: "") }
    var nickname by rememberSaveable { mutableStateOf(existingAircraft?.nickname ?: "") }
    var cruiseSpeedText by rememberSaveable {
        mutableStateOf((existingAircraft?.cruiseSpeedKt ?: AircraftListViewModel.DEFAULT_FORM_CRUISE_SPEED_KT).toString())
    }
    var ceilingText by rememberSaveable { mutableStateOf(existingAircraft?.ceilingFt?.toString() ?: "16000") }
    var isIfr by rememberSaveable { mutableStateOf(existingAircraft?.isIfr ?: false) }
    var isFiki by rememberSaveable { mutableStateOf(existingAircraft?.isFiki ?: false) }
    var isDefault by rememberSaveable { mutableStateOf(existingAircraft?.isDefault ?: false) }

    // A preset that follows the type moves with the type and the cruise speed.
    LaunchedEffect(icaoType, cruiseSpeedText) {
        performanceForm.typeChanged(icaoType, cruiseSpeedText.toIntOrNull()?.takeIf { it > 0 })
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
        ) {
            Text(
                text = if (existingAircraft != null) "Edit Aircraft" else "Add Aircraft",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
            Spacer(modifier = Modifier.height(12.dp))

            OutlinedTextField(
                value = icaoType,
                onValueChange = {
                    icaoType = it.uppercase()
                    viewModel.searchAircraftTypes(it)
                },
                label = { Text("ICAO Type Designator (e.g. SR22, C172)") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Characters,
                ),
                modifier = Modifier.fillMaxWidth(),
            )

            if (typeResults.isNotEmpty()) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 4.dp),
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        typeResults.take(5).forEach { result ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        icaoType = result.icao
                                        result.defaultSpeedKt?.let { cruiseSpeedText = it.toString() }
                                        result.defaultCeilingFt?.let { ceilingText = it.toString() }
                                        viewModel.searchAircraftTypes("")
                                    }
                                    .padding(vertical = 6.dp, horizontal = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = result.icao,
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Bold,
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = "${result.manufacturer} ${result.model}",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                OutlinedTextField(
                    value = tailNumber,
                    onValueChange = { tailNumber = it.uppercase() },
                    label = { Text("Tail Number") },
                    placeholder = { Text("N123AB / F-GXYZ") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Characters,
                    ),
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = nickname,
                    onValueChange = { nickname = it },
                    label = { Text("Nickname") },
                    placeholder = { Text("Club Cirrus") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(modifier = Modifier.height(10.dp))

            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                OutlinedTextField(
                    value = cruiseSpeedText,
                    onValueChange = { cruiseSpeedText = it.filter { ch -> ch.isDigit() } },
                    label = { Text("Cruise Speed (kt)") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = ceilingText,
                    onValueChange = { ceilingText = it.filter { ch -> ch.isDigit() } },
                    label = { Text("Service Ceiling (ft)") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            PerformanceSection(state = performanceState, form = performanceForm)

            Spacer(modifier = Modifier.height(12.dp))

            SwitchRow(
                title = "IFR Equipped & Current",
                checked = isIfr,
                onCheckedChange = { isIfr = it },
            )
            SwitchRow(
                title = "FIKI Certified (Flight Into Known Icing)",
                checked = isFiki,
                onCheckedChange = { isFiki = it },
            )
            SwitchRow(
                title = "Default Aircraft for New Flights",
                checked = isDefault,
                onCheckedChange = { isDefault = it },
            )

            errorMessage?.let { message ->
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = message,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(modifier = Modifier.height(18.dp))

            Button(
                onClick = {
                    val speed = cruiseSpeedText.toIntOrNull()
                    val ceiling = ceilingText.toIntOrNull()
                    // Null when the presets never loaded: the saved performance stays as it is.
                    val performance = performanceForm.state.value.overrides
                    if (existingAircraft != null) {
                        viewModel.updateAircraft(
                            aircraftId = existingAircraft.id,
                            icaoType = icaoType,
                            tailNumber = tailNumber,
                            nickname = nickname,
                            cruiseSpeedKt = speed,
                            ceilingFt = ceiling,
                            isIfr = isIfr,
                            isFiki = isFiki,
                            isDefault = isDefault,
                            performance = performance,
                            onSaved = onSaved,
                        )
                    } else {
                        viewModel.createAircraft(
                            icaoType = icaoType,
                            tailNumber = tailNumber,
                            nickname = nickname,
                            cruiseSpeedKt = speed,
                            ceilingFt = ceiling,
                            isIfr = isIfr,
                            isFiki = isFiki,
                            isDefault = isDefault,
                            performance = performance,
                            onSaved = onSaved,
                        )
                    }
                },
                enabled = !isSaving && icaoType.isNotBlank(),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
            ) {
                Text(if (existingAircraft != null) "Save Aircraft" else "Add Aircraft")
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

/** The preset picker over the values it sets, as in the web aircraft form. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PerformanceSection(
    state: PerformanceFormState,
    form: AircraftPerformanceFormController,
) {
    Text(
        text = "Climb and descent",
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
    )
    Spacer(modifier = Modifier.height(8.dp))

    val base = state.base
    if (base == null) {
        Text(
            text = "Climb and descent presets aren't available right now. Saving keeps the current values.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }

    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = AircraftPerformanceForm.selectionLabel(state),
            onValueChange = {},
            readOnly = true,
            label = { Text("Preset") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            state.presets.forEach { preset ->
                DropdownMenuItem(
                    text = { Text(AircraftPerformanceForm.presetName(preset.id)) },
                    onClick = {
                        form.pick(preset.id)
                        expanded = false
                    },
                )
            }
        }
    }

    Spacer(modifier = Modifier.height(10.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        PerformanceField("Climb speed (IAS kt)", state.climbIasText, form::editClimbIas, Modifier.weight(1f))
        PerformanceField("Climb rate (ft/min)", state.climbRateText, form::editClimbRate, Modifier.weight(1f))
    }
    Spacer(modifier = Modifier.height(10.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
        PerformanceField("Descent rate (ft/min)", state.descentRateText, form::editDescentRate, Modifier.weight(1f))
        PerformanceField(
            label = "or descent angle (°)",
            value = state.descentAngleText,
            onValueChange = form::editDescentAngle,
            modifier = Modifier.weight(1f),
            keyboardType = KeyboardType.Decimal,
        )
    }
    Spacer(modifier = Modifier.height(4.dp))
    SwitchRow(title = "Turbocharged", checked = state.turbo, onCheckedChange = form::editTurbo)
    Text(
        text = AircraftPerformanceForm.hint(state),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun PerformanceField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Number,
) {
    OutlinedTextField(
        value = value,
        onValueChange = { onValueChange(AircraftPerformanceForm.sanitizeNumber(it)) },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        modifier = modifier,
    )
}

@Composable
private fun SwitchRow(
    title: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(text = title, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
