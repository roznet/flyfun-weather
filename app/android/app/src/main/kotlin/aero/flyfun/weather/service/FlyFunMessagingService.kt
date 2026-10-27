package aero.flyfun.weather.service

import aero.flyfun.weather.FlyFunWeatherApp
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
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
        // Signed out: the next sign-in registers the token (AppStateViewModel.refreshUserSupportData).
        if (!appState.isAuthenticated.value) return
        scope.launch {
            PushNotificationManager.registerDeviceToken(applicationContext, appState.apiClient, token)
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val notification = message.notification ?: return // silent badge pushes
        val data = buildMap {
            putAll(message.data)
            notification.title?.let { put("title", it) }
            notification.body?.let { put("body", it) }
        }
        val payload = PushNotificationManager.parsePayload(data) ?: return
        PushNotificationManager.showLocalNotification(applicationContext, payload)
    }
}
