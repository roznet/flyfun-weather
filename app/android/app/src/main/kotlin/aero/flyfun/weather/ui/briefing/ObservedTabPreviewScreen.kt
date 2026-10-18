package aero.flyfun.weather.ui.briefing

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.LiveChange
import aero.flyfun.weather.model.LiveChanges
import aero.flyfun.weather.model.LiveGlance
import aero.flyfun.weather.model.LiveStorms
import aero.flyfun.weather.model.ObservedConditions
import aero.flyfun.weather.model.RouteObservations
import aero.flyfun.weather.model.RouteSigmets
import aero.flyfun.weather.model.SigmetAlongRoute
import aero.flyfun.weather.viewmodel.BriefingViewModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Flight-day **Observed** tab screen (Phase 1 preview):
 * - Renders `liveLayer` glance lines & highlights, departure/destination METAR tiles,
 *   live changes ("Since this briefing"), radar & lightning summary, METAR/TAF observations,
 *   and route SIGMETs from `snapshot` & `liveLayer`.
 */
@Composable
fun ObservedTabPreviewScreen(
    viewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val flight by viewModel.flight.collectAsState()
    val snapshot by viewModel.snapshot.collectAsState()
    val liveLayer by viewModel.liveLayer.collectAsState()

    val effectiveObservations = liveLayer?.routeObservations ?: snapshot?.routeObservations
    val effectiveSigmets = liveLayer?.routeSigmets ?: snapshot?.routeSigmets
    val effectiveObservedConditions = liveLayer?.observedConditions ?: snapshot?.observedConditions
    val glance = liveLayer?.glance
    val liveChanges = liveLayer?.changes
    val liveStorms = liveLayer?.storms

    val hasChanges = (liveChanges != null && (liveChanges.changes.isNotEmpty() || !liveChanges.recentlyCleared.isNullOrEmpty())) ||
        (liveStorms != null && liveStorms.cells.isNotEmpty())
    val hasRadar = effectiveObservedConditions != null &&
        (!effectiveObservedConditions.summaryEntries.isNullOrEmpty() || !effectiveObservedConditions.summary.isNullOrEmpty())
    val hasMetarTaf = effectiveObservations != null && effectiveObservations.airports.isNotEmpty()
    val hasSigmets = effectiveSigmets != null && effectiveSigmets.sigmets.isNotEmpty()

    val listState = rememberLazyListState()

    val (spySections, sectionIndices) = remember(hasChanges, hasRadar, hasMetarTaf, hasSigmets) {
        val sections = mutableListOf<SpySection>()
        val indices = mutableMapOf<String, Int>()
        var nextIdx = 0

        sections.add(SpySection("glance", "Now"))
        indices["glance"] = nextIdx++

        if (hasChanges) {
            sections.add(SpySection("changes", "Changes"))
            indices["changes"] = nextIdx++
        }
        if (hasRadar) {
            sections.add(SpySection("radar", "Radar"))
            indices["radar"] = nextIdx++
        }
        if (hasMetarTaf) {
            sections.add(SpySection("metar", "METAR/TAF"))
            indices["metar"] = nextIdx++
        }
        if (hasSigmets) {
            sections.add(SpySection("sigmets", "SIGMET"))
            indices["sigmets"] = nextIdx++
        }
        sections to indices
    }

    Column(modifier = modifier.fillMaxSize()) {
        SectionSpyBar(
            sections = spySections,
            listState = listState,
            sectionIndices = sectionIndices,
        )

        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // 1. At-a-Glance Card
            item(key = "glance") {
                ObservedGlanceSection(
                    glance = glance,
                    liveUpdatedAt = liveLayer?.liveUpdatedAt ?: effectiveObservations?.fetchTime,
                    waypoints = flight?.waypoints.orEmpty(),
                    observations = effectiveObservations,
                    liveChanges = liveChanges,
                    sigmets = effectiveSigmets,
                    onShowMap = {
                        viewModel.applyFocusIntent(
                            FocusIntent(
                                tab = BriefingTab.MAP,
                                preset = "observed",
                            ),
                        )
                    },
                )
            }

            // 2. Live Changes & Storms
            if (hasChanges) {
                item(key = "changes") {
                    ObservedChangesSection(
                        changes = liveChanges,
                        storms = liveStorms,
                    )
                }
            }

            // 3. Radar & Lightning Now
            if (hasRadar && effectiveObservedConditions != null) {
                item(key = "radar") {
                    ObservedRadarNowSection(observed = effectiveObservedConditions)
                }
            }

            // 4. METAR / TAF Observations
            if (hasMetarTaf && effectiveObservations != null) {
                item(key = "metar") {
                    ObservedMetarTafSection(observations = effectiveObservations)
                }
            }

            // 5. Route SIGMETs
            if (hasSigmets && effectiveSigmets != null) {
                item(key = "sigmets") {
                    ObservedSigmetsSection(sigmets = effectiveSigmets)
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ObservedGlanceSection(
    glance: LiveGlance?,
    liveUpdatedAt: String?,
    waypoints: List<String>,
    observations: RouteObservations?,
    liveChanges: LiveChanges?,
    sigmets: RouteSigmets?,
    onShowMap: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val depIcao = waypoints.firstOrNull()
    val destIcao = waypoints.lastOrNull()?.takeIf { waypoints.size > 1 }
    val depObs = depIcao?.let { icao -> observations?.airports?.firstOrNull { it.icao.equals(icao, ignoreCase = true) } }
    val destObs = destIcao?.let { icao -> observations?.airports?.firstOrNull { it.icao.equals(icao, ignoreCase = true) } }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Observed At a Glance",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                if (!liveUpdatedAt.isNullOrBlank()) {
                    Text(
                        text = "As of ${formatUtcTimestamp(liveUpdatedAt)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Departure & Destination tiles
            if (depIcao != null || destIcao != null) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    if (depIcao != null) {
                        TerminalMetarTile(
                            role = "Departure",
                            icao = depIcao,
                            obs = depObs,
                            modifier = Modifier.weight(1f),
                        )
                    }
                    if (destIcao != null) {
                        TerminalMetarTile(
                            role = "Destination",
                            icao = destIcao,
                            obs = destObs,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }

            // Glance headline & lines
            if (glance != null) {
                if (glance.headline.isNotBlank()) {
                    Text(
                        text = glance.headline,
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                if (!glance.subline.isNullOrBlank()) {
                    Text(
                        text = glance.subline,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                glance.highlights.forEach { hl ->
                    val hlColor = when (hl.level.lowercase(Locale.US)) {
                        "alert", "danger", "red" -> BriefingPalette.Red
                        "warn", "warning", "amber" -> BriefingPalette.Amber
                        else -> MaterialTheme.colorScheme.onSurface
                    }
                    Text(
                        text = "• ${hl.text}",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                        color = hlColor,
                    )
                }
                glance.lines.orEmpty().forEach { line ->
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            text = line.text,
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.Medium,
                        )
                        if (!line.detail.isNullOrBlank()) {
                            Text(
                                text = line.detail,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
                if (!glance.quietSummary.isNullOrBlank() && glance.headline.isBlank()) {
                    Text(
                        text = glance.quietSummary,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Summary chips
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                val alertCount = liveChanges?.alertCount ?: 0
                val worsenedCount = liveChanges?.worsenedCount ?: 0
                if (alertCount > 0) {
                    GlanceStatusChip(
                        text = if (alertCount == 1) "1 alert" else "$alertCount alerts",
                        color = BriefingPalette.Red,
                    )
                }
                if (worsenedCount > 0) {
                    GlanceStatusChip(
                        text = "$worsenedCount worse since briefing",
                        color = BriefingPalette.Amber,
                    )
                }
                if (liveChanges != null && liveChanges.changes.isEmpty()) {
                    GlanceStatusChip(
                        text = "No significant change",
                        color = BriefingPalette.Green,
                    )
                }
                val sigmetCount = sigmets?.count ?: sigmets?.sigmets?.size ?: 0
                if (sigmetCount > 0) {
                    val label = (if (sigmetCount == 1) "1 SIGMET" else "$sigmetCount SIGMETs") +
                        if (sigmets?.hasSevere == true) " · SEV" else ""
                    GlanceStatusChip(
                        text = label,
                        color = if (sigmets?.hasSevere == true) BriefingPalette.Red else BriefingPalette.Amber,
                    )
                }
            }

            OutlinedButton(
                onClick = onShowMap,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(
                    imageVector = Icons.Default.Map,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text("Show radar & cells on map")
            }
        }
    }
}

@Composable
private fun TerminalMetarTile(
    role: String,
    icao: String,
    obs: AirportObservation?,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text = role.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = icao,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                obs?.metarFlightCategory?.takeIf { it.isNotBlank() }?.let { cat ->
                    FlightCategoryPillBadge(category = cat)
                }
            }
            val subText = when {
                obs?.hasMetar == true && !obs.metarTime.isNullOrBlank() ->
                    "METAR ${formatUtcTimestamp(obs.metarTime)}"
                obs?.obsProxy != null ->
                    "Proxy ${obs.obsProxy.icao} (${obs.obsProxy.distanceNm.roundToInt()} NM)"
                else -> "No METAR"
            }
            Text(
                text = subText,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun GlanceStatusChip(
    text: String,
    color: androidx.compose.ui.graphics.Color,
) {
    Surface(
        shape = RoundedCornerShape(50),
        color = color.copy(alpha = 0.14f),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.SemiBold,
            color = color,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
        )
    }
}

// ============================================================================
// Since This Briefing (Live Changes & Storms)
// ============================================================================

@Composable
private fun ObservedChangesSection(
    changes: LiveChanges?,
    storms: LiveStorms?,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            text = "Since This Briefing",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )

        changes?.changes.orEmpty().forEach { change ->
            LiveChangeRowCard(change = change)
        }

        val cleared = changes?.recentlyCleared.orEmpty()
        if (cleared.isNotEmpty()) {
            Text(
                text = "Recently Cleared",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            cleared.forEach { change ->
                LiveChangeRowCard(change = change, isCleared = true)
            }
        }

        val stormCells = storms?.cells.orEmpty()
        if (stormCells.isNotEmpty()) {
            Text(
                text = "Radar Storm Cells Along Route",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            stormCells.forEach { storm ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Column(
                        modifier = Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(
                                text = storm.headline.ifBlank { "Storm cell (${storm.severity})" },
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                text = "${storm.distanceNm.roundToInt()} NM off track",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (storm.detail.isNotBlank()) {
                            Text(
                                text = storm.detail,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun LiveChangeRowCard(
    change: LiveChange,
    isCleared: Boolean = false,
) {
    val accent = when {
        isCleared -> BriefingPalette.Green
        change.newAlert || change.tier.equals("alert", ignoreCase = true) -> BriefingPalette.Red
        change.direction.equals("worse", ignoreCase = true) -> BriefingPalette.Amber
        change.direction.equals("better", ignoreCase = true) -> BriefingPalette.Green
        else -> MaterialTheme.colorScheme.primary
    }

    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = accent.copy(alpha = 0.08f),
        border = BorderStroke(0.5.dp, accent.copy(alpha = 0.35f)),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                change.icao?.takeIf { it.isNotBlank() }?.let { icao ->
                    Text(
                        text = icao,
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Text(
                    text = if (isCleared) "CLEARED" else change.direction.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = accent,
                )
                Spacer(modifier = Modifier.weight(1f))
                change.observedAt?.let { obsAt ->
                    Text(
                        text = formatUtcTimestamp(obsAt),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text(
                text = change.message,
                style = MaterialTheme.typography.bodyMedium,
            )
            change.place?.text?.takeIf { it.isNotBlank() }?.let { placeText ->
                Text(
                    text = placeText,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

// ============================================================================
// Radar & Lightning Now
// ============================================================================

@Composable
private fun ObservedRadarNowSection(
    observed: ObservedConditions,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Radar,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(20.dp),
            )
            Text(
                text = "Radar & Lightning Now",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        }
        observed.corridorNm?.let { corridor ->
            Text(
                text = "Within ${corridor.roundToInt()} NM of the route",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        val entries = observed.summaryEntries?.map { it.text }?.filter { it.isNotBlank() }
            ?: observed.summary?.filter { it.isNotBlank() }
            ?: emptyList()

        entries.forEach { text ->
            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(10.dp),
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
            ) {
                Text(
                    text = text,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(12.dp),
                )
            }
        }
    }
}

// ============================================================================
// METAR / TAF Observations
// ============================================================================

@Composable
private fun ObservedMetarTafSection(
    observations: RouteObservations,
    modifier: Modifier = Modifier,
) {
    val reportingAirports = remember(observations.airports) {
        observations.airports.filter { it.hasMetar || it.hasTaf }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "METAR / TAF Observations",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            observations.worstMetarCategory?.takeIf { it.isNotBlank() }?.let { worst ->
                FlightCategoryPillBadge(category = worst)
            }
        }

        if (observations.phenomenaAlongRoute.isNotEmpty()) {
            Text(
                text = "Phenomena along route: ${observations.phenomenaAlongRoute.joinToString(", ")}",
                style = MaterialTheme.typography.bodySmall,
                color = BriefingPalette.Amber,
                fontWeight = FontWeight.Medium,
            )
        }

        val listToShow = if (reportingAirports.isNotEmpty()) reportingAirports else observations.airports
        listToShow.forEach { airport ->
            AirportObservationCard(airport = airport)
        }
    }
}

@Composable
private fun AirportObservationCard(
    airport: AirportObservation,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = airport.icao,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                airport.metarFlightCategory?.takeIf { it.isNotBlank() }?.let { cat ->
                    FlightCategoryPillBadge(category = cat)
                }
                airport.tafFlightCategoryAtEta?.takeIf { it.isNotBlank() }?.let { tafCat ->
                    Text(
                        text = "TAF@ETA:",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    FlightCategoryPillBadge(category = tafCat)
                }
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = "${airport.distanceFromRouteNm.roundToInt()} NM off route",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (!airport.name.isNullOrBlank()) {
                Text(
                    text = airport.name,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            val metricsLine = listOfNotNull(
                airport.metarCeilingFt?.let { "Ceiling ${it} ft" },
                airport.metarVisibilityM?.let { "Vis ${it} m" },
                airport.metarWindSpeedKt?.let { spd ->
                    val dir = airport.metarWindDir?.let { String.format(Locale.US, "%03d°/", it) } ?: ""
                    val gst = airport.metarWindGustKt?.let { "G$it" } ?: ""
                    "Wind $dir${spd}${gst}kt"
                },
                airport.metarBestRunwayId?.let { rwy ->
                    val xw = airport.metarCrosswindKt?.let { " (${abs(it).roundToInt()}kt XW)" } ?: ""
                    "Rwy $rwy$xw"
                },
            ).joinToString(" · ")

            if (metricsLine.isNotBlank()) {
                Text(
                    text = metricsLine,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            if (!airport.metarRaw.isNullOrBlank()) {
                Text(
                    text = airport.metarRaw,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (!airport.tafRaw.isNullOrBlank()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Text(
                    text = airport.tafRaw,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

// ============================================================================
// Route SIGMETs
// ============================================================================

@Composable
private fun ObservedSigmetsSection(
    sigmets: RouteSigmets,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Warning,
                contentDescription = null,
                tint = if (sigmets.hasSevere) BriefingPalette.Red else BriefingPalette.Amber,
                modifier = Modifier.size(20.dp),
            )
            Text(
                text = "Route SIGMETs (${sigmets.sigmets.size})",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
        }

        sigmets.sigmets.forEach { sigmet ->
            SigmetCard(sigmet = sigmet)
        }
    }
}

@Composable
private fun SigmetCard(
    sigmet: SigmetAlongRoute,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = BriefingPalette.Amber.copy(alpha = 0.08f),
        ),
        border = BorderStroke(0.5.dp, BriefingPalette.Amber.copy(alpha = 0.35f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "${sigmet.firId} · ${sigmet.hazard.uppercase()} ${sigmet.qualifier.orEmpty()}".trim(),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                val altBand = if (sigmet.baseFt != null || sigmet.topFt != null) {
                    "${sigmet.baseFt ?: 0}–${sigmet.topFt ?: 0} ft"
                } else {
                    ""
                }
                if (altBand.isNotBlank()) {
                    Text(
                        text = altBand,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
            Text(
                text = "Valid ${formatUtcTimestamp(sigmet.validFrom)} → ${formatUtcTimestamp(sigmet.validTo)}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (sigmet.rawText.isNotBlank()) {
                Text(
                    text = sigmet.rawText,
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
