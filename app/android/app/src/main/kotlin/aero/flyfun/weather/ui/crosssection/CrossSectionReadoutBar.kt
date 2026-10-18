package aero.flyfun.weather.ui.crosssection

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AcUnit
import androidx.compose.material.icons.filled.Air
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Landscape
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.Thermostat
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import aero.flyfun.weather.model.viz.ColorScales
import aero.flyfun.weather.model.viz.CrossSectionThemes
import aero.flyfun.weather.model.viz.VizCloudLayer
import aero.flyfun.weather.model.viz.VizDataExtractor
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizRouteData
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Compact cockpit readout strip displaying the scrubbed or selected [VizPoint]:
 * - Waypoint / Distance (`42 NM · FL080`)
 * - Terrain (`1,450 ft`)
 * - Cloud layers (e.g. `BKN 4,200–7,800 ft`)
 * - Icing risk (e.g. `MOD 5,000–8,500 ft`)
 * - CAT risk (e.g. `LGT 7,000–10,000 ft`)
 * - Freezing level (`0°C at 9,200 ft`)
 * - Cruise Wind (`12 kt HW / 6 kt XW`)
 */
@Composable
fun CrossSectionReadoutBar(
    vizData: VizRouteData,
    selectedPointIndex: Int,
    scrubDistanceNm: Double?,
    hoverAltitudeFt: Double?,
    effectiveLayers: Map<String, Boolean>,
    modifier: Modifier = Modifier,
) {
    val points = vizData.points
    if (points.isEmpty()) return

    val activePoint: VizPoint = if (scrubDistanceNm != null) {
        points.minByOrNull { abs(it.distanceNm - scrubDistanceNm) }
            ?: points[selectedPointIndex.coerceIn(0, points.lastIndex)]
    } else {
        points[selectedPointIndex.coerceIn(0, points.lastIndex)]
    }

    val displayDistNm = scrubDistanceNm ?: activePoint.distanceNm
    val nearestWaypoint = vizData.waypoints
        .filter { abs(it.distanceNm - displayDistNm) <= 3.0 }
        .minByOrNull { abs(it.distanceNm - displayDistNm) }
        ?.icao
        ?: activePoint.waypointIcao

    val cruiseLabel = formatCruiseAltitude(vizData.cruiseAltitudeFt)
    val terrainFt = if (vizData.terrainProfile.isNotEmpty()) {
        VizDataExtractor.interpolateTerrainElevation(vizData.terrainProfile, displayDistNm)
    } else {
        activePoint.terrainElevationFt
    }

    val useNwpClouds = effectiveLayers["clouds-nwp"] == true ||
        effectiveLayers["clouds-icing-nwp"] == true ||
        effectiveLayers["clouds-diag-nwp"] == true ||
        effectiveLayers["clouds-band-nwp"] == true ||
        effectiveLayers["nwp-cloud-bands"] == true ||
        effectiveLayers["soft-nwp-clouds"] == true ||
        effectiveLayers["square-nwp-clouds"] == true ||
        effectiveLayers["soft-nwp-cloud-bands"] == true ||
        effectiveLayers["square-nwp-cloud-bands"] == true

    val activeClouds: List<VizCloudLayer> = if (useNwpClouds && activePoint.nwpCloudLayers.isNotEmpty()) {
        activePoint.nwpCloudLayers
    } else {
        activePoint.cloudLayers
    }

    val cloudSummary = summarizeClouds(activeClouds)
    val icingSummary = summarizeIcing(activePoint, effectiveLayers)
    val catSummary = summarizeCat(activePoint, effectiveLayers)
    val freezingFt = activePoint.altitudeLines.freezingLevelFt
    val hwKt = activePoint.headwindKt ?: 0.0
    val xwKt = activePoint.crosswindKt ?: 0.0
    val windSummary = formatCruiseWind(activePoint.headwindKt, activePoint.crosswindKt)

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 7.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            // Top header row: Position · Waypoint · Cruise FL · Time · Hover Altitude
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .clip(CircleShape)
                            .background(
                                if (scrubDistanceNm != null) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.secondary,
                            ),
                    )
                    Text(
                        text = buildString {
                            if (!nearestWaypoint.isNullOrBlank()) {
                                append(nearestWaypoint)
                                append(" · ")
                            }
                            append("${displayDistNm.roundToInt()} NM")
                            append(" · ")
                            append(cruiseLabel)
                        },
                        style = MaterialTheme.typography.labelMedium.copy(
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                        ),
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (hoverAltitudeFt != null) {
                        Surface(
                            shape = RoundedCornerShape(4.dp),
                            color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.75f),
                        ) {
                            Text(
                                text = "Alt ${formatFeet(hoverAltitudeFt)}",
                                style = MaterialTheme.typography.labelSmall.copy(
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 10.sp,
                                ),
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                                modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp),
                            )
                        }
                    }
                    val timeLabel = formatTimeShort(activePoint.flightTimeIso.orEmpty())
                    if (timeLabel.isNotEmpty()) {
                        Text(
                            text = timeLabel,
                            style = MaterialTheme.typography.labelSmall.copy(
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                            ),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            // Bottom horizontally scrollable telemetry chips
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // 1. Terrain
                ReadoutChip(
                    icon = Icons.Default.Landscape,
                    label = "Terrain",
                    value = formatFeet(terrainFt),
                    accentColor = Color(0xFF8D6E63),
                )

                // 2. Freezing level
                ReadoutChip(
                    icon = Icons.Default.Thermostat,
                    label = "0°C",
                    value = if (freezingFt != null) "at ${formatFeet(freezingFt)}" else "SFC / N/A",
                    accentColor = Color(CrossSectionThemes.STANDARD.freezingLineColor),
                )

                // 3. Cruise Wind
                ReadoutChip(
                    icon = Icons.Default.Air,
                    label = "Wind",
                    value = windSummary,
                    accentColor = if (hwKt > 15.0 || abs(xwKt) > 15.0) {
                        Color(0xFFF59E0B)
                    } else {
                        MaterialTheme.colorScheme.primary
                    },
                )

                // 4. Clouds
                ReadoutChip(
                    icon = Icons.Default.Cloud,
                    label = "Clouds",
                    value = cloudSummary.text,
                    accentColor = cloudSummary.color,
                )

                // 5. Icing risk
                ReadoutChip(
                    icon = Icons.Default.AcUnit,
                    label = "Icing",
                    value = icingSummary.text,
                    accentColor = icingSummary.color,
                )

                // 6. CAT risk
                ReadoutChip(
                    icon = Icons.Default.Speed,
                    label = "CAT",
                    value = catSummary.text,
                    accentColor = catSummary.color,
                )

                // 7. Convection (when non-none)
                val convRisk = activePoint.thermoConvective.riskLevel.lowercase(Locale.US)
                val nwpConvRisk = activePoint.nwpConvective?.riskLevel?.lowercase(Locale.US).orEmpty()
                val bestConv = when {
                    convRisk != "none" && convRisk.isNotBlank() -> convRisk
                    nwpConvRisk != "none" && nwpConvRisk.isNotBlank() -> nwpConvRisk
                    else -> null
                }
                if (bestConv != null) {
                    ReadoutChip(
                        icon = Icons.Default.Warning,
                        label = "Conv",
                        value = bestConv.uppercase(Locale.US),
                        accentColor = ColorScales.convectiveRiskColor(bestConv),
                    )
                }
            }
        }
    }
}

