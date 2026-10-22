package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.model.AgreementEntry
import aero.flyfun.weather.model.DayHourBucket
import aero.flyfun.weather.model.DaySummary
import aero.flyfun.weather.model.ForecastDayEntry
import aero.flyfun.weather.model.ForecastDaysResponse
import aero.flyfun.weather.model.ForecastLinkedDate
import aero.flyfun.weather.model.ForecastMapAirport
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.ForecastModelEntry
import aero.flyfun.weather.model.HistoricalMapAirport
import aero.flyfun.weather.model.HistoricalMapResponse
import aero.flyfun.weather.model.HistoricalMetar
import aero.flyfun.weather.model.HistoricalTaf
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.ui.map.AirportMapMetric
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for the Pan-European **Forecast Weather Overview Map** (`/api/maps/forecast`).
 * Supports live API fetching with automatic fallback to a rich 15-airport European fixture
 * (`EGLL`, `EGTK`, `LFPB`, `LFPG`, `LFMD`, `LFML`, `LSZH`, `LSGG`, `EDDF`, `EDDM`, `EHAM`,
 * `LEMD`, `LEBL`, `LIMC`, `LOWW`) across D0..D3 and hours 06..18Z.
 */
class ForecastMapViewModel(
    private val apiClient: ApiClient? = null,
    private val isMockModeProvider: () -> Boolean = { false },
    private val scopeOverride: CoroutineScope? = null,
    private val clock: () -> Instant = { Instant.now() },
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _days = MutableStateFlow<List<ForecastDayEntry>>(PanEuropeanMapFixtures.defaultForecastDays().days)
    val days: StateFlow<List<ForecastDayEntry>> = _days.asStateFlow()

    private val _selectedDay = MutableStateFlow(0)
    val selectedDay: StateFlow<Int> = _selectedDay.asStateFlow()

    private val _selectedHour = MutableStateFlow(12)
    val selectedHour: StateFlow<Int> = _selectedHour.asStateFlow()

    private val _availableHours = MutableStateFlow(listOf(6, 9, 12, 15, 18))
    val availableHours: StateFlow<List<Int>> = _availableHours.asStateFlow()

    private val _selectedModel = MutableStateFlow("ecmwf")
    val selectedModel: StateFlow<String> = _selectedModel.asStateFlow()

    private val _availableModels = MutableStateFlow(listOf("ecmwf", "gfs", "icon_eu"))
    val availableModels: StateFlow<List<String>> = _availableModels.asStateFlow()

    private val _selectedMetric = MutableStateFlow(AirportMapMetric.FLIGHT_CATEGORY)
    val selectedMetric: StateFlow<AirportMapMetric> = _selectedMetric.asStateFlow()

    private val _worstOfModels = MutableStateFlow(false)
    val worstOfModels: StateFlow<Boolean> = _worstOfModels.asStateFlow()

    private val _mapData = MutableStateFlow<ForecastMapResponse?>(null)
    val mapData: StateFlow<ForecastMapResponse?> = _mapData.asStateFlow()

    private val _selectedAirportIcao = MutableStateFlow<String?>(null)
    val selectedAirportIcao: StateFlow<String?> = _selectedAirportIcao.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    /**
     * Set when a linked `fc.date` couldn't be shown as is (#860): says which day is shown
     * instead. Cleared when the pilot picks a day.
     */
    private val _linkedDateNotice = MutableStateFlow<String?>(null)
    val linkedDateNotice: StateFlow<String?> = _linkedDateNotice.asStateFlow()

    /** True once the days request has finished (or was skipped), so [days] is final. */
    private var daysResolved = false

    /** The days request failed and [days] is the offline D+0…D+3 guess. */
    private var gridIsFallback = false

    /** A linked `fc.date` that arrived before the grid; resolved by [loadInitial]. */
    private var pendingLink: NavigationTarget.ForecastMap? = null

    init {
        loadInitial()
    }

    fun loadInitial() {
        scope.launch {
            _isLoading.value = true
            _error.value = null
            if (!isMockModeProvider() && apiClient != null) {
                runCatching { apiClient.getForecastDays() }
                    .onSuccess { resp ->
                        // Days beyond the model horizon have no hours: not offered
                        val offered = resp.days.filter { it.available }
                        if (offered.isNotEmpty()) {
                            _days.value = offered
                            gridIsFallback = false
                        } else {
                            gridIsFallback = true
                        }
                    }
                    .onFailure { gridIsFallback = true }
            }
            daysResolved = true
            pendingLink?.let { link ->
                pendingLink = null
                applyLinkedDay(link)
            }
            syncHoursToSelectedDay()
            fetchForecastSlice(day = _selectedDay.value, hourUtc = _selectedHour.value)
        }
    }

    fun selectDay(dayOffset: Int) {
        // The pilot's pick wins over a link still waiting for the grid
        pendingLink = null
        _linkedDateNotice.value = null
        if (_selectedDay.value == dayOffset && _mapData.value != null) return
        _selectedDay.value = dayOffset
        syncHoursToSelectedDay()
        fetchForecastSlice(day = dayOffset, hourUtc = _selectedHour.value)
    }

    fun selectHour(hourUtc: Int) {
        if (_selectedHour.value == hourUtc && _mapData.value != null) return
        _selectedHour.value = hourUtc
        fetchForecastSlice(day = _selectedDay.value, hourUtc = hourUtc)
    }

    fun selectModel(model: String) {
        _selectedModel.value = model
        _worstOfModels.value = false
    }

    fun setWorstOfModels(enabled: Boolean) {
        _worstOfModels.value = enabled
    }

    fun selectMetric(metric: AirportMapMetric) {
        _selectedMetric.value = metric
    }

    fun selectAirport(icao: String?) {
        _selectedAirportIcao.value = icao
    }

    fun applyDeepLinkTarget(target: NavigationTarget.ForecastMap) {
        target.metric?.let { _selectedMetric.value = AirportMapMetric.fromId(it) }
        target.model?.let {
            if (it == "worst") {
                _worstOfModels.value = true
            } else {
                _selectedModel.value = it
                _worstOfModels.value = false
            }
        }
        target.hourUtc?.let { _selectedHour.value = it }
        if (target.date != null && !daysResolved) {
            // `fc.date` resolves against the real grid: loadInitial applies it and fetches once
            // the days request lands, so don't fetch a provisional day now.
            pendingLink = target
            return
        }
        applyLinkedDay(target)
        syncHoursToSelectedDay()
        fetchForecastSlice(day = _selectedDay.value, hourUtc = _selectedHour.value)
    }

    /** `fc.date` wins over `fc.day` (#860); a malformed date or empty grid falls back to `fc.day`. */
    private fun applyLinkedDay(target: NavigationTarget.ForecastMap) {
        _linkedDateNotice.value = null
        // One clock reading for both the resolution and the notice's label, so a UTC-midnight
        // rollover in between can't mislabel the shown day.
        val today = LocalDate.ofInstant(clock(), ZoneOffset.UTC)
        val linked = target.date
        val resolved = linked?.let { date ->
            ForecastLinkedDate.resolve(date, offered = _days.value.filter { it.available }.map { it.day }, today = today)
        }
        if (linked == null || resolved == null) {
            target.dayOffset?.let { _selectedDay.value = it }
            return
        }
        _selectedDay.value = resolved.day
        _linkedDateNotice.value = ForecastLinkedDate.noticeFor(linked, resolved, gridIsFallback, today)
    }

    /**
     * Hour chips follow the selected day's sample hours (the last day often has only a few),
     * snapping the hour to the nearest one so the map never asks for an hour with no data.
     * Same rule as iOS `hours(forDay:)` + `snapHour`.
     */
    private fun syncHoursToSelectedDay() {
        val hours = _days.value.firstOrNull { it.day == _selectedDay.value }?.hours.orEmpty()
        if (hours.isEmpty()) return
        _availableHours.value = hours
        val hour = _selectedHour.value
        if (hour !in hours) _selectedHour.value = hours.minBy { abs(it - hour) }
    }

    private fun fetchForecastSlice(day: Int, hourUtc: Int) {
        scope.launch {
            _isLoading.value = true
            _error.value = null
            val client = apiClient
            if (!isMockModeProvider() && client != null) {
                runCatching {
                    client.getForecastMap(day = day, hourUtc = hourUtc)
                }.onSuccess { resp ->
                    _mapData.value = resp
                    val models = resp.modelInitTimes.keys.toList()
                        .ifEmpty { resp.airports.firstOrNull()?.models?.keys?.toList().orEmpty() }
                    if (models.isNotEmpty()) {
                        _availableModels.value = models
                        if (_selectedModel.value !in models) {
                            _selectedModel.value = models.first()
                        }
                    }
                    _isLoading.value = false
                    return@launch
                }.onFailure {
                    // Fall back to rich European fixture so map remains interactive offline/demo
                }
            }
            val fixture = PanEuropeanMapFixtures.buildForecastMapResponse(day = day, hourUtc = hourUtc)
            _mapData.value = fixture
            _availableModels.value = fixture.modelInitTimes.keys.toList()
            _isLoading.value = false
        }
    }
}

