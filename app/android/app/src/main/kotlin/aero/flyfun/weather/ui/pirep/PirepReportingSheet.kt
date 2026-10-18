package aero.flyfun.weather.ui.pirep

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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.PirepOfflineStore
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.PirepResponse
import aero.flyfun.weather.model.SubmitPirepRequest
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import kotlinx.coroutines.launch
import java.util.Locale
import java.util.UUID

/**
 * Turbulence intensity option for cockpit one-tap PIREP entry.
 */
enum class TurbulenceOption(val wireValue: String, val label: String, val badgeColor: Color) {
    NONE("none", "None", AssessmentGreen),
    LIGHT("light", "Light", AssessmentGreen),
    MODERATE("moderate", "Moderate", AssessmentAmber),
    SEVERE("severe", "Severe", AssessmentRed),
}

/**
 * Icing intensity option for cockpit one-tap PIREP entry.
 */
enum class IcingOption(val wireValue: String, val label: String, val badgeColor: Color) {
    NONE("none", "None", AssessmentGreen),
    TRACE("trace", "Trace", AssessmentGreen),
    LIGHT("light", "Light", AssessmentAmber),
    MODERATE("moderate", "Moderate", AssessmentAmber),
    SEVERE("severe", "Severe", AssessmentRed),
}

/**
 * Observed weather phenomenon chip option for PIREP entry.
 */
enum class PhenomenonOption(val wireValue: String, val label: String) {
    SMOOTH("smooth", "Smooth"),
    RAIN("rain", "Rain"),
    SNOW("snow", "Snow"),
    CB("cb", "CB"),
    TS("ts", "TS"),
    FOG_MIST("fg_br", "Fog/Mist"),
    HAZE("hz", "Haze"),
}

