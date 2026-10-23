package aero.flyfun.weather.ui.observed

import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.LiveFocus
import aero.flyfun.weather.model.LiveGlanceTerminal
import aero.flyfun.weather.model.RunwayEnd
import aero.flyfun.weather.model.RunwayWindWire
import aero.flyfun.weather.model.TafHour
import aero.flyfun.weather.model.WeatherWord
import aero.flyfun.weather.model.rules.AirportCardRules
import aero.flyfun.weather.model.rules.AirportCardRules.Role
import aero.flyfun.weather.ui.theme.AssessmentAmber
import aero.flyfun.weather.ui.theme.AssessmentRed
import aero.flyfun.weather.ui.theme.CategoryUnknown
import aero.flyfun.weather.ui.theme.flightCategoryColor
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AcUnit
import androidx.compose.material.icons.filled.Air
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Dehaze
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Grain
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Thunderstorm
import androidx.compose.material.icons.filled.Tornado
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.WaterDrop
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * Departure / arrival airport cards on the Observed tab (#883, part of #737).
 *
 * Agreed design: the "Airport Card Lab" mockup (Now → at ETA, category meter on,
 * TAF strip on, dial large, departure folded after Start). Every value is the
 * server's (#880: `glance.departure` / `glance.destination` and their `airport`
 * row). This file only lays them out; the choices of what to draw live in
 * [AirportCardRules] so they are unit-tested.
 *
 * SYNC: web `airport-card-view.ts` (#881) and iOS (#882) draw the same card.
 */

private val WIDE_LAYOUT_MIN = 600.dp

/** The dial in the card's corner: fixed, never stretched (phone ~92 dp, tablet ~120 dp). */
private val DIAL_PHONE = 92.dp
private val DIAL_TABLET = 120.dp

/**
 * Both cards: departure then arrival, side by side when there is room
 * (tablet / landscape), stacked on a phone. Once the server folds the
 * departure, the arrival comes first and the departure follows as one line
 * that opens on the full card; whether it is open is remembered per flight.
 */
@Composable
fun AirportCardsSection(
    departure: LiveGlanceTerminal?,
    destination: LiveGlanceTerminal?,
    flightId: String,
    modifier: Modifier = Modifier,
    nowMs: Long = System.currentTimeMillis(),
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val slots = AirportCardRules.slots(departure, destination)
    if (slots.isEmpty()) return

    val (departureOpen, toggleDeparture) = rememberFlightFlag("departure_open", flightId)

    BoxWithConstraints(modifier = modifier.fillMaxWidth()) {
        val wide = maxWidth >= WIDE_LAYOUT_MIN
        val dialSize = if (wide) DIAL_TABLET else DIAL_PHONE
        val anyFolded = slots.any { it.folded }

        // Side by side only for two full cards: a folded line stacks under the arrival.
        if (wide && !anyFolded && slots.size == 2) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                slots.forEach {
                    AirportCardSlot(it, departureOpen, toggleDeparture, dialSize, nowMs, onSelectFocus, Modifier.weight(1f))
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                slots.forEach {
                    AirportCardSlot(it, departureOpen, toggleDeparture, dialSize, nowMs, onSelectFocus, Modifier.fillMaxWidth())
                }
            }
        }
    }
}

@Composable
private fun AirportCardSlot(
    slot: AirportCardRules.Slot,
    departureOpen: Boolean,
    onToggleDeparture: () -> Unit,
    dialSize: Dp,
    nowMs: Long,
    onSelectFocus: ((LiveFocus) -> Unit)?,
    modifier: Modifier,
) {
    if (slot.folded) {
        FoldedDeparture(
            block = slot.block,
            open = departureOpen,
            onToggle = onToggleDeparture,
            dialSize = dialSize,
            nowMs = nowMs,
            onSelectFocus = onSelectFocus,
            modifier = modifier,
        )
    } else {
        AirportCard(
            block = slot.block,
            role = slot.role,
            dialSize = dialSize,
            nowMs = nowMs,
            onSelectFocus = onSelectFocus,
            modifier = modifier,
        )
    }
}

