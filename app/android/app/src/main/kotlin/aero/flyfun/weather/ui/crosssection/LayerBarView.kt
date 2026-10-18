package aero.flyfun.weather.ui.crosssection

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import aero.flyfun.weather.model.viz.CloudStyle
import aero.flyfun.weather.model.viz.ColorScales
import aero.flyfun.weather.model.viz.CrossSectionLayerDef
import aero.flyfun.weather.model.viz.CrossSectionPresets
import aero.flyfun.weather.model.viz.CrossSectionThemes
import aero.flyfun.weather.model.viz.LayerFamily

/**
 * 3-mode responsive layer bar for the Cross-Section visualization:
 * - [LensMenusRow]: Focus dropdown + Emulate dropdown + Theme picker + Gear icon opening [CrossSectionConfigSheet].
 * - [FamilyPillsRow]: 5 compact pills (`Clouds`, `Icing`, `Turbulence`, `Convection`, `Lines`) showing active state and method badge.
 * - [FamilyDetailRow]: Animated expandable detail strip when a family pill is tapped (method radio chips,
 *   `Square`/`Soft`/`Natural` cloud style selector for Clouds, line toggles for Lines, and `ⓘ` About dialog).
 */
@Composable
fun LayerBarView(
    enabledLayers: Map<String, Boolean>,
    effectiveLayers: Map<String, Boolean>,
    unavailableLayers: Set<String>,
    substitutedLayers: Set<String>,
    cloudStyle: CloudStyle,
    userThemeId: String,
    activeEmulationId: String?,
    activePresetId: String?,
    expandedFamily: LayerFamily?,
    onApplyAdvisoryPreset: (String) -> Unit,
    onApplyEmulation: (String?) -> Unit,
    onSetUserTheme: (String) -> Unit,
    onToggleExpandedFamily: (LayerFamily) -> Unit,
    onSelectFamilyMethod: (LayerFamily, String) -> Unit,
    onSetCloudStyle: (CloudStyle) -> Unit,
    onToggleLayer: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var showConfigSheet by remember { mutableStateOf(false) }
    var aboutFamily by remember { mutableStateOf<LayerFamily?>(null) }

    BoxWithConstraints(modifier = modifier.fillMaxWidth()) {
        val isWide = maxWidth >= 680.dp

        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (isWide) {
                    // Wide (tablet/landscape) mode: Lens menus + Family pills on a single coordinated strip
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        LensMenusRow(
                            activePresetId = activePresetId,
                            activeEmulationId = activeEmulationId,
                            userThemeId = userThemeId,
                            onApplyAdvisoryPreset = onApplyAdvisoryPreset,
                            onApplyEmulation = onApplyEmulation,
                            onSetUserTheme = onSetUserTheme,
                            onOpenConfigSheet = { showConfigSheet = true },
                        )
                        Box(
                            modifier = Modifier
                                .height(20.dp)
                                .width(1.dp)
                                .background(MaterialTheme.colorScheme.outlineVariant),
                        )
                        FamilyPillsRow(
                            effectiveLayers = effectiveLayers,
                            substitutedLayers = substitutedLayers,
                            cloudStyle = cloudStyle,
                            expandedFamily = expandedFamily,
                            onToggleExpandedFamily = onToggleExpandedFamily,
                        )
                    }
                } else {
                    // Compact / Medium mode: Row 1 = LensMenus, Row 2 = FamilyPills
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        LensMenusRow(
                            activePresetId = activePresetId,
                            activeEmulationId = activeEmulationId,
                            userThemeId = userThemeId,
                            onApplyAdvisoryPreset = onApplyAdvisoryPreset,
                            onApplyEmulation = onApplyEmulation,
                            onSetUserTheme = onSetUserTheme,
                            onOpenConfigSheet = { showConfigSheet = true },
                        )
                    }

                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        FamilyPillsRow(
                            effectiveLayers = effectiveLayers,
                            substitutedLayers = substitutedLayers,
                            cloudStyle = cloudStyle,
                            expandedFamily = expandedFamily,
                            onToggleExpandedFamily = onToggleExpandedFamily,
                        )
                    }
                }

                // Expandable Family Detail Row
                AnimatedVisibility(
                    visible = expandedFamily != null,
                    enter = expandVertically() + fadeIn(),
                    exit = shrinkVertically() + fadeOut(),
                ) {
                    val family = expandedFamily
                    if (family != null) {
                        FamilyDetailRow(
                            family = family,
                            enabledLayers = enabledLayers,
                            effectiveLayers = effectiveLayers,
                            unavailableLayers = unavailableLayers,
                            substitutedLayers = substitutedLayers,
                            cloudStyle = cloudStyle,
                            onSelectFamilyMethod = { methodOrLayer ->
                                onSelectFamilyMethod(family, methodOrLayer)
                            },
                            onSetCloudStyle = onSetCloudStyle,
                            onToggleLayer = onToggleLayer,
                            onShowAbout = { aboutFamily = family },
                            onClose = { onToggleExpandedFamily(family) },
                        )
                    }
                }
            }
        }
    }

    if (showConfigSheet) {
        CrossSectionConfigSheet(
            enabledLayers = enabledLayers,
            effectiveLayers = effectiveLayers,
            unavailableLayers = unavailableLayers,
            substitutedLayers = substitutedLayers,
            cloudStyle = cloudStyle,
            userThemeId = userThemeId,
            onSetUserTheme = onSetUserTheme,
            onSetCloudStyle = onSetCloudStyle,
            onToggleLayer = onToggleLayer,
            onDismiss = { showConfigSheet = false },
        )
    }

    val currentAbout = aboutFamily
    if (currentAbout != null) {
        FamilyAboutDialog(
            family = currentAbout,
            onDismiss = { aboutFamily = null },
        )
    }
}

