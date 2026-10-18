package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.AirportConditions
import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.ObservationComparison
import aero.flyfun.weather.model.RouteObservations
import aero.flyfun.weather.model.RunwayEnd
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.AssessmentUnavailable
import aero.flyfun.weather.ui.theme.flightCategoryColor
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/**
 * Route airport observations section (`Departure`, `Destination`, and `Corridor` stations)
 * displaying flight category badges, observation proxy badges (`via LFMN · 6 NM` when `obsProxy != null`),
 * ceiling/visibility/wind readouts, NWP model agreement comparison pills, expandable raw METAR/TAF text,
 * and inline/expandable [RunwayWindDialView] for terminal airports.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RouteObservationsSection(
    observations: RouteObservations?,
    waypoints: List<String>,
    modifier: Modifier = Modifier,
    airportConditions: AirportConditions? = null,
    extraRunwayEndsByIcao: Map<String, List<RunwayEnd>> = emptyMap(),
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val airports = observations?.airports.orEmpty()
    val comparisonsByIcao = remember(observations?.comparisons) {
        observations?.comparisons.orEmpty().associateBy { it.icao.uppercase() }
    }

    val originIcao = waypoints.firstOrNull()?.uppercase()
    val destIcao = waypoints.lastOrNull()?.uppercase()

    var showCorridorStations by rememberSaveable { mutableStateOf(true) }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Section header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = "Airport METAR / TAF & Runway Wind",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    val sub = if (observations != null) {
                        "${observations.airportsWithMetar}/${observations.airportsFound} METAR · " +
                            "${observations.airportsWithTaf} TAF · ±${observations.corridorNm.roundToInt()} NM"
                    } else {
                        "No live route station observations loaded"
                    }
                    Text(
                        text = sub,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                if (observations != null) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        observations.worstMetarCategory?.let { worst ->
                            CategoryBadgePill(label = "Worst METAR: $worst", category = worst)
                        }
                        if (observations.hasConflicts) {
                            Surface(
                                shape = RoundedCornerShape(6.dp),
                                color = AssessmentAmber.copy(alpha = 0.15f),
                                border = BorderStroke(1.dp, AssessmentAmber.copy(alpha = 0.4f)),
                            ) {
                                Text(
                                    text = "Obs ≠ Model",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = AssessmentAmber,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                )
                            }
                        }
                    }
                }
            }

            // Phenomena along route strip
            val phenomena = observations?.phenomenaAlongRoute.orEmpty()
            if (phenomena.isNotEmpty()) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = "Reported wx:",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                    phenomena.forEach { wx ->
                        Surface(
                            shape = RoundedCornerShape(5.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                        ) {
                            Text(
                                text = wx,
                                style = MaterialTheme.typography.labelSmall,
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
            }

            if (airports.isEmpty()) {
                Text(
                    text = "No airport observations available along this route.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                val terminalAirports = airports.filter { ap ->
                    val code = ap.icao.uppercase()
                    code == originIcao || code == destIcao || (ap.distanceFromRouteNm ?: 99.0) <= 1.5 &&
                        (ap == airports.first() || ap == airports.last())
                }
                val corridorAirports = airports.filter { it !in terminalAirports }

                // Terminal (Departure / Arrival) cards — RunwayWindDialView expanded by default
                terminalAirports.forEach { airport ->
                    val role = when (airport.icao.uppercase()) {
                        originIcao -> "Departure"
                        destIcao -> "Destination"
                        else -> if (airport == airports.first()) "Departure" else "Destination"
                    }
                    val runwayEnds = resolveRunwayEndsForAirport(
                        airport = airport,
                        role = role,
                        airportConditions = airportConditions,
                        extraRunwayEndsByIcao = extraRunwayEndsByIcao,
                    )
                    AirportObservationCard(
                        airport = airport,
                        roleLabel = role,
                        isTerminal = true,
                        comparison = comparisonsByIcao[airport.icao.uppercase()],
                        runwayEnds = runwayEnds,
                        onSelectFocus = onSelectFocus,
                    )
                }

                // Corridor stations filter & list
                if (corridorAirports.isNotEmpty()) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "Corridor Stations (${corridorAirports.size})",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                        FilterChip(
                            selected = showCorridorStations,
                            onClick = { showCorridorStations = !showCorridorStations },
                            label = {
                                Text(
                                    text = if (showCorridorStations) "Hide corridor" else "Show corridor",
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            },
                        )
                    }

                    AnimatedVisibility(visible = showCorridorStations) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            corridorAirports.forEach { airport ->
                                val runwayEnds = resolveRunwayEndsForAirport(
                                    airport = airport,
                                    role = "Corridor",
                                    airportConditions = airportConditions,
                                    extraRunwayEndsByIcao = extraRunwayEndsByIcao,
                                )
                                AirportObservationCard(
                                    airport = airport,
                                    roleLabel = "Corridor",
                                    isTerminal = false,
                                    comparison = comparisonsByIcao[airport.icao.uppercase()],
                                    runwayEnds = runwayEnds,
                                    onSelectFocus = onSelectFocus,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AirportObservationCard(
    airport: AirportObservation,
    roleLabel: String,
    isTerminal: Boolean,
    comparison: ObservationComparison?,
    runwayEnds: List<RunwayEnd>,
    onSelectFocus: ((LiveFocus) -> Unit)?,
) {
    var showRawText by rememberSaveable(airport.icao) { mutableStateOf(false) }
    var showRunwayDial by rememberSaveable(airport.icao) { mutableStateOf(isTerminal) }

    val metarCat = airport.metarFlightCategory
    val tafCat = airport.tafFlightCategoryAtEta ?: airport.tafAtEta?.flightCategory
    val catColor = flightCategoryColor(metarCat ?: tafCat)

    Surface(
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = if (isTerminal) 0.35f else 0.2f),
        border = BorderStroke(
            width = if (isTerminal) 1.2.dp else 1.dp,
            color = if (isTerminal) catColor.copy(alpha = 0.45f) else MaterialTheme.colorScheme.outlineVariant,
        ),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Row 1: Role + ICAO + Name + Proxy badge (#767) + Flight Category badges
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Surface(
                            shape = RoundedCornerShape(5.dp),
                            color = if (isTerminal) {
                                MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)
                            } else {
                                MaterialTheme.colorScheme.surfaceVariant
                            },
                        ) {
                            Text(
                                text = roleLabel.uppercase(),
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = if (isTerminal) {
                                    MaterialTheme.colorScheme.primary
                                } else {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                },
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }

                        Text(
                            text = airport.icao,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.ExtraBold,
                            fontFamily = FontFamily.Monospace,
                        )

                        // Observation proxy badge (#767: e.g. "via LFMN · 6 NM")
                        airport.obsProxy?.let { proxy ->
                            Surface(
                                shape = RoundedCornerShape(5.dp),
                                color = Color(0xFF0284C7).copy(alpha = 0.14f),
                                border = BorderStroke(1.dp, Color(0xFF0284C7).copy(alpha = 0.4f)),
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.SwapHoriz,
                                        contentDescription = null,
                                        tint = Color(0xFF0284C7),
                                        modifier = Modifier.size(12.dp),
                                    )
                                    Text(
                                        text = "via ${proxy.icao} · ${proxy.distanceNm.roundToInt()} NM",
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = FontWeight.SemiBold,
                                        fontFamily = FontFamily.Monospace,
                                        color = Color(0xFF0284C7),
                                    )
                                }
                            }
                        }
                    }

                    val locSub = buildList {
                        airport.name?.takeIf { it.isNotBlank() }?.let { add(it) }
                        airport.enrouteDistanceNm?.let { add("${it.roundToInt()} NM along route") }
                        airport.distanceFromRouteNm?.takeIf { it > 0.5 }?.let { add("${it.roundToInt()} NM off track") }
                    }.joinToString(" · ")
                    if (locSub.isNotBlank()) {
                        Text(
                            text = locSub,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                // METAR & TAF flight category pills
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (metarCat != null) {
                        CategoryBadgePill(label = "METAR $metarCat", category = metarCat)
                    } else {
                        CategoryBadgePill(label = "NO METAR", category = null)
                    }
                    if (tafCat != null) {
                        CategoryBadgePill(label = "TAF $tafCat", category = tafCat)
                    }
                }
            }

            // Row 2: Ceiling, Visibility, Wind, Temp/Dewpoint, QNH summary metrics
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                val ceilText = airport.metarCeilingFt?.let { "Ceil $it ft" }
                    ?: if (airport.hasMetar) "Ceil CAVOK/CLR" else "Ceil —"
                MetricReadout(label = ceilText)

                val visText = airport.metarVisibilityM?.let { "Vis ${formatVisibilityMeters(it)}" }
                    ?: "Vis —"
                MetricReadout(label = visText)

                val windText = if (airport.metarWindDir != null && airport.metarWindSpeedKt != null) {
                    val dir = airport.metarWindDir.toString().padStart(3, '0')
                    val gust = airport.metarWindGustKt?.takeIf { it > airport.metarWindSpeedKt }?.let { "G$it" }.orEmpty()
                    "Wind ${dir}°/${airport.metarWindSpeedKt}${gust}kt"
                } else if (airport.metarWindSpeedKt == 0) {
                    "Wind Calm"
                } else {
                    "Wind —"
                }
                MetricReadout(label = windText)

                if (airport.metarTemperatureC != null) {
                    val dp = airport.metarDewpointC?.let { "/${it}°C" }.orEmpty()
                    MetricReadout(label = "Temp ${airport.metarTemperatureC}°C$dp")
                }

                airport.metarQnh?.let { qnh ->
                    MetricReadout(label = "QNH ${qnh.roundToInt()} hPa")
                }

                airport.metarBestRunwayId?.let { rwy ->
                    val xw = airport.metarCrosswindKt?.roundToInt()?.let { " (XW $it kt)" }.orEmpty()
                    MetricReadout(label = "Best RWY $rwy$xw")
                }
            }

            // Row 3: NWP Model Agreement comparison pill (if present)
            if (comparison != null) {
                ModelComparisonRow(comparison = comparison)
            }

            // Row 4: Action bar — Toggle Runway Wind Dial + Toggle Raw METAR/TAF + View on Map
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (runwayEnds.isNotEmpty() || airport.metarWindSpeedKt != null || airport.tafWindSpeedKt != null) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.clickable { showRunwayDial = !showRunwayDial },
                        ) {
                            Icon(
                                imageVector = Icons.Default.Explore,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(15.dp),
                            )
                            Text(
                                text = if (showRunwayDial) "Hide Runway Dial" else "Runway Wind Dial",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                    }

                    if (!airport.metarRaw.isNullOrBlank() || !airport.tafRaw.isNullOrBlank()) {
                        Text(
                            text = if (showRawText) "Hide Raw METAR/TAF" else "Raw METAR/TAF",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.clickable { showRawText = !showRawText },
                        )
                    }
                }

                if (onSelectFocus != null) {
                    Text(
                        text = "Map ›",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.clickable {
                            onSelectFocus(
                                LiveFocus(
                                    layer = "stations",
                                    stationIcao = airport.icao,
                                    distanceNm = airport.enrouteDistanceNm,
                                ),
                            )
                        },
                    )
                }
            }

            // Expandable Raw METAR & TAF text box
            AnimatedVisibility(visible = showRawText) {
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surface,
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        if (!airport.metarRaw.isNullOrBlank()) {
                            Text(
                                text = "METAR",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.primary,
                            )
                            Text(
                                text = airport.metarRaw,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                            )
                        }
                        if (!airport.tafRaw.isNullOrBlank()) {
                            Text(
                                text = "TAF",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = AssessmentAmber,
                            )
                            Text(
                                text = airport.tafRaw,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                            )
                        }
                    }
                }
            }

            // Inline / expandable RunwayWindDialView
            AnimatedVisibility(visible = showRunwayDial) {
                RunwayWindDialView(
                    icao = airport.icao,
                    runwayEnds = runwayEnds,
                    windDirDeg = airport.metarWindDir,
                    windSpeedKt = airport.metarWindSpeedKt,
                    windGustKt = airport.metarWindGustKt,
                    tafWindDirDeg = airport.tafWindDir ?: airport.tafAtEta?.windDir,
                    tafWindSpeedKt = airport.tafWindSpeedKt ?: airport.tafAtEta?.windSpeedKt,
                    tafWindGustKt = airport.tafWindGustKt ?: airport.tafAtEta?.windGustKt,
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ModelComparisonRow(
    comparison: ObservationComparison,
) {
    val matchLower = comparison.categoryMatch.lowercase()
    val matchColor = when (matchLower) {
        "exact", "match", "good" -> AssessmentGreen
        "worse", "conflict", "mismatch" -> AssessmentRed
        "better" -> Color(0xFF0284C7)
        else -> AssessmentAmber
    }

    Surface(
        shape = RoundedCornerShape(8.dp),
        color = matchColor.copy(alpha = 0.08f),
        border = BorderStroke(1.dp, matchColor.copy(alpha = 0.3f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = "vs NWP Models:",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = matchColor,
                )
                Text(
                    text = "Obs ${comparison.obsCategory ?: "—"} vs Model ${comparison.modelCategory ?: "—"} (${comparison.categoryMatch})",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                )
                comparison.windSpeedDeltaKt?.let { delta ->
                    val sign = if (delta >= 0) "+" else ""
                    Text(
                        text = "ΔWind $sign${delta.roundToInt()} kt",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (comparison.detail.isNotBlank()) {
                Text(
                    text = comparison.detail,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun CategoryBadgePill(
    label: String,
    category: String?,
) {
    val color = if (category != null) flightCategoryColor(category) else AssessmentUnavailable
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = color.copy(alpha = 0.16f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.45f)),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .background(color, CircleShape),
            )
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                color = color,
            )
        }
    }
}

@Composable
private fun MetricReadout(label: String) {
    Text(
        text = label,
        style = MaterialTheme.typography.bodySmall,
        fontFamily = FontFamily.Monospace,
        color = MaterialTheme.colorScheme.onSurface,
    )
}

/**
 * Resolves [RunwayEnd] entries for an airport from:
 * 1. Explicit [extraRunwayEndsByIcao] map if provided.
 * 2. Briefing pack [AirportConditions] (`allRunways` / `bestRunway` on departure/arrival).
 * 3. Synthesizing opposite runway ends from `metarBestRunwayId` or `tafBestRunwayId` designator
 *    (e.g. `"04"` -> `04` at `040°` and `22` at `220°`) so the dial renders even without a separate airport DB query.
 */
