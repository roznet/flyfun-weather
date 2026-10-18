package aero.flyfun.weather.ui.skewt

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
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.HelpOutline
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.rzskewt.Thermodynamics
import aero.flyfun.weather.model.SoundingProfileResponse
import aero.flyfun.weather.model.ThermodynamicIndices
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Expandable/inline Skew-T log-P detail section card rendered below the Cross-Section / Route Graph.
 *
 * Includes:
 * - Route point stepper (`◀ Pt N (ICAO) ▶`), `Dynamic` vs `Compare Models` mode toggle, and `"Help me read this graph"` dialog
 * - Key thermodynamic indices pill strip (`CAPE`, `CIN`, `LI`, `PW`, `0°C`, `LCL`, `LFC`, `EL`)
 * - Overlay band toggle chips + Primary/Secondary side-panel variable dropdown selectors
 * - Interactive [SkewTCanvas]
 */
@Composable
fun SkewTDetailSection(
    profile: SoundingProfileResponse?,
    compareProfiles: Map<String, SoundingProfileResponse> = emptyMap(),
    waypoints: List<String>,
    selectedPointIndex: Int,
    totalPoints: Int,
    selectedModel: String,
    availableModels: List<String>,
    cruiseAltitudeFt: Int?,
    externalHoverAltitudeFt: Double? = null,
    activePresetInterpretation: String? = null,
    onSelectPointIndex: (Int) -> Unit,
    onSelectModel: (String) -> Unit,
    onHoverAltitudeChanged: ((Double?) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    var isCompareMode by rememberSaveable { mutableStateOf(false) }
    var enabledOverlays by remember {
        mutableStateOf(SkewTOverlayBand.defaultEnabledIds())
    }
    var primaryVariableId by rememberSaveable { mutableStateOf("hw_xw") }
    var secondaryVariableId by rememberSaveable { mutableStateOf<String?>(null) }
    var showHelpDialog by rememberSaveable { mutableStateOf(false) }

    val pointCount = totalPoints.coerceAtLeast(1)
    val pointTitle = remember(profile, waypoints, selectedPointIndex) {
        val icao = profile?.waypointIcao?.takeIf { it.isNotBlank() }
            ?: waypoints.getOrNull(selectedPointIndex)?.takeIf { it.isNotBlank() }
        val distStr = profile?.distanceFromOriginNm?.let { "${it.roundToInt()} nm" }
        buildString {
            append("Pt ${selectedPointIndex + 1}")
            if (icao != null) append(" ($icao)")
            else if (distStr != null) append(" ($distStr)")
        }
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // 1. Header Bar: Point Selector, Mode Toggle, Help Button
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                // Route Point Stepper: ◀ Pt N (ICAO) ▶
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    IconButton(
                        onClick = {
                            if (selectedPointIndex > 0) onSelectPointIndex(selectedPointIndex - 1)
                        },
                        enabled = selectedPointIndex > 0,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowLeft,
                            contentDescription = "Previous route point",
                        )
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "Sounding",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = pointTitle,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    IconButton(
                        onClick = {
                            if (selectedPointIndex < pointCount - 1) onSelectPointIndex(selectedPointIndex + 1)
                        },
                        enabled = selectedPointIndex < pointCount - 1,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                            contentDescription = "Next route point",
                        )
                    }
                }

                // Dynamic vs Compare Models toggle + Help Button
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (availableModels.size > 1 || compareProfiles.size > 1) {
                        FilterChip(
                            selected = !isCompareMode,
                            onClick = { isCompareMode = false },
                            label = { Text("Dynamic", style = MaterialTheme.typography.labelSmall) },
                        )
                        FilterChip(
                            selected = isCompareMode,
                            onClick = { isCompareMode = true },
                            label = { Text("Compare Models", style = MaterialTheme.typography.labelSmall) },
                        )
                    }

                    TextButton(
                        onClick = { showHelpDialog = true },
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp),
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.HelpOutline,
                            contentDescription = "Help me read this graph",
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = "Help me read this graph",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }

            // Model selector row when multiple models exist
            if (availableModels.size > 1) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        text = if (isCompareMode) "Primary model:" else "Model:",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    availableModels.forEach { model ->
                        val isSelected = model.equals(selectedModel, ignoreCase = true)
                        FilterChip(
                            selected = isSelected,
                            onClick = { onSelectModel(model) },
                            label = {
                                Text(
                                    text = model.uppercase(Locale.US) + if (isCompareMode && isSelected) " ★" else "",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                )
                            },
                        )
                    }
                }
            }

            if (profile == null) {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(220.dp),
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(
                            text = "Select a route point on the cross-section to load its Skew-T sounding.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else {
                // 2. Key Thermodynamic Indices Pill Strip
                profile.indices?.let { indices ->
                    ThermodynamicIndicesStrip(indices = indices)
                }

                // 3. Overlay Band Toggle Chips (hidden in Compare Mode per skewt-canvas.md)
                if (!isCompareMode) {
                    OverlayBandsChipRow(
                        enabledOverlays = enabledOverlays,
                        onToggleOverlay = { bandId ->
                            enabledOverlays = if (bandId in enabledOverlays) {
                                enabledOverlays - bandId
                            } else {
                                enabledOverlays + bandId
                            }
                        },
                    )
                }

                // 4. Primary / Secondary Side-Panel Variable Selectors
                SidePanelVariableSelectorRow(
                    primaryVariableId = primaryVariableId,
                    secondaryVariableId = secondaryVariableId,
                    onSelectPrimary = { primaryVariableId = it },
                    onSelectSecondary = { secondaryVariableId = it },
                )

                // 5. Skew-T Canvas
                SkewTCanvas(
                    profile = profile,
                    compareProfiles = if (compareProfiles.isNotEmpty()) {
                        compareProfiles
                    } else {
                        mapOf(profile.model to profile)
                    },
                    isCompareMode = isCompareMode,
                    enabledOverlays = enabledOverlays,
                    primaryVariableId = primaryVariableId,
                    secondaryVariableId = secondaryVariableId,
                    cruiseAltitudeFt = cruiseAltitudeFt ?: profile.cruiseAltitudeFt,
                    externalHoverAltitudeFt = externalHoverAltitudeFt,
                    onHoverAltitudeChanged = onHoverAltitudeChanged,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(390.dp),
                )
            }
        }
    }

    if (showHelpDialog) {
        SkewTReadingHelpDialog(
            profile = profile,
            activePresetInterpretation = activePresetInterpretation,
            enabledOverlays = enabledOverlays,
            onDismiss = { showHelpDialog = false },
        )
    }
}

