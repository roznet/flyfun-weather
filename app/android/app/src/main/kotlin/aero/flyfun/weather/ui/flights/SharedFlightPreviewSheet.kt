package aero.flyfun.weather.ui.flights

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.FlightDuration
import aero.flyfun.weather.model.SharedFlightPreview

/**
 * Preview sheet shown when opening a shared flight link (`flyfunweather://share/<code>`
 * or `https://weather.flyfun.aero/s/<code>`), mirroring iOS `SharedFlightPreviewSheet`.
 * Lets the pilot Save (subscribe), Copy as own flight, or View Read-Only.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SharedFlightPreviewSheet(
    preview: SharedFlightPreview,
    onAcceptSubscribe: (String) -> Unit,
    onCopyOwn: (String) -> Unit,
    onViewReadOnly: (SharedFlightPreview) -> Unit,
    onDismiss: () -> Unit,
) {
    val flight = preview.flight
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 16.dp),
        ) {
            Text(
                text = "Shared Flight Briefing",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
            preview.sharedBy?.let { sharer ->
                Text(
                    text = "Shared by $sharer",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(modifier = Modifier.height(16.dp))

            Text(
                text = flight.waypoints.takeIf { it.isNotEmpty() }?.joinToString(" → ") ?: flight.routeName,
                style = MaterialTheme.typography.headlineSmall,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
            )
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = "Departure: ${flight.departureTime}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = "Cruise: ${flight.cruiseAltitudeFt} ft • Ceiling: ${flight.flightCeilingFt} ft • Duration: ${
                    FlightDuration.label(flight.flightDurationHours)
                }",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(modifier = Modifier.height(24.dp))

            Button(
                onClick = { onAcceptSubscribe(preview.code) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Default.DownloadDone, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text("Subscribe to Flight")
            }

            Spacer(modifier = Modifier.height(8.dp))

            OutlinedButton(
                onClick = { onCopyOwn(preview.code) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(Icons.Default.ContentCopy, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text("Copy as My Own Flight")
            }

            Spacer(modifier = Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                TextButton(onClick = { onViewReadOnly(preview) }) {
                    Icon(Icons.Default.Visibility, contentDescription = null)
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("View Read-Only")
                }
                TextButton(onClick = onDismiss) {
                    Text("Cancel")
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
        }
    }
}