/** The full card for one terminal. */
@Composable
fun AirportCard(
    block: LiveGlanceTerminal,
    role: Role,
    modifier: Modifier = Modifier,
    dialSize: Dp = DIAL_PHONE,
    nowMs: Long = System.currentTimeMillis(),
    showBorder: Boolean = true,
    onSelectFocus: ((LiveFocus) -> Unit)? = null,
) {
    val a = block.airport
    val borderColor = if (block.alert) AssessmentAmber.copy(alpha = 0.7f) else MaterialTheme.colorScheme.outlineVariant
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        border = if (showBorder) BorderStroke(1.dp, borderColor) else null,
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            CardHeader(block, role)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (a != null) {
                        MeterRow(
                            icon = if (a.metarCavok) Icons.Default.WbSunny else Icons.Default.Cloud,
                            label = "Ceiling",
                            text = a.metarSkyText,
                            position = AirportCardRules.meterPosition(a.metarCeilingMeter),
                        )
                        MeterRow(
                            icon = Icons.Default.Visibility,
                            label = "Visibility",
                            text = a.metarVisibilityText,
                            position = AirportCardRules.meterPosition(a.metarVisibilityMeter),
                        )
                        WeatherWordsRow(a.metarWeatherWords)
                        WindRow(a)
                    } else {
                        // No airport row (an older server, or no corridor row): the block's own lines.
                        RowText(block.metarText)
                        block.windText?.let { RowText(it) }
                    }
                    NearRows(block, onSelectFocus)
                    ProvenanceRow(block, role, a, nowMs)
                    block.proxyCaption?.let {
                        Text(
                            text = it,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                a?.runwayWind?.takeIf { it.runways.isNotEmpty() }?.let { rw ->
                    CompactRunwayWindDial(rw, dialSize)
                }
            }
            a?.tafHours?.takeIf { it.isNotEmpty() }?.let { TafStrip(it) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CardHeader(block: LiveGlanceTerminal, role: Role) {
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        itemVerticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = if (role == Role.DEPARTURE) "DEPARTURE" else "ARRIVAL",
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = block.icao,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
        )
        block.name?.takeIf { it.isNotBlank() }?.let {
            Text(
                text = it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        val boxes = AirportCardRules.headerBoxes(block, role)
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            boxes.forEachIndexed { i, box ->
                if (i == 1 && !box.dashed) {
                    Text("→", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                CategoryBox(box.category, box.caption, dashed = box.dashed)
            }
        }
    }
}

/** A category box: filled = prevailing, dashed outline = a TEMPO/PROB group. */
@Composable
internal fun CategoryBox(category: String, caption: String? = null, dashed: Boolean = false, small: Boolean = false) {
    val color = flightCategoryColor(category)
    val shape = RoundedCornerShape(5.dp)
    val boxModifier = if (dashed) {
        Modifier.dashedBorder(color)
    } else {
        Modifier.background(color, shape)
    }
    Column(
        modifier = boxModifier.padding(horizontal = if (small) 4.dp else 6.dp, vertical = if (small) 1.dp else 2.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = category,
            fontSize = if (small) 10.sp else 11.sp,
            fontWeight = FontWeight.ExtraBold,
            fontFamily = FontFamily.Monospace,
            color = if (dashed) color else Color.White,
        )
        if (!caption.isNullOrBlank() && !small) {
            Text(
                text = caption,
                fontSize = 8.sp,
                color = if (dashed) color else Color.White.copy(alpha = 0.9f),
            )
        }
    }
}

private fun Modifier.dashedBorder(color: Color): Modifier =
    drawBehind {
        drawRoundRect(
            color = color,
            cornerRadius = CornerRadius(5.dp.toPx()),
            style = Stroke(
                width = 1.5.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()), 0f),
            ),
        )
    }

/** A row with the four equal bands LIFR | IFR | MVFR | VFR and a dot at the server's position. */
@Composable
private fun MeterRow(icon: ImageVector, label: String, text: String?, position: Float?) {
    val dotColor = MaterialTheme.colorScheme.onSurface
    val ringColor = MaterialTheme.colorScheme.surface
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(icon, contentDescription = label, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            text = text ?: "—",
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.width(92.dp),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Canvas(
            modifier = Modifier
                .weight(1f)
                .height(12.dp),
        ) {
            val bands = listOf("LIFR", "IFR", "MVFR", "VFR")
            val gap = 2.dp.toPx()
            val bandW = (size.width - gap * 3) / 4f
            val barH = 5.dp.toPx()
            val top = (size.height - barH) / 2f
            bands.forEachIndexed { i, cat ->
                drawRoundRect(
                    color = flightCategoryColor(cat).copy(alpha = 0.55f),
                    topLeft = Offset(i * (bandW + gap), top),
                    size = androidx.compose.ui.geometry.Size(bandW, barH),
                    cornerRadius = CornerRadius(2.dp.toPx()),
                )
            }
            if (position != null) {
                val x = position * size.width
                drawCircle(color = ringColor, radius = 5.5.dp.toPx(), center = Offset(x, size.height / 2f))
                drawCircle(color = dotColor, radius = 4.dp.toPx(), center = Offset(x, size.height / 2f))
            }
        }
    }
}

/** The server's weather-word icon kinds onto Material symbols. */
private fun weatherIcon(kind: String): ImageVector = when (kind) {
    "thunder" -> Icons.Default.Thunderstorm
    "hail", "drizzle" -> Icons.Default.Grain
    "snow", "freezing" -> Icons.Default.AcUnit
    "showers", "rain" -> Icons.Default.WaterDrop
    "fog", "mist", "haze", "smoke", "dust" -> Icons.Default.Dehaze
    "squall" -> Icons.Default.Air
    "funnel" -> Icons.Default.Tornado
    else -> Icons.Default.Cloud
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WeatherWordsRow(words: List<WeatherWord>) {
    if (words.isEmpty()) return
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        words.forEach { w ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Icon(weatherIcon(w.icon), contentDescription = w.code, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(w.text, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/** "290/15G25", the best runway chip and the crosswind, toned only by the server's advisory. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WindRow(a: AirportObservation) {
    val wind = a.metarWindShort ?: return
    val x = a.metarCrosswind
    val tone = when (AirportCardRules.crosswindTone(a)) {
        "red" -> AssessmentRed
        "amber" -> AssessmentAmber
        else -> null
    }
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        itemVerticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Default.Air, contentDescription = "Wind", modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(wind, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.SemiBold)
        if (x != null) {
            Surface(
                shape = RoundedCornerShape(4.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Text(
                    text = "RWY ${x.runway}",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp),
                )
            }
            Text(
                text = "${AirportCardRules.crosswindGlyph(x.side)}${x.text} X",
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                fontWeight = if (tone != null) FontWeight.Bold else FontWeight.Normal,
                color = tone ?: MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

/** Cells and lightning near the field, in the server's words; amber only on the server's row alert. */
@Composable
private fun NearRows(block: LiveGlanceTerminal, onSelectFocus: ((LiveFocus) -> Unit)?) {
    NearRow(Icons.Default.Radar, block.cellsText, block.cellsAlert, block.focus, onSelectFocus)
    block.cells.forEach { cell ->
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 22.dp)
                .then(
                    if (cell.focus != null && onSelectFocus != null) {
                        Modifier.clickable { onSelectFocus(cell.focus) }
                    } else {
                        Modifier
                    },
                ),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = cell.text,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            if (cell.focus != null && onSelectFocus != null) {
                Icon(Icons.Default.ChevronRight, contentDescription = null, modifier = Modifier.size(14.dp))
            }
        }
    }
    block.lightningText?.let { NearRow(Icons.Default.Bolt, it, block.lightningAlert, block.focus, onSelectFocus) }
}

@Composable
private fun NearRow(
    icon: ImageVector,
    text: String,
    alert: Boolean,
    focus: LiveFocus?,
    onSelectFocus: ((LiveFocus) -> Unit)?,
) {
    if (text.isBlank()) return
    val color = if (alert) AssessmentAmber else MaterialTheme.colorScheme.onSurface
    val tappable = focus != null && onSelectFocus != null
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (tappable) Modifier.clickable { onSelectFocus?.invoke(focus!!) } else Modifier),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(16.dp), tint = if (alert) AssessmentAmber else MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = color,
            fontWeight = if (alert) FontWeight.SemiBold else FontWeight.Normal,
            modifier = Modifier.weight(1f),
        )
        if (tappable) {
            Icon(Icons.Default.ChevronRight, contentDescription = null, modifier = Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** One piece of the provenance line. */
private data class ProvPart(val text: String, val tone: Color? = null, val tag: Boolean = false, val mono: Boolean = false)

/** "METAR 09:30Z · 8 min" (amber past the stale age) · SPECI · trend · ETD/ETA. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ProvenanceRow(block: LiveGlanceTerminal, role: Role, a: AirportObservation?, nowMs: Long) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val style = MaterialTheme.typography.labelSmall
    val parts = buildList {
        val metarAt = a?.metarTime ?: block.metarTime
        if (metarAt != null) {
            val min = AirportCardRules.minutesAgo(metarAt, nowMs)
            val text = buildString {
                append("METAR ${AirportCardRules.hhmmZ(metarAt)}")
                if (min != null) append(" · ${AirportCardRules.ageText(min)}")
            }
            add(ProvPart(text, tone = if (AirportCardRules.isStale(min)) AssessmentAmber else null, mono = true))
        }
        if (a?.metarReportType == "SPECI") add(ProvPart("SPECI", tone = AssessmentAmber, tag = true))
        AirportCardRules.trendText(a)?.let {
            add(ProvPart(it, tone = if (a?.metarTrend == "worse") AssessmentAmber else null))
        }
        AirportCardRules.tafReason(block)?.let { add(ProvPart(it)) }
        AirportCardRules.timeText(block, role)?.let { add(ProvPart(it)) }
    }
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
        itemVerticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Default.Schedule, contentDescription = null, modifier = Modifier.size(13.dp), tint = muted)
        parts.forEachIndexed { i, part ->
            if (i > 0) Text("·", style = style, color = muted)
            if (part.tag) {
                Surface(shape = RoundedCornerShape(3.dp), color = (part.tone ?: muted).copy(alpha = 0.16f)) {
                    Text(
                        part.text,
                        style = style,
                        fontWeight = FontWeight.Bold,
                        color = part.tone ?: muted,
                        modifier = Modifier.padding(horizontal = 4.dp),
                    )
                }
            } else {
                Text(
                    part.text,
                    style = style,
                    fontFamily = if (part.mono) FontFamily.Monospace else null,
                    color = part.tone ?: muted,
                )
            }
        }
    }
}

@Composable
private fun RowText(text: String) {
    Text(text = text, style = MaterialTheme.typography.bodySmall)
}

/** One cell per TAF hour: prevailing fill, TEMPO/PROB hatch when worse, the ETA hour outlined. */
@Composable
private fun TafStrip(hours: List<TafHour>) {
    val outline = MaterialTheme.colorScheme.onSurface
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        hours.forEach { h ->
            Column(
                modifier = Modifier.weight(1f),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                val prevailing = h.prevailingCategory?.let { flightCategoryColor(it) } ?: CategoryUnknown.copy(alpha = 0.3f)
                val temporary = h.temporaryCategory?.let { flightCategoryColor(it) }
                Canvas(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(12.dp)
                        .clipToBounds(),
                ) {
                    drawRect(color = prevailing.copy(alpha = 0.75f))
                    if (temporary != null) {
                        clipRect {
                            val step = 5.dp.toPx()
                            var x = -size.height
                            while (x < size.width) {
                                drawLine(
                                    color = temporary,
                                    start = Offset(x, size.height),
                                    end = Offset(x + size.height, 0f),
                                    strokeWidth = 2.dp.toPx(),
                                )
                                x += step
                            }
                        }
                    }
                    if (h.isEta) {
                        drawRect(color = outline, style = Stroke(width = 2.dp.toPx()))
                    }
                }
                Text(
                    text = AirportCardRules.hourLabel(h.time),
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = if (h.isEta) FontWeight.Bold else FontWeight.Normal,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/** The #758 runway + wind dial at a fixed size, from the server's picture: solid = METAR, dashed = TAF. */
@Composable
private fun CompactRunwayWindDial(picture: RunwayWindWire, size: Dp) {
    val pairs = remember(picture) {
        picture.runways.mapNotNull { r ->
            val first = r.ends.firstOrNull() ?: return@mapNotNull null
            PhysicalRunwayPair(
                primary = RunwayEnd(id = first.ident, headingDeg = first.headingTrue),
                reciprocal = r.ends.getOrNull(1)?.let { RunwayEnd(id = it.ident, headingDeg = it.headingTrue) },
            )
        }
    }
    val metar = picture.wind("metar")
    val taf = picture.wind("taf")
    // The server's best end (one picker for the dial and the table).
    val bestId = metar?.bestEnd ?: taf?.bestEnd
    val favored = pairs.flatMap { listOfNotNull(it.primary, it.reciprocal) }.firstOrNull { it.id == bestId }
    Box(modifier = Modifier.size(size)) {
        RunwayCompassCanvas(
            physicalPairs = pairs,
            favoredEnd = favored,
            // VRB: a speed with no direction, so no arrow.
            windDirDeg = metar?.wind?.let { w -> w.directionTrue.takeUnless { w.variable } },
            windSpeedKt = metar?.wind?.speedKt,
            windGustKt = metar?.wind?.gustKt,
            tafWindDirDeg = taf?.wind?.let { w -> w.directionTrue.takeUnless { w.variable } },
            tafWindSpeedKt = taf?.wind?.speedKt,
            dialSize = size,
            compact = true,
        )
    }
}

/** The departure after it folded: one tappable line that opens on the full card. */
@Composable
private fun FoldedDeparture(
    block: LiveGlanceTerminal,
    open: Boolean,
    onToggle: () -> Unit,
    dialSize: Dp,
    nowMs: Long,
    onSelectFocus: ((LiveFocus) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(onClick = onToggle)
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                AirportCardRules.foldedCategory(block)?.let { CategoryBox(it, small = true) }
                Text(
                    text = block.foldedText ?: block.icao,
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.weight(1f),
                    maxLines = if (open) 3 else 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Icon(
                    imageVector = if (open) Icons.Default.ExpandMore else Icons.Default.ChevronRight,
                    contentDescription = if (open) "Hide departure" else "Show departure",
                    modifier = Modifier.size(18.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (open) {
                AirportCard(
                    block = block,
                    role = Role.DEPARTURE,
                    dialSize = dialSize,
                    nowMs = nowMs,
                    showBorder = false,
                    onSelectFocus = onSelectFocus,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

/** "Offline · showing data from 09:40Z, 12 min ago" at the top of the Observed tab. */
@Composable
fun ObservedOfflineBanner(text: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = AssessmentAmber.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, AssessmentAmber.copy(alpha = 0.45f)),
        shape = RoundedCornerShape(10.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                imageVector = Icons.Default.CloudOff,
                contentDescription = null,
                tint = AssessmentAmber,
                modifier = Modifier.size(16.dp),
            )
            Text(
                text = text,
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

/** The flight strip (#816): the server's one line, slim, above everything else. */
@Composable
fun ObservedFlightStrip(text: String, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .background(MaterialTheme.colorScheme.primary, CircleShape),
        )
        Text(
            text = text,
            style = MaterialTheme.typography.labelMedium,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
