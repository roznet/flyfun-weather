package aero.flyfun.weather.service

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import aero.flyfun.weather.MainActivity
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.model.BadgeStatus
import aero.flyfun.weather.model.DeviceRegistrationRequest
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.PendingNavigation
import com.google.android.gms.tasks.Task
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

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

    private const val TYPE_LIVE_ALERT: String = "live_alert"
    private const val TYPE_LIVE_CLEAR: String = "live_clear"
    private const val TYPE_FLIGHT_DAY: String = "flight_day"

    private const val PREFS_NAME: String = "flyfun_push_prefs"
    private const val KEY_DEVICE_TOKEN: String = "registered_device_token"
    private const val KEY_PERMISSION_ASKED: String = "notification_permission_asked"

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
     * `true` once per install, on Android 13+ without the permission: the caller asks then.
     * After that the pilot grants it in system settings (the Observed tab's Start also asks).
     */
    fun shouldRequestNotificationPermission(context: Context): Boolean {
        if (hasNotificationPermission(context)) return false
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_PERMISSION_ASKED, false)) return false
        prefs.edit().putBoolean(KEY_PERMISSION_ASKED, true).apply()
        return true
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
     * Mark a specific flight's briefing as seen via `POST /api/flights/{id}/seen`; the server
     * answers with the new badge count.
     */
    suspend fun markFlightSeen(
        apiClient: ApiClient,
        flightId: String,
    ) {
        if (flightId.isBlank()) return
        runCatching { apiClient.markFlightSeen(flightId) }
            .onSuccess { _badgeStatus.value = it }
            .onFailure { Log.w("PushNotifications", "Mark seen failed for $flightId: $it") }
    }

    /**
     * Parse a notification or push data map into a [PushAlertPayload].
     *
     * The server's FCM data (`notify/push.py`) carries `flight_id`, `type` (`live_alert`,
     * `live_clear`, `flight_day`, absent for a briefing refresh) and `interruption_level`;
     * the title and body ride in the FCM `notification` block, which the caller merges in.
     * The channel mirrors the server's `_fcm_channel_id_for_payload`.
     */
    fun parsePayload(data: Map<String, String>): PushAlertPayload? {
        val flightId = data[EXTRA_FLIGHT_ID]?.takeIf { it.isNotBlank() }
            ?: data["flightId"]?.takeIf { it.isNotBlank() }
            ?: return null
        val packTimestamp = data[EXTRA_PACK_TIMESTAMP]?.takeIf { it.isNotBlank() }
            ?: data["packTimestamp"]?.takeIf { it.isNotBlank() }
        val type = data["type"]?.lowercase()
        val isLive = type == TYPE_LIVE_ALERT || type == TYPE_LIVE_CLEAR
        val openObservedRaw = data[EXTRA_OPEN_OBSERVED] ?: data["openObserved"]
        val openObserved = isLive ||
            openObservedRaw.equals("true", ignoreCase = true) ||
            openObservedRaw.equals("1", ignoreCase = true)
        val channel = when {
            type == TYPE_LIVE_CLEAR -> CHANNEL_LIVE_CLEAR
            type == TYPE_LIVE_ALERT && data["interruption_level"] == "active" -> CHANNEL_LIVE_CLEAR
            type == TYPE_LIVE_ALERT -> CHANNEL_LIVE_WEATHER_ALERTS
            type == TYPE_FLIGHT_DAY -> CHANNEL_FLIGHT_DAY
            openObserved -> CHANNEL_LIVE_WEATHER_ALERTS
            else -> CHANNEL_BRIEFING_ALERTS
        }

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
        val extras = intent?.extras ?: return null
        // Two shapes: the system-shown FCM notification puts the data keys in as strings;
        // [showLocalNotification] puts `open_observed` in as a Boolean.
        @Suppress("DEPRECATION")
        val data = extras.keySet().mapNotNull { key ->
            extras.get(key)?.let { key to it.toString() }
        }.toMap()
        val payload = parsePayload(data) ?: return null
        return routeAlertTap(payload)
    }

    /**
     * Fetch this device's FCM token and register it with the server. Called on every
     * signed-in app start (cheap: the server upserts) so a token that rotated while signed
     * out, or a new account on this device, is picked up. No-op when Firebase isn't
     * configured (a build without `google-services.json`).
     */
    suspend fun registerCurrentToken(context: Context, apiClient: ApiClient): Boolean {
        if (!isFirebaseConfigured(context)) return false
        val token = runCatching { FirebaseMessaging.getInstance().token.await() }
            .onFailure { Log.w("PushNotifications", "FCM token fetch failed: $it") }
            .getOrNull() ?: return false
        // For a Firebase console test send: `adb logcat -s PushNotifications`.
        if (aero.flyfun.weather.BuildConfig.DEBUG) Log.d("PushNotifications", "FCM token: $token")
        return registerDeviceToken(context, apiClient, token)
    }

    /**
     * On sign-out: drop the FCM token, so the server's next send to it comes back
     * `UNREGISTERED` and deletes the row (the request can't be authenticated any more by
     * then), and the next sign-in registers a fresh one.
     */
    suspend fun forgetDeviceToken(context: Context) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_DEVICE_TOKEN)
            .apply()
        _registeredToken.value = null
        if (!isFirebaseConfigured(context)) return
        runCatching { FirebaseMessaging.getInstance().deleteToken().await() }
            .onFailure { Log.w("PushNotifications", "FCM token delete failed: $it") }
    }

    private fun isFirebaseConfigured(context: Context): Boolean =
        FirebaseApp.getApps(context).isNotEmpty()

    private suspend fun <T> Task<T>.await(): T = suspendCancellableCoroutine { cont ->
        addOnCompleteListener { task ->
            if (task.isSuccessful) {
                cont.resume(task.result)
            } else {
                cont.resumeWithException(task.exception ?: IllegalStateException("FCM task failed"))
            }
        }
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
