package aero.flyfun.weather.ui.briefing

import aero.flyfun.weather.model.DebriefQuestion
import aero.flyfun.weather.model.DebriefReport
import aero.flyfun.weather.model.DebriefResponse
import aero.flyfun.weather.model.rules.DebriefSectionRules
import aero.flyfun.weather.viewmodel.BriefingViewModel
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * The **Debrief** section (#1024), at the top of the Observed tab after the live window: the
 * server's headline, the flown / cancelled tap, then the report's questions (or the cancel
 * reasons), a note, and the detail lines folded. Every word but the chrome is the server's
 * (#1023). Twins: web `components/debrief-section.ts`, iOS `DebriefSectionView`.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun DebriefSection(viewModel: BriefingViewModel, modifier: Modifier = Modifier) {
    val report by viewModel.debriefReport.collectAsState()
    val debrief by viewModel.debrief.collectAsState()
    val busy by viewModel.debriefBusy.collectAsState()
    val error by viewModel.debriefError.collectAsState()
    val pending by viewModel.debriefPendingAvoided.collectAsState()
    val current = report ?: return
    val decision = DebriefSectionRules.decision(debrief, current)

    Card(
        modifier = modifier.fillMaxWidth().testTag("debriefSection"),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Debrief", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            current.headline?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.testTag("debriefHeadline"),
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = decision == DebriefSectionRules.Decision.FLOWN,
                    onClick = { viewModel.chooseDebriefDecision(DebriefSectionRules.Decision.FLOWN) },
                    enabled = !busy,
                    label = { Text("I flew it") },
                    modifier = Modifier.testTag("debriefFlown"),
                )
                FilterChip(
                    selected = decision == DebriefSectionRules.Decision.CANCELLED,
                    onClick = { viewModel.chooseDebriefDecision(DebriefSectionRules.Decision.CANCELLED) },
                    enabled = !busy,
                    label = { Text("I cancelled") },
                    modifier = Modifier.testTag("debriefCancelled"),
                )
            }
            when (decision) {
                DebriefSectionRules.Decision.FLOWN -> {
                    if (current.questions.isEmpty()) {
                        Text(
                            "Nothing to ask about this flight: the instruments covered it. Thanks for flying it.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    current.questions.forEach { q ->
                        DebriefQuestionBlock(q, pending, busy, viewModel)
                    }
                    DebriefNote(debrief, busy, viewModel)
                }
                DebriefSectionRules.Decision.CANCELLED -> {
                    Text("What made you cancel?", style = MaterialTheme.typography.bodyMedium)
                    val chosen = debrief?.takeIf { it.decision == "cancelled" }?.reasons.orEmpty()
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        DebriefSectionRules.CANCEL_REASONS.forEach { (id, label) ->
                            FilterChip(
                                selected = id in chosen,
                                onClick = { viewModel.toggleDebriefCancelReason(id) },
                                enabled = !busy,
                                label = { Text(label) },
                                modifier = Modifier.testTag("debriefReason-$id"),
                            )
                        }
                    }
                    DebriefNote(debrief, busy, viewModel)
                }
                null -> Unit
            }
            error?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = BriefingPalette.Red)
            }
            DebriefDetails(current)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DebriefQuestionBlock(
    question: DebriefQuestion,
    pending: Map<String, Boolean>,
    busy: Boolean,
    viewModel: BriefingViewModel,
) {
    Column(
        verticalArrangement = Arrangement.spacedBy(6.dp),
        modifier = Modifier.testTag("debriefQuestion-${question.id}"),
    ) {
        Text(question.text, style = MaterialTheme.typography.bodyMedium)
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            question.options.forEach { option ->
                FilterChip(
                    selected = question.answer?.answer == option.id,
                    onClick = { viewModel.answerDebriefQuestion(question, option.id) },
                    enabled = !busy,
                    label = { Text(option.label) },
                    modifier = Modifier.testTag("debriefAnswer-${question.id}-${option.id}"),
                )
            }
        }
        if (question.avoidable) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(
                    checked = DebriefSectionRules.isAvoided(question, pending),
                    onCheckedChange = { viewModel.setDebriefAvoided(question, it) },
                    enabled = !busy,
                    modifier = Modifier.testTag("debriefAvoided-${question.id}"),
                )
                Text(
                    "I changed level or route to stay out of it",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun DebriefNote(debrief: DebriefResponse?, busy: Boolean, viewModel: BriefingViewModel) {
    var note by rememberSaveable(debrief?.note) { mutableStateOf(debrief?.note.orEmpty()) }
    val tooLong = note.length > DebriefSectionRules.NOTE_MAX_LENGTH
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedTextField(
            value = note,
            onValueChange = { note = it },
            label = { Text("Anything else? (optional)") },
            isError = tooLong,
            minLines = 2,
            modifier = Modifier.fillMaxWidth().testTag("debriefNote"),
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "${DebriefSectionRules.NOTE_MAX_LENGTH - note.length}",
                style = MaterialTheme.typography.labelSmall,
                color = if (tooLong) BriefingPalette.Red else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            TextButton(
                onClick = { viewModel.saveDebriefNote(note) },
                enabled = !busy && !tooLong,
                modifier = Modifier.testTag("debriefNoteSave"),
            ) { Text("Save note") }
        }
    }
}

@Composable
private fun DebriefDetails(report: DebriefReport) {
    val lines = report.summary?.lines.orEmpty()
    if (lines.isEmpty()) return
    var expanded by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        TextButton(onClick = { expanded = !expanded }, modifier = Modifier.testTag("debriefDetails")) {
            Text(if (expanded) "Hide details" else "Details")
        }
        if (expanded) {
            lines.forEach { line ->
                Text(line.text, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/**
 * The briefing's debrief card on the Advisory tab (owner, past flight): the stored decision and
 * a link to the Debrief section on the Observed tab (#1024), where the questions live.
 */
@Composable
internal fun DebriefCard(
    debrief: DebriefResponse?,
    questionsOpen: Int,
    canOpen: Boolean,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth().testTag("debriefCard"),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Debrief",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onOpen, enabled = canOpen, modifier = Modifier.testTag("debriefCardOpen")) {
                    Text(if (questionsOpen > 0) "Answer" else "Open")
                }
            }
            if (questionsOpen > 0) {
                Text(
                    if (questionsOpen == 1) "One quick question about this flight." else "$questionsOpen quick questions about this flight.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (debrief != null && debrief.decision.isNotEmpty()) {
                val (label, color) = when (debrief.decision) {
                    "cancelled" -> "Cancelled" to BriefingPalette.Red
                    "monitoring" -> "Monitor only" to BriefingPalette.Unavailable
                    else -> "Flown" to BriefingPalette.Green
                }
                Surface(color = color.copy(alpha = 0.14f), shape = RoundedCornerShape(50)) {
                    Text(
                        label,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = color,
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                    )
                }
                if (debrief.decision == "cancelled" && debrief.reasons.isNotEmpty()) {
                    val labels = DebriefSectionRules.CANCEL_REASONS.toMap()
                    Text(
                        "Because: " + debrief.reasons.joinToString(", ") { labels[it] ?: it },
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                debrief.note?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else if (questionsOpen == 0) {
                Text(
                    "Did you fly it? The debrief compares what was forecast with what was seen.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