@Composable
private fun ReadoutChip(
    icon: ImageVector,
    label: String,
    value: String,
    accentColor: Color,
) {
    Surface(
        shape = RoundedCornerShape(6.dp),
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
        border = BorderStroke(0.5.dp, accentColor.copy(alpha = 0.45f)),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = accentColor,
                modifier = Modifier.size(12.dp),
            )
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = value,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 10.5.sp,
                ),
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
            )
        }
    }
}

private data class SummaryBadge(
    val text: String,
    val color: Color,
)

private fun summarizeClouds(layers: List<VizCloudLayer>): SummaryBadge {
    val meaningful = layers.filter {
        val cov = it.coverage.lowercase(Locale.US)
        cov == "few" || cov == "sct" || cov == "bkn" || cov == "ovc" || (it.coveragePct ?: 0.0) >= 12.0
    }
    if (meaningful.isEmpty()) {
        return SummaryBadge("CLR", Color(0xFF22C55E))
    }
    val primary = meaningful.maxByOrNull { coverageRank(it.coverage) } ?: meaningful.first()
    val covUpper = primary.coverage.uppercase(Locale.US).ifBlank { "SCT" }
    val baseStr = formatAltCompact(primary.baseFt)
    val topStr = formatAltCompact(primary.topFt)
    val extra = if (meaningful.size > 1) " (+${meaningful.size - 1})" else ""
    val badgeColor = when (covUpper) {
        "OVC" -> Color(0xFF64748B)
        "BKN" -> Color(0xFF3B82F6)
        else -> Color(0xFF0EA5E9)
    }
    return SummaryBadge("$covUpper $baseStr–$topStr$extra", badgeColor)
}

