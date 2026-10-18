package aero.flyfun.weather.service

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import aero.flyfun.weather.MainActivity
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.model.BadgeStatus
import aero.flyfun.weather.model.DeviceRegistrationRequest
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.PendingNavigation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Parsed push notification payload for briefing and live weather alerts.
 */
data class PushAlertPayload(
    val flightId: String,
    val packTimestamp: String? = null,
    val openObserved: Boolean = false,
    val title: String? = null,
    val body: String? = null,
    val channelId: String = PushNotificationManager.CHANNEL_BRIEFING_ALERTS,
)

/**
 * Manages Android notification channels (`briefing_alerts`, `live_weather_alerts`),
 * device token registration/unregistration (`POST /api/devices`, `DELETE /api/devices`),
 * unread badge polling/clearing (`GET /api/flights/badge`, `POST /api/flights/{id}/seen`),
 * and notification tap routing to [PendingNavigation].
 *
 * Mirrors iOS `PushNotifications.swift`.
 */
object PushNotificationManager {

    const val CHANNEL_BRIEFING_ALERTS: String = "flyfun_briefing_updates"
    const val CHANNEL_FLIGHT_DAY: String = "flyfun_flight_day"
    const val CHANNEL_LIVE_WEATHER_ALERTS: String = "flyfun_live_alerts"
    const val CHANNEL_LIVE_CLEAR: String = "flyfun_live_clear"

    const val EXTRA_FLIGHT_ID: String = "flight_id"
    const val EXTRA_PACK_TIMESTAMP: String = "pack_timestamp"
    const val EXTRA_OPEN_OBSERVED: String = "open_observed"

    private const val PREFS_NAME: String = "flyfun_push_prefs"
    private const val KEY_DEVICE_TOKEN: String = "registered_device_token"

    private val _badgeStatus = MutableStateFlow(BadgeStatus())
    val badgeStatus: StateFlow<BadgeStatus> = _badgeStatus.asStateFlow()

    private val _registeredToken = MutableStateFlow<String?>(null)
    val registeredToken: StateFlow<String?> = _registeredToken.asStateFlow()

    /**
     * Create the FCM notification channels (`flyfun_briefing_updates`, `flyfun_flight_day`,
     * `flyfun_live_alerts`, `flyfun_live_clear`) on Android O+.
     */
    fun ensureChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return

        val briefingChannel = NotificationChannel(
            CHANNEL_BRIEFING_ALERTS,
            "Briefing Updates",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Scheduled briefing pack updates and assessment changes"
            setShowBadge(true)
        }

        val flightDayChannel = NotificationChannel(
            CHANNEL_FLIGHT_DAY,
            "Flight-Day Preflight Brief",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "T-2h preflight briefing and observed weather summary"
            setShowBadge(true)
        }

        val liveChannel = NotificationChannel(
            CHANNEL_LIVE_WEATHER_ALERTS,
            "Live Weather Alerts",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Time-sensitive live METAR/TAF/SIGMET/convective alerts along your route"
            setShowBadge(false)
        }

        val liveClearChannel = NotificationChannel(
            CHANNEL_LIVE_CLEAR,
            "Live Weather Cleared",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Notifications when an active flight-day weather alert clears"
            setShowBadge(false)
        }

