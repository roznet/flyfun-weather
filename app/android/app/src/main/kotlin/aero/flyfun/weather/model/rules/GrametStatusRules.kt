package aero.flyfun.weather.model.rules

/**
 * Wording for a briefing pack without a GRAMET (#994).
 *
 * The server says why in `PackMetaResponse.grametStatus` (`api/packs.py`
 * `_gramet_status`); this only words it. Null keeps the GRAMET section hidden,
 * as before #994: for unknown codes, and on purpose for `not_linked`,
 * `disabled` and `historical` (the pilot's own setup): most accounts never
 * link Autorouter, and a note on every briefing would be noise
 * (designs/briefing-charts-ios.md). Settings says whether it's linked.
 *
 * SYNC: iOS `GrametSectionView.swift` (`GrametStatus.message`).
 */
object GrametStatusRules {

    fun message(status: String?): String? = when (status) {
        "refresh_to_fetch" ->
            "The GRAMET is available now. Refresh the briefing to fetch it."
        "daily_limit" ->
            "Daily GRAMET limit reached. A refresh tomorrow fetches it."
        "failed" ->
            "Autorouter didn't return a GRAMET for this briefing. The next full refresh tries again."
        "expired" ->
            "The GRAMET of this older briefing is no longer kept."
        else -> null
    }
}
