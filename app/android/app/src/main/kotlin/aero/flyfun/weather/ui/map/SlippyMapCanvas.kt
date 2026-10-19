package aero.flyfun.weather.ui.map

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.data.ApiClient
import java.util.Collections
import java.util.Locale
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.tan
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Geographic coordinate pair in degrees.
 */
data class LatLon(
    val lat: Double,
    val lon: Double,
)

/**
 * Bounding box in geographic coordinates.
 */
data class GeoBounds(
    val minLat: Double,
    val maxLat: Double,
    val minLon: Double,
    val maxLon: Double,
) {
    val centerLat: Double get() = (minLat + maxLat) / 2.0
    val centerLon: Double get() = (minLon + maxLon) / 2.0
}

/**
 * Optional observed raster tile overlay specification (`/api/observed/tiles/{layer}/{stamp}/{z}/{x}/{y}.png`).
 */
data class ObservedRasterLayerSpec(
    val layer: String, // "radar" or "sat-ir"
    val stamp: String, // e.g. "202604181200"
    val alpha: Float = 0.65f,
)

/**
 * Immutable Web Mercator projection snapshot for a single render frame.
 */
class SlippyMapProjection(
    val centerLat: Double,
    val centerLon: Double,
    val zoom: Float,
    val widthPx: Float,
    val heightPx: Float,
) {
    val worldSizePx: Double = 256.0 * 2.0.pow(zoom.toDouble())
    private val centerWorldX: Double = lonToWorldX(centerLon, worldSizePx)
    private val centerWorldY: Double = latToWorldY(centerLat, worldSizePx)

    fun latLonToScreen(lat: Double, lon: Double): Offset {
        val wx = lonToWorldX(lon, worldSizePx)
        val wy = latToWorldY(lat, worldSizePx)
        val sx = (widthPx / 2.0) + (wx - centerWorldX)
        val sy = (heightPx / 2.0) + (wy - centerWorldY)
        return Offset(sx.toFloat(), sy.toFloat())
    }

    fun screenToLatLon(offset: Offset): LatLon {
        val wx = centerWorldX + (offset.x - widthPx / 2.0)
        val wy = centerWorldY + (offset.y - heightPx / 2.0)
        return LatLon(
            lat = worldYToLat(wy, worldSizePx),
            lon = worldXToLon(wx, worldSizePx),
        )
    }

    fun visibleBounds(): GeoBounds {
        val topLeft = screenToLatLon(Offset(0f, 0f))
        val bottomRight = screenToLatLon(Offset(widthPx, heightPx))
        return GeoBounds(
            minLat = min(topLeft.lat, bottomRight.lat).coerceIn(-85.0, 85.0),
            maxLat = max(topLeft.lat, bottomRight.lat).coerceIn(-85.0, 85.0),
            minLon = min(topLeft.lon, bottomRight.lon).coerceIn(-180.0, 180.0),
            maxLon = max(topLeft.lon, bottomRight.lon).coerceIn(-180.0, 180.0),
        )
    }

    companion object {
        fun lonToWorldX(lon: Double, worldSize: Double): Double {
            return ((lon + 180.0) / 360.0) * worldSize
        }

        fun latToWorldY(lat: Double, worldSize: Double): Double {
            val clamped = lat.coerceIn(-85.05112878, 85.05112878)
            val rad = clamped * PI / 180.0
            val mercN = ln(tan(PI / 4.0 + rad / 2.0))
            return (0.5 - mercN / (2.0 * PI)) * worldSize
        }

        fun worldXToLon(x: Double, worldSize: Double): Double {
            return (x / worldSize) * 360.0 - 180.0
        }

        fun worldYToLat(y: Double, worldSize: Double): Double {
            val n = PI - 2.0 * PI * (y / worldSize)
            return (180.0 / PI) * atan(0.5 * (exp(n) - exp(-n)))
        }
    }
}

/**
 * Interactive camera state for [SlippyMapCanvas].
 */
