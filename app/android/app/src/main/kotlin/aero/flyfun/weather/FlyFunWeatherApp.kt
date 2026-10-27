package aero.flyfun.weather

import android.app.Application
import aero.flyfun.weather.service.PushNotificationManager
import aero.flyfun.weather.viewmodel.AppStateViewModel

/**
 * Application class for FlyFun Weather Android.
 * Holds a process-level [AppStateViewModel] so deep links, OAuth callbacks,
 * and adaptive screen rotations share the same authentication & repository state.
 */
class FlyFunWeatherApp : Application() {

    lateinit var appState: AppStateViewModel
        private set

    override fun onCreate() {
        super.onCreate()
        appState = AppStateViewModel(context = applicationContext)
        // Before any push arrives: a system-shown FCM notification needs its channel to exist.
        PushNotificationManager.ensureChannels(this)
    }
}