internal fun resolveRunwayEndsForAirport(
    airport: AirportObservation,
    role: String,
    airportConditions: AirportConditions?,
    extraRunwayEndsByIcao: Map<String, List<RunwayEnd>>,
): List<RunwayEnd> {
    val code = airport.icao.uppercase()
    extraRunwayEndsByIcao[code]?.takeIf { it.isNotEmpty() }?.let { return it }

    val condSummary = when {
        airportConditions?.departure?.icao?.equals(code, ignoreCase = true) == true -> airportConditions.departure
        airportConditions?.arrival?.icao?.equals(code, ignoreCase = true) == true -> airportConditions.arrival
        role == "Departure" -> airportConditions?.departure
        role == "Destination" -> airportConditions?.arrival
        else -> null
    }

    condSummary?.runwayEnds?.takeIf { it.isNotEmpty() }?.let {
        return synthesizeReciprocalsIfNeeded(it)
    }

    val fromConditions = condSummary?.conditions
        ?.flatMap { it.allRunways }
        .orEmpty()
        .distinctBy { it.runwayId }
        .map { RunwayEnd(id = it.runwayId, headingDeg = it.headingDeg) }

    if (fromConditions.isNotEmpty()) {
        return synthesizeReciprocalsIfNeeded(fromConditions)
    }

    val bestId = airport.metarBestRunwayId ?: airport.tafBestRunwayId
    if (!bestId.isNullOrBlank()) {
        return synthesizeRunwayPairFromDesignator(bestId)
    }

    return emptyList()
}

