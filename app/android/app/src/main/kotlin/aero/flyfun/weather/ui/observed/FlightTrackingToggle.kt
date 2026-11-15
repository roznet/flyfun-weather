package aero.flyfun.weather.ui.observed

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.service.FlightTrackingService
import aero.flyfun.weather.viewmodel.BriefingViewModel

/** Whether this flight is being tracked, and the one action that starts or stops it. */
class FlightTrackingToggle(
    val isTracking: Boolean,
    val toggle: () -> Unit,
)

/**
 * Start / Stop flight tracking for the briefing on screen, shared by the toolbar's Start button
 * (#996) and the Observed tab's tracking bar so both start the same service the same way: ask for
 * location (and, from Android 13, notification) permission once, then start the foreground
 * service with the route's coordinates and the started flight's window end (#874). In demo mode
 * (fixture repository) it flips the view model's simulated flag instead.
 */
@Composable
fun rememberFlightTrackingToggle(viewModel: BriefingViewModel): FlightTrackingToggle {
    val context = LocalContext.current
    val flight by viewModel.flight.collectAsState()
    val snapshot by viewModel.snapshot.collectAsState()
    val elevation by viewModel.elevation.collectAsState()
    val serviceTracking by FlightTrackingService.isTracking.collectAsState()
    val serviceFlightId by FlightTrackingService.activeFlightId.collectAsState()
    val demoTracking by viewModel.demoTrackingActive.collectAsState()

    val isDemoMode = viewModel.repository is FixtureBriefingRepository
    val currentFlightId = flight?.id ?: viewModel.flightId.orEmpty()
    val isTracking = if (isDemoMode) demoTracking else serviceTracking && serviceFlightId == currentFlightId

    val routeCoordinates = remember(snapshot, elevation) {
        val wpCoords = snapshot?.route?.waypoints
            ?.filter { it.lat != 0.0 || it.lon != 0.0 }
            ?.map { it.lat to it.lon }
            .orEmpty()
        if (wpCoords.size >= 2) {
            wpCoords
        } else {
            elevation?.points
                ?.filter { it.lat != 0.0 || it.lon != 0.0 }
                ?.map { it.lat to it.lon }
                .orEmpty()
        }
    }

    val start: () -> Unit = {
        FlightTrackingService.startTracking(
            context = context,
            flightId = currentFlightId,
            waypoints = routeCoordinates,
            apiClient = viewModel.apiClient,
            // Until the landing, or the latest a late take-off could still be flying (#874).
            endEpochMs = flight?.trackingWindowEndEpochMs(started = true),
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val granted = grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true ||
            FlightTrackingService.hasLocationPermission(context)
        if (granted && currentFlightId.isNotBlank()) start()
    }

    val toggle: () -> Unit = {
        when {
            isDemoMode -> viewModel.demoTrackingActive.value = !demoTracking
            isTracking -> FlightTrackingService.stopTracking(context)
            currentFlightId.isBlank() -> Unit
            FlightTrackingService.hasLocationPermission(context) -> start()
            else -> {
                val permissions = buildList {
                    add(Manifest.permission.ACCESS_FINE_LOCATION)
                    add(Manifest.permission.ACCESS_COARSE_LOCATION)
                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
                        add(Manifest.permission.POST_NOTIFICATIONS)
                    }
                }.toTypedArray()
                permissionLauncher.launch(permissions)
            }
        }
    }
    return FlightTrackingToggle(isTracking = isTracking, toggle = toggle)
}
