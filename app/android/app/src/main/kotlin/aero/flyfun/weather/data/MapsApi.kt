package aero.flyfun.weather.data

import aero.flyfun.weather.model.ForecastDaysResponse
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.FrequentAirportsResponse
import aero.flyfun.weather.model.HistoricalMapResponse
import aero.flyfun.weather.model.HistoricalRangeResponse

/**
 * The calls the forecast and historical maps make. [ApiClient] implements it; JVM tests feed the
 * map view models canned payloads through a fake.
 */
interface MapsApi {
    suspend fun getForecastDays(): ForecastDaysResponse

    suspend fun getForecastMap(day: Int, hourUtc: Int): ForecastMapResponse

    suspend fun getFrequentAirports(): FrequentAirportsResponse

    suspend fun getHistoricalRange(): HistoricalRangeResponse

    suspend fun getHistoricalMap(at: String, lead: Int): HistoricalMapResponse
}
