package aero.flyfun.weather.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/**
 * Whether the latest pack's offline bundle may be auto-downloaded right now: any connection,
 * Wi-Fi or cellular, except when Data Saver restricts this app. Mirrors iOS, which auto-downloads
 * on any connection outside Low Data Mode. A bundle is ~0.1–0.7 MB gzipped; without it every
 * Skew-T point is a separate request (2026-11-26: ~150 in a minute from one cross-section scrub).
 */
object AutoDownloadGate {
    fun allowed(context: Context): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return false
        return cm.restrictBackgroundStatus != ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED
    }
}