@Stable
class SlippyMapCameraState(
    initialCenterLat: Double = 48.5,
    initialCenterLon: Double = 7.5,
    initialZoom: Float = 5.2f,
) {
    var centerLat by mutableDoubleStateOf(initialCenterLat.coerceIn(-80.0, 80.0))
    var centerLon by mutableDoubleStateOf(initialCenterLon.coerceIn(-180.0, 180.0))
    var zoom by mutableFloatStateOf(initialZoom.coerceIn(MIN_ZOOM, MAX_ZOOM))
    var viewportWidthPx by mutableFloatStateOf(0f)
    var viewportHeightPx by mutableFloatStateOf(0f)

    fun currentProjection(): SlippyMapProjection {
        return SlippyMapProjection(
            centerLat = centerLat,
            centerLon = centerLon,
            zoom = zoom,
            widthPx = max(1f, viewportWidthPx),
            heightPx = max(1f, viewportHeightPx),
        )
    }

    fun panByPixels(dx: Float, dy: Float) {
        val worldSize = 256.0 * 2.0.pow(zoom.toDouble())
        val newWx = SlippyMapProjection.lonToWorldX(centerLon, worldSize) - dx
        val newWy = (SlippyMapProjection.latToWorldY(centerLat, worldSize) - dy)
            .coerceIn(0.0, worldSize)
        centerLon = SlippyMapProjection.worldXToLon(newWx, worldSize).coerceIn(-180.0, 180.0)
        centerLat = SlippyMapProjection.worldYToLat(newWy, worldSize).coerceIn(-80.0, 80.0)
    }

    fun zoomByFactor(factor: Float, focalPoint: Offset? = null) {
        val oldZoom = zoom
        val newZoom = (oldZoom + (ln(factor.toDouble()) / ln(2.0)).toFloat())
            .coerceIn(MIN_ZOOM, MAX_ZOOM)
        if (abs(newZoom - oldZoom) < 1e-4f) return

        if (focalPoint != null && viewportWidthPx > 0f && viewportHeightPx > 0f) {
            val beforeProj = currentProjection()
            val anchorLatLon = beforeProj.screenToLatLon(focalPoint)
            zoom = newZoom
            val afterProj = currentProjection()
            val afterScreen = afterProj.latLonToScreen(anchorLatLon.lat, anchorLatLon.lon)
            panByPixels(focalPoint.x - afterScreen.x, focalPoint.y - afterScreen.y)
        } else {
            zoom = newZoom
        }
    }

    fun fitBounds(bounds: GeoBounds, paddingFraction: Float = 0.18f) {
        val w = if (viewportWidthPx > 10f) viewportWidthPx else 900f
        val h = if (viewportHeightPx > 10f) viewportHeightPx else 650f
        centerLat = bounds.centerLat.coerceIn(-80.0, 80.0)
        centerLon = bounds.centerLon.coerceIn(-180.0, 180.0)

        val latSpan = max(0.25, bounds.maxLat - bounds.minLat)
        val lonSpan = max(0.25, bounds.maxLon - bounds.minLon)
        val usableW = w * (1f - 2f * paddingFraction).coerceAtLeast(0.4f)
        val usableH = h * (1f - 2f * paddingFraction).coerceAtLeast(0.4f)

        val zoomLon = ln((usableW * 360.0) / (256.0 * lonSpan)) / ln(2.0)
        val cosLat = cos(centerLat * PI / 180.0).coerceAtLeast(0.2)
        val zoomLat = ln((usableH * 360.0 * cosLat) / (256.0 * latSpan)) / ln(2.0)
        zoom = min(zoomLon, zoomLat).toFloat().coerceIn(MIN_ZOOM, 10.5f)
    }

    companion object {
        const val MIN_ZOOM = 3.2f
        const val MAX_ZOOM = 12.0f
    }
}

@Composable
fun rememberSlippyMapCameraState(
    initialCenterLat: Double = 48.5,
    initialCenterLon: Double = 7.5,
    initialZoom: Float = 5.2f,
): SlippyMapCameraState {
    return remember {
        SlippyMapCameraState(
            initialCenterLat = initialCenterLat,
            initialCenterLon = initialCenterLon,
            initialZoom = initialZoom,
        )
    }
}

/**
 * Shared in-memory + on-disk HTTP tile cache across map instances.
 * Mirrors `web/ts/visualization/map-tiles.ts`:
 * - Dark theme: Stadia Maps `alidade_smooth_dark`
 * - Muted light theme (when observed radar/satellite imagery is active): Stadia Maps `alidade_smooth`
 * - Standard light theme: OpenStreetMap
 */
internal object MapTileCache {
    private val cache = object : LruCache<String, ImageBitmap>(180) {}
    private val inFlight: MutableSet<String> = Collections.synchronizedSet(HashSet())

    @Volatile
    private var diskHttpClient: okhttp3.OkHttpClient? = null

