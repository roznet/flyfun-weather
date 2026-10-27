package aero.flyfun.weather.service

import aero.flyfun.weather.FlyFunWeatherApp
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * FCM entry point. A message carrying a `notification` block is shown by the system while
 * the app is in the background (its tap reaches [aero.flyfun.weather.MainActivity] with the
 * data keys as extras); FCM hands it here only while the app is in the foreground, and here
 * we post it ourselves so it isn't dropped.
 */
class FlyFunMessagingService : FirebaseMessagingService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        val appState = (application as? FlyFunWeatherApp)?.appState ?: return
        // Signed out (or a cold start that hasn't restored the session yet): skipped, and the
        // next signed-in start registers the current token (AppStateViewModel.refreshUserSupportData).
        if (!appState.isAuthenticated.value) return
        scope.launch {
            PushNotificationManager.registerDeviceToken(applicationContext, appState.apiClient, token)
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val notification = message.notification
        if (notification == null) {
            // Data-only: the server's silent badge push (`send_silent_badge_push`) after a
            // flight is marked seen elsewhere. Re-read the unseen count rather than trust it.
            val appState = (application as? FlyFunWeatherApp)?.appState
            if (message.data.containsKey("badge") && appState?.isAuthenticated?.value == true) {
                scope.launch { PushNotificationManager.refreshBadgeStatus(appState.apiClient) }
            } else {
                Log.d("PushNotifications", "Ignored data-only push: ${message.data.keys}")
            }
            return
        }
        val data = buildMap {
            putAll(message.data)
            notification.title?.let { put("title", it) }
            notification.body?.let { put("body", it) }
        }
        val payload = PushNotificationManager.parsePayload(data) ?: return
        PushNotificationManager.showLocalNotification(applicationContext, payload)
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
