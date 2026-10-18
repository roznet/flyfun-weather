package aero.flyfun.weather.data

import android.content.Context
import aero.flyfun.weather.model.HelpCatalogResponse
import aero.flyfun.weather.model.MetricHelp
import aero.flyfun.weather.model.WbJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.Request
import java.io.File

@Serializable
data class MapMetricsCatalog(
    val version: Int = 1,
    val scales: MapScales = MapScales(),
    val metrics: Map<String, MapMetricDefinition> = emptyMap(),
)

@Serializable
data class MapScales(
    val categorical: Map<String, Map<String, String>> = emptyMap(),
    val bands: Map<String, MapBandScale> = emptyMap(),
)

@Serializable
data class MapBandScale(
    val kind: String = "threshold_asc",
    val convert: String? = null,
    @SerialName("null_color")
    val nullColor: String? = null,
    val stops: List<MapBandStop> = emptyList(),
    val default: String? = null,
    val base: Double? = null,
    val span: Double? = null,
    @SerialName("blue_boost")
    val blueBoost: Double? = null,
)

@Serializable
data class MapBandStop(
    val lt: Double? = null,
    val gte: Double? = null,
    val color: String = "",
)

@Serializable
data class MapMetricColor(
    val kind: String = "",
    val scale: String = "",
    val field: String? = null,
)

@Serializable
data class MapLegend(
    val title: String = "",
    val items: List<MapLegendRow> = emptyList(),
)

@Serializable
data class MapMetricDefinition(
    val label: String = "",
    @SerialName("short_label")
    val shortLabel: String? = null,
    val unit: String? = null,
    val scale: String = "",
    val field: String? = null,
    val color: MapMetricColor? = null,
    val legend: MapLegend? = null,
)

@Serializable
data class MapLegendRow(
    val color: String = "",
    val label: String = "",
)

/**
 * Loads `metrics-catalog.json` and `map-metrics-catalog.json` from Android assets
 * (with optional ETag-conditional refresh from `/api/help`), indexing [MetricHelp] by metric key.
 * Mirrors `HelpCatalogStore` on iOS.
 */
