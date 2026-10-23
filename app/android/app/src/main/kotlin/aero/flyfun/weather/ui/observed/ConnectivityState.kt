package aero.flyfun.weather.ui.observed

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext

/**
 * Whether the device has a validated internet connection, updated live
 * (#883 offline banner). Starts from the current state; true when it cannot
 * tell, so a missing service never shows a false "Offline".
 */
@Composable
fun rememberIsOnline(): State<Boolean> {
    val context = LocalContext.current
    val cm = remember(context) { context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager }
    val online = remember(cm) { mutableStateOf(cm?.isOnlineNow() ?: true) }
    DisposableEffect(cm) {
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                online.value = true
            }

            override fun onLost(network: Network) {
                online.value = cm?.isOnlineNow() ?: false
            }

            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                online.value = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            }
        }
        val registered = cm != null && runCatching { cm.registerDefaultNetworkCallback(callback) }.isSuccess
        onDispose {
            if (registered) runCatching { cm?.unregisterNetworkCallback(callback) }
        }
    }
    return online
}

private fun ConnectivityManager.isOnlineNow(): Boolean {
    val caps = getNetworkCapabilities(activeNetwork) ?: return false
    return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
}