private fun summarizeIcing(
    point: VizPoint,
    effectiveLayers: Map<String, Boolean>,
): SummaryBadge {
    val useOgimetDd = effectiveLayers["icing"] == true || effectiveLayers["icing-bands"] == true
    val useSfip = effectiveLayers["icing-sfip"] == true || effectiveLayers["sfip-bands"] == true
    val useIeng = effectiveLayers["icing-ieng"] == true || effectiveLayers["ieng-icing-bands"] == true

    if (useSfip && point.sfipZones.isNotEmpty()) {
        val worstSfip = point.sfipZones
            .filter { it.risk.lowercase(Locale.US) != "none" }
            .maxByOrNull { riskRank(it.risk) }
        if (worstSfip != null) {
            val riskLabel = shortRiskLabel(worstSfip.risk)
            return SummaryBadge(
                "$riskLabel ${formatAltCompact(worstSfip.baseFt)}–${formatAltCompact(worstSfip.topFt)}",
                ColorScales.icingRiskColor(worstSfip.risk),
            )
        }
    }

    val zones = when {
        useIeng && point.iengIcingZones.isNotEmpty() -> point.iengIcingZones
        useOgimetDd && point.icingZones.isNotEmpty() -> point.icingZones
        point.icingOgimetNwpZones.isNotEmpty() -> point.icingOgimetNwpZones
        point.icingZones.isNotEmpty() -> point.icingZones
        else -> emptyList()
    }

    val worstZone = zones
        .filter { it.risk.lowercase(Locale.US) != "none" }
        .maxByOrNull { riskRank(it.risk) }

    if (worstZone == null) {
        return SummaryBadge("None", Color(0xFF22C55E))
    }
    val riskShort = shortRiskLabel(worstZone.risk)
    return SummaryBadge(
        "$riskShort ${formatAltCompact(worstZone.baseFt)}–${formatAltCompact(worstZone.topFt)}",
        ColorScales.icingRiskColor(worstZone.risk),
    )
}

private fun summarizeCat(
    point: VizPoint,
    effectiveLayers: Map<String, Boolean>,
): SummaryBadge {
    val layers = if (
        (effectiveLayers["vertical-motion"] == true || effectiveLayers["e-shear-bands"] == true) &&
        point.eShearLayers.isNotEmpty()
    ) {
        point.eShearLayers
    } else {
        point.catLayers
    }
    val worstCat = layers
        .filter { it.risk.lowercase(Locale.US) != "none" }
        .maxByOrNull { riskRank(it.risk) }
        ?: return SummaryBadge("None", Color(0xFF22C55E))

    val riskShort = shortRiskLabel(worstCat.risk)
    return SummaryBadge(
        "$riskShort ${formatAltCompact(worstCat.baseFt)}–${formatAltCompact(worstCat.topFt)}",
        ColorScales.catRiskColor(worstCat.risk),
    )
}

private fun shortRiskLabel(risk: String): String = when (risk.lowercase(Locale.US)) {
    "light" -> "LGT"
    "moderate" -> "MOD"
    "severe" -> "SEV"
    else -> risk.uppercase(Locale.US).take(3)
}

private fun coverageRank(coverage: String): Int = when (coverage.lowercase(Locale.US)) {
    "ovc" -> 4
    "bkn" -> 3
    "sct" -> 2
    "few" -> 1
    else -> 0
}

private fun riskRank(risk: String): Int = when (risk.lowercase(Locale.US)) {
    "severe", "high" -> 3
    "moderate" -> 2
    "light", "low", "marginal" -> 1
    else -> 0
}

private fun formatCruiseAltitude(altFt: Double): String {
    val rounded = altFt.roundToInt()
    return if (rounded >= 10000 && rounded % 100 == 0) {
        String.format(Locale.US, "FL%03d", rounded / 100)
    } else {
        "${String.format(Locale.US, "%,d", rounded)} ft"
    }
}

private fun formatFeet(altFt: Double): String {
    val rounded = ((altFt / 50.0).roundToInt() * 50).coerceAtLeast(0)
    return "${String.format(Locale.US, "%,d", rounded)} ft"
}

private fun formatAltCompact(altFt: Double): String {
    val rounded = ((altFt / 100.0).roundToInt() * 100).coerceAtLeast(0)
    return if (rounded >= 10000) {
        String.format(Locale.US, "FL%03d", rounded / 100)
    } else {
        "${String.format(Locale.US, "%,d", rounded)}ft"
    }
}

private fun formatCruiseWind(headwindKt: Double?, crosswindKt: Double?): String {
    if (headwindKt == null && crosswindKt == null) return "N/A"
    val hw = headwindKt ?: 0.0
    val xw = crosswindKt ?: 0.0
    val hwRounded = abs(hw).roundToInt()
    val hwDir = if (hw >= 0) "HW" else "TW"
    val xwRounded = abs(xw).roundToInt()
    return "$hwRounded kt $hwDir / $xwRounded kt XW"
}

private fun formatTimeShort(isoTime: String): String {
    if (isoTime.isBlank()) return ""
    val tIdx = isoTime.indexOf('T')
    if (tIdx >= 0 && isoTime.length >= tIdx + 6) {
        return isoTime.substring(tIdx + 1, tIdx + 6) + "Z"
    }
    return isoTime.take(5)
}