private fun synthesizeReciprocalsIfNeeded(ends: List<RunwayEnd>): List<RunwayEnd> {
    val result = ends.toMutableList()
    ends.forEach { end ->
        val hasRecip = result.any { other ->
            other.id != end.id && kotlin.math.abs(((end.headingDeg + 180.0 - other.headingDeg + 540.0) % 360.0) - 180.0) < 25.0
        }
        if (!hasRecip) {
            val recipHeading = (end.headingDeg + 180.0) % 360.0
            val recipNum = ((recipHeading / 10.0).roundToInt().let { if (it == 0) 36 else it })
                .toString()
                .padStart(2, '0')
            val suffix = when (end.id.lastOrNull()?.uppercaseChar()) {
                'L' -> "R"
                'R' -> "L"
                'C' -> "C"
                else -> ""
            }
            val recipId = "$recipNum$suffix"
            if (result.none { it.id == recipId }) {
                result += RunwayEnd(id = recipId, headingDeg = recipHeading)
            }
        }
    }
    return result
}

private fun synthesizeRunwayPairFromDesignator(designator: String): List<RunwayEnd> {
    val digits = designator.filter { it.isDigit() }.toIntOrNull() ?: return emptyList()
    if (digits !in 1..36) return emptyList()
    val heading = (digits * 10).toDouble() % 360.0
    val primary = RunwayEnd(id = designator.uppercase(), headingDeg = heading)
    return synthesizeReciprocalsIfNeeded(listOf(primary))
}
