package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.viewmodel.BriefingViewModel
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Shared cockpit-grade colors for assessments and flight categories across `ui/briefing/`.
 */
internal object BriefingPalette {
    val Green = Color(0xFF2E7D32)
    val Amber = Color(0xFFEF6C00)
    val Red = Color(0xFFC62828)
    val Unavailable = Color(0xFF757575)

    val Vfr = Color(0xFF2E7D32)
    val Mvfr = Color(0xFF1565C0)
    val Ifr = Color(0xFFC62828)
    val Lifr = Color(0xFF6A1B9A)

    fun colorForAssessment(assessment: Assessment): Color = when (assessment) {
        Assessment.GREEN -> Green
        Assessment.AMBER -> Amber
        Assessment.RED -> Red
        Assessment.UNAVAILABLE -> Unavailable
    }

    fun colorForAssessment(status: String?): Color =
        colorForAssessment(Assessment.fromString(status))

    fun colorForFlightCategory(category: String?): Color = when (category?.trim()?.lowercase()) {
        "vfr" -> Vfr
        "mvfr" -> Mvfr
        "ifr" -> Ifr
        "lifr" -> Lifr
        else -> Unavailable
    }
}

private val PACK_TIME_FORMATTER: DateTimeFormatter =
    DateTimeFormatter.ofPattern("d MMM HH:mm'Z'", Locale.US).withZone(ZoneOffset.UTC)

internal fun formatUtcTimestamp(iso: String?): String {
    val ms = IsoTime.parseEpochMs(iso) ?: return iso ?: ""
    return PACK_TIME_FORMATTER.format(Instant.ofEpochMilli(ms))
}

internal fun String.shortModelName(): String = when (this.trim().lowercase()) {
    "meteofrance", "meteo_france", "arpege" -> "MF"
    "ecmwf" -> "ECMWF"
    "icon", "icon_eu", "icon-eu" -> "ICON"
    "gfs" -> "GFS"
    "ukmo" -> "UKMO"
    else -> this.uppercase()
}

/**
 * Compact colored pill for an [Assessment] (`GREEN` / `AMBER` / `RED` / `N/A`).
 */
@Composable
internal fun AssessmentPillBadge(
    assessment: Assessment,
    modifier: Modifier = Modifier,
) {
    val bgColor = BriefingPalette.colorForAssessment(assessment)
    Surface(
        modifier = modifier,
        color = bgColor,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            text = assessment.label.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = Color.White,
            maxLines = 1,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        )
    }
}

/**
 * Compact colored pill for a flight category (`VFR` / `MVFR` / `IFR` / `LIFR`).
 */
@Composable
internal fun FlightCategoryPillBadge(
    category: String,
    modifier: Modifier = Modifier,
) {
    if (category.isBlank()) return
    val bgColor = BriefingPalette.colorForFlightCategory(category)
    Surface(
        modifier = modifier,
        color = bgColor,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            text = category.trim().uppercase(),
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = Color.White,
            maxLines = 1,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        )
    }
}

/**
 * Leading pack chip in the TopAppBar:
 * - Shows freshness dot (green fresh / amber stale), `D-N` label, assessment badge,
 *   and offline status icon (green check when cached, spinner when downloading).
 * - Clicking opens a `DropdownMenu` showing the freshness detail header, pack history entries
 *   (`D-N` + timestamp + assessment pill), `Download for Offline`, and `Refresh Briefing`
 *   (with SSE progress banner when refreshing).
 */
