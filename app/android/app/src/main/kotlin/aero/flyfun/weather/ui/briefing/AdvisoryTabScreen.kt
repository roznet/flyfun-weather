package aero.flyfun.weather.ui.briefing

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.ShowChart
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.AdvisoriesResponse
import aero.flyfun.weather.model.AdvisoryCatalogEntry
import aero.flyfun.weather.model.AirportConditions
import aero.flyfun.weather.model.AirportConditionsSummary
import aero.flyfun.weather.model.AirportModelCondition
import aero.flyfun.weather.model.AlternateAirport
import aero.flyfun.weather.model.AlternateRequirement
import aero.flyfun.weather.model.AlternatesResponse
import aero.flyfun.weather.model.Assessment
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.DebriefRequest
import aero.flyfun.weather.model.DebriefResponse
import aero.flyfun.weather.model.DebriefTaxonomy
import aero.flyfun.weather.model.DigestResponse
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.FocusIntent
import aero.flyfun.weather.model.LiveChanges
import aero.flyfun.weather.model.ModelAdvisoryResult
import aero.flyfun.weather.model.OperationalFlag
import aero.flyfun.weather.model.PackMetaResponse
import aero.flyfun.weather.model.RegAlternateTrigger
import aero.flyfun.weather.model.RouteAdvisoryResult
import aero.flyfun.weather.model.TimeCandidate
import aero.flyfun.weather.model.TimeOptionsResponse
import aero.flyfun.weather.model.TimeScanJobStatus
import aero.flyfun.weather.viewmodel.BriefingViewModel
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * The **Advisory** tab screen:
 * - Sticky [SectionSpyBar] jumping between `Verdict`, `Advisories`, `Airports`, `Alternates`, `Timing`, `Watch`.
 * - Accented Verdict Hero card (`GREEN` / `AMBER` / `RED` / `UNAVAILABLE` + `assessmentReason`),
 *   flight-day `ObservedTeaserRow`, digest altitude warning, digest 👍/👎 feedback row, and `DebriefCard`.
 * - Responsive Advisory Grid: `AMBER`/`RED` as expandable [AdvisoryCard]s and `GREEN` collapsed into [GreenAdvisoryStrip].
 * - [AirportConditionsSection], [AlternatesSection], [TimingScenariosSection], and [WatchSection].
 */
@Composable
fun AdvisoryTabScreen(
    viewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val flight by viewModel.flight.collectAsState()
    val selectedPack by viewModel.selectedPack.collectAsState()
    val availableTabs by viewModel.availableTabs.collectAsState()
    val advisories by viewModel.advisories.collectAsState()
    val digest by viewModel.digest.collectAsState()
    val alternatesState by viewModel.alternates.collectAsState()
    val timeOptions by viewModel.timeOptions.collectAsState()
    val debrief by viewModel.debrief.collectAsState()
    val debriefTaxonomy by viewModel.debriefTaxonomy.collectAsState()
    val liveLayer by viewModel.liveLayer.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()

    val effectiveAlternates = alternatesState ?: advisories?.alternates
    val hasAirports = advisories?.airportConditions != null
    val hasAlternates = effectiveAlternates != null &&
        (effectiveAlternates.candidates.isNotEmpty() || effectiveAlternates.requirement != null)
    val hasTiming = timeOptions != null
    val watchItems = digest?.watchItems?.filter { it.isNotBlank() }.orEmpty()
    val hasWatch = watchItems.isNotEmpty()
    val showsObservedTeaser = BriefingTab.OBSERVED in availableTabs || liveLayer != null

    val listState = rememberLazyListState()

    // Compute section list and exact LazyColumn indices for SectionSpyBar
    val (spySections, sectionIndices) = remember(hasAirports, hasAlternates, hasTiming, hasWatch) {
        val sections = mutableListOf<SpySection>()
        val indices = mutableMapOf<String, Int>()
        var nextIdx = 0

        sections.add(SpySection("verdict", "Verdict"))
        indices["verdict"] = nextIdx++

        sections.add(SpySection("advisories", "Advisories"))
        indices["advisories"] = nextIdx++

        if (hasAirports) {
            sections.add(SpySection("airports", "Airports"))
            indices["airports"] = nextIdx++
        }
        if (hasAlternates) {
            sections.add(SpySection("alternates", "Alternates"))
            indices["alternates"] = nextIdx++
        }
        if (hasTiming) {
            sections.add(SpySection("timing", "Timing"))
            indices["timing"] = nextIdx++
        }
        if (hasWatch) {
            sections.add(SpySection("watch", "Watch"))
            indices["watch"] = nextIdx++
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
            // 1. Verdict section (Hero + Observed Teaser + Altitude Warning + Feedback + Debrief)
            item(key = "verdict") {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    VerdictHeroCard(
                        pack = selectedPack,
                        digest = digest,
                        flight = flight,
                    )

                    if (showsObservedTeaser) {
                        ObservedTeaserRow(
                            liveChanges = liveLayer?.changes,
                            onOpenObserved = { viewModel.selectTab(BriefingTab.OBSERVED) },
                        )
                    }

                    val packAlt = advisories?.cruiseAltitudeFt
                    val flightAlt = flight?.cruiseAltitudeFt
                    if (packAlt != null && flightAlt != null && packAlt > 0 && flightAlt > 0 && packAlt != flightAlt) {
                        DigestAltitudeWarningBanner(
                            packAltitudeFt = packAlt,
                            flightAltitudeFt = flightAlt,
                            onRecalculate = { viewModel.recalculateAdvisories(flightAlt) },
                        )
                    }

                    if (digest != null) {
                        DigestFeedbackRow(
                            onSubmitFeedback = { thumbsUp, comment ->
                                viewModel.submitDigestFeedback(thumbsUp, comment)
                            },
                        )
                    }

                    val currentFlight = flight
                    if (currentFlight != null && (currentFlight.isPast() || currentFlight.debriefEligible == true || debrief != null)) {
                        DebriefCard(
                            debrief = debrief,
                            taxonomy = debriefTaxonomy ?: debrief?.taxonomy,
                            advisories = advisories,
                            onSubmit = { req -> viewModel.submitDebrief(req) },
                            onDelete = { viewModel.deleteDebrief() },
                        )
                    }
                }
            }

            // 2. Advisories section
            item(key = "advisories") {
                AdvisoriesGridSection(
                    advisoriesResponse = advisories,
                    digest = digest,
                    isLoading = isLoading,
                    onFocusIntent = { intent -> viewModel.applyFocusIntent(intent) },
                )
            }

            // 3. Airport conditions section
            if (hasAirports) {
                item(key = "airports") {
                    advisories?.airportConditions?.let { conditions ->
                        AirportConditionsSection(conditions = conditions)
                    }
                }
            }

            // 4. Alternates section
            if (hasAlternates) {
                item(key = "alternates") {
                    effectiveAlternates?.let { alt ->
                        AlternatesSection(alternates = alt)
                    }
                }
            }

            // 5. Timing Scenarios section
            if (hasTiming) {
                item(key = "timing") {
                    timeOptions?.let { options ->
                        TimingScenariosSection(
                            timeOptions = options,
                            catalog = advisories?.catalog.orEmpty(),
                            isEditable = flight?.isEditable ?: true,
                            onConfirmTimeOption = { depTime -> viewModel.confirmTimeOption(depTime) },
                        )
                    }
                }
            }

            // 6. Watch section
            if (hasWatch) {
                item(key = "watch") {
                    WatchSection(watchItems = watchItems)
                }
            }
        }
    }
}

