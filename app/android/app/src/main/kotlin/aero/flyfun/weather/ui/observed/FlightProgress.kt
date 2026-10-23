package aero.flyfun.weather.ui.observed

/**
 * Live aircraft progress along the route used by [RouteRibbonCanvas] and the
 * in-flight GPS tracking bar in [ObservedTabScreen].
 *
 * (The "Observed Route Nutshell" card that used to live here built its phase rows
 * on the phone from the ribbon. #883 replaced it with the server's glance lines in
 * the shared order: see [ObservedLayoutRules][aero.flyfun.weather.model.rules.ObservedLayoutRules].)
 */
data class FlightProgress(
    val flownFraction: Double = 0.0,
    val flownNm: Double = 0.0,
    val totalNm: Double = 0.0,
    val groundSpeedKt: Double? = null,
    val altitudeFt: Double? = null,
    val phase: String? = null,
    val etaUtc: String? = null,
    val isTrackingActive: Boolean = false,
)