    fun httpClient(cacheDir: java.io.File?): okhttp3.OkHttpClient {
        diskHttpClient?.let { return it }
        return synchronized(this) {
            diskHttpClient ?: okhttp3.OkHttpClient.Builder()
                .apply {
                    if (cacheDir != null) {
                        val tileDir = java.io.File(cacheDir, "http_map_tiles")
                        cache(okhttp3.Cache(tileDir, 30L * 1024L * 1024L))
                    }
                }
                .connectTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(8, java.util.concurrent.TimeUnit.SECONDS)
                .build()
                .also { diskHttpClient = it }
        }
    }

    fun baseTileStyle(dark: Boolean, muted: Boolean): String = when {
        dark -> "stadia-dark"
        muted -> "stadia-muted"
        else -> "osm-light"
    }

    fun baseTileUrl(dark: Boolean, muted: Boolean, z: Int, x: Int, y: Int): String = when {
        dark -> "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/$z/$x/$y.png"
        muted -> "https://tiles.stadiamaps.com/tiles/alidade_smooth/$z/$x/$y.png"
        else -> "https://tile.openstreetmap.org/$z/$x/$y.png"
    }

    fun get(key: String): ImageBitmap? = synchronized(cache) { cache.get(key) }

    fun put(key: String, bitmap: ImageBitmap) = synchronized(cache) { cache.put(key, bitmap) }

    fun markInFlight(key: String): Boolean = inFlight.add(key)

    fun unmarkInFlight(key: String) {
        inFlight.remove(key)
    }
}

/**
 * Interactive Web Mercator slippy map composable used for both the Briefing Route Map
 * and the Pan-European Forecast / Historical Maps.
 */