// ============================================================================
// Verdict Hero, Observed Teaser, Altitude Warning, Feedback & Debrief
// ============================================================================

@Composable
private fun VerdictHeroCard(
    pack: PackMetaResponse?,
    digest: DigestResponse?,
    flight: FlightResponse?,
    modifier: Modifier = Modifier,
) {
    val assessment = when {
        pack != null && pack.assessment != null -> pack.resolvedAssessment
        digest != null && digest.assessment != null -> digest.resolvedAssessment
        flight != null -> flight.resolvedAssessment
        else -> Assessment.UNAVAILABLE
    }
    val accentColor = BriefingPalette.colorForAssessment(assessment)
    val rawReason = pack?.assessmentReason?.takeIf { it.isNotBlank() }
        ?: digest?.assessmentReason?.takeIf { it.isNotBlank() }
        ?: digest?.synoptic?.takeIf { it.isNotBlank() }

    val reasonText = when {
        assessment == Assessment.UNAVAILABLE && rawReason.isNullOrBlank() ->
            "We have no usable model data for this route. Treat this as missing information, not as clear weather."
        !rawReason.isNullOrBlank() -> rawReason
        else -> "Briefing verdict for ${flight?.shortTitle ?: "this route"}."
    }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = accentColor.copy(alpha = 0.10f),
        border = BorderStroke(0.5.dp, accentColor.copy(alpha = 0.35f)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(IntrinsicSize.Min),
        ) {
            Box(
                modifier = Modifier
                    .width(6.dp)
                    .fillMaxHeight()
                    .background(accentColor),
            )
            Column(
                modifier = Modifier
                    .padding(16.dp)
                    .weight(1f),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    AssessmentPillBadge(assessment = assessment)
                    if (pack != null) {
                        Text(
                            text = "D-${pack.daysOut} Forecast Verdict",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Text(
                    text = reasonText,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}

@Composable
internal fun ObservedTeaserRow(
    liveChanges: LiveChanges?,
    onOpenObserved: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val alertCount = liveChanges?.alertCount ?: liveChanges?.changes?.count { it.newAlert || it.tier == "alert" } ?: 0
    val changeCount = liveChanges?.changes?.size ?: 0
    val tint = if (alertCount > 0) BriefingPalette.Red else MaterialTheme.colorScheme.primary

    val labelText = when {
        liveChanges == null -> "Changes since briefing · Tap to view Observed ›"
        changeCount == 0 -> "Observed: no significant change since briefing · Tap to view Observed ›"
        else -> {
            val base = if (changeCount == 1) "1 change since briefing" else "$changeCount changes since briefing"
            val alertPart = if (alertCount > 0) " · $alertCount ${if (alertCount == 1) "alert" else "alerts"}" else ""
            "$base$alertPart · Tap to view Observed ›"
        }
    }

    Surface(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onOpenObserved),
        shape = RoundedCornerShape(10.dp),
        color = tint.copy(alpha = 0.10f),
        border = BorderStroke(0.5.dp, tint.copy(alpha = 0.3f)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Sensors,
                contentDescription = null,
                tint = tint,
                modifier = Modifier.size(20.dp),
            )
            Text(
                text = labelText,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.weight(1f),
            )
            Icon(
                imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
internal fun DigestAltitudeWarningBanner(
    packAltitudeFt: Int,
    flightAltitudeFt: Int,
    onRecalculate: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = BriefingPalette.Amber.copy(alpha = 0.12f),
        border = BorderStroke(0.5.dp, BriefingPalette.Amber.copy(alpha = 0.4f)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Warning,
                contentDescription = null,
                tint = BriefingPalette.Amber,
                modifier = Modifier.size(20.dp),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Altitude mismatch: pack graded at ${packAltitudeFt} ft vs flight cruise ${flightAltitudeFt} ft",
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = "Recalculate advisories for ${flightAltitudeFt} ft without re-fetching weather models.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TextButton(onClick = onRecalculate) {
                Text("Recalculate")
            }
        }
    }
}

@Composable
private fun DigestFeedbackRow(
    onSubmitFeedback: (thumbsUp: Boolean, comment: String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    var selectedVote by rememberSaveable { mutableStateOf<Boolean?>(null) }
    var showCommentField by rememberSaveable { mutableStateOf(false) }
    var commentText by rememberSaveable { mutableStateOf("") }
    var submitted by rememberSaveable { mutableStateOf(false) }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = if (submitted) "Thanks for rating this briefing digest" else "Was this briefing assessment helpful?",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = {
                        selectedVote = true
                        showCommentField = false
                        submitted = true
                        onSubmitFeedback(true, null)
                    },
                    modifier = Modifier.size(32.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.ThumbUp,
                        contentDescription = "Helpful",
                        tint = if (selectedVote == true) BriefingPalette.Green else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp),
                    )
                }
                IconButton(
                    onClick = {
                        selectedVote = false
                        showCommentField = true
                    },
                    modifier = Modifier.size(32.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.ThumbDown,
                        contentDescription = "Not helpful",
                        tint = if (selectedVote == false) BriefingPalette.Red else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp),
                    )
                }
            }

            AnimatedVisibility(visible = showCommentField) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedTextField(
                        value = commentText,
                        onValueChange = { commentText = it },
                        placeholder = { Text("What did the briefing miss or get wrong?") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    Button(
                        onClick = {
                            showCommentField = false
                            submitted = true
                            onSubmitFeedback(false, commentText.takeIf { it.isNotBlank() })
                        },
                    ) {
                        Text("Send")
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun DebriefCard(
    debrief: DebriefResponse?,
    taxonomy: DebriefTaxonomy?,
    advisories: AdvisoriesResponse?,
    onSubmit: (DebriefRequest) -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var isEditing by rememberSaveable { mutableStateOf(false) }

    val existingDecision = debrief?.outcomes?.get("decision") ?: "flown"
    var decision by rememberSaveable(debrief) { mutableStateOf(existingDecision) }
    var noteText by rememberSaveable(debrief) { mutableStateOf(debrief?.otherText.orEmpty()) }
    val outcomesMap = remember(debrief) {
        mutableStateMapOf<String, String>().apply {
            debrief?.outcomes?.forEach { (k, v) ->
                if (k != "decision") put(k, v)
            }
        }
    }

    val categories = remember(taxonomy, advisories) {
        val taxCats = taxonomy?.categories.orEmpty()
        if (taxCats.isNotEmpty()) {
            taxCats
        } else {
            listOf(
                aero.flyfun.weather.model.TaxonomyCategory(id = "icing", label = "Icing"),
                aero.flyfun.weather.model.TaxonomyCategory(id = "turbulence", label = "Turbulence"),
                aero.flyfun.weather.model.TaxonomyCategory(id = "clouds", label = "Clouds & Ceiling"),
                aero.flyfun.weather.model.TaxonomyCategory(id = "winds", label = "Winds"),
                aero.flyfun.weather.model.TaxonomyCategory(id = "convective", label = "Convection"),
                aero.flyfun.weather.model.TaxonomyCategory(id = "visibility", label = "Visibility"),
            )
        }
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface,
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "Post-Flight Debrief",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                TextButton(onClick = { isEditing = !isEditing }) {
                    Icon(
                        imageVector = Icons.Default.Edit,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(if (isEditing) "Close" else if (debrief == null) "Add Debrief" else "Edit")
                }
            }

            if (!isEditing) {
                if (debrief != null) {
                    val dec = debrief.outcomes["decision"] ?: "flown"
                    val decLabel = when (dec) {
                        "cancelled" -> "Cancelled"
                        "monitoring" -> "Monitoring only"
                        else -> "Flown"
                    }
                    val decColor = when (dec) {
                        "cancelled" -> BriefingPalette.Red
                        "monitoring" -> BriefingPalette.Unavailable
                        else -> BriefingPalette.Green
                    }
                    Surface(
                        color = decColor.copy(alpha = 0.14f),
                        shape = RoundedCornerShape(50),
                    ) {
                        Text(
                            text = decLabel,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = decColor,
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        )
                    }
                    val tagEntries = debrief.outcomes.filterKeys { it != "decision" }
                    if (tagEntries.isNotEmpty()) {
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            tagEntries.forEach { (catId, outcomeVal) ->
                                val catName = categories.firstOrNull { it.id == catId }?.label ?: catId
                                Surface(
                                    color = MaterialTheme.colorScheme.surfaceVariant,
                                    shape = RoundedCornerShape(50),
                                ) {
                                    Text(
                                        text = "$catName: $outcomeVal",
                                        style = MaterialTheme.typography.labelSmall,
                                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                    )
                                }
                            }
                        }
                    }
                    if (debrief.otherText.isNotBlank()) {
                        Text(
                            text = debrief.otherText,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    Text(
                        text = "How did this flight go? Record whether you flew, cancelled, or were monitoring, plus condition tags to calibrate future forecasts.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                // Decision selector: Flown / Cancelled / Monitoring
                Text(
                    text = "What happened?",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(
                        "flown" to "Flown",
                        "cancelled" to "Cancelled",
                        "monitoring" to "Monitoring",
                    ).forEach { (id, label) ->
                        FilterChip(
                            selected = decision == id,
                            onClick = { decision = id },
                            label = { Text(label) },
                        )
                    }
                }

                if (decision != "monitoring") {
                    Text(
                        text = if (decision == "cancelled") "Contributing conditions:" else "Conditions encountered:",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        categories.forEach { cat ->
                            val currentVal = outcomesMap[cat.id]
                            val isSelected = currentVal != null
                            FilterChip(
                                selected = isSelected,
                                onClick = {
                                    if (decision == "cancelled") {
                                        if (isSelected) outcomesMap.remove(cat.id) else outcomesMap[cat.id] = "reason"
                                    } else {
                                        val next = when (currentVal) {
                                            null -> "as_forecast"
                                            "as_forecast" -> "worse"
                                            "worse" -> "better"
                                            else -> null
                                        }
                                        if (next == null) outcomesMap.remove(cat.id) else outcomesMap[cat.id] = next
                                    }
                                },
                                label = {
                                    val suffix = if (decision == "flown" && currentVal != null) " ($currentVal)" else ""
                                    Text("${cat.label}$suffix")
                                },
                            )
                        }
                    }
                }

                OutlinedTextField(
                    value = noteText,
                    onValueChange = { noteText = it },
                    label = { Text("Notes (optional)") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                    maxLines = 4,
                )

                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Button(
                        onClick = {
                            val combined = LinkedHashMap<String, String>()
                            combined["decision"] = decision
                            combined.putAll(outcomesMap)
                            onSubmit(
                                DebriefRequest(
                                    outcomes = combined,
                                    otherText = noteText.trim(),
                                ),
                            )
                            isEditing = false
                        },
                    ) {
                        Text("Save Debrief")
                    }

                    if (debrief != null) {
                        OutlinedButton(
                            onClick = {
                                onDelete()
                                isEditing = false
                            },
                        ) {
                            Icon(
                                imageVector = Icons.Default.DeleteOutline,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("Delete")
                        }
                    }
                }
            }
        }
    }
}

// ============================================================================
// Responsive Advisory Grid & Green Advisory Strip
// ============================================================================

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AdvisoriesGridSection(
    advisoriesResponse: AdvisoriesResponse?,
    digest: DigestResponse?,
    isLoading: Boolean,
    onFocusIntent: (FocusIntent) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Advisories",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
        )

        if (advisoriesResponse == null) {
            if (isLoading) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.padding(vertical = 12.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text(
                        text = "Loading route advisories…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                Text(
                    text = "No advisories available for this briefing.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Column
        }

        val sorted = remember(advisoriesResponse.advisories) {
            advisoriesResponse.advisories.sortedBy { Assessment.severityRank(it.aggregateStatus) }
        }
        val activeAdvisories = remember(sorted) {
            sorted.filter {
                val status = it.resolvedStatus
                status == Assessment.RED || status == Assessment.AMBER
            }
        }
        val greenAdvisories = remember(sorted) {
            sorted.filter { it.resolvedStatus == Assessment.GREEN }
        }

        if (activeAdvisories.isEmpty() && greenAdvisories.isEmpty()) {
            Text(
                text = "No advisories evaluated for this route.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // Responsive 1- or 2-column layout depending on available width
        if (activeAdvisories.isNotEmpty()) {
            BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
                val columns = if (maxWidth >= 600.dp) 2 else 1
                if (columns == 1) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        activeAdvisories.forEach { advisory ->
                            AdvisoryCard(
                                advisory = advisory,
                                catalog = advisoriesResponse.catalog,
                                hazardNarrative = matchHazardNarrative(advisory, advisoriesResponse.catalog, digest),
                                onFocusIntent = onFocusIntent,
                            )
                        }
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        activeAdvisories.chunked(2).forEach { rowItems ->
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                            ) {
                                rowItems.forEach { advisory ->
                                    Box(modifier = Modifier.weight(1f)) {
                                        AdvisoryCard(
                                            advisory = advisory,
                                            catalog = advisoriesResponse.catalog,
                                            hazardNarrative = matchHazardNarrative(advisory, advisoriesResponse.catalog, digest),
                                            onFocusIntent = onFocusIntent,
                                        )
                                    }
                                }
                                if (rowItems.size == 1) {
                                    Spacer(modifier = Modifier.weight(1f))
                                }
                            }
                        }
                    }
                }
            }
        }

        if (greenAdvisories.isNotEmpty()) {
            GreenAdvisoryStrip(
                advisories = greenAdvisories,
                catalog = advisoriesResponse.catalog,
                onFocusIntent = onFocusIntent,
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun AdvisoryCard(
    advisory: RouteAdvisoryResult,
    catalog: List<AdvisoryCatalogEntry>,
    hazardNarrative: String? = null,
    onFocusIntent: (FocusIntent) -> Unit,
    externalExpanded: Boolean? = null,
    onExpandChange: ((Boolean) -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    var internalExpanded by rememberSaveable(advisory.advisoryId) { mutableStateOf(false) }
    val isExpanded = externalExpanded ?: internalExpanded
    val toggleExpand = {
        val next = !isExpanded
        if (onExpandChange != null) onExpandChange(next) else internalExpanded = next
    }

    val entry = remember(advisory.advisoryId, catalog) {
        catalog.firstOrNull { it.id == advisory.advisoryId }
    }
    val title = entry?.name?.takeIf { it.isNotBlank() } ?: advisory.advisoryId
    val mitigations = advisory.mitigations.orEmpty()
    val presetId = advisory.highlights?.preset ?: advisory.advisoryId

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
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Header row
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { toggleExpand() },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                AssessmentPillBadge(assessment = advisory.resolvedStatus)
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                if (mitigations.isNotEmpty()) {
                    Icon(
                        imageVector = Icons.Default.Lightbulb,
                        contentDescription = "Mitigation advice available",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(16.dp),
                    )
                }
                Icon(
                    imageVector = if (isExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = if (isExpanded) "Collapse" else "Expand",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Aggregate detail
            if (advisory.aggregateDetail.isNotBlank()) {
                Text(
                    text = advisory.aggregateDetail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Per-model status pills
            if (advisory.perModel.isNotEmpty()) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    advisory.perModel.forEach { modelResult ->
                        ModelStatusPill(modelResult = modelResult)
                    }
                }
            }

            // Mitigation advice items
            if (mitigations.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    mitigations.forEach { mitigation ->
                        Row(
                            verticalAlignment = Alignment.Top,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.Lightbulb,
                                contentDescription = null,
                                tint = BriefingPalette.Amber,
                                modifier = Modifier
                                    .size(15.dp)
                                    .padding(top = 2.dp),
                            )
                            Text(
                                text = mitigation.text,
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    }
                }
            }

            // FocusIntent deep-link buttons
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(
                    onClick = {
                        onFocusIntent(
                            FocusIntent(
                                tab = BriefingTab.CROSS_SECTION,
                                preset = presetId,
                            ),
                        )
                    },
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.ShowChart,
                        contentDescription = null,
                        modifier = Modifier.size(15.dp),
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = "View on Cross-Section ›",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }

                TextButton(
                    onClick = {
                        onFocusIntent(
                            FocusIntent(
                                tab = BriefingTab.MAP,
                                preset = presetId,
                            ),
                        )
                    },
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.Map,
                        contentDescription = null,
                        modifier = Modifier.size(15.dp),
                    )
                    Spacer(modifier = Modifier.width(4.dp))
                    Text(
                        text = "View on Map ›",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            // Expanded per-model breakdown & hazard narrative
            AnimatedVisibility(visible = isExpanded) {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    if (!hazardNarrative.isNullOrBlank()) {
                        Text(
                            text = hazardNarrative,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                    advisory.perModel.forEach { modelResult ->
                        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = modelResult.model.uppercase(),
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.Bold,
                                )
                                Text(
                                    text = "${modelResult.affectedPct.roundToInt()}% affected",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            if (modelResult.detail.isNotBlank()) {
                                Text(
                                    text = modelResult.detail,
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
}

@Composable
private fun ModelStatusPill(
    modelResult: ModelAdvisoryResult,
    modifier: Modifier = Modifier,
) {
    val bgColor = BriefingPalette.colorForAssessment(modelResult.resolvedStatus)
    Surface(
        modifier = modifier,
        color = bgColor,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            text = modelResult.model.shortModelName(),
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = Color.White,
            modifier = Modifier.padding(horizontal = 7.dp, vertical = 2.dp),
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun GreenAdvisoryStrip(
    advisories: List<RouteAdvisoryResult>,
    catalog: List<AdvisoryCatalogEntry>,
    onFocusIntent: (FocusIntent) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expandedAdvisoryId by rememberSaveable { mutableStateOf<String?>(null) }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = BriefingPalette.Green.copy(alpha = 0.08f),
        border = BorderStroke(0.5.dp, BriefingPalette.Green.copy(alpha = 0.25f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = null,
                    tint = BriefingPalette.Green,
                    modifier = Modifier.size(18.dp),
                )
                Text(
                    text = "All clear",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = "${advisories.size}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                advisories.forEach { advisory ->
                    val isOpen = expandedAdvisoryId == advisory.advisoryId
                    val name = catalog.firstOrNull { it.id == advisory.advisoryId }?.name ?: advisory.advisoryId
                    Surface(
                        shape = RoundedCornerShape(50),
                        color = BriefingPalette.Green.copy(alpha = if (isOpen) 0.22f else 0.12f),
                        modifier = Modifier
                            .clip(RoundedCornerShape(50))
                            .clickable {
                                expandedAdvisoryId = if (isOpen) null else advisory.advisoryId
                            },
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(5.dp),
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
                                    .clip(CircleShape)
                                    .background(BriefingPalette.Green),
                            )
                            Text(
                                text = name,
                                style = MaterialTheme.typography.labelMedium,
                                fontWeight = FontWeight.Medium,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    }
                }
            }

            val selectedAdvisory = advisories.firstOrNull { it.advisoryId == expandedAdvisoryId }
            if (selectedAdvisory != null) {
                AdvisoryCard(
                    advisory = selectedAdvisory,
                    catalog = catalog,
                    onFocusIntent = onFocusIntent,
                    externalExpanded = true,
                    onExpandChange = { open ->
                        if (!open) expandedAdvisoryId = null
                    },
                )
            }
        }
    }
}

private fun matchHazardNarrative(
    advisory: RouteAdvisoryResult,
    catalog: List<AdvisoryCatalogEntry>,
    digest: DigestResponse?,
): String? {
    if (digest == null) return null
    val entry = catalog.firstOrNull { it.id == advisory.advisoryId }
    val hay = listOf(advisory.advisoryId, entry?.category.orEmpty(), entry?.name.orEmpty())
        .joinToString(" ")
        .lowercase(Locale.US)
    return when {
        "icing" in hay || "ice" in hay -> digest.icing
        "turb" in hay -> digest.turbulence
        ("wind" in hay || "jet" in hay) && "crosswind" !in hay && "headwind" !in hay -> digest.winds
        "precip" in hay || "rain" in hay -> digest.precipitation
        "vis" in hay -> digest.visibility
        else -> null
    }
}

// ============================================================================
// Airport Conditions Section
// ============================================================================

@Composable
internal fun AirportConditionsSection(
    conditions: AirportConditions,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Airport Conditions",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )

        BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
            if (maxWidth >= 640.dp) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    AirportConditionCard(
                        role = "Departure",
                        summary = conditions.departure,
                        modifier = Modifier.weight(1f),
                    )
                    AirportConditionCard(
                        role = "Destination",
                        summary = conditions.arrival,
                        modifier = Modifier.weight(1f),
                    )
                }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    AirportConditionCard(
                        role = "Departure",
                        summary = conditions.departure,
                    )
                    AirportConditionCard(
                        role = "Destination",
                        summary = conditions.arrival,
                    )
                }
            }
        }
    }
}

@Composable
private fun AirportConditionCard(
    role: String,
    summary: AirportConditionsSummary,
    modifier: Modifier = Modifier,
) {
    var expanded by rememberSaveable(summary.icao, role) { mutableStateOf(true) }

    val worstCondition = remember(summary.conditions) {
        val rank = mapOf("lifr" to 3, "ifr" to 2, "mvfr" to 1, "vfr" to 0)
        summary.conditions.maxByOrNull { rank[it.flightCategory.lowercase(Locale.US)] ?: -1 }
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { expanded = !expanded },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = role.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = summary.icao,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                if (summary.name.isNotBlank()) {
                    Text(
                        text = summary.name,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                } else {
                    Spacer(modifier = Modifier.weight(1f))
                }
                if (worstCondition != null && worstCondition.flightCategory.isNotBlank()) {
                    FlightCategoryPillBadge(category = worstCondition.flightCategory)
                }
                Icon(
                    imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (expanded) {
                summary.conditions.forEach { cond ->
                    AirportModelConditionRow(condition = cond, showModel = true)
                }
                // Runway wind components table across available runways from worst/primary model
                val runways = worstCondition?.allRunways.orEmpty()
                if (runways.isNotEmpty()) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Text(
                        text = "Runway Wind Components (${worstCondition?.model?.shortModelName() ?: ""})",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    runways.forEach { rwy ->
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(
                                text = "Rwy ${rwy.runwayId} (${rwy.headingDeg.roundToInt()}°)",
                                style = MaterialTheme.typography.labelSmall,
                            )
                            val hwLabel = if (rwy.headwindKt >= 0) {
                                "${rwy.headwindKt.roundToInt()} kt HW"
                            } else {
                                "${abs(rwy.headwindKt).roundToInt()} kt TW"
                            }
                            Text(
                                text = "$hwLabel · ${abs(rwy.crosswindKt).roundToInt()} kt XW",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            } else if (worstCondition != null) {
                AirportModelConditionRow(condition = worstCondition, showModel = false)
            }
        }
    }
}

@Composable
private fun AirportModelConditionRow(
    condition: AirportModelCondition,
    showModel: Boolean,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (showModel) {
            Text(
                text = condition.model.shortModelName(),
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.width(54.dp),
            )
        }
        FlightCategoryPillBadge(category = condition.flightCategory)

        val windStr = formatWindString(
            dir = condition.windDirectionDeg,
            speed = condition.windSpeedKt,
            gust = condition.windGustKt,
        )
        if (windStr != null) {
            Text(
                text = windStr,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        condition.bestRunway?.let { rwy ->
            Text(
                text = "Rwy ${rwy.runwayId} (${abs(rwy.crosswindKt).roundToInt()}kt XW)",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

internal fun formatWindString(dir: Double?, speed: Double?, gust: Double?): String? {
    if (speed == null) return null
    val spd = speed.roundToInt()
    val dirPart = dir?.let { String.format(Locale.US, "%03d°/", it.roundToInt()) } ?: ""
    val gustPart = if (gust != null && gust > speed + 1.0) "G${gust.roundToInt()}" else ""
    return "$dirPart${spd}${gustPart}kt"
}

// ============================================================================
// Alternates Section
// ============================================================================

@Composable
internal fun AlternatesSection(
    alternates: AlternatesResponse,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Weather Alternates",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            alternates.destinationIcao?.takeIf { it.isNotBlank() }?.let { dest ->
                Text(
                    text = "for $dest",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        Text(
            text = "Planning-grade divert candidates that improve on destination weather — confirm fuel, minima, NOTAMs, and PPR independently.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        // EASA / FAA Alternate-Required Trigger Banner
        alternates.requirement?.let { req ->
            AlternateRequirementBanner(requirement = req)
        }

        // Nearest improving axis picks
        val picks = alternates.weatherPicks.orEmpty()
        if (picks.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                picks.forEach { pick ->
                    Text(
                        text = "• ${pick.label.ifBlank { pick.axis }}: ${pick.airport.icao} (${pick.airport.distanceToDestNm.roundToInt()} NM)",
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }
        }

        // Divert candidate cards
        alternates.candidates.forEach { candidate ->
            AlternateCandidateCard(candidate = candidate)
        }
    }
}

@Composable
private fun AlternateRequirementBanner(
    requirement: AlternateRequirement,
    modifier: Modifier = Modifier,
) {
    if (requirement.faa == null && requirement.easa == null) return

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            requirement.faa?.let { faa ->
                RegimeTriggerColumn(label = "FAA Alternate", trigger = faa, modifier = Modifier.weight(1f))
            }
            requirement.easa?.let { easa ->
                RegimeTriggerColumn(label = "EASA Alternate", trigger = easa, modifier = Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun RegimeTriggerColumn(
    label: String,
    trigger: RegAlternateTrigger,
    modifier: Modifier = Modifier,
) {
    val statusColor = when (trigger.status.lowercase(Locale.US)) {
        "required", "unlikely" -> BriefingPalette.Red
        "marginal" -> BriefingPalette.Amber
        "not_required", "likely" -> BriefingPalette.Green
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = trigger.status.replace('_', ' ').replaceFirstChar { it.uppercase() },
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
            color = statusColor,
        )
        if (trigger.reason.isNotBlank()) {
            Text(
                text = trigger.reason,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AlternateCandidateCard(
    candidate: AlternateAirport,
    modifier: Modifier = Modifier,
) {
    var expanded by rememberSaveable(candidate.icao) { mutableStateOf(false) }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded }
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = candidate.icao,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                if (candidate.worstCategory.isNotBlank()) {
                    FlightCategoryPillBadge(category = candidate.worstCategory)
                }
                if (candidate.name.isNotBlank()) {
                    Text(
                        text = candidate.name,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                } else {
                    Spacer(modifier = Modifier.weight(1f))
                }
                Text(
                    text = "${candidate.distanceToDestNm.roundToInt()} NM",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Icon(
                    imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Operational flags
            if (candidate.operationalFlags.isNotEmpty()) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    candidate.operationalFlags.forEach { flag ->
                        OperationalFlagChip(flag = flag)
                    }
                }
            }

            if (candidate.summary.isNotBlank()) {
                Text(
                    text = candidate.summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            AnimatedVisibility(visible = expanded) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    val posText = if (candidate.isEnroute) "En-route" else "Beyond destination"
                    val windText = formatWindString(
                        dir = candidate.windDirectionDeg,
                        speed = candidate.windSpeedKt,
                        gust = candidate.windGustKt,
                    )
                    Text(
                        text = listOfNotNull(
                            posText,
                            candidate.ceilingFt?.let { "Ceiling ${it} ft" },
                             windText?.let { "Wind $it" },
                            candidate.bestRunway?.let { "Best Rwy ${it.runwayId} (${abs(it.crosswindKt).roundToInt()}kt XW)" },
                        ).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall,
                    )
                    candidate.operationalFlags.forEach { flag ->
                        if (flag.detail.isNotBlank()) {
                            Text(
                                text = "⚠ ${flag.label}: ${flag.detail}",
                                style = MaterialTheme.typography.labelSmall,
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
private fun OperationalFlagChip(flag: OperationalFlag) {
    val flagColor = if (flag.status.lowercase(Locale.US) == "red") BriefingPalette.Red else BriefingPalette.Amber
    Surface(
        shape = RoundedCornerShape(50),
        color = flagColor.copy(alpha = 0.14f),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Warning,
                contentDescription = null,
                tint = flagColor,
                modifier = Modifier.size(12.dp),
            )
            Text(
                text = flag.label,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.SemiBold,
                color = flagColor,
            )
        }
    }
}

// ============================================================================
// Timing Scenarios Section
// ============================================================================

@Composable
internal fun TimingScenariosSection(
    timeOptions: TimeOptionsResponse,
    catalog: List<AdvisoryCatalogEntry>,
    isEditable: Boolean,
    onConfirmTimeOption: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val catalogNames = remember(catalog) {
        catalog.associate { it.id to it.name }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "Timing Scenarios",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )

        if (timeOptions.confirmation.show && timeOptions.confirmation.headline.isNotBlank()) {
            Surface(
                shape = RoundedCornerShape(10.dp),
                color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.45f),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = timeOptions.confirmation.headline,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    if (timeOptions.confirmation.detail.isNotBlank()) {
                        Text(
                            text = timeOptions.confirmation.detail,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }

        when (timeOptions.status.status) {
            TimeScanJobStatus.PENDING, TimeScanJobStatus.RUNNING -> {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text(
                        text = "Scanning departure time windows against this briefing…",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            TimeScanJobStatus.FAILED -> {
                Text(
                    text = timeOptions.status.error ?: "Timing scenario scan failed.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TimeScanJobStatus.READY -> {
                val scan = timeOptions.scan
                if (scan == null || scan.candidates.isEmpty()) {
                    Text(
                        text = timeOptions.status.summary ?: "No alternative departure candidates available.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    if (scan.summary.isNotBlank()) {
                        Text(
                            text = scan.summary,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    scan.candidates.forEach { candidate ->
                        TimingCandidateCard(
                            candidate = candidate,
                            isRecommended = candidate.departureTime == scan.recommendedDepartureTime,
                            catalogNames = catalogNames,
                            isEditable = isEditable,
                            onConfirm = { onConfirmTimeOption(candidate.departureTime) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun TimingCandidateCard(
    candidate: TimeCandidate,
    isRecommended: Boolean,
    catalogNames: Map<String, String>,
    isEditable: Boolean,
    onConfirm: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        border = BorderStroke(
            width = if (isRecommended) 1.dp else 0.5.dp,
            color = if (isRecommended) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant,
        ),
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
                    text = formatUtcTimestamp(candidate.departureTime),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                AssessmentPillBadge(assessment = candidate.resolvedAssessment)
                if (candidate.isCurrent) {
                    Text(
                        text = "Planned",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold,
                    )
                } else if (isRecommended) {
                    Text(
                        text = "★ Recommended",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(modifier = Modifier.weight(1f))
                Text(
                    text = "${candidate.advisorySummary.red}R · ${candidate.advisorySummary.amber}A · ${candidate.advisorySummary.green}G",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (candidate.prosVsCurrent.isNotEmpty()) {
                Text(
                    text = "Improves: " + candidate.prosVsCurrent.joinToString(", ") { catalogNames[it] ?: it },
                    style = MaterialTheme.typography.bodySmall,
                    color = BriefingPalette.Green,
                )
            }
            if (candidate.consVsCurrent.isNotEmpty()) {
                Text(
                    text = "Worsens: " + candidate.consVsCurrent.joinToString(", ") { catalogNames[it] ?: it },
                    style = MaterialTheme.typography.bodySmall,
                    color = BriefingPalette.Amber,
                )
            }

            val modelsLabel = if (candidate.modelsAvailable.isNotEmpty()) {
                "Models: " + candidate.modelsAvailable.joinToString(", ") { it.shortModelName() }
            } else {
                null
            }
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = modelsLabel ?: candidate.note.orEmpty(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                if (isEditable && !candidate.isCurrent) {
                    OutlinedButton(
                        onClick = onConfirm,
                        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                    ) {
                        Text(
                            text = "Confirm multi-model",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
        }
    }
}

// ============================================================================
// Watch Section
// ============================================================================

@Composable
internal fun WatchSection(
    watchItems: List<String>,
    modifier: Modifier = Modifier,
) {
    if (watchItems.isEmpty()) return

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Watch Items",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            val markdown = remember(watchItems) {
                watchItems.joinToString("\n") { item ->
                    val trimmed = item.trim()
                    if (trimmed.startsWith("- ") || trimmed.startsWith("• ") || trimmed.startsWith("* ")) {
                        trimmed
                    } else {
                        "- $trimmed"
                    }
                }
            }
            MarkdownLiteText(
                markdown = markdown,
                bulletLists = true,
            )
        }
    }
}