@Composable
fun BriefingPackToolbar(
    viewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val packs by viewModel.packs.collectAsState()
    val selectedPack by viewModel.selectedPack.collectAsState()
    val isOfflineCached by viewModel.isOfflineCached.collectAsState()
    val isDownloadingPack by viewModel.isDownloadingPack.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()
    val refreshProgressText by viewModel.refreshProgressText.collectAsState()
    val flight by viewModel.flight.collectAsState()
    val refreshGate = flight?.refreshGate()

    val currentPack = selectedPack ?: packs.firstOrNull()
    var menuExpanded by remember { mutableStateOf(false) }

    val isFresh = currentPack?.dataStatus?.fresh ?: true
    val freshnessDotColor = if (isFresh) BriefingPalette.Green else BriefingPalette.Amber
    val dLabel = currentPack?.let { "D-${it.daysOut}" } ?: "Pack"
    val assessment = currentPack?.resolvedAssessment ?: Assessment.UNAVAILABLE

    Box(modifier = modifier) {
        Surface(
            shape = RoundedCornerShape(50),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.75f),
            border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
            modifier = Modifier
                .clip(RoundedCornerShape(50))
                .clickable { menuExpanded = true },
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(freshnessDotColor),
                )
                Text(
                    text = dLabel,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                if (assessment != Assessment.UNAVAILABLE) {
                    AssessmentPillBadge(assessment = assessment)
                }
                when {
                    isDownloadingPack -> {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 2.dp,
                        )
                    }
                    isOfflineCached -> {
                        Icon(
                            imageVector = Icons.Default.CheckCircle,
                            contentDescription = "Saved for offline",
                            tint = BriefingPalette.Green,
                            modifier = Modifier.size(15.dp),
                        )
                    }
                }
                Icon(
                    imageVector = Icons.Default.ArrowDropDown,
                    contentDescription = "Open pack menu",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(16.dp),
                )
            }
        }

        DropdownMenu(
            expanded = menuExpanded,
            onDismissRequest = { menuExpanded = false },
            modifier = Modifier.widthIn(min = 260.dp, max = 340.dp),
        ) {
            // Freshness detail header
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(freshnessDotColor),
                    )
                    Text(
                        text = if (isFresh) "Model Data Fresh" else "Model Update Available",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
                Text(
                    text = buildFreshnessDetailText(currentPack),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                val nextModel = currentPack?.dataStatus?.nextExpectedModel
                val nextUpdate = currentPack?.dataStatus?.nextExpectedUpdate
                if (!nextModel.isNullOrBlank() && !nextUpdate.isNullOrBlank()) {
                    Text(
                        text = "Next expected: ${nextModel.uppercase()} (${formatUtcTimestamp(nextUpdate)})",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // SSE Refresh Progress Banner when refreshing
            if (isRefreshing) {
                HorizontalDivider()
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 2.dp,
                        )
                        Text(
                            text = refreshProgressText ?: "Refreshing briefing…",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.primary,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }

            // Pack History Entries
            if (packs.isNotEmpty()) {
                HorizontalDivider()
                Text(
                    text = "PACK HISTORY",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                )
                packs.forEach { pack ->
                    val isSelected = pack.fetchTimestamp == currentPack?.fetchTimestamp
                    DropdownMenuItem(
                        text = {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(
                                    text = "D-${pack.daysOut}",
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.SemiBold,
                                )
                                Text(
                                    text = formatUtcTimestamp(pack.fetchTimestamp),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.weight(1f),
                                )
                                AssessmentPillBadge(assessment = pack.resolvedAssessment)
                            }
                        },
                        trailingIcon = {
                            if (isSelected) {
                                Icon(
                                    imageVector = Icons.Default.Check,
                                    contentDescription = "Selected pack",
                                    tint = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.size(18.dp),
                                )
                            }
                        },
                        onClick = {
                            menuExpanded = false
                            viewModel.selectPack(pack)
                        },
                    )
                }
            }

            HorizontalDivider()

            // Download for Offline
            DropdownMenuItem(
                text = {
                    Text(
                        text = when {
                            isDownloadingPack -> "Downloading for Offline…"
                            isOfflineCached -> "Saved for Offline (Re-download)"
                            else -> "Download for Offline"
                        },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                },
                leadingIcon = {
                    if (isDownloadingPack) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(
                            imageVector = if (isOfflineCached) Icons.Default.CheckCircle else Icons.Default.CloudDownload,
                            contentDescription = null,
                            tint = if (isOfflineCached) BriefingPalette.Green else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                enabled = !isDownloadingPack && currentPack != null,
                onClick = {
                    menuExpanded = false
                    viewModel.downloadCurrentPack()
                },
            )

            // Refresh Briefing
            DropdownMenuItem(
                text = {
                    Column {
                        Text(
                            text = if (isRefreshing) {
                                refreshProgressText ?: "Refreshing Briefing…"
                            } else {
                                "Refresh Briefing"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        if (refreshGate != null) {
                            Text(
                                text = refreshGate.reason,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.widthIn(max = 260.dp),
                            )
                        }
                    }
                },
                leadingIcon = {
                    if (isRefreshing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = null,
                        )
                    }
                },
                enabled = !isRefreshing && refreshGate == null,
                onClick = {
                    menuExpanded = false
                    viewModel.refreshBriefing()
                },
            )
        }
    }
}

private fun buildFreshnessDetailText(pack: PackMetaResponse?): String {
    if (pack == null) return "No pack loaded"
    val status = pack.dataStatus
    val initTimes = when {
        status != null && status.modelInitTimes.isNotEmpty() -> status.modelInitTimes
        pack.modelInitTimes.isNotEmpty() -> pack.modelInitTimes
        else -> emptyMap()
    }
    if (status != null && !status.fresh && status.staleModels.isNotEmpty()) {
        val staleList = status.staleModels.joinToString(", ") { it.shortModelName() }
        return "Updating: $staleList"
    }
    if (initTimes.isNotEmpty()) {
        return initTimes.entries
            .sortedBy { it.key }
            .take(4)
            .joinToString(" · ") { (model, hour) ->
                "${model.shortModelName()} ${String.format(Locale.US, "%02dZ", hour % 24)}"
            }
    }
    return "Fetched ${formatUtcTimestamp(pack.fetchTimestamp)}"
}