/**
 * ViewModel for the Pan-European **Historical Weather & Verification Map** (`/api/maps/historical`).
 */
class HistoricalMapViewModel(
    private val apiClient: ApiClient? = null,
    private val isMockModeProvider: () -> Boolean = { false },
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _minDate = MutableStateFlow(LocalDate.now().minusDays(30).toString())
    val minDate: StateFlow<String> = _minDate.asStateFlow()

    private val _maxDate = MutableStateFlow(LocalDate.now().minusDays(1).toString())
    val maxDate: StateFlow<String> = _maxDate.asStateFlow()

    private val _selectedDate = MutableStateFlow(LocalDate.now().minusDays(1).toString())
    val selectedDate: StateFlow<String> = _selectedDate.asStateFlow()

    private val _selectedHour = MutableStateFlow(12)
    val selectedHour: StateFlow<Int> = _selectedHour.asStateFlow()

    private val _availableHours = MutableStateFlow(listOf(6, 9, 12, 15, 18))
    val availableHours: StateFlow<List<Int>> = _availableHours.asStateFlow()

    private val _selectedMetric = MutableStateFlow(AirportMapMetric.FLIGHT_CATEGORY)
    val selectedMetric: StateFlow<AirportMapMetric> = _selectedMetric.asStateFlow()

    /** `"metar"`, `"taf"`, or `"worst"` */
    private val _viewMode = MutableStateFlow("metar")
    val viewMode: StateFlow<String> = _viewMode.asStateFlow()

    private val _mapData = MutableStateFlow<HistoricalMapResponse?>(null)
    val mapData: StateFlow<HistoricalMapResponse?> = _mapData.asStateFlow()

    private val _selectedAirportIcao = MutableStateFlow<String?>(null)
    val selectedAirportIcao: StateFlow<String?> = _selectedAirportIcao.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    init {
        loadInitial()
    }

    fun loadInitial() {
        scope.launch {
            _isLoading.value = true
            _error.value = null
            if (!isMockModeProvider() && apiClient != null) {
                runCatching { apiClient.getHistoricalRange() }.onSuccess { range ->
                    range.earliestDate?.let { _minDate.value = it }
                    range.latestDate?.let {
                        _maxDate.value = it
                        _selectedDate.value = it
                    }
                }
            }
            fetchHistoricalSlice(date = _selectedDate.value, hourUtc = _selectedHour.value)
        }
    }

    fun selectDate(dateIso: String) {
        if (_selectedDate.value == dateIso && _mapData.value != null) return
        _selectedDate.value = dateIso
        fetchHistoricalSlice(date = dateIso, hourUtc = _selectedHour.value)
    }

    fun stepDay(deltaDays: Long) {
        val current = runCatching { LocalDate.parse(_selectedDate.value) }
            .getOrDefault(LocalDate.now().minusDays(1))
        val minD = runCatching { LocalDate.parse(_minDate.value) }.getOrDefault(current.minusDays(30))
        val maxD = runCatching { LocalDate.parse(_maxDate.value) }.getOrDefault(LocalDate.now())
        val next = current.plusDays(deltaDays).coerceIn(minD, maxD)
        selectDate(next.toString())
    }

    fun selectHour(hourUtc: Int) {
        if (_selectedHour.value == hourUtc && _mapData.value != null) return
        _selectedHour.value = hourUtc
        fetchHistoricalSlice(date = _selectedDate.value, hourUtc = hourUtc)
    }

    fun selectMetric(metric: AirportMapMetric) {
        _selectedMetric.value = metric
    }

    fun selectViewMode(mode: String) {
        _viewMode.value = mode
    }

    fun selectAirport(icao: String?) {
        _selectedAirportIcao.value = icao
    }

    fun applyDeepLinkTarget(target: NavigationTarget.HistoricalMap) {
        target.metric?.let { _selectedMetric.value = AirportMapMetric.fromId(it) }
        target.viewMode?.let { _viewMode.value = it }
        val date = target.date ?: _selectedDate.value
        val hour = target.hourUtc ?: _selectedHour.value
        _selectedDate.value = date
        _selectedHour.value = hour
        fetchHistoricalSlice(date = date, hourUtc = hour)
    }

    private fun fetchHistoricalSlice(date: String, hourUtc: Int) {
        scope.launch {
            _isLoading.value = true
            _error.value = null
            val client = apiClient
            if (!isMockModeProvider() && client != null) {
                runCatching {
                    client.getHistoricalMap(date = date, hourUtc = hourUtc)
                }.onSuccess { resp ->
                    _mapData.value = resp
                    resp.availableHours?.map { it.hour }?.takeIf { it.isNotEmpty() }?.let {
                        _availableHours.value = it
                    }
                    _isLoading.value = false
                    return@launch
                }.onFailure {
                    // Fall back to rich European historical fixture
                }
            }
            _mapData.value = PanEuropeanMapFixtures.buildHistoricalMapResponse(date = date, hourUtc = hourUtc)
            _isLoading.value = false
        }
    }
}

