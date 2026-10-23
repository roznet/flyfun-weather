package aero.flyfun.weather.data

import aero.flyfun.weather.model.LivePositionFix
import aero.flyfun.weather.model.WbJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Fixes recorded for one flight that the server has not accepted yet; `landed` is a landing still to send. */
@Serializable
data class PendingFixQueue(
    @SerialName("flight_id")
    val flightId: String,
    val fixes: List<LivePositionFix> = emptyList(),
    val landed: Boolean = false,
)

/** The tracking session, so a service restarted by Android after a process kill keeps recording for it. */
@Serializable
data class TrackingSession(
    @SerialName("flight_id")
    val flightId: String,
    val waypoints: List<List<Double>> = emptyList(),
    @SerialName("end_epoch_ms")
    val endEpochMs: Long? = null,
)

@Serializable
private data class PendingFixFile(
    val session: TrackingSession? = null,
    val queues: List<PendingFixQueue> = emptyList(),
)

/**
 * Unsent live-position fixes on disk (`<filesDir>/pending_position_fixes.json`), per flight (#889).
 *
 * The in-flight queue used to live only in memory, so a process kill (low memory, the user
 * swiping the app away) lost every fix recorded while offline. The tracking service saves its
 * queue after each fix; whatever is left is uploaded on the next app start. A corrupt or
 * unreadable file reads as empty: losing a queue is better than failing to track.
 */
class PendingFixStore(filesDir: File) {
    private val file = File(filesDir, FILENAME)

    init {
        filesDir.mkdirs()
    }

    @Synchronized
    fun queues(): List<PendingFixQueue> = read().queues

    @Synchronized
    fun queue(flightId: String): PendingFixQueue? = read().queues.firstOrNull { it.flightId == flightId }

    /** Replace `flightId`'s queue; an empty queue with no landing to send is removed. */
    @Synchronized
    fun save(flightId: String, fixes: List<LivePositionFix>, landed: Boolean) {
        val current = read()
        val others = current.queues.filterNot { it.flightId == flightId }
        val queues = if (fixes.isEmpty() && !landed) others else others + PendingFixQueue(flightId, fixes.toList(), landed)
        write(current.copy(queues = queues))
    }

    @Synchronized
    fun session(): TrackingSession? = read().session

    @Synchronized
    fun saveSession(session: TrackingSession?) {
        write(read().copy(session = session))
    }

    /** Sign-out: the fixes belong to the previous user and would be refused with the next one's token. */
    @Synchronized
    fun clear() {
        file.delete()
    }

    private fun read(): PendingFixFile {
        if (!file.exists()) return PendingFixFile()
        return runCatching {
            WbJson.decodeFromString(PendingFixFile.serializer(), file.readText(Charsets.UTF_8))
        }.getOrDefault(PendingFixFile())
    }

    private fun write(content: PendingFixFile) {
        if (content.session == null && content.queues.isEmpty()) {
            file.delete()
            return
        }
        // Write then move, so a kill mid-write leaves the previous file rather than half a file.
        val tmp = File(file.parentFile, "$FILENAME.tmp")
        tmp.writeText(WbJson.encodeToString(PendingFixFile.serializer(), content), Charsets.UTF_8)
        runCatching {
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        }.onFailure {
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }

    companion object {
        const val FILENAME: String = "pending_position_fixes.json"
    }
}
