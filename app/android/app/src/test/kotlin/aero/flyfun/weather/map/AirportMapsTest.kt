package aero.flyfun.weather.map

import androidx.compose.ui.graphics.Color
import aero.flyfun.weather.data.HelpCatalogStore
import aero.flyfun.weather.data.MapsApi
import aero.flyfun.weather.model.ForecastDaysResponse
import aero.flyfun.weather.model.ForecastMapResponse
import aero.flyfun.weather.model.ForecastModelMode
import aero.flyfun.weather.model.FrequentAirport
import aero.flyfun.weather.model.FrequentAirportsResponse
import aero.flyfun.weather.model.HistoricalMapResponse
import aero.flyfun.weather.model.HistoricalRangeResponse
import aero.flyfun.weather.model.HistoricalSource
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.WbJson
import aero.flyfun.weather.ui.map.AirportMapCatalog
import aero.flyfun.weather.ui.map.MapWindFormat
import aero.flyfun.weather.ui.map.parseCatalogColor
import aero.flyfun.weather.viewmodel.ForecastMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalMapViewModel
import aero.flyfun.weather.viewmodel.HistoricalTime
import aero.flyfun.weather.viewmodel.MAPS_DEMO_UNAVAILABLE
import java.time.Instant
import java.time.LocalDate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The forecast and historical maps against the server's real payload shapes
 * (`src/test/resources/maps/`, trimmed from the server's map responses): decoding, the served catalog's
 * colours, and both view models through a fake [MapsApi]. The maps used to read an invented shape
 * and silently fall back to 15 fixture airports.
 */
class AirportMapsTest {

    private fun resource(name: String): String =
        requireNotNull(javaClass.classLoader?.getResource("maps/$name")) { "missing test resource maps/$name" }.readText()

    private val forecast = WbJson.decodeFromString(ForecastMapResponse.serializer(), resource("forecast_slice.json"))
    private val historical = WbJson.decodeFromString(HistoricalMapResponse.serializer(), resource("historical_latest.json"))
    private val historicalLead6 = WbJson.decodeFromString(HistoricalMapResponse.serializer(), resource("historical_lead6.json"))
    private val catalog = requireNotNull(HelpCatalogStore().mapCatalog.value) { "bundled map-metrics-catalog.json" }

    private fun hex(s: String) = requireNotNull(parseCatalogColor(s))

    // -------------------------------------------------------------------------
    // Decoding
    // -------------------------------------------------------------------------

    @Test
    fun `forecast payload decodes models, both consensus blocks and agreement`() {
        assertEquals(setOf("gfs", "icon", "ecmwf"), forecast.modelInitTimes.keys)
        val egll = forecast.airports.first { it.icao == "EGLL" }
        assertEquals("ILS", egll.approachType)
        assertEquals(setOf("gfs", "icon", "ecmwf"), egll.models.keys)
        assertEquals("MVFR", egll.models["icon"]?.flightCategory)
        assertEquals("27R", egll.models["gfs"]?.bestRunwayId)
        assertEquals(true, egll.models["gfs"]?.altRequired?.faa)
        // ECMWF has no visibility on the wire: null, not a default
        assertNull(egll.models["ecmwf"]?.visibilityM)
        assertEquals("IFR", egll.consensus.flightCategory)
        assertEquals("mixed", egll.consensus.agreement["flight_category"])
        // Crosswind reads the wind agreement; convective reads CAPE's
        assertEquals("consistent", egll.consensus.agreement(forMetric = "crosswind_kt"))

        val lfpb = forecast.airports.first { it.icao == "LFPB" }
        assertEquals("LIFR", lfpb.consensus(ForecastModelMode.Worst).flightCategory)
        assertEquals("IFR", lfpb.consensus(ForecastModelMode.Majority).flightCategory)
        assertEquals("divergent", lfpb.consensus.agreement(forMetric = "visibility_m"))
    }

    @Test
    fun `historical payload decodes flat METAR and TAF blocks and model provenance`() {
        assertEquals(0, historical.leadDays)
        assertEquals(90, historical.sources["metar"]?.maxAgeMin)
        assertEquals(12, historical.sources["gfs"]?.leadHours)

        val egll = historical.airports.first { it.icao == "EGLL" }
        val metar = requireNotNull(egll.metar)
        assertEquals("MVFR", metar.values.flightCategory)
        assertEquals("METAR", metar.reportType)
        assertTrue(metar.raw!!.startsWith("METAR EGLL"))
        val taf = requireNotNull(egll.taf)
        assertEquals("IFR", taf.values.flightCategory)
        assertEquals("PROB40 TEMPO", taf.temporaryType)
        assertEquals("VFR", taf.prevailingCategory)
        assertEquals("2026-10-24T00:00:00+00:00", egll.models["gfs"]?.modelInitTime)
        assertEquals("2026-10-24T12:00:00+00:00", egll.models["gfs"]?.validTime)

        // An airport with a METAR and no TAF
        val eddfm = historical.airports.first { it.icao == "EDFM" }
        assertNotNull(eddfm.metar)
        assertNull(eddfm.taf)
    }

    @Test
    fun `a beyond-horizon lead has observations only and no consensus`() {
        assertEquals(6, historicalLead6.leadDays)
        assertEquals("beyond_horizon", historicalLead6.sources["icon"]?.reason)
        val biar = historicalLead6.airports.first { it.icao == "BIAR" }
        assertNull(biar.consensus)
        assertTrue(biar.models.isEmpty())
        // Observed sources present it; consensus and model sources leave it off the map
        assertNotNull(biar.forecastAirport(HistoricalSource.METAR))
        assertNotNull(biar.forecastAirport(HistoricalSource.TAF))
        assertNull(biar.forecastAirport(HistoricalSource.WORST))
        assertNull(biar.forecastAirport(HistoricalSource.GFS))
    }

    @Test
    fun `a historical source is presented as the one model the marker layer reads`() {
        val egll = historical.airports.first { it.icao == "EGLL" }
        val asMetar = requireNotNull(egll.forecastAirport(HistoricalSource.METAR))
        assertEquals(setOf("metar"), asMetar.models.keys)
        assertEquals("MVFR", asMetar.cell(HistoricalSource.METAR.mapMode)?.categoryField("flight_category"))
        // Consensus sources keep the NWP models only: an observation never votes
        val asWorst = requireNotNull(egll.forecastAirport(HistoricalSource.WORST))
        assertEquals(setOf("gfs", "icon", "ecmwf"), asWorst.models.keys)
        // The card's combined airport carries every source side by side
        assertEquals(setOf("gfs", "icon", "ecmwf", "metar", "taf"), egll.allSourcesAirport.models.keys)
    }

    // -------------------------------------------------------------------------
    // Catalog colours (the served map-metrics-catalog.json)
    // -------------------------------------------------------------------------

    @Test
    fun `categorical colours follow the catalog and the active mode`() {
        val lfpb = forecast.airports.first { it.icao == "LFPB" }
        assertEquals(hex("#a855f7"), AirportMapCatalog.color(catalog, "flight_category", lfpb, ForecastModelMode.Worst))
        assertEquals(hex("#ef4444"), AirportMapCatalog.color(catalog, "flight_category", lfpb, ForecastModelMode.Majority))
        assertEquals(hex("#ef4444"), AirportMapCatalog.color(catalog, "flight_category", lfpb, ForecastModelMode.Model("icon")))
        // A model the airport doesn't carry is muted, never a guess
        assertEquals(AirportMapCatalog.MUTED, AirportMapCatalog.color(catalog, "flight_category", lfpb, ForecastModelMode.Model("arome")))
    }

    @Test
    fun `band scales cover asc, desc with conversion, null colour, fallback and gray ramp`() {
        val egll = forecast.airports.first { it.icao == "EGLL" }
        // GFS wind 17.9 kt: asc stops <10 green, <15, <20 yellow
        assertEquals(hex("#eab308"), AirportMapCatalog.color(catalog, "wind_speed_kt", egll, ForecastModelMode.Model("gfs")))
        // Visibility 24 140 m = 15 SM ≥ 4.97 → green (threshold_desc after m_to_sm)
        assertEquals(hex("#22c55e"), AirportMapCatalog.color(catalog, "visibility_m", egll, ForecastModelMode.Model("gfs")))
        // ECMWF visibility absent → numeric fallback 99999 m → green, not muted
        assertEquals(hex("#22c55e"), AirportMapCatalog.color(catalog, "visibility_m", egll, ForecastModelMode.Model("ecmwf")))

        val ceiling = requireNotNull(catalog.scales.bands["ceiling_ft"])
        assertEquals(hex("#888"), AirportMapCatalog.bandColor(ceiling, null, null))
        assertEquals(hex("#a855f7"), AirportMapCatalog.bandColor(ceiling, 400.0, null))
        assertEquals(hex("#22c55e"), AirportMapCatalog.bandColor(ceiling, 5000.0, null))

        val cloud = requireNotNull(catalog.scales.bands["cloud_cover_pct"])
        // 100 % → 220 - 160 = 60, blue + 10
        assertEquals(Color(60 / 255f, 60 / 255f, 70 / 255f), AirportMapCatalog.bandColor(cloud, 100.0, null))
    }

    @Test
    fun `alternate required aggregates the models per mode`() {
        val egll = forecast.airports.first { it.icao == "EGLL" }
        // Every model says FAA + EASA → red under worst and majority
        assertEquals(Color(0xFFEF4444), AirportMapCatalog.color(catalog, "alternate_needed", egll, ForecastModelMode.Worst))
        assertEquals("FAA + EASA", AirportMapCatalog.altLabel(egll.aggregatedAltRequired(ForecastModelMode.Majority)))

        val lead6 = historicalLead6.airports.first { it.icao == "BIAR" }
        val asMetar = requireNotNull(lead6.forecastAirport(HistoricalSource.METAR))
        assertEquals(Color(0xFF22C55E), AirportMapCatalog.color(catalog, "alternate_needed", asMetar, HistoricalSource.METAR.mapMode))
    }

    @Test
    fun `agreement ring colour and cell text match iOS`() {
        assertEquals(hex("#ef4444"), AirportMapCatalog.agreementColor(catalog, "divergent"))
        assertEquals(AirportMapCatalog.MUTED, AirportMapCatalog.agreementColor(catalog, null))

        val egll = forecast.airports.first { it.icao == "EGLL" }
        val gfs = egll.models["gfs"]
        assertEquals("217@18G40", AirportMapCatalog.compactCell(gfs, "wind_speed_kt"))
        assertEquals(">10 km", AirportMapCatalog.compactCell(gfs, "visibility_m"))
        assertEquals("—", AirportMapCatalog.compactCell(egll.models["ecmwf"], "visibility_m"))
        assertEquals("IFR", AirportMapCatalog.compactCell(egll.consensus, "flight_category"))
        // Gust shown only 4+ kt above the sustained value
        assertEquals("12", MapWindFormat.component(12.0, gust = 15.0))
        assertEquals("12G16", MapWindFormat.component(12.0, gust = 16.0))
        assertEquals("005@7", MapWindFormat.wind(7.0, 5.0, null))
    }

    // -------------------------------------------------------------------------
    // View models
    // -------------------------------------------------------------------------

    private class FakeMapsApi(
        private val forecast: ForecastMapResponse,
        private val days: ForecastDaysResponse,
        private val range: HistoricalRangeResponse,
        private val historical: Map<Int, HistoricalMapResponse>,
        private val frequent: FrequentAirportsResponse = FrequentAirportsResponse(),
    ) : MapsApi {
        val forecastCalls = mutableListOf<Pair<Int, Int>>()
        val historicalCalls = mutableListOf<Pair<String, Int>>()

        override suspend fun getForecastDays() = days

        override suspend fun getForecastMap(day: Int, hourUtc: Int): ForecastMapResponse {
            forecastCalls += day to hourUtc
            return forecast
        }

        override suspend fun getFrequentAirports() = frequent

        override suspend fun getHistoricalRange() = range

        override suspend fun getHistoricalMap(at: String, lead: Int): HistoricalMapResponse {
            historicalCalls += at to lead
            return historical[lead] ?: error("no lead $lead")
        }
    }

    private fun fakeApi(frequent: FrequentAirportsResponse = FrequentAirportsResponse()) = FakeMapsApi(
        forecast = forecast,
        days = WbJson.decodeFromString(ForecastDaysResponse.serializer(), resource("forecast_days.json")),
        range = WbJson.decodeFromString(HistoricalRangeResponse.serializer(), resource("historical_range.json")),
        historical = mapOf(0 to historical, 6 to historicalLead6),
        frequent = frequent,
    )

    private val scope = CoroutineScope(Dispatchers.Unconfined)

    @Test
    fun `forecast view model shows the server's airports, never a fixture`() {
        val api = fakeApi(FrequentAirportsResponse(departures = listOf(FrequentAirport("EGTK", 4))))
        val vm = ForecastMapViewModel(api = api, scopeOverride = scope, clock = { Instant.parse("2026-10-24T17:10:00Z") })
        assertEquals(listOf("EGLL", "EGTK", "LFPB"), vm.payload.value?.airports?.map { it.icao })
        assertTrue(vm.didLoadOnce.value)
        assertNull(vm.loadError.value)
        // Cold open: nearest sample hour to now (17Z → 18Z), centred on the usual departure
        assertEquals(18, vm.selectedHour.value)
        assertEquals("EGTK", forecast.airports.first { it.lat == vm.focusRequest.value?.lat }.icao)
        assertEquals(0 to 18, api.forecastCalls.first())
        // Metric and model switches are a recolour: no refetch
        val calls = api.forecastCalls.size
        vm.selectMetric("crosswind_kt")
        vm.selectMode(ForecastModelMode.Model("gfs"))
        assertEquals(calls, api.forecastCalls.size)
    }

    @Test
    fun `forecast deep link selects mode, metric and opens the airport`() {
        val api = fakeApi()
        val vm = ForecastMapViewModel(api = api, scopeOverride = scope, clock = { Instant.parse("2026-10-24T10:00:00Z") })
        vm.applyDeepLinkTarget(NavigationTarget.ForecastMap(dayOffset = 1, hourUtc = 15, model = "majority", metric = "ceiling_ft", airport = "LFPB"))
        assertEquals(1, vm.selectedDay.value)
        assertEquals(15, vm.selectedHour.value)
        assertEquals(ForecastModelMode.Majority, vm.mode.value)
        assertEquals("ceiling_ft", vm.metric.value)
        assertEquals("LFPB", vm.selectedIcao.value)
        // The linked slot, then its neighbours prefetched for ‹ › stepping
        assertTrue(api.forecastCalls.containsAll(listOf(1 to 15, 1 to 12, 1 to 18)))
    }

    @Test
    fun `demo mode says the maps need an account instead of drawing fake airports`() {
        val fVm = ForecastMapViewModel(isMockModeProvider = { true }, scopeOverride = scope)
        assertNull(fVm.payload.value)
        assertEquals(MAPS_DEMO_UNAVAILABLE, fVm.loadError.value)
        val hVm = HistoricalMapViewModel(isMockModeProvider = { true }, scopeOverride = scope)
        assertNull(hVm.mapPayload.value)
        assertEquals(MAPS_DEMO_UNAVAILABLE, hVm.loadError.value)
    }

    @Test
    fun `historical view model loads the latest instant and recolours on a source switch`() {
        val api = fakeApi()
        val vm = HistoricalMapViewModel(api = api, scopeOverride = scope, now = { Instant.parse("2026-10-24T14:10:00Z") })
        // The range's latest instant, sent as `at` with lead 0
        assertEquals("2026-10-24T14:00:00Z" to 0, api.historicalCalls.single())
        assertEquals(Instant.parse("2026-10-24T14:00:00Z"), vm.selectedInstant.value)
        // METAR source: every airport with a METAR
        assertEquals(3, vm.mapPayload.value?.airports?.size)
        assertEquals("3 airports · reports ≤90 min old", vm.statusLine())

        vm.selectSource(HistoricalSource.TAF)
        assertEquals(listOf("EDDV", "EGLL"), vm.mapPayload.value?.airports?.map { it.icao })
        assertEquals(1, api.historicalCalls.size)
        assertTrue(vm.statusLine()!!.startsWith("2 airports · TAFs read at"))
    }

    @Test
    fun `historical lead switch refetches and an unavailable source explains itself`() {
        val api = fakeApi()
        val vm = HistoricalMapViewModel(api = api, scopeOverride = scope, now = { Instant.parse("2026-10-24T14:10:00Z") })
        vm.selectLead(6)
        assertEquals(6, api.historicalCalls.last().second)
        assertFalse(vm.sourceAvailable(HistoricalSource.ICON))
        vm.selectSource(HistoricalSource.ICON)
        assertEquals(HistoricalSource.METAR, vm.source.value)
        assertEquals("ICON isn't stored 6 days ahead. Its map horizon is shorter.", vm.notice.value)
        vm.selectSource(HistoricalSource.WORST)
        assertEquals("No model has data at this time and lead.", vm.notice.value)
    }

    @Test
    fun `historical time grid steps by 30 min and clamps to the range`() {
        val api = fakeApi()
        val vm = HistoricalMapViewModel(api = api, scopeOverride = scope, now = { Instant.parse("2026-10-24T14:10:00Z") })
        assertFalse(vm.canStep(1)) // already at the latest instant
        assertTrue(vm.canStep(-1))
        vm.step(-1)
        assertEquals(Instant.parse("2026-10-24T13:30:00Z"), vm.selectedInstant.value)
        assertEquals(48, vm.timeSlots().size)
        // Picking an earlier day keeps the time of day
        vm.selectDay(LocalDate.parse("2026-10-20"))
        assertEquals(Instant.parse("2026-10-20T13:30:00Z"), vm.selectedInstant.value)
        // Before the model archive: METAR/TAF only
        assertTrue(vm.isObservationOnly(Instant.parse("2026-10-10T12:00:00Z")))
        assertFalse(vm.isObservationOnly(Instant.parse("2026-10-20T12:00:00Z")))
        // Out of range clamps to the earliest observation (floored to the grid)
        vm.selectInstant(Instant.parse("2025-01-01T00:00:00Z"))
        assertEquals(Instant.parse("2026-04-01T18:00:00Z"), vm.selectedInstant.value)
    }

    @Test
    fun `historical deep link applies the linked instant, lead, source and airport`() {
        val api = fakeApi()
        val vm = HistoricalMapViewModel(api = api, scopeOverride = scope, now = { Instant.parse("2026-10-24T14:10:00Z") })
        vm.applyDeepLinkTarget(
            NavigationTarget.HistoricalMap(date = "2026-10-23", time = "12:00", lead = 6, source = "taf", metric = "ceiling_ft", airport = "BIAR"),
        )
        assertEquals(Instant.parse("2026-10-23T12:00:00Z"), vm.selectedInstant.value)
        assertEquals("2026-10-23T12:00:00Z" to 6, api.historicalCalls.last())
        assertEquals(HistoricalSource.TAF, vm.source.value)
        assertEquals("ceiling_ft", vm.metric.value)
        assertEquals("BIAR", vm.selectedIcao.value)
    }

    @Test
    fun `historical time labels parse python isoformat`() {
        assertEquals(Instant.parse("2026-10-14T19:19:42.270433Z"), HistoricalTime.parse("2026-10-14T19:19:42.270433+00:00"))
        assertEquals(Instant.parse("2026-10-14T19:00:00Z"), HistoricalTime.parse("2026-10-14T19:00:00"))
        assertEquals("19:19Z", HistoricalTime.timeLabel("2026-10-14T19:19:42.270433+00:00"))
        assertEquals("24 00Z", HistoricalTime.runLabel("2026-10-24T00:00:00+00:00"))
        assertNull(HistoricalTime.instant("2026-10-24", null))
    }
}