// ============================================================================
// Rich Pan-European 15-Airport Fixtures (Forecast & Historical)
// ============================================================================

object PanEuropeanMapFixtures {

    private data class AirportSeed(
        val icao: String,
        val name: String,
        val lat: Double,
        val lon: Double,
        val rwyId: String,
        val baseCategory: String,
        val baseWindDir: Double,
        val baseWindKt: Double,
        val baseCrosswindKt: Double,
        val baseCeilingFt: Double?,
        val baseVisM: Double,
        val baseConvective: String,
        val baseCloudPct: Double,
        val baseCape: Double,
        val agreement: String,
    )

    private val EUROPEAN_AIRPORT_SEEDS = listOf(
        AirportSeed("EGLL", "London Heathrow", 51.4700, -0.4543, "27L", "VFR", 250.0, 14.0, 5.0, 4200.0, 9999.0, "none", 30.0, 40.0, "consistent"),
        AirportSeed("EGTK", "Oxford Kidlington", 51.8369, -1.3200, "19", "MVFR", 230.0, 16.0, 10.0, 2400.0, 8000.0, "marginal", 65.0, 120.0, "consistent"),
        AirportSeed("LFPB", "Paris Le Bourget", 48.9694, 2.4414, "25", "VFR", 240.0, 12.0, 3.0, 4800.0, 9999.0, "none", 25.0, 80.0, "consistent"),
        AirportSeed("LFPG", "Paris Charles de Gaulle", 49.0097, 2.5479, "26R", "VFR", 240.0, 13.0, 4.5, 4500.0, 9999.0, "none", 28.0, 75.0, "consistent"),
        AirportSeed("LFMD", "Cannes Mandelieu", 43.5420, 6.9535, "17", "VFR", 150.0, 9.0, 3.0, 8500.0, 9999.0, "none", 12.0, 20.0, "consistent"),
        AirportSeed("LFML", "Marseille Provence", 43.4393, 5.2214, "31R", "VFR", 330.0, 24.0, 8.0, 7000.0, 9999.0, "none", 15.0, 30.0, "consistent"),
        AirportSeed("LSZH", "Zurich", 47.4582, 8.5555, "28", "IFR", 290.0, 18.0, 6.0, 800.0, 3800.0, "moderate", 88.0, 680.0, "mixed"),
        AirportSeed("LSGG", "Geneva", 46.2381, 6.1090, "22", "MVFR", 240.0, 15.0, 5.5, 2200.0, 7000.0, "low", 60.0, 340.0, "mixed"),
        AirportSeed("EDDF", "Frankfurt", 50.0379, 8.5622, "25C", "MVFR", 220.0, 19.0, 9.5, 1800.0, 6000.0, "low", 72.0, 290.0, "mixed"),
        AirportSeed("EDDM", "Munich", 48.3537, 11.7750, "26R", "IFR", 280.0, 22.0, 12.0, 700.0, 3200.0, "moderate", 92.0, 950.0, "divergent"),
        AirportSeed("EHAM", "Amsterdam Schiphol", 52.3105, 4.7683, "24", "MVFR", 270.0, 26.0, 13.0, 1900.0, 7500.0, "marginal", 68.0, 180.0, "consistent"),
        AirportSeed("LEMD", "Madrid Barajas", 40.4983, -3.5676, "32L", "VFR", 310.0, 8.0, 2.0, 9500.0, 9999.0, "none", 10.0, 15.0, "consistent"),
        AirportSeed("LEBL", "Barcelona El Prat", 41.2974, 2.0833, "24R", "VFR", 210.0, 11.0, 5.5, 6500.0, 9999.0, "none", 18.0, 90.0, "consistent"),
        AirportSeed("LIMC", "Milan Malpensa", 45.6301, 8.7255, "35R", "LIFR", 140.0, 7.0, 3.5, 400.0, 1200.0, "high", 96.0, 1450.0, "divergent"),
        AirportSeed("LOWW", "Vienna", 48.1103, 16.5697, "29", "VFR", 300.0, 17.0, 4.0, 5200.0, 9999.0, "low", 35.0, 260.0, "consistent"),
    )