@Composable
private fun ThermodynamicIndicesStrip(
    indices: ThermodynamicIndices,
    modifier: Modifier = Modifier,
) {
    val pills = remember(indices) {
        buildList {
            indices.capeSurfaceJkg?.let { add("CAPE" to "${it.roundToInt()} J/kg") }
            indices.cinSurfaceJkg?.let { add("CIN" to "${it.roundToInt()} J/kg") }
            indices.liftedIndex?.let { add("LI" to String.format(Locale.US, "%.1f", it)) }
            indices.precipitableWaterMm?.let { add("PW" to String.format(Locale.US, "%.1f mm", it)) }
            indices.freezingLevelFt?.let { add("0°C" to formatAltitudeOrFl(it)) }
            val lclFt = indices.lclAltitudeFt ?: indices.lclPressureHpa?.let { Thermodynamics.pressureToAltitudeFt(it) }
            lclFt?.let { add("LCL" to formatAltitudeOrFl(it)) }
            val lfcFt = indices.lfcAltitudeFt ?: indices.lfcPressureHpa?.let { Thermodynamics.pressureToAltitudeFt(it) }
            lfcFt?.let { add("LFC" to formatAltitudeOrFl(it)) }
            val elFt = indices.elAltitudeFt ?: indices.elPressureHpa?.let { Thermodynamics.pressureToAltitudeFt(it) }
            elFt?.let { add("EL" to formatAltitudeOrFl(it)) }
        }
    }

    if (pills.isEmpty()) return

    Row(
        modifier = modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        pills.forEach { (label, value) ->
            Surface(
                shape = RoundedCornerShape(8.dp),
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.65f),
                border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = value,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
    }
}

@Composable
private fun OverlayBandsChipRow(
    enabledOverlays: Set<String>,
    onToggleOverlay: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Bands:",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        SkewTOverlayBand.entries.forEach { band ->
            val selected = band.isEnabledIn(enabledOverlays)
            FilterChip(
                selected = selected,
                onClick = { onToggleOverlay(band.id) },
                label = {
                    Text(
                        text = band.label,
                        style = MaterialTheme.typography.labelSmall,
                    )
                },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                    selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
                ),
            )
        }
    }
}