        nm.createNotificationChannel(briefingChannel)
        nm.createNotificationChannel(flightDayChannel)
        nm.createNotificationChannel(liveChannel)
        nm.createNotificationChannel(liveClearChannel)
    }

    /**
     * Returns `true` if `POST_NOTIFICATIONS` is granted (or not required on Android < 13).
     */
    fun hasNotificationPermission(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    /**
     * Register a push token with `POST /api/devices` and persist it locally for sign-out cleanup.
     */
    suspend fun registerDeviceToken(
        context: Context,
        apiClient: ApiClient,
        token: String,
        environment: String = "production",
    ): Boolean {
        if (token.isBlank()) return false
        return runCatching {
            apiClient.registerDeviceToken(
                DeviceRegistrationRequest(
                    token = token,
                    environment = environment,
                    platform = "android",
                ),
            )
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_DEVICE_TOKEN, token)
                .apply()
            _registeredToken.value = token
            true
        }.getOrDefault(false)
    }

    /**
     * Unregister the currently stored device token via `DELETE /api/devices` (e.g., on sign-out).
     */
    suspend fun unregisterDeviceToken(
        context: Context,
        apiClient: ApiClient,
        explicitToken: String? = null,
    ): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val token = explicitToken ?: _registeredToken.value ?: prefs.getString(KEY_DEVICE_TOKEN, null)
        if (token.isNullOrBlank()) return false

        return runCatching {
            apiClient.unregisterDeviceToken(token)
            prefs.edit().remove(KEY_DEVICE_TOKEN).apply()
            _registeredToken.value = null
            true
        }.getOrDefault(false)
    }

    /**
     * Poll `GET /api/flights/badge` and update [badgeStatus].
     */
    suspend fun refreshBadgeStatus(apiClient: ApiClient): BadgeStatus {
        val status = runCatching { apiClient.getBadgeStatus() }
            .getOrDefault(_badgeStatus.value)
        _badgeStatus.value = status
        return status
    }

    /**
     * Mark a specific flight's briefing notification as seen via `POST /api/flights/{id}/seen`
     * and update [badgeStatus].
     */
    suspend fun markFlightSeen(
        apiClient: ApiClient,
        flightId: String,
    ) {
        if (flightId.isBlank()) return
        runCatching {
            apiClient.postEmpty("/api/flights/$flightId/seen")
        }.onFailure {
            runCatching { apiClient.clearBadge() }
        }
        val remainingIds = _badgeStatus.value.unreadFlightIds.filterNot { it == flightId }
        _badgeStatus.value = BadgeStatus(
            hasUnread = remainingIds.isNotEmpty(),
            unreadFlightIds = remainingIds,
        )
    }

    /**
     * Clear all unread badges via `POST /api/flights/badge/clear`.
     */
    suspend fun clearAllBadges(apiClient: ApiClient) {
        runCatching { apiClient.clearBadge() }
        _badgeStatus.value = BadgeStatus(hasUnread = false, unreadFlightIds = emptyList())
    }

    /**
     * Parse a notification or push data map into a [PushAlertPayload].
     */
    fun parsePayload(data: Map<String, String>): PushAlertPayload? {
        val flightId = data[EXTRA_FLIGHT_ID]?.takeIf { it.isNotBlank() }
            ?: data["flightId"]?.takeIf { it.isNotBlank() }
            ?: return null
        val packTimestamp = data[EXTRA_PACK_TIMESTAMP]?.takeIf { it.isNotBlank() }
            ?: data["packTimestamp"]?.takeIf { it.isNotBlank() }
        val openObservedRaw = data[EXTRA_OPEN_OBSERVED] ?: data["openObserved"] ?: data["type"]
        val openObserved = openObservedRaw.equals("true", ignoreCase = true) ||
            openObservedRaw.equals("1", ignoreCase = true) ||
            openObservedRaw.equals("live_weather", ignoreCase = true) ||
            openObservedRaw.equals("observed", ignoreCase = true)
        val channel = if (openObserved) CHANNEL_LIVE_WEATHER_ALERTS else CHANNEL_BRIEFING_ALERTS

        return PushAlertPayload(
            flightId = flightId,
            packTimestamp = packTimestamp,
            openObserved = openObserved,
            title = data["title"],
            body = data["body"] ?: data["message"],
            channelId = channel,
        )
    }

    /**
     * Route a notification tap (`Intent`) into [PendingNavigation] if it carries `flight_id`.
     * Returns the posted [NavigationTarget.Briefing] if matched, or `null`.
     */
    fun handleNotificationIntent(intent: Intent?): NavigationTarget.Briefing? {
        if (intent == null) return null
        val extras = intent.extras ?: return null
        val flightId = extras.getString(EXTRA_FLIGHT_ID)?.takeIf { it.isNotBlank() }
            ?: extras.getString("extra_flight_id")?.takeIf { it.isNotBlank() }
            ?: return null
        val packTimestamp = extras.getString(EXTRA_PACK_TIMESTAMP)?.takeIf { it.isNotBlank() }
        val openObserved = when {
            extras.containsKey(EXTRA_OPEN_OBSERVED) -> {
                extras.getBoolean(EXTRA_OPEN_OBSERVED, false) ||
                    extras.getString(EXTRA_OPEN_OBSERVED)?.equals("true", ignoreCase = true) == true
            }
            else -> false
        }

        val target = NavigationTarget.Briefing(
            flightId = flightId,
            packTimestamp = packTimestamp,
            openObserved = openObserved,
        )
        PendingNavigation.post(target)
        return target
    }

    /**
     * Route a parsed [PushAlertPayload] directly into [PendingNavigation].
     */
    fun routeAlertTap(payload: PushAlertPayload): NavigationTarget.Briefing {
        val target = NavigationTarget.Briefing(
            flightId = payload.flightId,
            packTimestamp = payload.packTimestamp,
            openObserved = payload.openObserved,
        )
        PendingNavigation.post(target)
        return target
    }

    /**
     * Post a local system notification for a [PushAlertPayload] that routes to the flight briefing when tapped.
     */
    fun showLocalNotification(
        context: Context,
        payload: PushAlertPayload,
    ) {
        ensureChannels(context)
        if (!hasNotificationPermission(context)) return

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        val tapIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_FLIGHT_ID, payload.flightId)
            if (payload.packTimestamp != null) {
                putExtra(EXTRA_PACK_TIMESTAMP, payload.packTimestamp)
            }
            putExtra(EXTRA_OPEN_OBSERVED, payload.openObserved)
        }
        val pendingTap = PendingIntent.getActivity(
            context,
            payload.flightId.hashCode(),
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(context, payload.channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(payload.title ?: "FlyFun Weather Update")
            .setContentText(payload.body ?: "Tap to open flight briefing")
            .setAutoCancel(true)
            .setContentIntent(pendingTap)
            .setPriority(
                if (payload.channelId == CHANNEL_LIVE_WEATHER_ALERTS) {
                    NotificationCompat.PRIORITY_HIGH
                } else {
                    NotificationCompat.PRIORITY_DEFAULT
                },
            )
            .build()

        nm.notify(payload.flightId.hashCode(), notification)
    }
}
