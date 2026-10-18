package aero.flyfun.weather

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
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
    val pendingNav by appState.pendingNavTarget.collectAsState()
    val unseenWhatsNew by (appState.whatsNewStore?.unseenCount?.collectAsState() ?: remember { mutableStateOf(0) })

    if (!isAuthenticated) {
        LoginScreen(
            appState = appState,
            showDevLogin = BuildConfig.DEBUG && aero.flyfun.weather.data.ApiClient.isRunningOnEmulator(),
        )
        return
    }

    val flightListViewModel = remember(isMockMode, isGuestBrowsing) {
        FlightListViewModel(
            repository = appState.repository,
            tripRepository = appState.tripRepository,
        ).also { it.loadFlights() }
    }

    val tripsViewModel = remember(isMockMode, isGuestBrowsing) {
        TripsViewModel(tripRepository = appState.tripRepository)
    }

    val aircraftViewModel = remember(isMockMode, isGuestBrowsing) {
        AircraftListViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
        )
    }

    val forecastMapViewModel = remember(isMockMode, isGuestBrowsing) {
        ForecastMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val historicalMapViewModel = remember(isMockMode, isGuestBrowsing) {
        HistoricalMapViewModel(
            apiClient = if (isMockMode || isGuestBrowsing) null else appState.apiClient,
            isMockModeProvider = { isMockMode || isGuestBrowsing },
        )
    }

    val mapCatalog by (appState.helpStore?.mapCatalog?.collectAsState() ?: remember { mutableStateOf(null) })

    var currentScreen by remember { mutableStateOf<RootScreen>(RootScreen.Flights) }
    var selectedBriefingVm by remember { mutableStateOf<BriefingViewModel?>(null) }
    var activeFlightId by remember { mutableStateOf<String?>(null) }

    // Add / Edit Flight modal sheet state
    var showAddFlightSheet by remember { mutableStateOf(false) }
    var editingFlight by remember { mutableStateOf<FlightResponse?>(null) }
    var nextLegSourceFlight by remember { mutableStateOf<FlightResponse?>(null) }

    val sharedPreview by flightListViewModel.sharedPreview.collectAsState()

    fun openBriefing(
        flightId: String,
        flight: FlightResponse? = null,
        packTimestamp: String? = null,
        openObserved: Boolean = false,
    ) {
        activeFlightId = flightId
        val vm = BriefingViewModel(
            flightId = flightId,
            repository = appState.repository,
            initialFlight = flight,
            helpStore = appState.helpStore,
        )
        if (openObserved) {
            vm.selectTab(BriefingTab.OBSERVED)
        }
        vm.loadInitialBriefing(preferredTimestamp = packTimestamp)
        selectedBriefingVm = vm
        currentScreen = RootScreen.Briefing(
            flightId = flightId,
            flight = flight,
            initialTimestamp = packTimestamp,
            openObserved = openObserved,
        )
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
                currentScreen = RootScreen.TripDetail(target.tripId)
            }
            is NavigationTarget.SharedFlight -> {
                flightListViewModel.loadSharedFlightPreview(target.code)
            }
            is NavigationTarget.SharedTrip -> {
                currentScreen = RootScreen.TripDetail(target.code)
            }
            is NavigationTarget.ForecastMap -> {
                forecastMapViewModel.applyDeepLinkTarget(target)
                currentScreen = RootScreen.PanEuropeanMap(PanEuropeanMapMode.FORECAST)
            }
            is NavigationTarget.HistoricalMap -> {
                historicalMapViewModel.applyDeepLinkTarget(target)
                currentScreen = RootScreen.PanEuropeanMap(PanEuropeanMapMode.HISTORICAL)
            }
        }
    }

    val configuration = LocalConfiguration.current
    val isExpandedScreen = configuration.screenWidthDp >= 840

    when (val screen = currentScreen) {
        is RootScreen.PanEuropeanMap -> {
            PanEuropeanMapScreen(
                forecastViewModel = forecastMapViewModel,
                historicalViewModel = historicalMapViewModel,
                initialMode = screen.initialMode,
                mapCatalog = mapCatalog,
                onBack = { currentScreen = RootScreen.Flights },
            )
        }
        RootScreen.AircraftList -> {
            AircraftListScreen(
                viewModel = aircraftViewModel,
                onBack = { currentScreen = RootScreen.Flights },
            )
        }
        RootScreen.WhatsNew -> {
            WhatsNewScreen(
                whatsNewStore = appState.whatsNewStore,
                apiClient = appState.apiClient,
                onBack = { currentScreen = RootScreen.Flights },
            )
        }
        RootScreen.Settings -> {
            SettingsScreen(
                appState = appState,
                onBack = { currentScreen = RootScreen.Flights },
                onOpenAircraft = { currentScreen = RootScreen.AircraftList },
                onOpenWhatsNew = { currentScreen = RootScreen.WhatsNew },
            )
        }
        is RootScreen.TripDetail -> {
            TripDetailScreen(
                tripId = screen.tripId,
                viewModel = tripsViewModel,
                onBack = {
                    flightListViewModel.loadFlights()
                    currentScreen = RootScreen.Flights
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
                            currentScreen = RootScreen.TripDetail(tripId)
                        },
                        onOpenMapsClick = {
                            currentScreen = RootScreen.PanEuropeanMap(PanEuropeanMapMode.FORECAST)
                        },
                        onOpenAircraftClick = {
                            currentScreen = RootScreen.AircraftList
                        },
                        onOpenWhatsNewClick = {
                            currentScreen = RootScreen.WhatsNew
                        },
                        onOpenSettingsClick = {
                            currentScreen = RootScreen.Settings
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
                                    selectedBriefingVm = null
                                    activeFlightId = null
                                    currentScreen = RootScreen.Flights
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
                            currentScreen = RootScreen.Flights
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
                            currentScreen = RootScreen.TripDetail(tripId)
                        },
                        onOpenMapsClick = {
                            currentScreen = RootScreen.PanEuropeanMap(PanEuropeanMapMode.FORECAST)
                        },
                        onOpenAircraftClick = {
                            currentScreen = RootScreen.AircraftList
                        },
                        onOpenWhatsNewClick = {
                            currentScreen = RootScreen.WhatsNew
                        },
                        onOpenSettingsClick = {
                            currentScreen = RootScreen.Settings
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
