package aero.flyfun.weather.ui.help

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.model.DigestFeedbackRequest
import aero.flyfun.weather.model.GeneralFeedbackRequest
import aero.flyfun.weather.model.HighlightFeedbackRequest
import kotlinx.coroutines.launch

/**
 * Feedback modal bottom sheet supporting General feedback, Digest feedback, and Highlight feedback,
 * mirroring iOS `FeedbackSheet`.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun FeedbackSheet(
    apiClient: ApiClient,
    flightId: String? = null,
    packTimestamp: String? = null,
    highlightTarget: String? = null,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val categories = listOf("Briefing Accuracy", "Advisories", "Cross-Section", "Skew-T", "Feature Request", "Bug")
    var selectedCategory by remember { mutableStateOf(categories.first()) }
    var comment by remember { mutableStateOf("") }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 12.dp),
        ) {
            Text(
                text = "Send Feedback",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = if (flightId != null) {
                    "Feedback linked to flight $flightId"
                } else {
                    "Help us improve FlyFun Weather briefings and advisories."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(modifier = Modifier.height(12.dp))

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                categories.forEach { cat ->
                    FilterChip(
                        selected = selectedCategory == cat,
                        onClick = { selectedCategory = cat },
                        label = { Text(cat) },
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            OutlinedTextField(
                value = comment,
                onValueChange = { comment = it },
                label = { Text("Details or observations") },
                placeholder = { Text("What worked well or what looked inaccurate?") },
                minLines = 3,
                maxLines = 6,
                modifier = Modifier.fillMaxWidth(),
            )

            if (errorText != null) {
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = errorText ?: "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            Button(
                onClick = {
                    isSubmitting = true
                    errorText = null
                    scope.launch {
                        runCatching {
                            when {
                                flightId != null && packTimestamp != null && highlightTarget != null -> {
                                    apiClient.submitHighlightFeedback(
                                        HighlightFeedbackRequest(
                                            flightId = flightId,
                                            packTimestamp = packTimestamp,
                                            target = highlightTarget,
                                            vote = selectedCategory,
                                            comment = comment.trim(),
                                        ),
                                    )
                                }
                                flightId != null && packTimestamp != null -> {
                                    apiClient.submitDigestFeedback(
                                        DigestFeedbackRequest(
                                            flightId = flightId,
                                            packTimestamp = packTimestamp,
                                            category = selectedCategory,
                                            comment = comment.trim(),
                                        ),
                                    )
                                }
                                else -> {
                                    apiClient.submitGeneralFeedback(
                                        GeneralFeedbackRequest(
                                            category = selectedCategory,
                                            message = comment.trim(),
                                        ),
                                    )
                                }
                            }
                        }.onSuccess {
                            isSubmitting = false
                            onDismiss()
                        }.onFailure { err ->
                            isSubmitting = false
                            errorText = err.message ?: "Failed to submit feedback"
                        }
                    }
                },
                enabled = !isSubmitting && comment.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (isSubmitting) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Text("Submit Feedback")
                }
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}
