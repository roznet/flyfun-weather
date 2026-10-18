package aero.flyfun.weather.data

import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.PirepResponse
import aero.flyfun.weather.model.SubmitPirepRequest
import aero.flyfun.weather.model.WbJson
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/**
 * Disk-persisted offline PIREP queue entry stored in `<filesDir>/pending_pireps.json`.
 */
@Serializable
data class QueuedPirep(
    val clientUuid: String,
    val queuedAtEpochMs: Long,
    val attempts: Int = 0,
    val lastError: String? = null,
    val request: SubmitPirepRequest,
)

/**
 * Summary of a [PirepOfflineStore.flushAll] run.
 */
data class PirepFlushResult(
    val succeeded: List<PirepResponse>,
    val remaining: List<QueuedPirep>,
    val droppedPermanentFailures: List<QueuedPirep>,
)

/**
 * Disk-backed offline queue for Pilot Weather Reports (`<filesDir>/pending_pireps.json`).
 *
 * Ensures every queued PIREP carries a stable `clientUuid` (`client_uuid` on wire) and original
 * `observedAt` timestamp so delayed submissions are idempotent on the server and attributed to
 * the actual observation time rather than upload time.
 *
 * Mirrors iOS `PirepOfflineStore.swift`.
 */
class PirepOfflineStore(
    private val filesDir: File,
) {
    private val mutex = Mutex()
    private val queueFile = File(filesDir, QUEUE_FILENAME)

    private val _pendingFlow = MutableStateFlow<List<QueuedPirep>>(emptyList())
    val pendingFlow: StateFlow<List<QueuedPirep>> = _pendingFlow.asStateFlow()

    val pendingCount: Int
        get() = _pendingFlow.value.size

    init {
        filesDir.mkdirs()
        _pendingFlow.value = readFromDisk()
    }

    /**
     * Current snapshot of queued PIREPs in FIFO order.
     */
    fun pendingItems(): List<QueuedPirep> = _pendingFlow.value

    /**
     * Enqueue a PIREP for offline persistence and eventual upload.
     *
     * Deduplicates by `clientUuid`: if an item with the same `clientUuid` is already queued,
     * its payload is updated in-place without creating a duplicate entry.
     */
    suspend fun enqueue(
        request: SubmitPirepRequest,
        clientUuid: String = request.clientUuid.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString(),
        nowEpochMs: Long = System.currentTimeMillis(),
    ): QueuedPirep = mutex.withLock {
        val normalizedObservationTime = request.observedAt.takeIf { it.isNotBlank() }
            ?: IsoTime.formatUtc(nowEpochMs)
        val normalizedRequest = request.copy(
            clientUuid = clientUuid,
            observedAt = normalizedObservationTime,
        )

        val current = _pendingFlow.value.toMutableList()
        val existingIdx = current.indexOfFirst { it.clientUuid == clientUuid }
        val entry = if (existingIdx >= 0) {
            val prev = current[existingIdx]
            prev.copy(request = normalizedRequest)
        } else {
            QueuedPirep(
                clientUuid = clientUuid,
                queuedAtEpochMs = nowEpochMs,
                attempts = 0,
                lastError = null,
                request = normalizedRequest,
            )
        }

        if (existingIdx >= 0) {
            current[existingIdx] = entry
        } else {
            current.add(entry)
        }

        writeToDisk(current)
        _pendingFlow.value = current.toList()
        entry
    }

    /**
     * Remove a specific queued PIREP by its `clientUuid`.
     */
    suspend fun remove(clientUuid: String): Boolean = mutex.withLock {
        val current = _pendingFlow.value
        val updated = current.filterNot { it.clientUuid == clientUuid }
        if (updated.size == current.size) return@withLock false
        writeToDisk(updated)
        _pendingFlow.value = updated
        true
    }

    /**
     * Clear all queued PIREPs from memory and disk.
     */
    suspend fun clear() = mutex.withLock {
        writeToDisk(emptyList())
        _pendingFlow.value = emptyList()
    }

    /**
     * Reload the queue from `<filesDir>/pending_pireps.json`.
     */
    suspend fun reload(): List<QueuedPirep> = mutex.withLock {
        val loaded = readFromDisk()
        _pendingFlow.value = loaded
        loaded
    }

    /**
     * Attempt to submit a PIREP immediately via `submitter`; if a transient/network error occurs,
     * automatically enqueue it into `<filesDir>/pending_pireps.json` and return `null`.
     * Permanent 4xx validation errors are rethrown so the UI can surface them.
     */
    suspend fun submitOrEnqueue(
        request: SubmitPirepRequest,
        nowEpochMs: Long = System.currentTimeMillis(),
        submitter: suspend (SubmitPirepRequest) -> PirepResponse,
    ): PirepResponse? {
        val uuid = request.clientUuid.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString()
        val obsIso = request.observedAt.takeIf { it.isNotBlank() } ?: IsoTime.formatUtc(nowEpochMs)
        val stamped = request.copy(clientUuid = uuid, observedAt = obsIso)

        return try {
            val response = submitter(stamped)
            // Also remove if it was previously queued
            remove(uuid)
            response
        } catch (err: Throwable) {
            if (isPermanentClientError(err)) {
                throw err
            }
            enqueue(stamped, clientUuid = uuid, nowEpochMs = nowEpochMs)
            null
        }
    }

    /**
     * Flush all pending PIREPs in FIFO order using `submitter`.
     *
     * - Successful submissions are removed from disk and returned in [PirepFlushResult.succeeded].
     * - Permanent 4xx errors (`400`, `403`, `404`, `409`, `422`) are dropped from the queue so they
     *   cannot poison subsequent flushes, and are returned in [PirepFlushResult.droppedPermanentFailures].
     * - Transient/network/5xx errors remain in the queue with incremented `attempts` and updated `lastError`.
     */
    suspend fun flushAll(
        submitter: suspend (SubmitPirepRequest) -> PirepResponse,
    ): PirepFlushResult = mutex.withLock {
        val snapshot = _pendingFlow.value
        if (snapshot.isEmpty()) {
            return@withLock PirepFlushResult(
                succeeded = emptyList(),
                remaining = emptyList(),
                droppedPermanentFailures = emptyList(),
            )
        }

        val succeeded = mutableListOf<PirepResponse>()
        val remaining = mutableListOf<QueuedPirep>()
        val dropped = mutableListOf<QueuedPirep>()

        for (item in snapshot) {
            try {
                val res = submitter(item.request)
                succeeded.add(res)
            } catch (err: Throwable) {
                if (isPermanentClientError(err)) {
                    dropped.add(
                        item.copy(
                            attempts = item.attempts + 1,
                            lastError = err.message ?: err.javaClass.simpleName,
                        ),
                    )
                } else {
                    remaining.add(
                        item.copy(
                            attempts = item.attempts + 1,
                            lastError = err.message ?: err.javaClass.simpleName,
                        ),
                    )
                }
            }
        }

        writeToDisk(remaining)
        _pendingFlow.value = remaining.toList()

        PirepFlushResult(
            succeeded = succeeded,
            remaining = remaining,
            droppedPermanentFailures = dropped,
        )
    }

    /**
     * Convenience overload flushing via [BriefingRepository.submitPirep].
     */
    suspend fun flushAll(repository: BriefingRepository): PirepFlushResult =
        flushAll { req -> repository.submitPirep(req) }

    /**
     * Convenience overload flushing via [ApiClient.submitPirep].
     */
    suspend fun flushAll(apiClient: ApiClient): PirepFlushResult =
        flushAll { req -> apiClient.submitPirep(req) }

    private fun readFromDisk(): List<QueuedPirep> {
        if (!queueFile.exists()) return emptyList()
        return runCatching {
            val raw = queueFile.readText(Charsets.UTF_8)
            if (raw.isBlank()) emptyList()
            else WbJson.decodeFromString(ListSerializer(QueuedPirep.serializer()), raw)
        }.getOrElse { emptyList() }
    }

    private fun writeToDisk(items: List<QueuedPirep>) {
        filesDir.mkdirs()
        if (items.isEmpty()) {
            if (queueFile.exists()) {
                queueFile.delete()
            }
            return
        }
        val payload = WbJson.encodeToString(ListSerializer(QueuedPirep.serializer()), items)
        val tmpFile = File(filesDir, "$QUEUE_FILENAME.tmp")
        tmpFile.writeText(payload, Charsets.UTF_8)
        runCatching {
            Files.move(
                tmpFile.toPath(),
                queueFile.toPath(),
                StandardCopyOption.REPLACE_EXISTING,
                StandardCopyOption.ATOMIC_MOVE,
            )
        }.onFailure {
            tmpFile.copyTo(queueFile, overwrite = true)
            tmpFile.delete()
        }
    }

    companion object {
        const val QUEUE_FILENAME: String = "pending_pireps.json"

        /**
         * Returns `true` if `error` represents a permanent 4xx validation/client failure
         * (`400`, `403`, `404`, `409`, `422`) that will never succeed on retry.
         *
         * Transient errors (`408`, `429`, `5xx`, `NetworkError`, `Unauthorized` awaiting token refresh)
         * return `false` so the PIREP stays queued on disk.
         */
        fun isPermanentClientError(error: Throwable): Boolean = when (error) {
            is ApiError.Forbidden,
            is ApiError.NotFound -> true
            is ApiError.ServerError -> {
                val code = error.statusCode
                code in 400..499 && code != 401 && code != 408 && code != 429
            }
            else -> false
        }
    }
}
