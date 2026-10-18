package aero.flyfun.weather.ui.briefing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.ZoomIn
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import aero.flyfun.weather.viewmodel.BriefingViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * GRAMET cross-section on the Discussion tab (mirrors `GrametSectionView.swift`):
 * - Renders `grametDocument` bytes using [PdfRenderer] when starting with `%PDF`,
 *   or [BitmapFactory] when PNG/image.
 * - Opens a pinch-to-zoom fullscreen dialog on tap.
 */
@Composable
fun GrametSection(
    viewModel: BriefingViewModel,
    modifier: Modifier = Modifier,
) {
    val selectedPack by viewModel.selectedPack.collectAsState()
    val context = LocalContext.current

    var state by remember(selectedPack?.fetchTimestamp) {
        mutableStateOf<GrametLoadState>(GrametLoadState.Loading)
    }
    var showZoomDialog by remember { mutableStateOf(false) }

    LaunchedEffect(selectedPack?.fetchTimestamp) {
        state = GrametLoadState.Loading
        val bytes = viewModel.loadGrametDocument()
        if (bytes == null || bytes.size < 4) {
            state = GrametLoadState.Failed
            return@LaunchedEffect
        }
        val bitmap = withContext(Dispatchers.IO) {
            decodeGrametBytes(context, bytes)
        }
        state = if (bitmap != null) {
            GrametLoadState.Loaded(bitmap)
        } else {
            GrametLoadState.Failed
        }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = "GRAMET",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )

        when (val current = state) {
            is GrametLoadState.Loading -> {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 160.dp),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                ) {
                    Box(contentAlignment = Alignment.Center, modifier = Modifier.padding(32.dp)) {
                        CircularProgressIndicator()
                    }
                }
            }
            is GrametLoadState.Loaded -> {
                val bitmap = current.bitmap
                val ratio = if (bitmap.height > 0) {
                    bitmap.width.toFloat() / bitmap.height.toFloat()
                } else {
                    1.6f
                }
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(ratio)
                        .clip(RoundedCornerShape(8.dp))
                        .clickable { showZoomDialog = true },
                ) {
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "GRAMET cross-section",
                        contentScale = ContentScale.Fit,
                        modifier = Modifier.fillMaxSize(),
                    )
                    Surface(
                        color = Color.Black.copy(alpha = 0.45f),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(8.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Default.ZoomIn,
                                contentDescription = "Tap to zoom GRAMET",
                                tint = Color.White,
                                modifier = Modifier.size(14.dp),
                            )
                            Text(
                                text = "Zoom",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color.White,
                            )
                        }
                    }
                }
            }
            is GrametLoadState.Failed -> {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 100.dp),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
                    border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.CloudOff,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = "GRAMET couldn't be loaded — it isn't saved on this device. Connect to load it.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }

        Text(
            text = "GRAMET cross-section from Autorouter",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    val loadedBitmap = (state as? GrametLoadState.Loaded)?.bitmap
    if (showZoomDialog && loadedBitmap != null) {
        ZoomableChartDialog(
            title = "GRAMET",
            caption = "GRAMET cross-section from Autorouter",
            bitmap = loadedBitmap,
            overlayEntry = null,
            onDismiss = { showZoomDialog = false },
        )
    }
}

private sealed interface GrametLoadState {
    data object Loading : GrametLoadState
    data class Loaded(val bitmap: Bitmap) : GrametLoadState
    data object Failed : GrametLoadState
}

internal fun isPdfBytes(bytes: ByteArray): Boolean {
    return bytes.size >= 4 &&
        bytes[0] == 0x25.toByte() && // '%'
        bytes[1] == 0x50.toByte() && // 'P'
        bytes[2] == 0x44.toByte() && // 'D'
        bytes[3] == 0x46.toByte()    // 'F'
}

private fun decodeGrametBytes(context: Context, bytes: ByteArray): Bitmap? {
    if (isPdfBytes(bytes)) {
        val tempFile = File.createTempFile("gramet_", ".pdf", context.cacheDir)
        try {
            tempFile.writeBytes(bytes)
            ParcelFileDescriptor.open(tempFile, ParcelFileDescriptor.MODE_READ_ONLY).use { pfd ->
                PdfRenderer(pfd).use { renderer ->
                    if (renderer.pageCount <= 0) return null
                    renderer.openPage(0).use { page ->
                        val scale = 2
                        val width = (page.width * scale).coerceAtLeast(1)
                        val height = (page.height * scale).coerceAtLeast(1)
                        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
                        bitmap.eraseColor(android.graphics.Color.WHITE)
                        page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                        return bitmap
                    }
                }
            }
        } catch (_: Exception) {
            return null
        } finally {
            runCatching { tempFile.delete() }
        }
    }
    return runCatching {
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
    }.getOrNull()
}