@Composable
fun SlippyMapCanvas(
    cameraState: SlippyMapCameraState,
    modifier: Modifier = Modifier,
    apiClient: ApiClient? = null,
    observedRasterLayer: ObservedRasterLayerSpec? = null,
    defaultBounds: GeoBounds? = null,
    onTap: ((Offset, LatLon, SlippyMapProjection) -> Unit)? = null,
    overlayDraw: DrawScope.(SlippyMapProjection) -> Unit = {},
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val isDark = isSystemInDarkTheme()
    val isMuted = observedRasterLayer != null && observedRasterLayer.stamp.isNotBlank()
    val scope = rememberCoroutineScope()
    var tileTick by remember { mutableIntStateOf(0) }
    var hasFittedInitialBounds by remember { mutableIntStateOf(0) }

    LaunchedEffect(defaultBounds) {
        if (defaultBounds != null && hasFittedInitialBounds == 0) {
            cameraState.fitBounds(defaultBounds)
            hasFittedInitialBounds = 1
        }
    }

    Box(
        modifier = modifier
            .clip(RoundedCornerShape(12.dp))
            .background(if (isDark) Color(0xFF0F172A) else Color(0xFFDCE7F3))
            .onSizeChanged { size ->
                val wasZero = cameraState.viewportWidthPx <= 1f
                cameraState.viewportWidthPx = size.width.toFloat()
                cameraState.viewportHeightPx = size.height.toFloat()
                if (wasZero && defaultBounds != null && size.width > 0 && size.height > 0) {
                    cameraState.fitBounds(defaultBounds)
                }
            }
            .pointerInput(Unit) {
                detectTransformGestures { centroid, pan, zoomChange, _ ->
                    if (abs(pan.x) > 0.1f || abs(pan.y) > 0.1f) {
                        cameraState.panByPixels(pan.x, pan.y)
                    }
                    if (abs(zoomChange - 1f) > 0.002f) {
                        cameraState.zoomByFactor(zoomChange, centroid)
                    }
                }
            }
            .pointerInput(onTap) {
                detectTapGestures(
                    onDoubleTap = { tapOffset ->
                        cameraState.zoomByFactor(1.6f, tapOffset)
                    },
                    onTap = { tapOffset ->
                        val proj = cameraState.currentProjection()
                        val latLon = proj.screenToLatLon(tapOffset)
                        onTap?.invoke(tapOffset, latLon, proj)
                    },
                )
            },
    ) {
        Canvas(modifier = Modifier.matchParentSize()) {
            // Read tileTick so recomposition triggers when async tiles arrive
            @Suppress("UNUSED_VARIABLE")
            val tickSnapshot = tileTick

            val projection = SlippyMapProjection(
                centerLat = cameraState.centerLat,
                centerLon = cameraState.centerLon,
                zoom = cameraState.zoom,
                widthPx = size.width,
                heightPx = size.height,
            )

            // 1. Draw ocean/land background + lat/lon graticule + European outline fallback
            drawFallbackBasemap(projection = projection, isDark = isDark)

            // 2. Draw Stadia Maps (dark/muted) or OSM (light) raster tiles
            val tileZoom = floor(cameraState.zoom.toDouble()).toInt().coerceIn(3, 11)
            val nTiles = 1 shl tileZoom
            val scaleFactor = 2.0.pow(cameraState.zoom.toDouble() - tileZoom)
            val tileSizeOnScreen = (256.0 * scaleFactor).toFloat()

            val topLeftLatLon = projection.screenToLatLon(Offset(0f, 0f))
            val bottomRightLatLon = projection.screenToLatLon(Offset(size.width, size.height))

            val minTileX = lonToTileX(min(topLeftLatLon.lon, bottomRightLatLon.lon), nTiles).coerceIn(0, nTiles - 1)
            val maxTileX = lonToTileX(max(topLeftLatLon.lon, bottomRightLatLon.lon), nTiles).coerceIn(0, nTiles - 1)
            val minTileY = latToTileY(max(topLeftLatLon.lat, bottomRightLatLon.lat), nTiles).coerceIn(0, nTiles - 1)
            val maxTileY = latToTileY(min(topLeftLatLon.lat, bottomRightLatLon.lat), nTiles).coerceIn(0, nTiles - 1)

            val styleId = MapTileCache.baseTileStyle(isDark, isMuted)

            for (tx in minTileX..maxTileX) {
                for (ty in minTileY..maxTileY) {
                    val nwLon = tileXToLon(tx, nTiles)
                    val nwLat = tileYToLat(ty, nTiles)
                    val topLeftScreen = projection.latLonToScreen(nwLat, nwLon)
                    val dstOffset = IntOffset(topLeftScreen.x.roundToInt(), topLeftScreen.y.roundToInt())
                    val dstSize = IntSize(tileSizeOnScreen.roundToInt() + 1, tileSizeOnScreen.roundToInt() + 1)

                    val baseKey = "$styleId:$tileZoom:$tx:$ty"
                    val baseBmp = MapTileCache.get(baseKey)
                    if (baseBmp != null) {
                        drawImage(
                            image = baseBmp,
                            dstOffset = dstOffset,
                            dstSize = dstSize,
                            alpha = 0.94f,
                        )
                    } else if (MapTileCache.markInFlight(baseKey)) {
                        val url = MapTileCache.baseTileUrl(isDark, isMuted, tileZoom, tx, ty)
                        val fallbackUrl = if (isDark || isMuted) {
                            MapTileCache.baseTileUrl(dark = false, muted = false, tileZoom, tx, ty)
                        } else {
                            null
                        }
                        val appCacheDir = context.applicationContext?.cacheDir
                        scope.launch(Dispatchers.IO) {
                            try {
                                val bmp = fetchTileBitmap(url, appCacheDir)
                                    ?: fallbackUrl?.let { fetchTileBitmap(it, appCacheDir) }
                                if (bmp != null) {
                                    MapTileCache.put(baseKey, bmp)
                                    withContext(Dispatchers.Main) { tileTick++ }
                                }
                            } finally {
                                MapTileCache.unmarkInFlight(baseKey)
                            }
                        }
                    }

                    // 3. Draw Observed Weather Imagery Tile overlay (radar / sat-ir) if active
                    val obsSpec = observedRasterLayer
                    if (obsSpec != null && obsSpec.stamp.isNotBlank() && tileZoom <= 8) {
                        val obsKey = "obs:${obsSpec.layer}:${obsSpec.stamp}:$tileZoom:$tx:$ty"
                        val obsBmp = MapTileCache.get(obsKey)
                        if (obsBmp != null) {
                            drawImage(
                                image = obsBmp,
                                dstOffset = dstOffset,
                                dstSize = dstSize,
                                alpha = obsSpec.alpha,
                            )
                        } else if (apiClient != null && MapTileCache.markInFlight(obsKey)) {
                            val path = "/api/observed/tiles/${obsSpec.layer}/${obsSpec.stamp}/$tileZoom/$tx/$ty.png"
                            scope.launch(Dispatchers.IO) {
                                try {
                                    val bytes = runCatching { apiClient.requestRaw(path) }.getOrNull()
                                    if (bytes != null && bytes.isNotEmpty()) {
                                        val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                                        if (decoded != null) {
                                            MapTileCache.put(obsKey, decoded.asImageBitmap())
                                            withContext(Dispatchers.Main) { tileTick++ }
                                        }
                                    }
                                } finally {
                                    MapTileCache.unmarkInFlight(obsKey)
                                }
                            }
                        }
                    }
                }
            }

            // 4. Caller-provided vector overlays (routes, airports, radar cells, lightning, SIGMETs, wind barbs)
            overlayDraw(projection)
        }

        // Floating Zoom & Fit-Route controls in bottom-end
        Surface(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(10.dp),
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.surface.copy(alpha = 0.90f),
            tonalElevation = 4.dp,
            shadowElevation = 4.dp,
        ) {
            Column(
                modifier = Modifier.border(
                    width = 0.5.dp,
                    color = MaterialTheme.colorScheme.outlineVariant,
                    shape = RoundedCornerShape(10.dp),
                ),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .clickable { cameraState.zoomByFactor(1.45f) },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = Icons.Default.Add,
                        contentDescription = "Zoom in",
                        modifier = Modifier.size(18.dp),
                    )
                }
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .clickable { cameraState.zoomByFactor(1f / 1.45f) },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = Icons.Default.Remove,
                        contentDescription = "Zoom out",
                        modifier = Modifier.size(18.dp),
                    )
                }
                if (defaultBounds != null) {
                    Box(
                        modifier = Modifier
                            .size(34.dp)
                            .clickable { cameraState.fitBounds(defaultBounds) },
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            imageVector = Icons.Default.CenterFocusStrong,
                            contentDescription = "Fit route",
                            modifier = Modifier.size(17.dp),
                        )
                    }
                }
            }
        }
    }
}

