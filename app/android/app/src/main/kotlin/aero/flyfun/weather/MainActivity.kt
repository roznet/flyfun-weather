package aero.flyfun.weather

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import aero.flyfun.weather.model.BriefingTab
import aero.flyfun.weather.model.FlightResponse
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.ui.aircraft.AircraftListScreen
import aero.flyfun.weather.ui.auth.LoginScreen
import aero.flyfun.weather.ui.briefing.BriefingContainerScreen
import aero.flyfun.weather.ui.flights.AddFlightSheet
import aero.flyfun.weather.ui.flights.FlightListScreen
import aero.flyfun.weather.ui.flights.SharedFlightPreviewSheet
import aero.flyfun.weather.ui.help.SettingsScreen
import aero.flyfun.weather.ui.help.WhatsNewScreen
import aero.flyfun.weather.ui.map.PanEuropeanMapMode
import aero.flyfun.weather.ui.map.PanEuropeanMapScreen
import aero.flyfun.weather.ui.setup.SetupWizardScreen
import aero.flyfun.weather.ui.theme.FlyFunWeatherTheme
import aero.flyfun.weather.ui.trips.TripDetailScreen
import aero.flyfun.weather.viewmodel.AddFlightViewModel
import aero.flyfun.weather.viewmodel.AircraftListViewModel
import aero.flyfun.weather.viewmodel.AppStateViewModel
import aero.flyfun.weather.viewmodel.BriefingViewModel
import aero.flyfun.weather.viewmodel.FlightListViewModel
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalMapViewModel
import aero.flyfun.weather.viewmodel.TripsViewModel
import aero.flyfun.weather.viewmodel.SetupWizardViewModel
import aero.flyfun.weather.viewmodel.UiTestLaunch
import aero.flyfun.weather.viewmodel.ViewModelScopes

/**
 * Main activity for FlyFun Weather Android.
 * Handles:
 * - Custom scheme deep links (`flyfunweather://...`) and App Links (`https://weather.flyfun.aero/...`)
 * - Adaptive 2-pane layout on tablets/foldables (`screenWidthDp >= 840`) and single-pane stack on phones
 * - Root switching between [LoginScreen] and the authenticated flight/briefing workspace
 */
class MainActivity : ComponentActivity() {

    private val appState: AppStateViewModel by lazy {
        (application as? FlyFunWeatherApp)?.appState ?: AppStateViewModel(context = applicationContext)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // A UI test's launch extras (#895); debug builds only. Only on a fresh start, so a
        // rotation mid-journey doesn't reseed the fixtures under it.
        if (BuildConfig.DEBUG && savedInstanceState == null) {
            UiTestLaunch.from { intent?.getStringExtra(it) }?.let(appState::startUiTestSession)
        }
        handleIntent(intent)

        setContent {
            FlyFunWeatherTheme {
                FlyFunWeatherRoot(appState = appState)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val dataUri = intent?.dataString ?: return
        appState.handleIncomingUri(dataUri)
    }
}

private enum class RootRoute {
    FLIGHTS,
    BRIEFING,
    TRIP,
    MAP_FORECAST,
    MAP_HISTORICAL,
    AIRCRAFT,
    WHATS_NEW,
    SETTINGS,
}

private sealed interface RootScreen {
    data object Flights : RootScreen
    data class Briefing(
        val flightId: String,
        val flight: FlightResponse? = null,
        val initialTimestamp: String? = null,
        val openObserved: Boolean = false,
    ) : RootScreen
    data class TripDetail(val tripId: String) : RootScreen
    data class PanEuropeanMap(
        val initialMode: PanEuropeanMapMode = PanEuropeanMapMode.FORECAST,
    ) : RootScreen
    data object AircraftList : RootScreen
    data object WhatsNew : RootScreen
    data object Settings : RootScreen
}

@Composable
fun FlyFunWeatherRoot(
    appState: AppStateViewModel,
) {
    val isAuthenticated by appState.isAuthenticated.collectAsState()
    val isMockMode by appState.isMockMode.collectAsState()
    val isGuestBrowsing by appState.isGuestBrowsing.collectAsState()
    val cacheScope by appState.cacheScope.collectAsState()
    val pendingNav by appState.pendingNavTarget.collectAsState()
    val unseenWhatsNew by (appState.whatsNewStore?.unseenCount?.collectAsState() ?: remember { mutableStateOf(0) })

    // Session and briefing ViewModels live in these owners, cleared when no longer on screen
    val viewModelScopes: ViewModelScopes = viewModel(key = "viewModelScopes") { ViewModelScopes() }

    if (!isAuthenticated) {
        LaunchedEffect(Unit) { viewModelScopes.retainOnly(emptySet()) }
        LoginScreen(
            appState = appState,
            showDevLogin = BuildConfig.DEBUG && aero.flyfun.weather.data.ApiClient.isRunningOnEmulator(),
        )
        return
    }

    val sessionKey = "$cacheScope:$isMockMode:$isGuestBrowsing"
    val sessionOwner = viewModelScopes.owner(ViewModelScopes.sessionKey(sessionKey))

    // First-run setup (#900): shown over everything until finished or skipped.
    val showSetupWizard by appState.showSetupWizard.collectAsState()
    val setupRepository = appState.setupRepository
    if (showSetupWizard && setupRepository != null) {
        val setupViewModel: SetupWizardViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "setupWizard") {
            SetupWizardViewModel(setupRepository).also { it.start() }
        }
        SetupWizardScreen(viewModel = setupViewModel, onFinished = appState::dismissSetupWizard)
        return
    }

    val flightListViewModel: FlightListViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "flightList") {
        FlightListViewModel(
            repository = appState.repository,
            tripRepository = appState.tripRepository,
        ).also { it.loadFlights() }
    }

