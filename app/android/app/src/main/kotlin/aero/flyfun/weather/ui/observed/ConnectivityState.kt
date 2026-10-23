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
        // No onAvailable: it fires before the capabilities are known, and
        // onCapabilitiesChanged follows it with the real answer.
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onLost(network: Network) {
                online.value = cm?.isOnlineNow() ?: false
            }

            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                online.value = caps.isValidatedInternet()
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
    return caps.isValidatedInternet()
}

/** A captive portal not yet signed in has INTERNET but not VALIDATED: that is offline here. */
private fun NetworkCapabilities.isValidatedInternet(): Boolean =
    hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
        hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