// ============================================================================
// Tile Math & Offline Vector Graticule / Coastline Fallback
// ============================================================================

private fun lonToTileX(lon: Double, nTiles: Int): Int {
    return floor(((lon + 180.0) / 360.0) * nTiles).toInt()
}

private fun latToTileY(lat: Double, nTiles: Int): Int {
    val clamped = lat.coerceIn(-85.0, 85.0)
    val rad = clamped * PI / 180.0
    val y = (1.0 - ln(tan(rad) + 1.0 / cos(rad)) / PI) / 2.0 * nTiles
    return floor(y).toInt()
}

private fun tileXToLon(x: Int, nTiles: Int): Double {
    return x.toDouble() / nTiles.toDouble() * 360.0 - 180.0
}

private fun tileYToLat(y: Int, nTiles: Int): Double {
    val n = PI - 2.0 * PI * y.toDouble() / nTiles.toDouble()
    return 180.0 / PI * atan(0.5 * (exp(n) - exp(-n)))
}

private fun fetchTileBitmap(urlString: String, cacheDir: java.io.File?): ImageBitmap? {
    return runCatching {
        val client = MapTileCache.httpClient(cacheDir)
        val request = okhttp3.Request.Builder()
            .url(urlString)
            .header("User-Agent", "FlyFunWeather-Android/1.5 (https://weather.flyfun.aero)")
            .header("Referer", "https://weather.flyfun.aero/")
            .build()
        client.newCall(request).execute().use { response ->
            if (response.isSuccessful) {
                val bytes = response.body?.bytes() ?: return null
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            } else {
                null
            }
        }
    }.getOrNull()
}

/**
 * Simplified European landmass polygons so the map always shows recognizable geographic
 * context even in offline / airplane mode or before tiles finish downloading.
 */
