package aero.flyfun.weather.ui.help

import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.AirplanemodeActive
import androidx.compose.material.icons.filled.Feedback
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.NewReleases
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.viewmodel.AppStateViewModel
import kotlinx.coroutines.launch

/**
 * Settings, account status, Autorouter PKCE linking, offline/demo toggles, and help screen.
 * Mirrors iOS Settings/Account & Help controls.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    appState: AppStateViewModel,
    onBack: () -> Unit,
    onOpenAircraft: () -> Unit,
    onOpenWhatsNew: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val userEmail by appState.userEmail.collectAsState()
    val isGuest by appState.isGuestBrowsing.collectAsState()
    val isMockMode by appState.isMockMode.collectAsState()
    val isOfflineMode by appState.isOfflineMode.collectAsState()
    val autorouterLinked by appState.autorouterLinked.collectAsState()

    var showFeedbackSheet by remember { mutableStateOf(false) }

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = { Text("Settings & Account", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // Account Card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Account",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = when {
                            isGuest -> "Guest / Demo Mode (Fixture Data)"
                            userEmail != null -> "Signed in as $userEmail"
                            else -> "Authenticated Pilot"
                        },
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    OutlinedButton(
                        onClick = {
                            appState.logout()
                            onBack()
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.AutoMirrored.Filled.Logout, contentDescription = null)
                        Spacer(modifier = Modifier.padding(horizontal = 4.dp))
                        Text(if (isGuest) "Exit Demo Mode & Sign In" else "Sign Out")
                    }
                }
            }

            // Autorouter Integration Card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Autorouter Integration",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = if (autorouterLinked) {
                            "Connected — Autorouter route suggestions and GRAMET enrichment are active."
                        } else {
                            "Link your Autorouter.aero account to fetch IFR/VFR route suggestions and GRAMET cross-sections."
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    if (autorouterLinked) {
                        OutlinedButton(
                            onClick = { appState.unlinkAutorouter() },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("Unlink Autorouter Account")
                        }
                    } else {
                        Button(
                            onClick = {
                                scope.launch {
                                    runCatching { appState.buildAutorouterLinkUrl() }
                                        .onSuccess { url ->
                                            if (url != null) {
                                                runCatching {
                                                    CustomTabsIntent.Builder().build()
                                                        .launchUrl(context, Uri.parse(url))
                                                }
                                            }
                                        }
                                }
                            },
                            enabled = !isGuest,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Default.Link, contentDescription = null)
                            Spacer(modifier = Modifier.padding(horizontal = 4.dp))
                            Text("Link Autorouter Account")
                        }
                    }
                }
            }

            // Fleet & Resources
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Pilot Resources",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    OutlinedButton(
                        onClick = onOpenAircraft,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.Default.AirplanemodeActive, contentDescription = null)
                        Spacer(modifier = Modifier.padding(horizontal = 4.dp))
                        Text("Manage Aircraft Fleet")
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = onOpenWhatsNew,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.Default.NewReleases, contentDescription = null)
                        Spacer(modifier = Modifier.padding(horizontal = 4.dp))
                        Text("What's New & Release Notes")
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = { showFeedbackSheet = true },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.Default.Feedback, contentDescription = null)
                        Spacer(modifier = Modifier.padding(horizontal = 4.dp))
                        Text("Send Feedback")
                    }
                }
            }

            // Offline & Mock Mode Toggles
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Connectivity & Demo",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Offline Cache Mode", style = MaterialTheme.typography.bodyMedium)
                            Text(
                                "Read packs strictly from local briefing cache",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = isOfflineMode,
                            onCheckedChange = { appState.setOfflineMode(it) },
                        )
                    }
                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Demo Fixture Mode", style = MaterialTheme.typography.bodyMedium)
                            Text(
                                "Use bundled LFMD → LFML fixture data",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = isMockMode,
                            onCheckedChange = { appState.setMockMode(it) },
                        )
                    }
                }
            }
        }
    }

    if (showFeedbackSheet) {
        FeedbackSheet(
            apiClient = appState.apiClient,
            onDismiss = { showFeedbackSheet = false },
        )
    }
}