/**
 * Cockpit-friendly Material 3 modal bottom sheet for submitting a Pilot Weather Report (PIREP).
 *
 * Supports one-tap turbulence/icing/phenomena chips, altitude and cloud base/top steppers,
 * in-cloud toggle, auto-filled GPS coordinates, and automatic offline queueing via [PirepOfflineStore].
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun PirepReportingSheet(
    flightId: String? = null,
    initialLat: Double? = null,
    initialLon: Double? = null,
    initialAltitudeFt: Int? = null,
    aircraftType: String? = null,
    repository: BriefingRepository? = null,
    apiClient: ApiClient? = null,
    offlineStore: PirepOfflineStore? = null,
    onSubmitted: (PirepResponse?) -> Unit = {},
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val effectiveOfflineStore = remember(offlineStore, context) {
        offlineStore ?: PirepOfflineStore(context.filesDir)
    }
    val pendingQueue by effectiveOfflineStore.pendingFlow.collectAsState()

    var selectedTurbulence by remember { mutableStateOf<TurbulenceOption?>(null) }
    var selectedIcing by remember { mutableStateOf<IcingOption?>(null) }
    var selectedPhenomena by remember { mutableStateOf(setOf<PhenomenonOption>()) }
    var inCloud by remember { mutableStateOf(false) }

    var reportedAltitudeFt by remember {
        mutableIntStateOf(((initialAltitudeFt ?: 4500) / 500) * 500)
    }
    var includeCloudBase by remember { mutableStateOf(false) }
    var cloudBaseFt by remember { mutableIntStateOf(3000) }
    var includeCloudTop by remember { mutableStateOf(false) }
    var cloudTopFt by remember { mutableIntStateOf(7000) }

    var remarks by remember { mutableStateOf("") }
    var isSubmitting by remember { mutableStateOf(false) }
    var statusBanner by remember { mutableStateOf<String?>(null) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    val hasAnyObservation = selectedTurbulence != null ||
        selectedIcing != null ||
        selectedPhenomena.isNotEmpty() ||
        inCloud ||
        includeCloudBase ||
        includeCloudTop ||
        remarks.isNotBlank()

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = "Report PIREP",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                    val gpsLine = if (initialLat != null && initialLon != null) {
                        String.format(Locale.US, "GPS %.3f°, %.3f°", initialLat, initialLon)
                    } else {
                        "Route / manual position"
                    }
                    Text(
                        text = gpsLine,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                if (pendingQueue.isNotEmpty()) {
                    AssistChip(
                        onClick = {
                            val sub = buildSubmitter(repository, apiClient)
                            if (sub != null && !isSubmitting) {
                                isSubmitting = true
                                scope.launch {
                                    val res = effectiveOfflineStore.flushAll(sub)
                                    isSubmitting = false
                                    statusBanner = "Flushed ${res.succeeded.size} queued report(s); ${res.remaining.size} pending."
                                }
                            }
                        },
                        label = { Text("${pendingQueue.size} Queued") },
                    )
                }
            }

            // Altitude stepper
            AltitudeStepperRow(
                label = "Altitude",
                altitudeFt = reportedAltitudeFt,
                onAdjust = { delta ->
                    reportedAltitudeFt = (reportedAltitudeFt + delta).coerceIn(0, 45_000)
                },
            )

            HorizontalDivider()

            // Turbulence section
            Text(
                text = "Turbulence",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                TurbulenceOption.entries.forEach { option ->
                    val isSelected = selectedTurbulence == option
                    FilterChip(
                        selected = isSelected,
                        onClick = {
                            selectedTurbulence = if (isSelected) null else option
                        },
                        label = { Text(option.label) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = option.badgeColor.copy(alpha = 0.22f),
                        ),
                    )
                }
            }

            // Icing section
            Text(
                text = "Icing",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                IcingOption.entries.forEach { option ->
                    val isSelected = selectedIcing == option
                    FilterChip(
                        selected = isSelected,
                        onClick = {
                            selectedIcing = if (isSelected) null else option
                        },
                        label = { Text(option.label) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = option.badgeColor.copy(alpha = 0.22f),
                        ),
                    )
                }
            }

            // In-cloud toggle + Cloud Base / Top steppers
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "In Cloud (IMC)",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                )
                Switch(
                    checked = inCloud,
                    onCheckedChange = { inCloud = it },
                )
            }

            OptionalAltitudeStepperRow(
                label = "Cloud Base",
                enabled = includeCloudBase,
                altitudeFt = cloudBaseFt,
                onToggle = { includeCloudBase = it },
                onAdjust = { delta ->
                    cloudBaseFt = (cloudBaseFt + delta).coerceIn(0, 45_000)
                },
            )

            OptionalAltitudeStepperRow(
                label = "Cloud Top",
                enabled = includeCloudTop,
                altitudeFt = cloudTopFt,
                onToggle = { includeCloudTop = it },
                onAdjust = { delta ->
                    cloudTopFt = (cloudTopFt + delta).coerceIn(0, 45_000)
                },
            )

            HorizontalDivider()

            // Weather phenomena chips
            Text(
                text = "Weather Phenomena",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                PhenomenonOption.entries.forEach { phenom ->
                    val isSelected = phenom in selectedPhenomena
                    FilterChip(
                        selected = isSelected,
                        onClick = {
                            selectedPhenomena = if (isSelected) {
                                selectedPhenomena - phenom
                            } else {
                                selectedPhenomena + phenom
                            }
                        },
                        label = { Text(phenom.label) },
                    )
                }
            }

            // Remarks field
            OutlinedTextField(
                value = remarks,
                onValueChange = { remarks = it },
                label = { Text("Remarks (optional)") },
                placeholder = { Text("e.g. Smooth above FL065, scattered CU below") },
                minLines = 2,
                maxLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )

            if (statusBanner != null) {
                Surface(
                    color = MaterialTheme.colorScheme.secondaryContainer,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = statusBanner ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(10.dp),
                    )
                }
            }

            if (errorMessage != null) {
                Text(
                    text = errorMessage ?: "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            // Submit & Cancel Actions
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(
                    onClick = onDismiss,
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Cancel")
                }

                Button(
                    onClick = {
                        isSubmitting = true
                        errorMessage = null
                        statusBanner = null

                        val nowMs = System.currentTimeMillis()
                        val combinedRemarks = buildRemarks(
                            phenomena = selectedPhenomena,
                            freeText = remarks,
                        )
                        val request = SubmitPirepRequest(
                            clientUuid = UUID.randomUUID().toString(),
                            flightId = flightId,
                            observedAt = IsoTime.formatUtc(nowMs),
                            latitude = initialLat ?: 0.0,
                            longitude = initialLon ?: 0.0,
                            gpsAltitudeFt = initialAltitudeFt,
                            reportedAltitudeFt = reportedAltitudeFt,
                            inCloud = inCloud,
                            icingIntensity = selectedIcing?.wireValue,
                            turbulenceIntensity = selectedTurbulence?.wireValue,
                            cloudBaseFt = if (includeCloudBase) cloudBaseFt else null,
                            cloudTopFt = if (includeCloudTop) cloudTopFt else null,
                            remarks = combinedRemarks,
                            aircraftType = aircraftType,
                            source = "manual",
                        )

                        scope.launch {
                            val submitter = buildSubmitter(repository, apiClient)
                            if (submitter == null) {
                                effectiveOfflineStore.enqueue(request, nowEpochMs = nowMs)
                                isSubmitting = false
                                onSubmitted(null)
                                onDismiss()
                            } else {
                                runCatching {
                                    effectiveOfflineStore.submitOrEnqueue(
                                        request = request,
                                        nowEpochMs = nowMs,
                                        submitter = submitter,
                                    )
                                }.onSuccess { response ->
                                    isSubmitting = false
                                    onSubmitted(response)
                                    onDismiss()
                                }.onFailure { err ->
                                    isSubmitting = false
                                    errorMessage = err.message ?: "Failed to submit PIREP"
                                }
                            }
                        }
                    },
                    enabled = !isSubmitting && hasAnyObservation,
                    modifier = Modifier.weight(2f),
                ) {
                    if (isSubmitting) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Text("Submit PIREP")
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}

@Composable
private fun AltitudeStepperRow(
    label: String,
    altitudeFt: Int,
    onAdjust: (Int) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column {
            Text(
                text = label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                text = "$altitudeFt ft (FL${String.format(Locale.US, "%03d", altitudeFt / 100)})",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton(onClick = { onAdjust(-500) }) {
                Text("-500")
            }
            OutlinedButton(onClick = { onAdjust(500) }) {
                Text("+500")
            }
        }
    }
}

@Composable
private fun OptionalAltitudeStepperRow(
    label: String,
    enabled: Boolean,
    altitudeFt: Int,
    onToggle: (Boolean) -> Unit,
    onAdjust: (Int) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            FilterChip(
                selected = enabled,
                onClick = { onToggle(!enabled) },
                label = {
                    Text(
                        if (enabled) "$label: $altitudeFt ft" else "$label: Not set",
                    )
                },
            )
        }
        if (enabled) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                OutlinedButton(onClick = { onAdjust(-500) }) {
                    Text("-500")
                }
                Spacer(modifier = Modifier.width(2.dp))
                OutlinedButton(onClick = { onAdjust(500) }) {
                    Text("+500")
                }
            }
        }
    }
}

private fun buildSubmitter(
    repository: BriefingRepository?,
    apiClient: ApiClient?,
): (suspend (SubmitPirepRequest) -> PirepResponse)? = when {
    repository != null -> { req -> repository.submitPirep(req) }
    apiClient != null -> { req -> apiClient.submitPirep(req) }
    else -> null
}

private fun buildRemarks(
    phenomena: Set<PhenomenonOption>,
    freeText: String,
): String? {
    val tags = phenomena.map { it.label }
    val trimmed = freeText.trim()
    return when {
        tags.isEmpty() && trimmed.isEmpty() -> null
        tags.isEmpty() -> trimmed
        trimmed.isEmpty() -> "WX: ${tags.joinToString(", ")}"
        else -> "WX: ${tags.joinToString(", ")} — $trimmed"
    }
}