    fun defaultForecastDays(): ForecastDaysResponse {
        // Server days are UTC dates
        val today = LocalDate.now(ZoneOffset.UTC)
        val days = (0..3).map { d ->
            val dateStr = today.plusDays(d.toLong()).toString()
            ForecastDayEntry(
                day = d,
                date = dateStr,
                hours = listOf(6, 9, 12, 15, 18),
                models = listOf("ecmwf", "gfs", "icon_eu"),
            )
        }
        return ForecastDaysResponse(days = days)
    }

    fun buildForecastMapResponse(day: Int, hourUtc: Int): ForecastMapResponse {
        val dateStr = LocalDate.now().plusDays(day.toLong()).toString()
        val forecastTime = String.format(Locale.US, "%sT%02d:00:00Z", dateStr, hourUtc)
        val hourShift = (hourUtc - 12) / 3
        val dayShift = day.coerceIn(0, 3)

        val airports = EUROPEAN_AIRPORT_SEEDS.mapIndexed { idx, seed ->
            val windDelta = (hourShift * 1.5) + (dayShift * 2.0) * if (idx % 2 == 0) 1.0 else -0.5
            val ecmwfWind = (seed.baseWindKt + windDelta).coerceAtLeast(3.0)
            val gfsWind = (ecmwfWind + if (idx % 3 == 0) 4.0 else -2.0).coerceAtLeast(3.0)
            val iconWind = (ecmwfWind + if (idx % 2 == 0) -1.5 else 3.0).coerceAtLeast(3.0)

            val ecmwfCat = shiftCategory(seed.baseCategory, dayShift, hourShift, idx)
            val gfsCat = if (seed.agreement == "divergent") shiftCategory(ecmwfCat, 1, 1, idx + 1) else ecmwfCat
            val iconCat = if (seed.agreement != "consistent" && idx % 2 == 0) shiftCategory(ecmwfCat, 1, 0, idx) else ecmwfCat

            val ecmwfEntry = ForecastModelEntry(
                category = ecmwfCat,
                windSpeedKt = ecmwfWind,
                windDir = (seed.baseWindDir + dayShift * 10.0) % 360.0,
                windGustKt = ecmwfWind + 8.0,
                bestRunway = seed.rwyId,
                crosswindKt = (seed.baseCrosswindKt + abs(windDelta) * 0.5).coerceAtLeast(1.0),
                ceilingFt = seed.baseCeilingFt?.let { (it - dayShift * 150.0).coerceAtLeast(200.0) },
                visibilityM = seed.baseVisM,
                cloudCoverPct = (seed.baseCloudPct + dayShift * 5.0).coerceIn(0.0, 100.0),
                capeJkg = (seed.baseCape + max(0, hourShift) * 120.0).coerceAtLeast(0.0),
                convectiveRisk = seed.baseConvective,
                temperatureC = 14.0 + hourShift * 2.0 - (seed.lat - 45.0) * 0.6,
                precipMm = if (ecmwfCat == "IFR" || ecmwfCat == "LIFR") 2.8 else 0.0,
                freezingLevelFt = 7800.0,
                pressureHpa = 1016.0,
            )

            val gfsEntry = ecmwfEntry.copy(
                category = gfsCat,
                windSpeedKt = gfsWind,
                windGustKt = gfsWind + 9.0,
                crosswindKt = (ecmwfEntry.crosswindKt ?: 5.0) + 2.5,
                ceilingFt = ecmwfEntry.ceilingFt?.let { (it - 200.0).coerceAtLeast(200.0) },
                capeJkg = (ecmwfEntry.capeJkg ?: 0.0) * 1.15,
            )

            val iconEntry = ecmwfEntry.copy(
                category = iconCat,
                windSpeedKt = iconWind,
                windGustKt = iconWind + 7.0,
                crosswindKt = (ecmwfEntry.crosswindKt ?: 5.0) - 1.0,
                ceilingFt = ecmwfEntry.ceilingFt?.let { it + 150.0 },
            )

            ForecastMapAirport(
                icao = seed.icao,
                name = seed.name,
                lat = seed.lat,
                lon = seed.lon,
                models = mapOf(
                    "ecmwf" to ecmwfEntry,
                    "gfs" to gfsEntry,
                    "icon_eu" to iconEntry,
                ),
                consensus = mapOf(
                    "category" to ecmwfCat,
                    "overall" to seed.agreement,
                ),
                agreement = mapOf(
                    "ecmwf" to AgreementEntry(
                        level = seed.agreement,
                        detail = if (seed.agreement == "consistent") "All models agree" else "Spread across models",
                    ),
                    "gfs" to AgreementEntry(level = seed.agreement, detail = ""),
                    "icon_eu" to AgreementEntry(level = seed.agreement, detail = ""),
                ),
                daySummary = mapOf(
                    "ecmwf" to DaySummary(
                        worstCategory = ecmwfCat,
                        maxCrosswindKt = ecmwfEntry.crosswindKt,
                        maxWindSpeedKt = ecmwfWind,
                        minCeilingFt = ecmwfEntry.ceilingFt,
                    ),
                ),
            )
        }

        return ForecastMapResponse(
            forecastTime = forecastTime,
            forecastDay = day,
            hourUtc = hourUtc,
            availableHours = listOf(6, 9, 12, 15, 18).map { hr ->
                DayHourBucket(hour = hr, label = String.format(Locale.US, "%02dZ", hr))
            },
            modelInitTimes = mapOf(
                "ecmwf" to "${LocalDate.now()}T00:00:00Z",
                "gfs" to "${LocalDate.now()}T06:00:00Z",
                "icon_eu" to "${LocalDate.now()}T06:00:00Z",
            ),
            airports = airports,
        )
    }