private val EUROPE_LANDMASS_RINGS: List<List<LatLon>> = listOf(
    // Continental Europe simplified perimeter
    listOf(
        LatLon(36.0, -9.0), LatLon(43.3, -9.2), LatLon(43.5, -1.8),
        LatLon(48.4, -4.7), LatLon(49.6, -1.9), LatLon(51.0, 1.8),
        LatLon(53.4, 5.0), LatLon(54.5, 8.6), LatLon(57.7, 10.6),
        LatLon(54.4, 10.2), LatLon(54.0, 14.2), LatLon(54.8, 19.6),
        LatLon(60.0, 29.0), LatLon(47.0, 29.0), LatLon(44.2, 28.6),
        LatLon(41.0, 29.0), LatLon(38.0, 24.0), LatLon(39.0, 20.0),
        LatLon(45.5, 13.7), LatLon(44.4, 12.3), LatLon(40.0, 18.5),
        LatLon(38.2, 15.6), LatLon(41.2, 13.5), LatLon(44.4, 8.9),
        LatLon(43.3, 5.4), LatLon(42.4, 3.2), LatLon(36.6, -4.4),
        LatLon(36.0, -9.0),
    ),
    // Great Britain simplified perimeter
    listOf(
        LatLon(50.1, -5.2), LatLon(51.4, -3.2), LatLon(53.4, -3.0),
        LatLon(55.0, -4.9), LatLon(58.6, -3.1), LatLon(57.5, -1.8),
        LatLon(55.0, -1.4), LatLon(52.8, 1.7), LatLon(51.3, 1.4),
        LatLon(50.7, -1.1), LatLon(50.1, -5.2),
    ),
    // Ireland simplified perimeter
    listOf(
        LatLon(51.5, -9.8), LatLon(53.3, -10.0), LatLon(55.3, -7.4),
        LatLon(54.6, -5.5), LatLon(52.2, -6.4), LatLon(51.5, -9.8),
    ),
)

