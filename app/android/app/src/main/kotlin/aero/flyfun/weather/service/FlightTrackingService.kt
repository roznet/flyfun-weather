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
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import aero.flyfun.weather.MainActivity
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.ApiError
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.LivePositionFix
import aero.flyfun.weather.model.LivePositionRequest
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
import kotlinx.coroutines.withContext
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
        // Null, never 0, when the device has no value: the server reads altitude 0 as on the ground.
        val altitudeFt = if (location.hasAltitude()) location.altitude * METERS_TO_FEET else null
        val speedKt = if (location.hasSpeed()) location.speed * MPS_TO_KNOTS else null
        val trackDeg = if (location.hasBearing()) location.bearing.toDouble() else null
        val accuracyM = if (location.hasAccuracy()) location.accuracy.toDouble() else null
        val recordedIso = IsoTime.formatUtc(if (location.time > 0L) location.time else System.currentTimeMillis())

        serviceScope.launch {
            val stop = onLocationFix(
                lat = location.latitude,
                lon = location.longitude,
                altitudeFt = altitudeFt,
                groundSpeedKt = speedKt,
                trackDeg = trackDeg,
                hAccuracyM = accuracyM,
                recordedAt = recordedIso,
            )
            if (stop != null) {
                withContext(Dispatchers.Main) {
                    stopLocationUpdates()
                    clearTrackingState()
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                }
            }
        }
    }

    /** Why tracking stopped on its own (#874). */
    enum class AutoStop {
        /** Slow near the destination after the take-off roll: the landing was sent. */
        LANDED,

        /** Past [trackingEndEpochMs]: the latest the flight could still be flying. */
        WINDOW_ENDED,
    }

    companion object {
        const val ACTION_START: String = "aero.flyfun.weather.service.action.START_TRACKING"
        const val ACTION_STOP: String = "aero.flyfun.weather.service.action.STOP_TRACKING"
        const val EXTRA_FLIGHT_ID: String = "extra_flight_id"

        const val CHANNEL_ID: String = "flyfun_flight_tracking"
        const val NOTIFICATION_ID: Int = 4101
        private const val TAG: String = "FlightTracking"

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

        /** Tracking auto-stops after this (`FlightResponse.trackingWindowEndEpochMs(started = true)`), null: never. */
        private var trackingEndEpochMs: Long? = null

        /** A fix at take-off speed was seen: only then can a slow fix near the destination be the landing. */
        private var airborneSeen = false

        /** The landing was detected but its post has not gone through yet: retried with each next fix. */
        private var landingPending = false

        /** Auto-stop when the flight is not known yet (no departure to compute the window from). */
        private const val FALLBACK_TRACKING_MS: Long = 12L * 3_600_000L
        private var lastQueuedFix: LivePositionFix? = null
        private val pendingFixes = mutableListOf<LivePositionFix>()

        /**
         * Start foreground GPS tracking for `flightId` along `waypoints`, until the landing is
         * detected or `endEpochMs` passes (#874).
         */
        fun startTracking(
            context: Context,
            flightId: String,
            waypoints: List<Pair<Double, Double>>,
            apiClient: ApiClient? = null,
            endEpochMs: Long? = null,
        ) {
            configureSession(
                flightId = flightId,
                waypoints = waypoints,
                apiClient = apiClient,
                endEpochMs = endEpochMs ?: (System.currentTimeMillis() + FALLBACK_TRACKING_MS),
            )
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
            endEpochMs: Long? = null,
        ) {
            _activeFlightId.value = flightId
            activeWaypoints = waypoints.toList()
            if (apiClient != null) {
                activeApiClient = apiClient
            }
            trackingEndEpochMs = endEpochMs
            airborneSeen = false
            landingPending = false
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
            trackingEndEpochMs = null
            airborneSeen = false
            landingPending = false
            lastQueuedFix = null
            pendingFixes.clear()
        }

        /**
         * Process a single GPS fix: project it onto `activeWaypoints` via [LivePositionRules.projectOntoRoute],
         * update [currentPosition], and when [LivePositionRules.shouldSendPositionFix] is satisfied,
         * enqueue and post the fix to the backend.
         *
         * Returns why tracking should stop, or null to carry on (#874): [AutoStop.LANDED] once a fix
         * reads as the landing ([LivePositionRules.isLanding], after the take-off roll was seen) and
         * the post with `landed = true` went through (like iOS); a landing post that fails for want of
         * data is retried with each next fix, tracking running on meanwhile; [AutoStop.WINDOW_ENDED] past
         * [trackingEndEpochMs]. A fix without a horizontal accuracy is projected but not sent (the
         * server requires one).
         */
        suspend fun onLocationFix(
            lat: Double,
            lon: Double,
            altitudeFt: Double?,
            groundSpeedKt: Double?,
            trackDeg: Double?,
            hAccuracyM: Double?,
            recordedAt: String = IsoTime.formatUtc(System.currentTimeMillis()),
            nowEpochMs: Long = System.currentTimeMillis(),
        ): AutoStop? = stateMutex.withLock {
            trackingEndEpochMs?.let { end ->
                if (nowEpochMs > end) return@withLock AutoStop.WINDOW_ENDED
            }

            val projected = LivePositionRules.projectOntoRoute(
                lat = lat,
                lon = lon,
                altitudeFt = altitudeFt ?: 0.0,
                groundSpeedKt = groundSpeedKt ?: 0.0,
                trackDeg = trackDeg ?: 0.0,
                waypoints = activeWaypoints,
            )
            _currentPosition.value = projected

            val fix = hAccuracyM?.let {
                LivePositionFix(
                    lat = lat,
                    lon = lon,
                    altitudeFt = altitudeFt?.roundToInt()?.toDouble(),
                    groundSpeedKt = groundSpeedKt?.roundToInt()?.toDouble(),
                    trackDeg = trackDeg?.roundToInt()?.toDouble(),
                    recordedAt = recordedAt,
                    hAccuracyM = it,
                )
            }

            if (fix == null) {
                Log.d(TAG, "Fix without horizontal accuracy: projected, not sent")
            }

            if (landingPending ||
                (airborneSeen && LivePositionRules.isLanding(lat, lon, groundSpeedKt, activeWaypoints))
            ) {
                if (!landingPending && fix != null) enqueueLocked(fix)
                landingPending = true
                return@withLock if (flushPendingFixesLocked(landed = true)) AutoStop.LANDED else null
            }
            if (groundSpeedKt != null && groundSpeedKt >= LivePositionRules.LANDING_MAX_SPEED_KT) {
                airborneSeen = true
            }

            if (fix != null && LivePositionRules.shouldSendPositionFix(lastQueuedFix, fix)) {
                enqueueLocked(fix)
                flushPendingFixesLocked()
            }

            null
        }

        private fun enqueueLocked(fix: LivePositionFix) {
            lastQueuedFix = fix
            pendingFixes.add(fix)
            val trimmed = LivePositionRules.trimFixBuffer(pendingFixes)
            pendingFixes.clear()
            pendingFixes.addAll(trimmed)
        }

        /**
         * Post the queued fixes oldest first, in batches of at most [LivePositionRules.MAX_BATCH_SIZE]
         * (the server's limit), to `POST /api/flights/{id}/live/position`; `landed` goes with the last
         * batch. A batch that fails is kept for the next flush (or dropped on a permanent refusal).
         * Returns whether everything, the landing included, was delivered or dropped for good.
         */
        private suspend fun flushPendingFixesLocked(landed: Boolean = false): Boolean {
            val client = activeApiClient ?: return false
            val flightId = _activeFlightId.value
            if (flightId.isNullOrBlank()) return false

            do {
                val batch = pendingFixes.take(LivePositionRules.MAX_BATCH_SIZE)
                val last = batch.size == pendingFixes.size
                if (batch.isEmpty() && !landed) return true
                try {
                    client.postLivePosition(flightId, LivePositionRequest(fixes = batch, landed = landed && last))
                } catch (err: Throwable) {
                    val statusCode = (err as? ApiError.ServerError)?.statusCode
                        ?: when (err) {
                            is ApiError.Unauthorized -> 401
                            is ApiError.Forbidden -> 403
                            is ApiError.NotFound -> 404
                            else -> null
                        }
                    if (LivePositionRules.failureAction(statusCode) != LivePositionRules.FailureAction.DROP_BATCH) {
                        return false
                    }
                    Log.w(TAG, "Position post refused ($statusCode), ${batch.size} fixes dropped")
                }
                repeat(batch.size) { pendingFixes.removeAt(0) }
            } while (pendingFixes.isNotEmpty())
            return true
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