class HelpCatalogStore(
    private val context: Context? = null,
    private val cacheDir: File? = context?.cacheDir,
    private val json: Json = WbJson,
) {
    private val _metrics = MutableStateFlow<Map<String, MetricHelp>>(emptyMap())
    val metrics: StateFlow<Map<String, MetricHelp>> = _metrics.asStateFlow()

    private val _mapCatalog = MutableStateFlow<MapMetricsCatalog?>(null)
    val mapCatalog: StateFlow<MapMetricsCatalog?> = _mapCatalog.asStateFlow()

    private var currentEtag: String? = null

    init {
        loadBundledOrCached()
    }

    /**
     * Look up a single [MetricHelp] entry by its key / ID (e.g. `"cape_surface_jkg"`).
     */
    fun metric(key: String): MetricHelp? = _metrics.value[key]

    /**
     * Loads `metrics-catalog.json` and `map-metrics-catalog.json` from disk cache or bundled Android assets.
     */
    fun loadBundledOrCached() {
        // 1. Try disk-cached server payload first
        val cachedHelp = cacheDir?.let { File(it, CACHED_HELP_FILE) }
        val cachedEtagFile = cacheDir?.let { File(it, CACHED_ETAG_FILE) }
        if (cachedEtagFile?.isFile == true) {
            currentEtag = runCatching { cachedEtagFile.readText(Charsets.UTF_8).trim() }.getOrNull()
        }
        if (cachedHelp?.isFile == true) {
            val loaded = runCatching { parseHelpPayload(cachedHelp.readText(Charsets.UTF_8)) }.getOrDefault(false)
            if (loaded) return
        }

        // 2. Fall back to bundled Android assets (or classpath resources in JVM tests)
        readAssetText(METRICS_ASSET_NAME)?.let { loadMetricsFromJson(it) }
        readAssetText(MAP_METRICS_ASSET_NAME)?.let { loadMapCatalogFromJson(it) }
    }

    fun loadMetricsFromJson(jsonText: String) {
        val parsed: Map<String, MetricHelp> = runCatching {
            val obj = json.parseToJsonElement(jsonText).jsonObject
            if ("metrics" in obj && obj["metrics"] is JsonObject) {
                json.decodeFromString(HelpCatalogResponse.serializer(), jsonText).metrics
            } else {
                json.decodeFromString(
                    MapSerializer(String.serializer(), MetricHelp.serializer()),
                    jsonText,
                )
            }
        }.getOrDefault(emptyMap())
        if (parsed.isNotEmpty()) {
            _metrics.value = parsed
        }
    }

    fun loadMapCatalogFromJson(jsonText: String) {
        runCatching {
            json.decodeFromString(MapMetricsCatalog.serializer(), jsonText)
        }.onSuccess {
            _mapCatalog.value = it
        }
    }

    /**
     * Refreshes the help catalog from `GET /api/help` using `If-None-Match` ETag caching.
     */
    suspend fun refreshIfNeeded(api: ApiClient) = withContext(Dispatchers.IO) {
        val url = api.buildUrl("/api/help")
        val reqBuilder = Request.Builder().url(url).get()
        currentEtag?.takeIf { it.isNotEmpty() }?.let { etag ->
            reqBuilder.header("If-None-Match", etag)
        }

        val response = runCatching { api.httpClient.newCall(reqBuilder.build()).execute() }.getOrNull()
            ?: return@withContext
        response.use { resp ->
            if (resp.code == 304) return@withContext
            if (!resp.isSuccessful) return@withContext
            val bodyText = resp.body?.string() ?: return@withContext
            if (parseHelpPayload(bodyText)) {
                val newEtag = resp.header("ETag")
                if (!newEtag.isNullOrBlank()) {
                    currentEtag = newEtag
                    cacheDir?.let { dir ->
                        dir.mkdirs()
                        runCatching { File(dir, CACHED_ETAG_FILE).writeText(newEtag, Charsets.UTF_8) }
                    }
                }
                cacheDir?.let { dir ->
                    dir.mkdirs()
                    runCatching { File(dir, CACHED_HELP_FILE).writeText(bodyText, Charsets.UTF_8) }
                }
            }
        }
    }

    private fun parseHelpPayload(text: String): Boolean {
        val root = runCatching { json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return false
        var updated = false
        val metricsElem = root["metrics"]
        if (metricsElem is JsonObject) {
            runCatching {
                json.decodeFromJsonElement(
                    MapSerializer(String.serializer(), MetricHelp.serializer()),
                    metricsElem,
                )
            }.onSuccess {
                _metrics.value = it
                updated = true
            }
        }
        val mapsElem = root["maps"]
        if (mapsElem is JsonObject) {
            runCatching {
                json.decodeFromJsonElement(MapMetricsCatalog.serializer(), mapsElem)
            }.onSuccess {
                _mapCatalog.value = it
                updated = true
            }
        }
        return updated
    }

    private fun readAssetText(assetName: String): String? {
        context?.assets?.let { assets ->
            runCatching {
                assets.open(assetName).bufferedReader(Charsets.UTF_8).use { it.readText() }
            }.getOrNull()?.let { return it }
        }
        // Fallback for JVM unit tests: look in app/src/main/assets
        val candidateFiles = listOf(
            File("src/main/assets/$assetName"),
            File("app/src/main/assets/$assetName"),
            File("app/android/app/src/main/assets/$assetName"),
        )
        for (file in candidateFiles) {
            if (file.isFile) {
                return runCatching { file.readText(Charsets.UTF_8) }.getOrNull()
            }
        }
        return null
    }

    companion object {
        const val METRICS_ASSET_NAME = "metrics-catalog.json"
        const val MAP_METRICS_ASSET_NAME = "map-metrics-catalog.json"
        private const val CACHED_HELP_FILE = "help-catalog-cache.json"
        private const val CACHED_ETAG_FILE = "help-catalog-etag.txt"
    }
}