private fun DrawScope.drawFallbackBasemap(
    projection: SlippyMapProjection,
    isDark: Boolean,
) {
    val waterColor = if (isDark) Color(0xFF0B1528) else Color(0xFFD6E6F5)
    val landColor = if (isDark) Color(0xFF172235) else Color(0xFFEDF2F7)
    val coastStroke = if (isDark) Color(0xFF2D4263) else Color(0xFF94A3B8)
    val gridColor = if (isDark) Color(0xFF1E293B) else Color(0xFFCBD5E1)

    drawRect(color = waterColor)

    for (ring in EUROPE_LANDMASS_RINGS) {
        val path = Path()
        ring.forEachIndexed { idx, pt ->
            val p = projection.latLonToScreen(pt.lat, pt.lon)
            if (idx == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
        }
        path.close()
        drawPath(path = path, color = landColor)
        drawPath(path = path, color = coastStroke, style = Stroke(width = 1.4f))
    }

    // Draw 5-degree lat/lon graticule lines
    val bounds = projection.visibleBounds()
    val startLon = (floor(bounds.minLon / 5.0) * 5.0).toInt()
    val endLon = (floor(bounds.maxLon / 5.0) * 5.0).toInt()
    for (lon in startLon..endLon step 5) {
        val top = projection.latLonToScreen(bounds.maxLat, lon.toDouble())
        val bot = projection.latLonToScreen(bounds.minLat, lon.toDouble())
        drawLine(
            color = gridColor,
            start = Offset(top.x, 0f),
            end = Offset(bot.x, size.height),
            strokeWidth = 0.8f,
        )
    }

    val startLat = (floor(bounds.minLat / 5.0) * 5.0).toInt()
    val endLat = (floor(bounds.maxLat / 5.0) * 5.0).toInt()
    for (lat in startLat..endLat step 5) {
        val left = projection.latLonToScreen(lat.toDouble(), bounds.minLon)
        val right = projection.latLonToScreen(lat.toDouble(), bounds.maxLon)
        drawLine(
            color = gridColor,
            start = Offset(0f, left.y),
            end = Offset(size.width, right.y),
            strokeWidth = 0.8f,
        )
    }
}

// ============================================================================
// Reusable Vector Map Overlay Drawing Helpers
// ============================================================================

/**
 * Draw a meteorological wind barb at [center] pointing into the wind direction ([windDirDeg]).
 */
fun DrawScope.drawWindBarbOnMap(
    center: Offset,
    windSpeedKt: Double,
    windDirDeg: Double,
    color: Color,
    staffLengthPx: Float = 26f,
) {
    if (windSpeedKt < 2.5) {
        drawCircle(
            color = color,
            radius = 4f,
            center = center,
            style = Stroke(width = 1.6f),
        )
        return
    }

    rotate(degrees = windDirDeg.toFloat(), pivot = center) {
        val tip = Offset(center.x, center.y - staffLengthPx)
        drawLine(
            color = color,
            start = center,
            end = tip,
            strokeWidth = 2.0f,
            cap = StrokeCap.Round,
        )

        var remaining = ((windSpeedKt + 2.5) / 5.0).toInt() * 5
        var cursorY = tip.y
        val barbSpanX = 9.5f
        val barbSlantY = 3.5f
        val stepY = 4.5f

        while (remaining >= 50) {
            val path = Path().apply {
                moveTo(center.x, cursorY)
                lineTo(center.x + barbSpanX, cursorY - barbSlantY)
                lineTo(center.x, cursorY + stepY)
                close()
            }
            drawPath(path = path, color = color)
            cursorY += stepY + 1.5f
            remaining -= 50
        }

        while (remaining >= 10) {
            drawLine(
                color = color,
                start = Offset(center.x, cursorY),
                end = Offset(center.x + barbSpanX, cursorY - barbSlantY),
                strokeWidth = 1.8f,
                cap = StrokeCap.Round,
            )
            cursorY += stepY
            remaining -= 10
        }

        if (remaining >= 5) {
            val startY = if (cursorY == tip.y) cursorY + stepY * 0.7f else cursorY
            drawLine(
                color = color,
                start = Offset(center.x, startY),
                end = Offset(center.x + barbSpanX * 0.55f, startY - barbSlantY * 0.55f),
                strokeWidth = 1.8f,
                cap = StrokeCap.Round,
            )
        }
    }
}

/**
 * Draw a frontal boundary crossing the route at [center] perpendicular to [routeBearingDeg].
 */
fun DrawScope.drawFrontSymbolOnMap(
    center: Offset,
    routeBearingDeg: Double,
    frontType: String,
    lengthPx: Float = 38f,
) {
    val isCold = frontType.lowercase(Locale.US).contains("cold")
    val isWarm = frontType.lowercase(Locale.US).contains("warm")
    val color = when {
        isCold -> Color(0xFF3B82F6)
        isWarm -> Color(0xFFEF4444)
        else -> Color(0xFFA855F7) // Occluded / stationary
    }

    val perpAngleDeg = (routeBearingDeg + 90.0).toFloat()
    rotate(degrees = perpAngleDeg, pivot = center) {
        val left = Offset(center.x - lengthPx / 2f, center.y)
        val right = Offset(center.x + lengthPx / 2f, center.y)
        drawLine(
            color = color,
            start = left,
            end = right,
            strokeWidth = 3.2f,
            cap = StrokeCap.Round,
        )
        // Draw triangles (cold) or semicircles (warm) along the front line
        for (i in -1..1) {
            val sx = center.x + i * 11f
            if (isCold || (!isWarm && i % 2 == 0)) {
                val tri = Path().apply {
                    moveTo(sx - 4.5f, center.y)
                    lineTo(sx + 4.5f, center.y)
                    lineTo(sx, center.y - 7.5f)
                    close()
                }
                drawPath(path = tri, color = color)
            } else {
                drawCircle(
                    color = color,
                    radius = 4.2f,
                    center = Offset(sx, center.y - 2f),
                )
            }
        }
    }
}

/**
 * Draw an observed storm cell polygon (`CellDisplay`) and its forward motion vector arrow.
 */
fun DrawScope.drawStormCellOverlay(
    polygonLatLon: List<LatLon>,
    centroid: LatLon,
    severity: String,
    motionEndLatLon: LatLon?,
    projection: SlippyMapProjection,
) {
    val baseColor = when (severity.lowercase(Locale.US)) {
        "severe", "high", "extreme" -> Color(0xFFEF4444)
        "moderate", "medium" -> Color(0xFFF97316)
        else -> Color(0xFFFACC15)
    }

    if (polygonLatLon.size >= 3) {
        val path = Path()
        polygonLatLon.forEachIndexed { idx, pt ->
            val screen = projection.latLonToScreen(pt.lat, pt.lon)
            if (idx == 0) path.moveTo(screen.x, screen.y) else path.lineTo(screen.x, screen.y)
        }
        path.close()
        drawPath(path = path, color = baseColor.copy(alpha = 0.36f))
        drawPath(path = path, color = baseColor, style = Stroke(width = 2.4f))
    } else {
        val cScreen = projection.latLonToScreen(centroid.lat, centroid.lon)
        drawCircle(color = baseColor.copy(alpha = 0.4f), radius = 12f, center = cScreen)
        drawCircle(color = baseColor, radius = 12f, center = cScreen, style = Stroke(width = 2.2f))
    }

    if (motionEndLatLon != null) {
        val start = projection.latLonToScreen(centroid.lat, centroid.lon)
        val end = projection.latLonToScreen(motionEndLatLon.lat, motionEndLatLon.lon)
        drawLine(
            color = baseColor,
            start = start,
            end = end,
            strokeWidth = 2.5f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f)),
        )
        drawCircle(color = baseColor, radius = 3.5f, center = end)
    }
}

