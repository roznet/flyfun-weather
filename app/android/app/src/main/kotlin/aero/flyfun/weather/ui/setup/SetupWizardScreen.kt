package aero.flyfun.weather.ui.setup

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.model.ProfileResponse
import aero.flyfun.weather.viewmodel.SetupStep
import aero.flyfun.weather.viewmodel.SetupWizardState
import aero.flyfun.weather.viewmodel.SetupWizardViewModel
import kotlinx.coroutines.launch

/**
 * First-run setup (#900), shown once after sign-in while the account's `setup_completed` is false:
 * welcome → profile → aircraft → done. The port of the web welcome wizard and of iOS
 * `SetupWizardView`; the copy is the web's `wizard.*` English strings, and the test tags are the
 * iOS accessibility identifiers. Skip (any step) and Get Started both mark setup done.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SetupWizardScreen(
    viewModel: SetupWizardViewModel,
    onFinished: () -> Unit,
) {
    val state by viewModel.state.collectAsState()
    val coroutineScope = rememberCoroutineScope()
    val finish: () -> Unit = {
        coroutineScope.launch {
            viewModel.complete()
            onFinished()
        }
    }

    // System Back steps back through the wizard and never leaves the app from it.
    BackHandler { viewModel.back() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(titleFor(state.step)) },
                actions = {
                    if (state.step != SetupStep.DONE) {
                        TextButton(onClick = finish, modifier = Modifier.testTag("setupSkipButton")) {
                            Text("Skip")
                        }
                    }
                },
            )
        },
        bottomBar = {
            Surface(tonalElevation = 3.dp) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .navigationBarsPadding()
                        .imePadding()
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (state.step == SetupStep.PROFILE || state.step == SetupStep.AIRCRAFT) {
                        OutlinedButton(onClick = viewModel::back, modifier = Modifier.testTag("setupBackButton")) {
                            Text("Back")
                        }
                    }
                    Spacer(Modifier.weight(1f))
                    if (state.step == SetupStep.DONE) {
                        Button(onClick = finish, modifier = Modifier.testTag("setupFinishButton")) {
                            Text("Get Started")
                        }
                    } else {
                        Button(
                            onClick = viewModel::onNext,
                            // The welcome step needs nothing loaded; the others wait for the profiles.
                            enabled = !state.isSaving && !(state.isLoading && state.step != SetupStep.WELCOME),
                            modifier = Modifier.testTag("setupNextButton"),
                        ) {
                            if (state.isSaving) {
                                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                            } else {
                                Text("Next")
                            }
                        }
                    }
                }
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            when (state.step) {
                SetupStep.WELCOME -> WelcomeStep()
                SetupStep.PROFILE -> ProfileStep(state, viewModel)
                SetupStep.AIRCRAFT -> AircraftStep(state, viewModel)
                SetupStep.DONE -> DoneStep(state)
            }
        }
    }
}

private fun titleFor(step: SetupStep): String = when (step) {
    SetupStep.WELCOME -> "Welcome"
    SetupStep.PROFILE -> "Default Profile"
    SetupStep.AIRCRAFT -> "Your Aircraft & Mission"
    SetupStep.DONE -> "All Set"
}

@Composable
private fun WelcomeStep() {
    Text("Welcome to Flyfun Weather", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text(
        "Aviation weather assessment for cross-country GA flights",
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    TitledLine("AI synopsis & advisories", "An AI-generated overview plus go/no-go factors for your route")
    TitledLine("Route advisories", "Icing, turbulence, clouds and airport conditions rated GREEN/AMBER/RED")
    TitledLine("Multi-model comparison", "See where GFS, ECMWF, ICON and others agree or diverge")
    TitledLine("Interactive cross-section", "Vertical weather layers along your route with terrain profile")
    TitledLine("Sounding analysis", "Skew-T diagrams, thermodynamic indices and stability at every waypoint")
    HorizontalDivider()
    Text(
        "Flyfun Weather is currently in beta. We appreciate your time trying the tool. Please help us " +
            "improve by sending feedback when a briefing appears suspicious or you encounter any issues.",
        style = MaterialTheme.typography.bodyMedium,
    )
    Hint("Let's set up your aircraft defaults. You can always change these later in Settings.")
}

@Composable
private fun ProfileStep(state: SetupWizardState, viewModel: SetupWizardViewModel) {
    Text(
        "Each profile stores how you fly: cruise altitude, ceiling, advisory preferences, and model selections.",
        style = MaterialTheme.typography.bodyMedium,
    )
    Hint("Tap a profile to make it your default. You can switch or customize profiles later in Settings.")
    val profiles = state.systemProfiles
    if (profiles.isEmpty()) {
        Hint(
            if (state.isLoading) "Loading profiles…"
            else "No system profiles available. You can configure your settings on the next step.",
        )
    }
    val selectedId = state.selectedProfile?.id
    profiles.forEach { profile ->
        ProfileCard(
            profile = profile,
            description = state.templateDescription(profile),
            selected = profile.id == selectedId,
            onSelect = { viewModel.selectProfile(profile.id) },
        )
    }
}

@Composable
private fun ProfileCard(profile: ProfileResponse, description: String, selected: Boolean, onSelect: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = selected, onClick = onSelect, role = Role.RadioButton)
            .testTag("setupProfileCard-${profile.systemTemplateKey ?: profile.id}"),
        colors = CardDefaults.cardColors(
            containerColor = if (selected) MaterialTheme.colorScheme.primaryContainer
            else MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
            RadioButton(selected = selected, onClick = null)
            Column(modifier = Modifier.padding(start = 8.dp)) {
                Text(profile.name, style = MaterialTheme.typography.titleMedium)
                if (description.isNotEmpty()) {
                    Text(description, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}

@Composable
private fun AircraftStep(state: SetupWizardState, viewModel: SetupWizardViewModel) {
    SectionTitle("Your aircraft")
    Hint("What the machine is and can do. Saved as your default aircraft.")
    val pickedType = state.selectedType
    val typeSupport: String? = when {
        state.icaoTypeMissing -> "Enter your aircraft's ICAO type (use ZZZZ if it has none)."
        pickedType != null && pickedType.manufacturer.isNotEmpty() -> "${pickedType.manufacturer} ${pickedType.model}".trim()
        else -> null
    }
    OutlinedTextField(
        value = state.icaoType,
        onValueChange = viewModel::setIcaoType,
        label = { Text("ICAO type") },
        placeholder = { Text("e.g. SR22, C172") },
        singleLine = true,
        isError = state.icaoTypeMissing,
        supportingText = if (typeSupport != null) {
            { Text(typeSupport) }
        } else {
            null
        },
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
        modifier = Modifier.fillMaxWidth().testTag("setupIcaoTypeField"),
    )
    state.typeSuggestions.forEach { type ->
        Text(
            "${type.icao} - ${type.manufacturer} ${type.model}".trim(),
            modifier = Modifier
                .fillMaxWidth()
                .clickable { viewModel.selectType(type) }
                .padding(vertical = 8.dp),
            color = MaterialTheme.colorScheme.primary,
        )
    }
    OutlinedTextField(
        value = state.nickname,
        onValueChange = viewModel::setNickname,
        label = { Text("Name") },
        placeholder = { Text("e.g. Club SR22") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth().testTag("setupNicknameField"),
    )
    NumberField("Cruise speed (IAS in kt)", state.cruiseSpeedKt, viewModel::setCruiseSpeed)
    NumberField("Service ceiling (ft)", state.serviceCeilingFt, viewModel::setServiceCeiling)
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text("FIKI equipped", modifier = Modifier.weight(1f))
        Switch(
            checked = state.isFiki,
            onCheckedChange = viewModel::setFiki,
            modifier = Modifier.testTag("setupFikiToggle"),
        )
    }
    Hint("Icing advisories are graded for this: without FIKI any icing at cruise counts against the flight.")

    HorizontalDivider()
    SectionTitle("Your usual mission")
    Hint("How you usually fly it. Saved to your profile; you can change it for each flight.")
    RuleOption("VFR + IFR capable", "vfr_ifr", state.flightRules, viewModel::setFlightRules)
    RuleOption("VFR only", "vfr_only", state.flightRules, viewModel::setFlightRules)
    NumberField("Cruise altitude (ft)", state.cruiseAltitudeFt, viewModel::setCruiseAltitude)
    NumberField("Flight ceiling (ft)", state.flightCeilingFt, viewModel::setFlightCeiling)
    state.ceilingWarning?.let { warning ->
        Text(warning, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary)
    }
    Hint("You can edit these, add more aircraft or create more profiles later in Settings.")
    state.errorMessage?.let { error ->
        Text(error, color = MaterialTheme.colorScheme.error)
    }
}

@Composable
private fun RuleOption(label: String, value: String, current: String, onSelect: (String) -> Unit) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .selectable(selected = current == value, onClick = { onSelect(value) }, role = Role.RadioButton),
    ) {
        RadioButton(selected = current == value, onClick = null)
        Text(label, modifier = Modifier.padding(start = 8.dp))
    }
}

@Composable
private fun NumberField(label: String, value: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { text -> onChange(text.filter(Char::isDigit)) },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun DoneStep(state: SetupWizardState) {
    SectionTitle("Saved")
    Text(doneSummary(state), modifier = Modifier.testTag("setupSummary"))
    HorizontalDivider()
    SectionTitle("Getting started")
    TitledLine(
        "Create a flight",
        "Enter your waypoints, date and time. Your aircraft and usual cruise altitude and ceiling are " +
            "pre-filled from what you just set up.",
    )
    TitledLine(
        "Read the briefing",
        "Red/amber/green advisories, an AI synopsis, the cross-section and sounding analysis for each waypoint.",
    )
    TitledLine(
        "Refresh for updates",
        "Come back and refresh as the flight date approaches. Each refresh captures a new snapshot you can compare.",
    )
}

/** What the server now holds: the default profile, and the aircraft as the server returned it. */
internal fun doneSummary(state: SetupWizardState): String = buildList {
    state.selectedProfile?.let { add("Default profile: ${it.name}") }
    state.savedAircraft?.let {
        add("Aircraft: ${it.displayLabel} (${it.icaoType}), ${if (it.isFiki) "FIKI equipped" else "not FIKI"}")
    }
}.joinToString("\n")

@Composable
private fun TitledLine(title: String, detail: String) {
    Column {
        Text(title, style = MaterialTheme.typography.titleSmall)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun Hint(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}