    fun buildHistoricalMapResponse(date: String, hourUtc: Int): HistoricalMapResponse {
        val obsTime = String.format(Locale.US, "%sT%02d:00:00Z", date, hourUtc)
        val airports = EUROPEAN_AIRPORT_SEEDS.mapIndexed { idx, seed ->
            val windSpd = seed.baseWindKt.toInt()
            val windDir = seed.baseWindDir.toInt()
            val visInt = seed.baseVisM.toInt()
            val ceilInt = seed.baseCeilingFt?.toInt()
            val tafCat = if (idx % 4 == 0) shiftCategory(seed.baseCategory, 1, 0, idx) else seed.baseCategory

            val metarRaw = buildString {
                append("METAR ${seed.icao} ${date.takeLast(2)}${String.format(Locale.US, "%02d50Z", hourUtc)} ")
                append(String.format(Locale.US, "%03d%02dKT ", windDir, windSpd))
                append(if (visInt >= 9999) "9999 " else String.format(Locale.US, "%04d ", visInt))
                if (ceilInt != null && ceilInt < 5000) {
                    append(String.format(Locale.US, "BKN%03d ", ceilInt / 100))
                } else {
                    append("FEW045 ")
                }
                append("14/08 Q1016 NOSIG")
            }

            val tafRaw = buildString {
                append("TAF ${seed.icao} ${date.takeLast(2)}0500Z ${date.takeLast(2)}06/${date.takeLast(2)}24 ")
                append(String.format(Locale.US, "%03d%02dKT ", windDir, maxOf(5, windSpd - 2)))
                append(if (tafCat == "IFR") "3000 BKN008" else "9999 SCT035")
            }

            HistoricalMapAirport(
                icao = seed.icao,
                name = seed.name,
                lat = seed.lat,
                lon = seed.lon,
                metar = HistoricalMetar(
                    raw = metarRaw,
                    observationTime = obsTime,
                    category = seed.baseCategory,
                    windDir = seed.baseWindDir,
                    windSpeedKt = seed.baseWindKt,
                    windGustKt = if (windSpd >= 18) seed.baseWindKt + 8.0 else null,
                    crosswindKt = seed.baseCrosswindKt,
                    bestRunway = seed.rwyId,
                    ceilingFt = seed.baseCeilingFt,
                    visibilityM = seed.baseVisM,
                    temperatureC = 14.0,
                    dewpointC = 8.0,
                    pressureHpa = 1016.0,
                ),
                taf = HistoricalTaf(
                    raw = tafRaw,
                    issueTime = "${date}T05:00:00Z",
                    category = tafCat,
                    windDir = seed.baseWindDir,
                    windSpeedKt = max(5.0, seed.baseWindKt - 2.0),
                    windGustKt = if (windSpd >= 20) seed.baseWindKt + 6.0 else null,
                    crosswindKt = (seed.baseCrosswindKt - 1.5).coerceAtLeast(1.0),
                    bestRunway = seed.rwyId,
                    ceilingFt = if (tafCat == "IFR") 800.0 else seed.baseCeilingFt,
                    visibilityM = if (tafCat == "IFR") 3000.0 else 9999.0,
                ),
                categoryMatch = tafCat == seed.baseCategory,
                verificationScore = if (tafCat == seed.baseCategory) "good" else "mixed",
            )
        }

        return HistoricalMapResponse(
            date = date,
            hourUtc = hourUtc,
            forecastTime = obsTime,
            availableHours = listOf(6, 9, 12, 15, 18).map { hr ->
                DayHourBucket(hour = hr, label = String.format(Locale.US, "%02dZ", hr))
            },
            airports = airports,
        )
    }

    private fun shiftCategory(base: String, dayShift: Int, hourShift: Int, idx: Int): String {
        if (dayShift == 0 && hourShift == 0) return base
        val order = listOf("VFR", "MVFR", "IFR", "LIFR")
        val cur = order.indexOf(base).coerceAtLeast(0)
        val delta = if ((dayShift + hourShift + idx) % 3 == 0) 1 else 0
        return order[(cur + delta).coerceIn(0, order.lastIndex)]
    }
}