/**
 * Draw a lightning flash bolt marker at [center].
 */
fun DrawScope.drawLightningFlashMarker(
    center: Offset,
    ageFactor: Float = 1f,
) {
    val boltColor = Color(0xFFFACC15).copy(alpha = ageFactor.coerceIn(0.35f, 1f))
    val glowColor = Color(0xFFF59E0B).copy(alpha = (ageFactor * 0.4f).coerceIn(0.15f, 0.5f))
    drawCircle(color = glowColor, radius = 8f, center = center)
    val path = Path().apply {
        moveTo(center.x + 1.5f, center.y - 6.5f)
        lineTo(center.x - 3.5f, center.y + 0.5f)
        lineTo(center.x + 0.5f, center.y + 0.5f)
        lineTo(center.x - 1.5f, center.y + 6.5f)
        lineTo(center.x + 3.5f, center.y - 0.5f)
        lineTo(center.x - 0.5f, center.y - 0.5f)
        close()
    }
    drawPath(path = path, color = boltColor)
}

/**
 * Draw a SIGMET / AIRMET polygon on the map with dashed amber/red border and hazard label.
 */
fun DrawScope.drawSigmetPolygonOnMap(
    polygonLatLon: List<LatLon>,
    label: String,
    isSevere: Boolean,
    projection: SlippyMapProjection,
) {
    if (polygonLatLon.size < 3) return
    val strokeColor = if (isSevere) Color(0xFFEF4444) else Color(0xFFF59E0B)
    val fillColor = strokeColor.copy(alpha = 0.14f)

    val path = Path()
    var sumX = 0f
    var sumY = 0f
    polygonLatLon.forEachIndexed { idx, pt ->
        val p = projection.latLonToScreen(pt.lat, pt.lon)
        sumX += p.x
        sumY += p.y
        if (idx == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
    }
    path.close()

    drawPath(path = path, color = fillColor)
    drawPath(
        path = path,
        color = strokeColor,
        style = Stroke(
            width = 2.2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 6f)),
            join = StrokeJoin.Round,
        ),
    )

    val cx = sumX / polygonLatLon.size
    val cy = sumY / polygonLatLon.size
    val paint = android.graphics.Paint().apply {
        isAntiAlias = true
        color = android.graphics.Color.WHITE
        textSize = 24f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
        textAlign = android.graphics.Paint.Align.CENTER
    }
    drawContext.canvas.nativeCanvas.drawText(label, cx, cy, paint)
}

/**
 * Draw an airport or waypoint pin with optional ICAO callout pill.
 */
fun DrawScope.drawAirportPinOnMap(
    center: Offset,
    radiusPx: Float,
    fillColor: Color,
    isSelected: Boolean = false,
    isAlternate: Boolean = false,
    label: String? = null,
) {
    if (isSelected) {
        drawCircle(
            color = Color.White.copy(alpha = 0.35f),
            radius = radiusPx + 7f,
            center = center,
        )
        drawCircle(
            color = fillColor,
            radius = radiusPx + 4f,
            center = center,
            style = Stroke(width = 2.5f),
        )
    }

    if (isAlternate) {
        // Diamond marker for alternates
        val r = radiusPx * 1.25f
        val diamond = Path().apply {
            moveTo(center.x, center.y - r)
            lineTo(center.x + r, center.y)
            lineTo(center.x, center.y + r)
            lineTo(center.x - r, center.y)
            close()
        }
        drawPath(path = diamond, color = fillColor)
        drawPath(path = diamond, color = Color.White, style = Stroke(width = 2f))
    } else {
        drawCircle(
            color = fillColor,
            radius = radiusPx,
            center = center,
        )
        drawCircle(
            color = Color(0xFF0F172A),
            radius = radiusPx,
            center = center,
            style = Stroke(width = if (isSelected) 2.6f else 1.6f),
        )
    }

    if (!label.isNullOrBlank()) {
        val textPaint = android.graphics.Paint().apply {
            isAntiAlias = true
            color = android.graphics.Color.WHITE
            textSize = 25f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            textAlign = android.graphics.Paint.Align.CENTER
            setShadowLayer(4f, 0f, 1f, android.graphics.Color.BLACK)
        }
        drawContext.canvas.nativeCanvas.drawText(
            label,
            center.x,
            center.y - radiusPx - 8f,
            textPaint,
        )
    }
}
