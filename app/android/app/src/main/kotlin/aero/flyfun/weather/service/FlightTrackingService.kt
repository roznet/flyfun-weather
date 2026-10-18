package aero.flyfun.weather.service

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import aero.flyfun.weather.MainActivity
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.LivePositionFix
import aero.flyfun.weather.model.LivePositionRequest
import aero.flyfun.weather.model.LivePositionResult
import aero.flyfun.weather.model.rules.LivePositionRules
import aero.flyfun.weather.model.rules.ProjectedRoutePosition
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Foreground location service for in-flight GPS route tracking and throttled live position reporting.
 *
 * Uses Android's [LocationManager] (`FUSED_PROVIDER` / `GPS_PROVIDER`), projects fixes onto the
 * active route via [LivePositionRules.projectOntoRoute], and uploads throttled fixes via
 * `POST /api/flights/{id}/live/position` (with fallback to `POST /api/live-position`).
 *
 * Mirrors iOS `FlightTrackingService.swift` and `LivePositionService.swift`.
 */
class FlightTrackingService : Service() {

    inner class LocalBinder : Binder() {
        val service: FlightTrackingService
            get() = this@FlightTrackingService
    }

    private val binder = LocalBinder()
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var locationManager: LocationManager? = null

    private val locationListener = LocationListener { location ->
        handleAndroidLocation(location)
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        ensureNotificationChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopLocationUpdates()
                clearTrackingState()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_START, null -> {
                val flightId = intent?.getStringExtra(EXTRA_FLIGHT_ID) ?: _activeFlightId.value
                if (flightId != null) {
                    _activeFlightId.value = flightId
                }
                val notification = buildForegroundNotification(this, flightId)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    startForeground(
                        NOTIFICATION_ID,
                        notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION,
                    )
                } else {
                    startForeground(NOTIFICATION_ID, notification)
                }
                startLocationUpdates()
                return START_STICKY
            }
            else -> return START_NOT_STICKY
        }
    }

    override fun onDestroy() {
        stopLocationUpdates()
        clearTrackingState()
        serviceScope.cancel()
        super.onDestroy()
    }

    @SuppressLint("MissingPermission")
    private fun startLocationUpdates() {
        if (!hasLocationPermission(this)) {
            return
        }
        val mgr = getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return
        locationManager = mgr
        _isTracking.value = true

        val providers = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                runCatching { mgr.isProviderEnabled(LocationManager.FUSED_PROVIDER) }.getOrDefault(false)
            ) {
                add(LocationManager.FUSED_PROVIDER)
            }
            if (runCatching { mgr.isProviderEnabled(LocationManager.GPS_PROVIDER) }.getOrDefault(false)) {
                add(LocationManager.GPS_PROVIDER)
            }
            if (isEmpty() && runCatching { mgr.isProviderEnabled(LocationManager.NETWORK_PROVIDER) }.getOrDefault(false)) {
                add(LocationManager.NETWORK_PROVIDER)
            }
        }

        for (provider in providers) {
            runCatching {
                mgr.requestLocationUpdates(
                    provider,
                    MIN_UPDATE_INTERVAL_MS,
                    MIN_UPDATE_DISTANCE_M,
                    locationListener,
                    Looper.getMainLooper(),
                )
            }
        }

        // Seed initial fix from last known location if fresh (< 2 min old)
        providers.firstNotNullOfOrNull { provider ->
            runCatching { mgr.getLastKnownLocation(provider) }.getOrNull()
        }?.let { lastLoc ->
            val ageMs = System.currentTimeMillis() - lastLoc.time
            if (ageMs in 0..120_000L) {
                handleAndroidLocation(lastLoc)
            }
        }
    }

    private fun stopLocationUpdates() {
        runCatching {
            locationManager?.removeUpdates(locationListener)
        }
        locationManager = null
    }

    private fun handleAndroidLocation(location: Location) {
        if (location.hasAccuracy() && location.accuracy > MAX_ACCEPTABLE_ACCURACY_M) {
            return
        }
        val altitudeFt = if (location.hasAltitude()) location.altitude * METERS_TO_FEET else 0.0
        val speedKt = if (location.hasSpeed()) location.speed * MPS_TO_KNOTS else 0.0
        val trackDeg = if (location.hasBearing()) location.bearing.toDouble() else 0.0
        val recordedIso = IsoTime.formatUtc(if (location.time > 0L) location.time else System.currentTimeMillis())

        serviceScope.launch {
            onLocationFix(
                lat = location.latitude,
                lon = location.longitude,
                altitudeFt = altitudeFt,
                groundSpeedKt = speedKt,
                trackDeg = trackDeg,
                recordedAt = recordedIso,
            )
        }
    }

    companion object {
        const val ACTION_START: String = "aero.flyfun.weather.service.action.START_TRACKING"
        const val ACTION_STOP: String = "aero.flyfun.weather.service.action.STOP_TRACKING"
        const val EXTRA_FLIGHT_ID: String = "extra_flight_id"

        const val CHANNEL_ID: String = "flyfun_flight_tracking"
        const val NOTIFICATION_ID: Int = 4101

        private const val MIN_UPDATE_INTERVAL_MS: Long = 5_000L
        private const val MIN_UPDATE_DISTANCE_M: Float = 25f
        private const val MAX_ACCEPTABLE_ACCURACY_M: Float = 250f
        private const val METERS_TO_FEET: Double = 3.28084
        private const val MPS_TO_KNOTS: Double = 1.94384

        private val stateMutex = Mutex()

        private val _currentPosition = MutableStateFlow<ProjectedRoutePosition?>(null)
        val currentPosition: StateFlow<ProjectedRoutePosition?> = _currentPosition.asStateFlow()

        private val _isTracking = MutableStateFlow(false)
        val isTracking: StateFlow<Boolean> = _isTracking.asStateFlow()

        private val _activeFlightId = MutableStateFlow<String?>(null)
        val activeFlightId: StateFlow<String?> = _activeFlightId.asStateFlow()

        private var activeWaypoints: List<Pair<Double, Double>> = emptyList()
        private var activeApiClient: ApiClient? = null
        private var lastQueuedFix: LivePositionFix? = null
        private val pendingFixes = mutableListOf<LivePositionFix>()

        /**
         * Start foreground GPS tracking for `flightId` along `waypoints`.
         */
        fun startTracking(
            context: Context,
            flightId: String,
            waypoints: List<Pair<Double, Double>>,
            apiClient: ApiClient? = null,
        ) {
            configureSession(flightId = flightId, waypoints = waypoints, apiClient = apiClient)
            val appCtx = context.applicationContext
            val intent = Intent(appCtx, FlightTrackingService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_FLIGHT_ID, flightId)
            }
            runCatching {
                ContextCompat.startForegroundService(appCtx, intent)
            }.onFailure {
                _isTracking.value = true
            }
        }

        /**
         * Stop foreground GPS tracking and clear the active session.
         */
        fun stopTracking(context: Context) {
            val appCtx = context.applicationContext
            val intent = Intent(appCtx, FlightTrackingService::class.java).apply {
                action = ACTION_STOP
            }
            runCatching {
                appCtx.startService(intent)
            }.onFailure {
                appCtx.stopService(Intent(appCtx, FlightTrackingService::class.java))
            }
            clearTrackingState()
        }

        /**
         * Configure the active route session in memory without starting the Android service
         * (useful for previews and unit tests).
         */
        fun configureSession(
            flightId: String,
            waypoints: List<Pair<Double, Double>>,
            apiClient: ApiClient? = null,
        ) {
            _activeFlightId.value = flightId
            activeWaypoints = waypoints.toList()
            if (apiClient != null) {
                activeApiClient = apiClient
            }
            lastQueuedFix = null
            pendingFixes.clear()
        }

        /**
         * Clear in-memory tracking state.
         */
        fun clearTrackingState() {
            _isTracking.value = false
            _activeFlightId.value = null
            _currentPosition.value = null
            activeWaypoints = emptyList()
            lastQueuedFix = null
            pendingFixes.clear()
        }

        /**
         * Process a single GPS fix: project it onto `activeWaypoints` via [LivePositionRules.projectOntoRoute],
         * update [currentPosition], and when [LivePositionRules.shouldSendPositionFix] is satisfied,
         * enqueue and post the fix to the backend.
         */
        suspend fun onLocationFix(
            lat: Double,
            lon: Double,
            altitudeFt: Double,
            groundSpeedKt: Double,
            trackDeg: Double,
            recordedAt: String = IsoTime.formatUtc(System.currentTimeMillis()),
        ): ProjectedRoutePosition? = stateMutex.withLock {
            val projected = LivePositionRules.projectOntoRoute(
                lat = lat,
                lon = lon,
                altitudeFt = altitudeFt,
                groundSpeedKt = groundSpeedKt,
                trackDeg = trackDeg,
                waypoints = activeWaypoints,
            )
            _currentPosition.value = projected

            val fix = LivePositionFix(
                lat = lat,
                lon = lon,
                altitudeFt = altitudeFt.roundToInt().toDouble(),
                groundSpeedKt = groundSpeedKt.roundToInt().toDouble(),
                trackDeg = trackDeg.roundToInt().toDouble(),
                recordedAt = recordedAt,
            )

            if (LivePositionRules.shouldSendPositionFix(lastQueuedFix, fix)) {
                lastQueuedFix = fix
                pendingFixes.add(fix)
                val trimmed = LivePositionRules.trimFixBuffer(pendingFixes)
                pendingFixes.clear()
                pendingFixes.addAll(trimmed)
                flushPendingFixesLocked()
            }

            projected
        }

        private suspend fun flushPendingFixesLocked() {
            val client = activeApiClient ?: return
            val flightId = _activeFlightId.value
            if (pendingFixes.isEmpty()) return

            val batch = pendingFixes.take(LivePositionRules.MAX_BATCH_SIZE)
            try {
                if (!flightId.isNullOrBlank()) {
                    runCatching {
                        client.post<LivePositionRequest, LivePositionResult>(
                            path = "/api/flights/$flightId/live/position",
                            body = LivePositionRequest(fixes = batch),
                        )
                    }.getOrElse {
                        client.reportLivePosition(batch)
                    }
                } else {
                    client.reportLivePosition(batch)
                }
                repeat(batch.size.coerceAtMost(pendingFixes.size)) {
                    pendingFixes.removeAt(0)
                }
            } catch (err: Throwable) {
                val statusCode = (err as? ApiError.ServerError)?.statusCode
                    ?: when (err) {
                        is ApiError.Unauthorized -> 401
                        is ApiError.Forbidden -> 403
                        is ApiError.NotFound -> 404
                        else -> null
                    }
                if (LivePositionRules.failureAction(statusCode) == LivePositionRules.FailureAction.DROP_BATCH) {
                    repeat(batch.size.coerceAtMost(pendingFixes.size)) {
                        pendingFixes.removeAt(0)
                    }
                }
            }
        }

        fun hasLocationPermission(context: Context): Boolean {
            val fine = ContextCompat.checkSelfPermission(
                context,
                android.Manifest.permission.ACCESS_FINE_LOCATION,
            ) == PackageManager.PERMISSION_GRANTED
            val coarse = ContextCompat.checkSelfPermission(
                context,
                android.Manifest.permission.ACCESS_COARSE_LOCATION,
            ) == PackageManager.PERMISSION_GRANTED
            return fine || coarse
        }

        private fun ensureNotificationChannel(context: Context) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    "In-Flight Route Tracking",
                    NotificationManager.IMPORTANCE_LOW,
                ).apply {
                    description = "Shows active GPS route progress while tracking a flight"
                    setShowBadge(false)
                }
                nm.createNotificationChannel(channel)
            }
        }

        private fun buildForegroundNotification(
            context: Context,
            flightId: String?,
        ): Notification {
            val launchIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                if (flightId != null) {
                    putExtra(EXTRA_FLIGHT_ID, flightId)
                }
            }
            val pendingLaunch = PendingIntent.getActivity(
                context,
                0,
                launchIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )

            val stopIntent = Intent(context, FlightTrackingService::class.java).apply {
                action = ACTION_STOP
            }
            val pendingStop = PendingIntent.getService(
                context,
                1,
                stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )

            val pos = _currentPosition.value
            val contentText = if (pos != null) {
                String.format(
                    Locale.US,
                    "%.0f%% along route · %.0f ft · %.0f kt",
                    pos.routeFraction * 100.0,
                    pos.altitudeFt,
                    pos.groundSpeedKt,
                )
            } else {
                "Tracking live route progress"
            }

            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentTitle("FlyFun In-Flight Tracking")
                .setContentText(contentText)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(NotificationCompat.CATEGORY_NAVIGATION)
                .setContentIntent(pendingLaunch)
                .addAction(
                    android.R.drawable.ic_media_pause,
                    "Stop",
                    pendingStop,
                )
                .build()
        }
    }
}