@Composable
private fun LensMenusRow(
    activePresetId: String?,
    activeEmulationId: String?,
    userThemeId: String,
    onApplyAdvisoryPreset: (String) -> Unit,
    onApplyEmulation: (String?) -> Unit,
    onSetUserTheme: (String) -> Unit,
    onOpenConfigSheet: () -> Unit,
) {
    var focusExpanded by remember { mutableStateOf(false) }
    var emulateExpanded by remember { mutableStateOf(false) }
    var themeExpanded by remember { mutableStateOf(false) }

    val focusLabel = CrossSectionPresets.ADVISORY_PRESETS
        .firstOrNull { it.id == activePresetId }
        ?.label
        ?: "Custom"

    val emulateLabel = CrossSectionPresets.EMULATION_PRESETS
        .firstOrNull { it.id == activeEmulationId }
        ?.label
        ?: "FlyFun"

    val themeLabel = CrossSectionThemes.ALL
        .firstOrNull { it.id == userThemeId }
        ?.label
        ?: "Standard"

    // 1. Focus Dropdown
    Box {
        LensMenuChip(
            icon = Icons.Default.Visibility,
            title = "Focus",
            value = focusLabel,
            onClick = { focusExpanded = true },
        )
        DropdownMenu(
            expanded = focusExpanded,
            onDismissRequest = { focusExpanded = false },
        ) {
            CrossSectionPresets.ADVISORY_PRESETS.forEach { preset ->
                val isSelected = preset.id == activePresetId
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(
                                text = preset.label,
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                            )
                            if (preset.caption.isNotBlank()) {
                                Text(
                                    text = preset.caption,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 2,
                                )
                            }
                        }
                    },
                    leadingIcon = if (isSelected) {
                        { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp)) }
                    } else null,
                    onClick = {
                        focusExpanded = false
                        onApplyAdvisoryPreset(preset.id)
                    },
                )
            }
        }
    }

    // 2. Emulate Dropdown
    Box {
        LensMenuChip(
            icon = Icons.Default.Tune,
            title = "Emulate",
            value = emulateLabel,
            onClick = { emulateExpanded = true },
        )
        DropdownMenu(
            expanded = emulateExpanded,
            onDismissRequest = { emulateExpanded = false },
        ) {
            // FlyFun (default / null emulation)
            val isFlyFun = activeEmulationId == null || activeEmulationId == "flyfun"
            DropdownMenuItem(
                text = {
                    Column {
                        Text(
                            text = "FlyFun",
                            fontWeight = if (isFlyFun) FontWeight.Bold else FontWeight.Normal,
                        )
                        Text(
                            text = "Briefing graded methods (NWP clouds + Ogimet-NWP icing)",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                leadingIcon = if (isFlyFun) {
                    { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp)) }
                } else null,
                onClick = {
                    emulateExpanded = false
                    onApplyEmulation(null)
                },
            )
            CrossSectionPresets.EMULATION_PRESETS
                .filter { it.id != "flyfun" }
                .forEach { preset ->
                    val isSelected = preset.id == activeEmulationId
                    DropdownMenuItem(
                        text = {
                            Text(
                                text = preset.label,
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                            )
                        },
                        leadingIcon = if (isSelected) {
                            { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp)) }
                        } else null,
                        onClick = {
                            emulateExpanded = false
                            onApplyEmulation(preset.id)
                        },
                    )
                }
        }
    }

    // 3. Theme Picker
    Box {
        LensMenuChip(
            icon = Icons.Default.Palette,
            title = "Theme",
            value = themeLabel,
            onClick = { themeExpanded = true },
        )
        DropdownMenu(
            expanded = themeExpanded,
            onDismissRequest = { themeExpanded = false },
        ) {
            CrossSectionThemes.ALL.forEach { theme ->
                val isSelected = theme.id == userThemeId
                DropdownMenuItem(
                    text = {
                        Text(
                            text = theme.label,
                            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                        )
                    },
                    leadingIcon = if (isSelected) {
                        { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp)) }
                    } else null,
                    onClick = {
                        themeExpanded = false
                        onSetUserTheme(theme.id)
                    },
                )
            }
        }
    }

    // 4. Gear button opening CrossSectionConfigSheet
    Surface(
        shape = CircleShape,
        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f),
        modifier = Modifier
            .size(28.dp)
            .clip(CircleShape)
            .clickable(onClick = onOpenConfigSheet),
    ) {
        Box(contentAlignment = Alignment.Center) {
            Icon(
                imageVector = Icons.Default.Settings,
                contentDescription = "Chart options",
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(15.dp),
            )
        }
    }
}

