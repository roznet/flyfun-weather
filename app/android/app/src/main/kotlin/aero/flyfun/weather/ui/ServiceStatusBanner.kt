package aero.flyfun.weather.ui

import aero.flyfun.weather.data.ServiceStatus
import aero.flyfun.weather.data.ServiceStatusMonitor
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/**
 * Wraps the whole app: while [ServiceStatusMonitor] holds a status (server in
 * maintenance or down), one strip with the server's own sentence sits above
 * everything. It takes the status-bar inset itself, so the screens below don't
 * pad for it twice. Gone on the next successful request.
 */
@Composable
fun ServiceStatusFrame(content: @Composable () -> Unit) {
    val status by ServiceStatusMonitor.current.collectAsState()
    Column(Modifier.fillMaxSize()) {
        // No enter/exit animation on purpose: the inset consumption below is
        // keyed on `status`, so a banner lingering through an exit animation
        // would pad the status bar twice for that moment.
        status?.let { ServiceStatusBanner(it) }
        val inner = if (status != null) {
            Modifier.consumeWindowInsets(WindowInsets.statusBars)
        } else {
            Modifier
        }
        Box(Modifier.weight(1f).then(inner)) { content() }
    }
}

@Composable
fun ServiceStatusBanner(status: ServiceStatus) {
    Surface(
        color = MaterialTheme.colorScheme.tertiaryContainer,
        contentColor = MaterialTheme.colorScheme.onTertiaryContainer,
        modifier = Modifier.fillMaxWidth().testTag("serviceStatusBanner"),
    ) {
        Column(
            Modifier
                .windowInsetsPadding(WindowInsets.statusBars)
                .padding(horizontal = 16.dp, vertical = 10.dp),
        ) {
            Text(
                status.message,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
            )
            status.until?.let { until ->
                val local = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT)
                    .withZone(ZoneId.systemDefault())
                    .format(until)
                Text("Your time: $local", style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(2.dp))
            Text("Saved flights stay available.", style = MaterialTheme.typography.bodySmall)
        }
    }
}
