package aero.flyfun.weather

import android.content.Intent
import android.os.Bundle
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
import androidx.compose.ui.unit.dp
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

    if (!isAuthenticated) {
        LoginScreen(
            appState = appState,
            showDevLogin = BuildConfig.DEBUG && aero.flyfun.weather.data.ApiClient.isRunningOnEmulator(),
        )
        return
    }

    val sessionKey = "$cacheScope:$isMockMode:$isGuestBrowsing"

    val flightListViewModel: FlightListViewModel = viewModel(key = "flightList:$sessionKey") {
        FlightListViewModel(
            repository = appState.repository,
            tripRepository = appState.tripRepository,
        ).also { it.loadFlights() }
    }

    val tripsViewModel: TripsViewModel = viewModel(key = "trips:$sessionKey") {
        TripsViewModel(tripRepository = appState.tripRepository)
    }

    val aircraftViewModel: AircraftListViewModel = viewModel(key = "aircraft:$sessionKey") {
        AircraftListViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
        )
    }

    val forecastMapViewModel: ForecastMapViewModel = viewModel(key = "forecastMap:$sessionKey") {
        ForecastMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val historicalMapViewModel: HistoricalMapViewModel = viewModel(key = "historicalMap:$sessionKey") {
        HistoricalMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val mapCatalog by (appState.helpStore?.mapCatalog?.collectAsState() ?: remember { mutableStateOf(null) })

    // Persist navigation state across configuration changes (rotation, foldable transitions, dark mode)
    var currentRoute by rememberSaveable(sessionKey) { mutableStateOf("flights") }
    var activeFlightId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activeTripId by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activePackTimestamp by rememberSaveable(sessionKey) { mutableStateOf<String?>(null) }
    var activeOpenObserved by rememberSaveable(sessionKey) { mutableStateOf(false) }
    var cachedFlightForBriefing by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }

    val selectedBriefingVm: BriefingViewModel? = activeFlightId?.let { fId ->
        viewModel(key = "briefing:$sessionKey:$fId") {
            BriefingViewModel(
                flightId = fId,
                repository = appState.repository,
                initialFlight = cachedFlightForBriefing?.takeIf { it.id == fId },
                helpStore = appState.helpStore,
            ).also { vm ->
                if (activeOpenObserved) {
                    vm.selectTab(BriefingTab.OBSERVED)
                }
                vm.loadInitialBriefing(preferredTimestamp = activePackTimestamp)
            }
        }
    }

    val currentScreen: RootScreen = when (currentRoute) {
        "briefing" -> {
            val fId = activeFlightId
            if (fId != null) {
                RootScreen.Briefing(
                    flightId = fId,
                    flight = cachedFlightForBriefing,
                    initialTimestamp = activePackTimestamp,
                    openObserved = activeOpenObserved,
                )
            } else {
                RootScreen.Flights
            }
        }
        "trip" -> {
            val tId = activeTripId
            if (tId != null) RootScreen.TripDetail(tId) else RootScreen.Flights
        }
        "map_forecast" -> RootScreen.PanEuropeanMap(PanEuropeanMapMode.FORECAST)
        "map_historical" -> RootScreen.PanEuropeanMap(PanEuropeanMapMode.HISTORICAL)
        "aircraft" -> RootScreen.AircraftList
        "whats_new" -> RootScreen.WhatsNew
        "settings" -> RootScreen.Settings
        else -> RootScreen.Flights
    }

    // Add / Edit Flight modal sheet state
    var showAddFlightSheet by rememberSaveable(sessionKey) { mutableStateOf(false) }
    var editingFlight by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }
    var nextLegSourceFlight by remember(sessionKey) { mutableStateOf<FlightResponse?>(null) }

    val sharedPreview by flightListViewModel.sharedPreview.collectAsState()

    fun openBriefing(
        flightId: String,
        flight: FlightResponse? = null,
        packTimestamp: String? = null,
        openObserved: Boolean = false,
    ) {
        cachedFlightForBriefing = flight
        activeFlightId = flightId
        activePackTimestamp = packTimestamp
        activeOpenObserved = openObserved
        currentRoute = "briefing"
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
                currentRoute = "trip"
            }
            is NavigationTarget.SharedFlight -> {
                flightListViewModel.loadSharedFlightPreview(target.code)
            }
            is NavigationTarget.SharedTrip -> {
                activeTripId = target.code
                currentRoute = "trip"
            }
            is NavigationTarget.ForecastMap -> {
                forecastMapViewModel.applyDeepLinkTarget(target)
                currentRoute = "map_forecast"
            }
            is NavigationTarget.HistoricalMap -> {
                historicalMapViewModel.applyDeepLinkTarget(target)
                currentRoute = "map_historical"
            }
        }
    }

    val configuration = LocalConfiguration.current
    val isExpandedScreen = configuration.screenWidthDp >= 840

    // Predictive back / system Back button handling for single-pane and modal screens
    val shouldInterceptBack = when (currentScreen) {
        RootScreen.Flights -> false
        is RootScreen.Briefing -> !isExpandedScreen
        else -> true
    }
    BackHandler(enabled = shouldInterceptBack) {
        if (currentScreen is RootScreen.TripDetail) {
            flightListViewModel.loadFlights()
        }
        currentRoute = "flights"
    }

    when (val screen = currentScreen) {
        is RootScreen.PanEuropeanMap -> {
            PanEuropeanMapScreen(
                forecastViewModel = forecastMapViewModel,
                historicalViewModel = historicalMapViewModel,
                initialMode = screen.initialMode,
                mapCatalog = mapCatalog,
                onBack = { currentRoute = "flights" },
            )
        }
        RootScreen.AircraftList -> {
            AircraftListScreen(
                viewModel = aircraftViewModel,
                onBack = { currentRoute = "flights" },
            )
        }
        RootScreen.WhatsNew -> {
            WhatsNewScreen(
                whatsNewStore = appState.whatsNewStore,
                apiClient = appState.apiClient,
                onBack = { currentRoute = "flights" },
            )
        }
        RootScreen.Settings -> {
            SettingsScreen(
                appState = appState,
                onBack = { currentRoute = "flights" },
                onOpenAircraft = { currentRoute = "aircraft" },
                onOpenWhatsNew = { currentRoute = "whats_new" },
            )
        }
        is RootScreen.TripDetail -> {
            TripDetailScreen(
                tripId = screen.tripId,
                viewModel = tripsViewModel,
                onBack = {
                    flightListViewModel.loadFlights()
                    currentRoute = "flights"
                },
                onOpenFlight = { flight ->
                    openBriefing(flightId = flight.id, flight = flight)
                },
                onAddNextLeg = { prevLeg ->
                    editingFlight = null
                    nextLegSourceFlight = prevLeg
                    showAddFlightSheet = true
                },
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
                        onAddFlightClick = {
                            editingFlight = null
                            nextLegSourceFlight = null
                            showAddFlightSheet = true
                        },
                        onEditFlightClick = { flight ->
                            editingFlight = flight
                            nextLegSourceFlight = null
                            showAddFlightSheet = true
                        },
                        onAddNextLegClick = { flight ->
                            editingFlight = null
                            nextLegSourceFlight = flight
                            showAddFlightSheet = true
                        },
                        onOpenTripClick = { tripId ->
                            activeTripId = tripId
                            currentRoute = "trip"
                        },
                        onOpenMapsClick = {
                            currentRoute = "map_forecast"
                        },
                        onOpenAircraftClick = {
                            currentRoute = "aircraft"
                        },
                        onOpenWhatsNewClick = {
                            currentRoute = "whats_new"
                        },
                        onOpenSettingsClick = {
                            currentRoute = "settings"
                        },
                        modifier = Modifier
                            .width(380.dp)
                            .fillMaxHeight(),
                    )
                    VerticalDivider()
                    Box(modifier = Modifier.weight(1f).fillMaxHeight()) {
                        val vm = selectedBriefingVm
                        if (vm != null) {
                            BriefingContainerScreen(
                                viewModel = vm,
                                onBack = {
                                    activeFlightId = null
                                    currentRoute = "flights"
                                },
                                showBackButton = false,
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
                if (screen is RootScreen.Briefing && vm != null) {
                    BriefingContainerScreen(
                        viewModel = vm,
                        onBack = {
                            currentRoute = "flights"
                        },
                        showBackButton = true,
                    )
                } else {
                    FlightListScreen(
                        viewModel = flightListViewModel,
                        selectedFlightId = activeFlightId,
                        unseenWhatsNewCount = unseenWhatsNew,
                        onFlightClick = { flight ->
                            openBriefing(flightId = flight.id, flight = flight)
                        },
                        onAddFlightClick = {
                            editingFlight = null
                            nextLegSourceFlight = null
                            showAddFlightSheet = true
                        },
                        onEditFlightClick = { flight ->
                            editingFlight = flight
                            nextLegSourceFlight = null
                            showAddFlightSheet = true
                        },
                        onAddNextLegClick = { flight ->
                            editingFlight = null
                            nextLegSourceFlight = flight
                            showAddFlightSheet = true
                        },
                        onOpenTripClick = { tripId ->
                            activeTripId = tripId
                            currentRoute = "trip"
                        },
                        onOpenMapsClick = {
                            currentRoute = "map_forecast"
                        },
                        onOpenAircraftClick = {
                            currentRoute = "aircraft"
                        },
                        onOpenWhatsNewClick = {
                            currentRoute = "whats_new"
                        },
                        onOpenSettingsClick = {
                            currentRoute = "settings"
                        },
                    )
                }
            }
        }
    }

    if (showAddFlightSheet) {
        val addVm = remember(editingFlight, nextLegSourceFlight) {
            AddFlightViewModel(
                repository = appState.repository,
                apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
                tripRepository = appState.tripRepository,
                airportDb = appState.airportDb,
            ).also { vm ->
                editingFlight?.let { vm.loadExistingFlight(it) }
                nextLegSourceFlight?.let { vm.populateNextLeg(it) }
            }
        }
        AddFlightSheet(
            viewModel = addVm,
            onDismiss = { showAddFlightSheet = false },
            onFlightSaved = { saved ->
                showAddFlightSheet = false
                flightListViewModel.loadFlights()
                openBriefing(flightId = saved.id, flight = saved)
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