    val tripsViewModel: TripsViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "trips") {
        TripsViewModel(tripRepository = appState.tripRepository)
    }

    val aircraftViewModel: AircraftListViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "aircraft") {
        AircraftListViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
        )
    }

    val forecastMapViewModel: ForecastMapViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "forecastMap") {
        ForecastMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val historicalMapViewModel: HistoricalMapViewModel = viewModel(viewModelStoreOwner = sessionOwner, key = "historicalMap") {
        HistoricalMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val mapCatalog by (appState.helpStore?.mapCatalog?.collectAsState() ?: remember { mutableStateOf(null) })
    val allFlights by flightListViewModel.flights.collectAsState()

    // Persist navigation state across configuration changes (rotation, foldable transitions, dark mode)
    var currentRoute by rememberSaveable(sessionKey) { mutableStateOf(RootRoute.FLIGHTS) }
    var activeFlightId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activeTripId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activePackTimestamp by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activeOpenObserved by rememberSaveable(sessionKey) { mutableStateOf(false) }
    var cachedFlightForBriefing by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }

    val resolvedFlightForBriefing = cachedFlightForBriefing?.takeIf { it.id == activeFlightId }
        ?: allFlights.firstOrNull { it.id == activeFlightId }

    // Each open briefing gets its own owner; closing it (or switching flight / session) clears
    // that owner, so a reopened briefing is a fresh ViewModel. Rotation keeps the key, so the VM.
    val briefingOwner = activeFlightId?.let { viewModelScopes.owner(ViewModelScopes.briefingKey(sessionKey, it)) }
    LaunchedEffect(sessionKey, activeFlightId) {
        viewModelScopes.retainOnly(
            setOfNotNull(
                ViewModelScopes.sessionKey(sessionKey),
                activeFlightId?.let { ViewModelScopes.briefingKey(sessionKey, it) },
            ),
        )
    }

    val selectedBriefingVm: BriefingViewModel? = activeFlightId?.let { fId ->
        viewModel(viewModelStoreOwner = checkNotNull(briefingOwner), key = "briefing") {
            BriefingViewModel(
                flightId = fId,
                repository = appState.repository,
                initialFlight = resolvedFlightForBriefing,
                helpStore = appState.helpStore,
            )
        }
    }

    LaunchedEffect(selectedBriefingVm, activeFlightId, activePackTimestamp, activeOpenObserved) {
        val vm = selectedBriefingVm ?: return@LaunchedEffect
        vm.applyLaunchOptions(
            preferredTimestamp = activePackTimestamp,
            openObserved = activeOpenObserved,
        )
        if (activeOpenObserved) {
            activeOpenObserved = false
        }
    }

    val currentScreen: RootScreen = when (currentRoute) {
        RootRoute.FLIGHTS -> RootScreen.Flights
        RootRoute.BRIEFING -> {
            val fId = activeFlightId
            if (fId != null) {
                RootScreen.Briefing(
                    flightId = fId,
                    flight = resolvedFlightForBriefing,
                    initialTimestamp = activePackTimestamp,
                    openObserved = activeOpenObserved,
                )
            } else {
                RootScreen.Flights
            }
        }
        RootRoute.TRIP -> {
            val tId = activeTripId
            if (tId != null) RootScreen.TripDetail(tId) else RootScreen.Flights
        }
        RootRoute.MAP_FORECAST -> RootScreen.PanEuropeanMap(PanEuropeanMapMode.FORECAST)
        RootRoute.MAP_HISTORICAL -> RootScreen.PanEuropeanMap(PanEuropeanMapMode.HISTORICAL)
        RootRoute.AIRCRAFT -> RootScreen.AircraftList
        RootRoute.WHATS_NEW -> RootScreen.WhatsNew
        RootRoute.SETTINGS -> RootScreen.Settings
    }

    // Add / Edit Flight modal sheet state (flight IDs saved across rotation)
    var showAddFlightSheet by rememberSaveable(sessionKey) { mutableStateOf(false) }
    var editingFlightId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var nextLegSourceFlightId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var duplicateSourceFlightId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var editingFlightMem by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }
    var nextLegSourceFlightMem by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }
    var duplicateSourceFlightMem by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }

    val resolvedEditingFlight = editingFlightMem?.takeIf { it.id == editingFlightId }
        ?: editingFlightId?.let { id -> allFlights.firstOrNull { it.id == id } }
    val resolvedNextLegSourceFlight = nextLegSourceFlightMem?.takeIf { it.id == nextLegSourceFlightId }
        ?: nextLegSourceFlightId?.let { id -> allFlights.firstOrNull { it.id == id } }
    val resolvedDuplicateSourceFlight = duplicateSourceFlightMem?.takeIf { it.id == duplicateSourceFlightId }
        ?: duplicateSourceFlightId?.let { id -> allFlights.firstOrNull { it.id == id } }

    /** Open the add / edit / duplicate sheet; exactly one source (or none for a new flight). */
    fun openFlightSheet(
        editing: FlightResponse? = null,
        nextLegAfter: FlightResponse? = null,
        duplicateOf: FlightResponse? = null,
    ) {
        editingFlightId = editing?.id
        editingFlightMem = editing
        nextLegSourceFlightId = nextLegAfter?.id
        nextLegSourceFlightMem = nextLegAfter
        duplicateSourceFlightId = duplicateOf?.id
        duplicateSourceFlightMem = duplicateOf
        showAddFlightSheet = true
    }

    val sharedPreview by flightListViewModel.sharedPreview.collectAsState()

    fun openBriefing(
        flightId: String,
        flight: FlightResponse? = null,
        packTimestamp: String? = null,
        openObserved: Boolean = false,
        forceReload: Boolean = false,
    ) {
        cachedFlightForBriefing = flight
        // Tapping the selected flight again from another screen is a fresh open: reload it, and
        // so is coming back from editing it (same id, new values, maybe a refresh queued).
        // The current composition may still hold the cleared VM for one frame; the next one
        // finds no owner under this key, creates a new one and with it a fresh BriefingViewModel.
        if (activeFlightId == flightId && (forceReload || currentRoute != RootRoute.BRIEFING)) {
            viewModelScopes.clear(ViewModelScopes.briefingKey(sessionKey, flightId))
        }
        activeFlightId = flightId
        activePackTimestamp = packTimestamp
        activeOpenObserved = openObserved
        currentRoute = RootRoute.BRIEFING
    }

    fun closeBriefingToFlights() {
        activeFlightId = null
        activePackTimestamp = null
        activeOpenObserved = false
        flightListViewModel.loadFlights()
        currentRoute = RootRoute.FLIGHTS
    }

    // Consume deep-link / universal-link navigation target
    LaunchedEffect(pendingNav) {
        val target = appState.consumePendingNavigation() ?: return@LaunchedEffect
        when (target) {
            is NavigationTarget.Briefing -> {
                openBriefing(
                    flightId = target.flightId,
                    packTimestamp = target.packTimestamp,
                    openObserved = target.openObserved,
                )
            }
            is NavigationTarget.Trip -> {
                activeTripId = target.tripId
                currentRoute = RootRoute.TRIP
            }
            is NavigationTarget.SharedFlight -> {
                flightListViewModel.loadSharedFlightPreview(target.code)
            }
            is NavigationTarget.SharedTrip -> {
                activeTripId = target.code
                currentRoute = RootRoute.TRIP
            }
            is NavigationTarget.ForecastMap -> {
                forecastMapViewModel.applyDeepLinkTarget(target)
                currentRoute = RootRoute.MAP_FORECAST
            }
            is NavigationTarget.HistoricalMap -> {
                historicalMapViewModel.applyDeepLinkTarget(target)
                currentRoute = RootRoute.MAP_HISTORICAL
            }
        }
    }

    val configuration = LocalConfiguration.current
    val isExpandedScreen = configuration.screenWidthDp >= 840 && configuration.smallestScreenWidthDp >= 600

    // Predictive back / system Back button handling for single-pane and modal screens
    val shouldInterceptBack = when (currentScreen) {
        RootScreen.Flights -> false
        is RootScreen.Briefing -> !isExpandedScreen
        else -> true
    }
    BackHandler(enabled = shouldInterceptBack) {
        when (currentScreen) {
            is RootScreen.TripDetail -> {
                flightListViewModel.loadFlights()
                currentRoute = RootRoute.FLIGHTS
            }
            is RootScreen.Briefing -> {
                closeBriefingToFlights()
            }
            else -> {
                currentRoute = RootRoute.FLIGHTS
            }
        }
    }

    when (val screen = currentScreen) {
        is RootScreen.PanEuropeanMap -> {
            PanEuropeanMapScreen(
                forecastViewModel = forecastMapViewModel,
                historicalViewModel = historicalMapViewModel,
                initialMode = screen.initialMode,
                mapCatalog = mapCatalog,
                onBack = { currentRoute = RootRoute.FLIGHTS },
            )
        }
        RootScreen.AircraftList -> {
            AircraftListScreen(
                viewModel = aircraftViewModel,
                onBack = { currentRoute = RootRoute.FLIGHTS },
            )
        }
        RootScreen.WhatsNew -> {
            WhatsNewScreen(
                whatsNewStore = appState.whatsNewStore,
                apiClient = appState.apiClient,
                onBack = { currentRoute = RootRoute.FLIGHTS },
            )
        }
        RootScreen.Settings -> {
            SettingsScreen(
                appState = appState,
                onBack = { currentRoute = RootRoute.FLIGHTS },
                onOpenAircraft = { currentRoute = RootRoute.AIRCRAFT },
                onOpenWhatsNew = { currentRoute = RootRoute.WHATS_NEW },
            )
        }
        is RootScreen.TripDetail -> {
            TripDetailScreen(
                tripId = screen.tripId,
                viewModel = tripsViewModel,
                onBack = {
                    flightListViewModel.loadFlights()
                    currentRoute = RootRoute.FLIGHTS
                },
                onOpenFlight = { flight ->
                    openBriefing(flightId = flight.id, flight = flight)
                },
                onAddNextLeg = { prevLeg -> openFlightSheet(nextLegAfter = prevLeg) },
            )
        }
        RootScreen.Flights,
        is RootScreen.Briefing,
        -> {
            if (isExpandedScreen) {
                // Two-pane tablet / foldable layout: FlightList on left, BriefingContainer on right
                Row(modifier = Modifier.fillMaxSize()) {
                    FlightListScreen(
                        viewModel = flightListViewModel,
                        selectedFlightId = activeFlightId,
                        unseenWhatsNewCount = unseenWhatsNew,
                        onFlightClick = { flight ->
                            openBriefing(flightId = flight.id, flight = flight)
                        },
                        onAddFlightClick = { openFlightSheet() },
                        onEditFlightClick = { flight -> openFlightSheet(editing = flight) },
                        onDuplicateFlightClick = { flight -> openFlightSheet(duplicateOf = flight) },
                        onAddNextLegClick = { flight -> openFlightSheet(nextLegAfter = flight) },
                        onOpenTripClick = { tripId ->
                            activeTripId = tripId
                            currentRoute = RootRoute.TRIP
                        },
                        onOpenMapsClick = {
                            currentRoute = RootRoute.MAP_FORECAST
                        },
                        onOpenAircraftClick = {
                            currentRoute = RootRoute.AIRCRAFT
                        },
                        onOpenWhatsNewClick = {
                            currentRoute = RootRoute.WHATS_NEW
                        },
                        onOpenSettingsClick = {
                            currentRoute = RootRoute.SETTINGS
                        },
                        modifier = Modifier
                            .width(380.dp)
                            .fillMaxHeight(),
                    )
                    VerticalDivider()
                    Box(modifier = Modifier.weight(1f).fillMaxHeight()) {
                        val vm = selectedBriefingVm
                        if (vm != null && briefingOwner != null) {
                            BriefingPane(
                                vm,
                                briefingOwner,
                                onBack = { closeBriefingToFlights() },
                                showBackButton = false,
                                onEditFlight = { flight -> openFlightSheet(editing = flight) },
                                onDuplicateFlight = { flight -> openFlightSheet(duplicateOf = flight) },
                            )
                        } else {
                            Surface(
                                modifier = Modifier.fillMaxSize(),
                                color = MaterialTheme.colorScheme.background,
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Text(
                                        text = "Select a flight to view its weather briefing",
                                        style = MaterialTheme.typography.bodyLarge,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(24.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            } else {
                // Phone single-pane layout
                val vm = selectedBriefingVm
                if (screen is RootScreen.Briefing && vm != null && briefingOwner != null) {
                    BriefingPane(
                        vm,
                        briefingOwner,
                        onBack = { closeBriefingToFlights() },
                        showBackButton = true,
                        onEditFlight = { flight -> openFlightSheet(editing = flight) },
                        onDuplicateFlight = { flight -> openFlightSheet(duplicateOf = flight) },
                    )
                } else {
                    FlightListScreen(
                        viewModel = flightListViewModel,
                        selectedFlightId = activeFlightId,
                        unseenWhatsNewCount = unseenWhatsNew,
                        onFlightClick = { flight ->
                            openBriefing(flightId = flight.id, flight = flight)
                        },
                        onAddFlightClick = { openFlightSheet() },
                        onEditFlightClick = { flight -> openFlightSheet(editing = flight) },
                        onDuplicateFlightClick = { flight -> openFlightSheet(duplicateOf = flight) },
                        onAddNextLegClick = { flight -> openFlightSheet(nextLegAfter = flight) },
                        onOpenTripClick = { tripId ->
                            activeTripId = tripId
                            currentRoute = RootRoute.TRIP
                        },
                        onOpenMapsClick = {
                            currentRoute = RootRoute.MAP_FORECAST
                        },
                        onOpenAircraftClick = {
                            currentRoute = RootRoute.AIRCRAFT
                        },
                        onOpenWhatsNewClick = {
                            currentRoute = RootRoute.WHATS_NEW
                        },
                        onOpenSettingsClick = {
                            currentRoute = RootRoute.SETTINGS
                        },
                    )
                }
            }
        }
    }

    if (showAddFlightSheet) {
        val context = LocalContext.current
        val addVm = remember(editingFlightId, nextLegSourceFlightId, duplicateSourceFlightId) {
            AddFlightViewModel(
                repository = appState.repository,
                apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
                airportDb = appState.airportDb,
                // The process-lived app state, so a queued re-brief retry survives the sheet.
                backgroundScope = appState.viewModelScope,
            ).also { vm ->
                resolvedEditingFlight?.let { vm.loadExistingFlight(it) }
                resolvedNextLegSourceFlight?.let { vm.populateNextLeg(it) }
                resolvedDuplicateSourceFlight?.let { vm.loadDuplicateOf(it) }
            }
        }
        AddFlightSheet(
            viewModel = addVm,
            onDismiss = { showAddFlightSheet = false },
            onFlightSaved = { saved ->
                addVm.saveWarning.value?.let { Toast.makeText(context, it, Toast.LENGTH_LONG).show() }
                showAddFlightSheet = false
                flightListViewModel.loadFlights()
                openBriefing(flightId = saved.id, flight = saved, forceReload = true)
            },
        )
    }

    sharedPreview?.let { preview ->
        SharedFlightPreviewSheet(
            preview = preview,
            onAcceptSubscribe = { code ->
                flightListViewModel.acceptSharedFlight(code) { flight ->
                    openBriefing(flightId = flight.id, flight = flight)
                }
            },
            onCopyOwn = { code ->
                flightListViewModel.copySharedFlight(code) { flight ->
                    openBriefing(flightId = flight.id, flight = flight)
                }
            },
            onViewReadOnly = { prev ->
                flightListViewModel.clearSharedPreview()
                openBriefing(flightId = prev.flight.id, flight = prev.flight)
            },
            onDismiss = { flightListViewModel.clearSharedPreview() },
        )
    }
}

/** The briefing, with nested `viewModel()` calls (cross-section) scoped to the briefing's own owner. */
@Composable
private fun BriefingPane(
    viewModel: BriefingViewModel,
    owner: ViewModelStoreOwner,
    onBack: () -> Unit,
    showBackButton: Boolean,
    onEditFlight: (FlightResponse) -> Unit,
    onDuplicateFlight: (FlightResponse) -> Unit,
) {
    CompositionLocalProvider(LocalViewModelStoreOwner provides owner) {
        BriefingContainerScreen(
            viewModel = viewModel,
            onBack = onBack,
            showBackButton = showBackButton,
            onEditFlight = onEditFlight,
            onDuplicateFlight = onDuplicateFlight,
        )
    }
}