@Composable
private fun SidePanelVariableSelectorRow(
    primaryVariableId: String,
    secondaryVariableId: String?,
    onSelectPrimary: (String) -> Unit,
    onSelectSecondary: (String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val primaryDef = SkewTVariableCatalog.findById(primaryVariableId) ?: SkewTVariableCatalog.ALL_VARIABLES.first()
    val secondaryDef = secondaryVariableId?.let { SkewTVariableCatalog.findById(it) }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Side panel:",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        VariableDropdownMenuButton(
            prefix = "1st",
            selected = primaryDef,
            allowNone = false,
            onSelect = { def -> if (def != null) onSelectPrimary(def.id) },
        )

        VariableDropdownMenuButton(
            prefix = "2nd",
            selected = secondaryDef,
            allowNone = true,
            onSelect = { def -> onSelectSecondary(def?.id) },
        )
    }
}

@Composable
private fun VariableDropdownMenuButton(
    prefix: String,
    selected: SkewTVariableDef?,
    allowNone: Boolean,
    onSelect: (SkewTVariableDef?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val grouped = remember { SkewTVariableCatalog.groupedVariables() }

    Box {
        Surface(
            shape = RoundedCornerShape(8.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.65f),
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .clickable { expanded = true },
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = "$prefix:",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (selected != null) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(Color(selected.colorHex)),
                    )
                    Text(
                        text = "${selected.shortLabel} — ${selected.label}",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                } else {
                    Text(
                        text = "None",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Icon(
                    imageVector = Icons.Default.ArrowDropDown,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
            }
        }

        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            if (allowNone) {
                DropdownMenuItem(
                    text = { Text("None", style = MaterialTheme.typography.bodySmall) },
                    onClick = {
                        onSelect(null)
                        expanded = false
                    },
                )
                HorizontalDivider()
            }
            grouped.forEach { (group, vars) ->
                DropdownMenuItem(
                    text = {
                        Text(
                            text = group.title.uppercase(Locale.US),
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    },
                    onClick = {},
                    enabled = false,
                )
                vars.forEach { v ->
                    val unitPart = if (v.unit.isNotEmpty()) " (${v.unit})" else ""
                    DropdownMenuItem(
                        text = {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(8.dp)
                                        .clip(CircleShape)
                                        .background(Color(v.colorHex)),
                                )
                                Text(
                                    text = "${v.shortLabel} — ${v.label}$unitPart",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        },
                        onClick = {
                            onSelect(v)
                            expanded = false
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun SkewTReadingHelpDialog(
    profile: SoundingProfileResponse?,
    activePresetInterpretation: String?,
    enabledOverlays: Set<String>,
    onDismiss: () -> Unit,
) {
    val indices = profile?.indices
    val activeBands = SkewTOverlayBand.entries
        .filter { it.isEnabledIn(enabledOverlays) }
        .joinToString(", ") { it.label }
        .ifEmpty { "None" }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "How to Read This Skew-T",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    text = activePresetInterpretation?.takeIf { it.isNotBlank() }
                        ?: "The red curve is environmental temperature (T) and the blue curve is dewpoint (Td). " +
                        "Where T and Td pinch together, relative humidity is high and cloud is likely. " +
                        "The dashed amber curve traces a lifted surface parcel: red shading (CAPE) between LFC and EL " +
                        "indicates positive buoyant energy for convective updrafts, while blue shading (CIN) marks a " +
                        "capping inversion below LFC.",
                    style = MaterialTheme.typography.bodySmall,
                )

                if (indices != null) {
                    HorizontalDivider()
                    Text(
                        text = "Key Sounding Values",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    val summaryLines = buildList {
                        indices.capeSurfaceJkg?.let { add("CAPE: ${it.roundToInt()} J/kg") }
                        indices.cinSurfaceJkg?.let { add("CIN: ${it.roundToInt()} J/kg") }
                        indices.freezingLevelFt?.let { add("Freezing level (0°C): ${formatAltitudeOrFl(it)}") }
                        indices.lclAltitudeFt?.let { add("LCL (cloud base): ${formatAltitudeOrFl(it)}") }
                        indices.lfcAltitudeFt?.let { add("LFC (free convection): ${formatAltitudeOrFl(it)}") }
                        indices.elAltitudeFt?.let { add("EL (convective top): ${formatAltitudeOrFl(it)}") }
                    }
                    summaryLines.forEach { line ->
                        Text(text = "• $line", style = MaterialTheme.typography.bodySmall)
                    }
                }

                HorizontalDivider()
                Text(
                    text = "Shaded bands: $activeBands",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Got it")
            }
        },
    )
}

private fun formatAltitudeOrFl(altFt: Double): String {
    return if (altFt >= 10000.0) {
        String.format(Locale.US, "FL%03d", (altFt / 100.0).roundToInt())
    } else {
        "${(altFt / 100.0).roundToInt() * 100} ft"
    }
}