@Composable
private fun LensMenuChip(
    icon: ImageVector,
    title: String,
    value: String,
    onClick: () -> Unit,
) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.10f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.25f)),
        modifier = Modifier
            .clip(RoundedCornerShape(16.dp))
            .clickable(onClick = onClick),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(13.dp),
            )
            Text(
                text = title,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.5.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = value,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                ),
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
            )
            Icon(
                imageVector = Icons.Default.ArrowDropDown,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(14.dp),
            )
        }
    }
}

@Composable
private fun FamilyPillsRow(
    effectiveLayers: Map<String, Boolean>,
    substitutedLayers: Set<String>,
    cloudStyle: CloudStyle,
    expandedFamily: LayerFamily?,
    onToggleExpandedFamily: (LayerFamily) -> Unit,
) {
    LayerFamily.entries.forEach { family ->
        val badgeInfo = familyMethodBadge(family, effectiveLayers, substitutedLayers, cloudStyle)
        val isExpanded = expandedFamily == family
        val dotColor = familyDotColor(family)

        Surface(
            shape = RoundedCornerShape(16.dp),
            color = when {
                isExpanded -> MaterialTheme.colorScheme.primaryContainer
                badgeInfo.isActive -> MaterialTheme.colorScheme.surface
                else -> MaterialTheme.colorScheme.surface.copy(alpha = 0.45f)
            },
            border = BorderStroke(
                width = if (isExpanded) 1.5.dp else 0.75.dp,
                color = when {
                    isExpanded -> MaterialTheme.colorScheme.primary
                    badgeInfo.isActive -> dotColor.copy(alpha = 0.65f)
                    else -> MaterialTheme.colorScheme.outlineVariant
                },
            ),
            modifier = Modifier
                .clip(RoundedCornerShape(16.dp))
                .clickable { onToggleExpandedFamily(family) },
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .clip(CircleShape)
                        .background(if (badgeInfo.isActive) dotColor else dotColor.copy(alpha = 0.3f)),
                )
                Text(
                    text = family.label,
                    style = MaterialTheme.typography.labelSmall.copy(
                        fontWeight = if (badgeInfo.isActive || isExpanded) FontWeight.SemiBold else FontWeight.Normal,
                        fontSize = 11.sp,
                    ),
                    color = if (badgeInfo.isActive || isExpanded) {
                        MaterialTheme.colorScheme.onSurface
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
                Text(
                    text = badgeInfo.badgeText,
                    style = MaterialTheme.typography.labelSmall.copy(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                    ),
                    color = if (badgeInfo.isSubstituted) {
                        Color(0xFFD97706)
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FamilyDetailRow(
    family: LayerFamily,
    enabledLayers: Map<String, Boolean>,
    effectiveLayers: Map<String, Boolean>,
    unavailableLayers: Set<String>,
    substitutedLayers: Set<String>,
    cloudStyle: CloudStyle,
    onSelectFamilyMethod: (String) -> Unit,
    onSetCloudStyle: (CloudStyle) -> Unit,
    onToggleLayer: (String) -> Unit,
    onShowAbout: () -> Unit,
    onClose: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // Header row: Family title + hint + (i) About button + Close
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        text = family.label,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = "· ${familyHint(family)}",
                        style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(
                        onClick = onShowAbout,
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 2.dp),
                        modifier = Modifier.height(26.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.Info,
                            contentDescription = "About ${family.label}",
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(modifier = Modifier.width(3.dp))
                        Text(
                            text = "About",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                    IconButton(
                        onClick = onClose,
                        modifier = Modifier.size(24.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.Close,
                            contentDescription = "Close detail row",
                            modifier = Modifier.size(14.dp),
                        )
                    }
                }
            }

            // Family-specific controls
            when (family) {
                LayerFamily.CLOUDS -> {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "Source:",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        val nwpOn = isNwpCloudActive(effectiveLayers)
                        val ddOn = isDdCloudActive(effectiveLayers)
                        val nwpUnavailable = isNwpCloudUnavailable(unavailableLayers)

                        MethodRadioChip(
                            label = if (nwpUnavailable) "NWP (Unavail)" else "NWP",
                            selected = nwpOn,
                            enabled = !nwpUnavailable,
                            onClick = { onSelectFamilyMethod("clouds-nwp") },
                        )
                        MethodRadioChip(
                            label = if (substitutedLayers.any { it.contains("cloud") }) "DD (Auto)" else "DD (Sounding)",
                            selected = ddOn,
                            enabled = true,
                            onClick = { onSelectFamilyMethod("clouds") },
                        )
                        MethodRadioChip(
                            label = "Off",
                            selected = !nwpOn && !ddOn,
                            enabled = true,
                            onClick = { onSelectFamilyMethod("off") },
                        )

                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = "Style:",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        CloudStyle.entries.forEach { style ->
                            MethodRadioChip(
                                label = style.label,
                                selected = cloudStyle == style,
                                enabled = true,
                                onClick = { onSetCloudStyle(style) },
                            )
                        }
                    }
                }

                LayerFamily.ICING -> {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val icingOptions = listOf(
                            Triple("icing-ogimet-nwp", "icing-ogimet-nwp-bands", "Ogimet-NWP"),
                            Triple("icing", "icing-bands", "Ogimet-DD"),
                            Triple("icing-sfip", "sfip-bands", "SFIP-NWP"),
                            Triple("icing-ieng", "ieng-icing-bands", "IENG"),
                            Triple("sld", "sld-bands", "SLD Warm-Nose"),
                        )
                        icingOptions.forEach { (shortId, altId, label) ->
                            val isOn = effectiveLayers[shortId] == true || effectiveLayers[altId] == true
                            val isUnavail = shortId in unavailableLayers || altId in unavailableLayers
                            val isSub = shortId in substitutedLayers || altId in substitutedLayers
                            val displayLabel = when {
                                isSub -> "$label (Auto)"
                                isUnavail -> "$label (N/A)"
                                else -> label
                            }
                            MethodRadioChip(
                                label = displayLabel,
                                selected = isOn,
                                enabled = !isUnavail,
                                onClick = {
                                    if (shortId == "sld") {
                                        onToggleLayer(shortId)
                                    } else {
                                        onSelectFamilyMethod(shortId)
                                    }
                                },
                            )
                        }
                        val anyIcingOn = icingOptions.any {
                            effectiveLayers[it.first] == true || effectiveLayers[it.second] == true
                        }
                        MethodRadioChip(
                            label = "Off",
                            selected = !anyIcingOn,
                            enabled = true,
                            onClick = { onSelectFamilyMethod("off") },
                        )
                    }
                }

                LayerFamily.TURBULENCE -> {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val turbOptions = listOf(
                            Triple("cat", "cat-bands", "CAT (Richardson)"),
                            Triple("vertical-motion", "e-shear-bands", "E-Shear / Vert Motion"),
                            Triple("inversions", "inversion-bands", "Inversions"),
                        )
                        turbOptions.forEach { (shortId, altId, label) ->
                            val isOn = effectiveLayers[shortId] == true || effectiveLayers[altId] == true
                            val isUnavail = shortId in unavailableLayers || altId in unavailableLayers
                            MethodRadioChip(
                                label = label,
                                selected = isOn,
                                enabled = !isUnavail,
                                onClick = {
                                    if (shortId == "inversions") {
                                        onToggleLayer(shortId)
                                    } else {
                                        onSelectFamilyMethod(shortId)
                                    }
                                },
                            )
                        }
                        val anyTurbOn = turbOptions.any {
                            effectiveLayers[it.first] == true || effectiveLayers[it.second] == true
                        }
                        MethodRadioChip(
                            label = "Off",
                            selected = !anyTurbOn,
                            enabled = true,
                            onClick = { onSelectFamilyMethod("off") },
                        )
                    }
                }

                LayerFamily.CONVECTION -> {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val convOptions = listOf(
                            Triple("convective-nwp", "nwp-convective-bg", "NWP Convective"),
                            Triple("convective", "thermo-convective-bg", "Thermo (CAPE/LFC→EL)"),
                        )
                        convOptions.forEach { (shortId, altId, label) ->
                            val isOn = effectiveLayers[shortId] == true || effectiveLayers[altId] == true
                            val isUnavail = shortId in unavailableLayers || altId in unavailableLayers
                            val isSub = shortId in substitutedLayers || altId in substitutedLayers
                            val displayLabel = when {
                                isSub -> "$label (Auto)"
                                isUnavail -> "$label (N/A)"
                                else -> label
                            }
                            MethodRadioChip(
                                label = displayLabel,
                                selected = isOn,
                                enabled = !isUnavail,
                                onClick = { onSelectFamilyMethod(shortId) },
                            )
                        }
                        val anyConvOn = convOptions.any {
                            effectiveLayers[it.first] == true || effectiveLayers[it.second] == true
                        }
                        MethodRadioChip(
                            label = "Off",
                            selected = !anyConvOn,
                            enabled = true,
                            onClick = { onSelectFamilyMethod("off") },
                        )
                    }
                }

                LayerFamily.LINES -> {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val lineLayers = listOf(
                            Triple("freezing", "freezing-level", "0°C Freezing"),
                            Triple("minus-10", "minus-10c", "−10°C"),
                            Triple("minus-20", "minus-20c", "−20°C"),
                            Triple("lcl", "lcl", "LCL"),
                            Triple("lfc", "lfc", "LFC"),
                            Triple("el", "el", "EL"),
                            Triple("ceiling", "ceiling", "DD Ceiling"),
                            Triple("ceiling-nwp", "ceiling-nwp", "NWP Ceiling"),
                            Triple("cruise", "cruise-altitude", "Cruise / Ceiling"),
                            Triple("fronts", "fronts-markers", "Fronts"),
                            Triple("day-night", "night-shading", "Night Shading"),
                            Triple("current-conditions", "current-conditions", "METAR / SIGMET"),
                        )
                        lineLayers.forEach { (shortId, altId, label) ->
                            val isOn = effectiveLayers[shortId] == true || effectiveLayers[altId] == true
                            val targetId = if (shortId in enabledLayers) shortId else altId
                            MethodRadioChip(
                                label = label,
                                selected = isOn,
                                enabled = shortId !in unavailableLayers && altId !in unavailableLayers,
                                onClick = { onToggleLayer(targetId) },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MethodRadioChip(
    label: String,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        enabled = enabled,
        label = {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
            )
        },
        leadingIcon = if (selected) {
            {
                Icon(
                    imageVector = Icons.Default.Check,
                    contentDescription = null,
                    modifier = Modifier.size(13.dp),
                )
            }
        } else null,
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
            selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer,
        ),
        modifier = Modifier.height(28.dp),
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun CrossSectionConfigSheet(
    enabledLayers: Map<String, Boolean>,
    effectiveLayers: Map<String, Boolean>,
    unavailableLayers: Set<String>,
    substitutedLayers: Set<String>,
    cloudStyle: CloudStyle,
    userThemeId: String,
    onSetUserTheme: (String) -> Unit,
    onSetCloudStyle: (CloudStyle) -> Unit,
    onToggleLayer: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "Cross-Section Layer Configuration",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )

            // Cloud Style selector
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "Cloud Rendering Style",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CloudStyle.entries.forEach { style ->
                        MethodRadioChip(
                            label = style.label,
                            selected = cloudStyle == style,
                            enabled = true,
                            onClick = { onSetCloudStyle(style) },
                        )
                    }
                }
            }

            // Theme selector
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "Visual Theme",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    CrossSectionThemes.ALL.forEach { theme ->
                        MethodRadioChip(
                            label = theme.label,
                            selected = theme.id == userThemeId,
                            enabled = true,
                            onClick = { onSetUserTheme(theme.id) },
                        )
                    }
                }
            }

            HorizontalDivider()

            // Full Layer Checklist grouped by LayerFamily
            Text(
                text = "All Visualization Layers",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            CrossSectionPresets.ALL_LAYERS.groupBy { layerFamilyForDef(it) }.forEach { (family, layers) ->
                Text(
                    text = family.label,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 4.dp),
                )

                layers.forEach { entry ->
                    val isEffective = effectiveLayers[entry.id] == true
                    val isEnabled = enabledLayers[entry.id] == true
                    val isUnavailable = entry.id in unavailableLayers
                    val isSubstituted = entry.id in substitutedLayers

                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(6.dp))
                            .clickable(enabled = !isUnavailable) { onToggleLayer(entry.id) }
                            .padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.weight(1f),
                        ) {
                            Checkbox(
                                checked = isEffective || (isEnabled && !isUnavailable),
                                onCheckedChange = { if (!isUnavailable) onToggleLayer(entry.id) },
                                enabled = !isUnavailable,
                            )
                            Column {
                                Text(
                                    text = entry.name,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = if (isUnavailable) {
                                        MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                                    } else {
                                        MaterialTheme.colorScheme.onSurface
                                    },
                                )
                                Text(
                                    text = entry.group.replaceFirstChar { it.uppercase() },
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }

                        if (isSubstituted) {
                            Surface(
                                shape = RoundedCornerShape(4.dp),
                                color = Color(0xFFF59E0B).copy(alpha = 0.18f),
                            ) {
                                Text(
                                    text = "Auto-Fallback",
                                    style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                    color = Color(0xFFD97706),
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                )
                            }
                        } else if (isUnavailable) {
                            Surface(
                                shape = RoundedCornerShape(4.dp),
                                color = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.5f),
                            ) {
                                Text(
                                    text = "Unavailable",
                                    style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                    color = MaterialTheme.colorScheme.onErrorContainer,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}

@Composable
private fun FamilyAboutDialog(
    family: LayerFamily,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "About ${family.label}",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    text = familyAboutIntro(family),
                    style = MaterialTheme.typography.bodyMedium,
                )
                val familyLayers = CrossSectionPresets.ALL_LAYERS.filter { layerFamilyForDef(it) == family }
                familyLayers.forEach { layer ->
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Column(
                            modifier = Modifier.padding(10.dp),
                            verticalArrangement = Arrangement.spacedBy(3.dp),
                        ) {
                            Text(
                                text = layer.name,
                                style = MaterialTheme.typography.labelLarge,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                text = "ID: ${layer.id} · Group: ${layer.group}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Got it")
            }
        },
    )
}

private fun layerFamilyForDef(def: CrossSectionLayerDef): LayerFamily = when (def.group.lowercase()) {
    "clouds" -> LayerFamily.CLOUDS
    "icing" -> LayerFamily.ICING
    "turbulence" -> LayerFamily.TURBULENCE
    "convection" -> LayerFamily.CONVECTION
    else -> LayerFamily.LINES
}

private data class FamilyBadgeInfo(
    val badgeText: String,
    val isActive: Boolean,
    val isSubstituted: Boolean,
)

private fun isNwpCloudActive(effectiveLayers: Map<String, Boolean>): Boolean =
    effectiveLayers["clouds-nwp"] == true ||
        effectiveLayers["clouds-icing-nwp"] == true ||
        effectiveLayers["clouds-diag-nwp"] == true ||
        effectiveLayers["clouds-band-nwp"] == true ||
        effectiveLayers["nwp-cloud-bands"] == true ||
        effectiveLayers["soft-nwp-clouds"] == true ||
        effectiveLayers["square-nwp-clouds"] == true ||
        effectiveLayers["soft-nwp-cloud-bands"] == true ||
        effectiveLayers["square-nwp-cloud-bands"] == true

private fun isDdCloudActive(effectiveLayers: Map<String, Boolean>): Boolean =
    effectiveLayers["clouds"] == true ||
        effectiveLayers["clouds-icing"] == true ||
        effectiveLayers["clouds-diag"] == true ||
        effectiveLayers["clouds-band"] == true ||
        effectiveLayers["dd-cloud-bands"] == true ||
        effectiveLayers["cloud-bands"] == true ||
        effectiveLayers["soft-dd-clouds"] == true ||
        effectiveLayers["soft-cloud-bands"] == true ||
        effectiveLayers["square-dd-clouds"] == true ||
        effectiveLayers["square-cloud-bands"] == true

private fun isNwpCloudUnavailable(unavailableLayers: Set<String>): Boolean =
    "clouds-nwp" in unavailableLayers ||
        "nwp-cloud-bands" in unavailableLayers ||
        "soft-nwp-clouds" in unavailableLayers ||
        "square-nwp-clouds" in unavailableLayers

private fun familyMethodBadge(
    family: LayerFamily,
    effectiveLayers: Map<String, Boolean>,
    substitutedLayers: Set<String>,
    cloudStyle: CloudStyle,
): FamilyBadgeInfo = when (family) {
    LayerFamily.CLOUDS -> {
        val nwp = isNwpCloudActive(effectiveLayers)
        val dd = isDdCloudActive(effectiveLayers)
        val sub = substitutedLayers.any { it.contains("cloud") }
        when {
            nwp && dd -> FamilyBadgeInfo("NWP+DD · ${cloudStyle.label}", true, sub)
            nwp -> FamilyBadgeInfo("NWP · ${cloudStyle.label}", true, sub)
            dd -> FamilyBadgeInfo("DD · ${cloudStyle.label}", true, sub)
            else -> FamilyBadgeInfo("off", false, false)
        }
    }

    LayerFamily.ICING -> {
        val activeNames = mutableListOf<String>()
        if (effectiveLayers["icing-ogimet-nwp"] == true || effectiveLayers["icing-ogimet-nwp-bands"] == true) activeNames.add("Ogimet-NWP")
        if (effectiveLayers["icing"] == true || effectiveLayers["icing-bands"] == true) activeNames.add("Ogimet-DD")
        if (effectiveLayers["icing-sfip"] == true || effectiveLayers["sfip-bands"] == true) activeNames.add("SFIP")
        if (effectiveLayers["icing-ieng"] == true || effectiveLayers["ieng-icing-bands"] == true) activeNames.add("IENG")
        if (effectiveLayers["sld"] == true || effectiveLayers["sld-bands"] == true) activeNames.add("SLD")
        val sub = substitutedLayers.any { it.contains("icing") || it.contains("sfip") }
        if (activeNames.isEmpty()) {
            FamilyBadgeInfo("off", false, false)
        } else {
            FamilyBadgeInfo(activeNames.take(2).joinToString("+"), true, sub)
        }
    }

    LayerFamily.TURBULENCE -> {
        val activeNames = mutableListOf<String>()
        if (effectiveLayers["cat"] == true || effectiveLayers["cat-bands"] == true) activeNames.add("Ri")
        if (effectiveLayers["vertical-motion"] == true || effectiveLayers["e-shear-bands"] == true) activeNames.add("E-Shear")
        if (effectiveLayers["inversions"] == true || effectiveLayers["inversion-bands"] == true) activeNames.add("Inv")
        if (activeNames.isEmpty()) {
            FamilyBadgeInfo("off", false, false)
        } else {
            FamilyBadgeInfo(activeNames.joinToString("+"), true, false)
        }
    }

    LayerFamily.CONVECTION -> {
        val nwp = effectiveLayers["convective-nwp"] == true || effectiveLayers["nwp-convective-bg"] == true
        val thermo = effectiveLayers["convective"] == true || effectiveLayers["thermo-convective-bg"] == true
        val sub = "convective" in substitutedLayers || "thermo-convective-bg" in substitutedLayers
        when {
            nwp && thermo -> FamilyBadgeInfo("NWP+Thermo", true, sub)
            nwp -> FamilyBadgeInfo("NWP", true, sub)
            thermo -> FamilyBadgeInfo("Thermo", true, sub)
            else -> FamilyBadgeInfo("off", false, false)
        }
    }

    LayerFamily.LINES -> {
        val lineIds = listOf(
            "freezing", "freezing-level",
            "minus-10", "minus-10c",
            "minus-20", "minus-20c",
            "lcl", "lfc", "el",
            "ceiling", "ceiling-nwp",
            "cruise", "cruise-altitude", "reference-lines", "flight-ceiling",
            "surface-obscuration-bands", "fronts", "fronts-markers", "day-night", "night-shading", "current-conditions",
        )
        val count = lineIds.count { effectiveLayers[it] == true }
        if (count == 0) {
            FamilyBadgeInfo("off", false, false)
        } else {
            FamilyBadgeInfo("$count on", true, false)
        }
    }
}

private fun familyDotColor(family: LayerFamily): Color = when (family) {
    LayerFamily.CLOUDS -> Color(0xFF8C8C99)
    LayerFamily.ICING -> ColorScales.icingRiskColor("moderate")
    LayerFamily.TURBULENCE -> ColorScales.catRiskColor("moderate")
    LayerFamily.CONVECTION -> ColorScales.convectiveRiskColor("high")
    LayerFamily.LINES -> Color(CrossSectionThemes.STANDARD.freezingLineColor)
}

private fun familyHint(family: LayerFamily): String = when (family) {
    LayerFamily.CLOUDS -> "Model cloud along the route — NWP vs Sounding DD."
    LayerFamily.ICING -> "Airframe icing indices: Ogimet-NWP, Ogimet-DD, SFIP, IENG & SLD."
    LayerFamily.TURBULENCE -> "Clear-air turbulence (Richardson / E-Shear) & temperature inversions."
    LayerFamily.CONVECTION -> "Model convective background vs thermodynamic CAPE/LFC→EL towers."
    LayerFamily.LINES -> "Isotherms (0°C/−10°C/−20°C), stability levels (LCL/LFC/EL), fronts & reference lines."
}

private fun familyAboutIntro(family: LayerFamily): String = when (family) {
    LayerFamily.CLOUDS ->
        "Neither source is the sole truth. DD infers cloud from dewpoint depression in the sounding; NWP uses the model's native cloud-fraction field. Comparing both reveals where the sounding and cloud scheme agree."
    LayerFamily.ICING ->
        "Icing methods split on where cloud liquid water comes from: Ogimet-DD and IENG read moisture from the sounding, while Ogimet-NWP and SFIP use the model's cloud field. SLD highlights warm-nose freezing rain hazards."
    LayerFamily.TURBULENCE ->
        "CAT (Ri) diagnoses vertical wind-shear instability via the Richardson number, while E-Shear combines vertical and horizontal shear. Inversions mark stable layers that trap haze or induce low-level wind shear."
    LayerFamily.CONVECTION ->
        "NWP Convective reflects the model's sub-grid convective parameterization; Thermo Convective builds parcel towers from LFC to EL scaled by CAPE."
    LayerFamily.LINES ->
        "Reference altitudes, isotherms (0°C, −10°C, −20°C), parcel stability levels (LCL, LFC, EL), synoptic fronts, and day/twilight/night shading."
}
