package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.RouteSigmets
import aero.flyfun.weather.model.SigmetAlongRoute
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentGreen
import aero.flyfun.weather.ui.theme.AssessmentRed
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircleOutline
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
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
 * Active and pending route SIGMETs section rendering [RouteSigmets] cards with
 * hazard badge (`TS`, `ICE`, `TURB`, `MTW`), flight level band (`FL050–FL240`),
 * validity window, route span, and expandable raw SIGMET text.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RouteSigmetsSection(
    routeSigmets: RouteSigmets?,
    modifier: Modifier = Modifier,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val sigmets = routeSigmets?.sigmets.orEmpty()

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
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Section header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.Warning,
                        contentDescription = null,
                        tint = when {
                            routeSigmets?.hasSevere == true -> AssessmentRed
                            sigmets.isNotEmpty() -> AssessmentAmber
                            else -> AssessmentGreen
                        },
                        modifier = Modifier.size(18.dp),
                    )
                    Column {
                        Text(
                            text = "Route SIGMETs & AIRMETs",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        val firsStr = routeSigmets?.routeFirs?.takeIf { it.isNotEmpty() }
                            ?.joinToString(", ")
                            ?.let { "FIRs: $it" }
                            ?: "Checked along route corridor"
                        Text(
                            text = firsStr,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                if (routeSigmets != null) {
                    val badgeColor = when {
                        routeSigmets.hasSevere -> AssessmentRed
                        sigmets.isNotEmpty() -> AssessmentAmber
                        else -> AssessmentGreen
                    }
                    Surface(
                        shape = RoundedCornerShape(6.dp),
                        color = badgeColor.copy(alpha = 0.14f),
                        border = BorderStroke(1.dp, badgeColor.copy(alpha = 0.4f)),
                    ) {
                        Text(
                            text = if (sigmets.isEmpty()) "0 active" else "${sigmets.size} active",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            color = badgeColor,
                            modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                        )
                    }
                }
            }

            if (sigmets.isEmpty()) {
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = AssessmentGreen.copy(alpha = 0.08f),
                    border = BorderStroke(1.dp, AssessmentGreen.copy(alpha = 0.25f)),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.CheckCircleOutline,
                            contentDescription = null,
                            tint = AssessmentGreen,
                            modifier = Modifier.size(18.dp),
                        )
                        Text(
                            text = "No active or pending SIGMETs intersecting the route corridor and altitude window.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    sigmets.forEach { sigmet ->
                        SigmetCard(
                            sigmet = sigmet,
                            onSelectFocus = onSelectFocus,
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SigmetCard(
    sigmet: SigmetAlongRoute,
    onSelectFocus: ((LiveFocus) -> Unit)?,
) {
    var rawExpanded by rememberSaveable(sigmet.id) { mutableStateOf(false) }
    val hazardColor = sigmetCardHazardColor(sigmet.hazard, sigmet.qualifier)

    Surface(
        shape = RoundedCornerShape(10.dp),
        color = hazardColor.copy(alpha = 0.06f),
        border = BorderStroke(1.dp, hazardColor.copy(alpha = 0.35f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // Top badges: Hazard pill + Flight Level band + FIR + Series ID
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    val hazardTitle = listOfNotNull(
                        sigmet.qualifier?.takeIf { it.isNotBlank() },
                        sigmet.hazard.ifBlank { "SIGMET" },
                    ).joinToString(" ").uppercase()

                    Surface(
                        shape = RoundedCornerShape(5.dp),
                        color = hazardColor.copy(alpha = 0.16f),
                        border = BorderStroke(1.dp, hazardColor.copy(alpha = 0.45f)),
                    ) {
                        Text(
                            text = hazardTitle,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            color = hazardColor,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        )
                    }

                    // Flight level band badge (e.g. FL050–FL240)
                    Surface(
                        shape = RoundedCornerShape(5.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant,
                    ) {
                        Text(
                            text = formatFlightLevelBand(sigmet.baseFt, sigmet.topFt),
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        )
                    }

                    // FIR badge
                    Text(
                        text = buildString {
                            append(sigmet.firId)
                            sigmet.seriesId?.takeIf { it.isNotBlank() }?.let { append(" #$it") }
                        },
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier.padding(top = 1.dp),
                    )
                }

                // Validity window
                val validWindow = buildString {
                    if (sigmet.validFrom.isNotBlank()) {
                        append(formatUtcTimestampShort(sigmet.validFrom))
                        append("–")
                    }
                    if (sigmet.validTo.isNotBlank()) {
                        append(formatUtcTimestampShort(sigmet.validTo))
                    }
                }
                if (validWindow.isNotBlank()) {
                    Text(
                        text = validWindow,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Route span & movement summary
            val details = buildList {
                if (sigmet.firName.isNotBlank()) add(sigmet.firName)
                if (sigmet.enrouteDistanceFromNm != null && sigmet.enrouteDistanceToNm != null) {
                    add("Route ${sigmet.enrouteDistanceFromNm.roundToInt()}–${sigmet.enrouteDistanceToNm.roundToInt()} NM")
                } else if (sigmet.minDistanceNm != null) {
                    if (sigmet.minDistanceNm <= 1.0) {
                        add("Intersects route")
                    } else {
                        add("${sigmet.minDistanceNm.roundToInt()} NM from route")
                    }
                }
                if (!sigmet.direction.isNullOrBlank()) {
                    val spd = sigmet.speedKt?.let { " at $it kt" }.orEmpty()
                    add("Moving ${sigmet.direction}$spd")
                }
            }
            if (details.isNotEmpty()) {
                Text(
                    text = details.joinToString(" · "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }

            // Bottom action bar: Raw SIGMET toggle + View on Map
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (sigmet.rawText.isNotBlank()) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.clickable { rawExpanded = !rawExpanded },
                    ) {
                        Text(
                            text = if (rawExpanded) "Hide raw SIGMET" else "Raw SIGMET text",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Icon(
                            imageVector = if (rawExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(16.dp),
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
                                    layer = "sigmets",
                                    sigmetKey = sigmet.id,
                                    distanceNm = sigmet.enrouteDistanceFromNm,
                                ),
                            )
                        },
                    )
                }
            }

            // Expandable raw SIGMET text
            if (sigmet.rawText.isNotBlank()) {
                AnimatedVisibility(visible = rawExpanded) {
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = MaterialTheme.colorScheme.surface,
                        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            text = sigmet.rawText,
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = FontFamily.Monospace,
                            modifier = Modifier.padding(10.dp),
                        )
                    }
                }
            }
        }
    }
}

private fun sigmetCardHazardColor(hazard: String, qualifier: String?): Color {
    val h = hazard.uppercase()
    val q = qualifier?.uppercase().orEmpty()
    return when {
        h in setOf("TS", "TSGR", "CB", "SQL") || q.contains("SEV") -> AssessmentRed
        else -> AssessmentAmber
    }
}
