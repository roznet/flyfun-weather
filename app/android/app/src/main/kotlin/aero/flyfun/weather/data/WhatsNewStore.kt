package aero.flyfun.weather.data

import android.content.Context
import android.content.SharedPreferences
import aero.flyfun.weather.model.SystemMessageResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Manages "What's New" system messages and tracks which message IDs the user has seen.
 * Mirrors `WhatsNewStore` on iOS, persisting `lastSeenMessageId` in [SharedPreferences]
 * (with an in-memory fallback for JVM unit tests) and syncing with `/api/messages`.
 */
class WhatsNewStore(
    context: Context? = null,
    private val prefs: SharedPreferences? = context?.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE),
) {
    private val _unseenCount = MutableStateFlow(0)
    val unseenCount: StateFlow<Int> = _unseenCount.asStateFlow()

    private val _messages = MutableStateFlow<List<SystemMessageResponse>>(emptyList())
    val messages: StateFlow<List<SystemMessageResponse>> = _messages.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private var inMemoryLastSeenId: Int = 0

    var lastSeenId: Int
        get() = prefs?.getInt(KEY_LAST_SEEN_ID, inMemoryLastSeenId) ?: inMemoryLastSeenId
        private set(value) {
            inMemoryLastSeenId = value
            prefs?.edit()?.putInt(KEY_LAST_SEEN_ID, value)?.apply()
        }

    /**
     * Filters [allMessages] to those with `id > lastSeenId`.
     */
    fun unreadMessages(allMessages: List<SystemMessageResponse> = _messages.value): List<SystemMessageResponse> {
        val seen = lastSeenId
        return allMessages.filter { it.id > seen }
    }

    /**
     * Marks messages up to [upToId] as seen locally and updates [unseenCount].
     */
    fun markSeenLocally(upToId: Int) {
        if (upToId > lastSeenId) {
            lastSeenId = upToId
        }
        _unseenCount.value = unreadMessages().size
    }

    /**
     * Polls `/api/messages/status` for the badge count.
     */
    suspend fun checkStatus(api: ApiClient) {
        runCatching {
            val status = api.getMessagesStatus()
            _unseenCount.value = status.unseenCount
        }
    }

    /**
     * Loads all messages from `/api/messages` and updates [messages] and [unseenCount].
     */
    suspend fun loadMessages(api: ApiClient) {
        _isLoading.value = true
        try {
            val list = api.listSystemMessages()
            _messages.value = list
            _unseenCount.value = unreadMessages(list).size
        } catch (_: Throwable) {
            // Keep existing messages on transient failure
        } finally {
            _isLoading.value = false
        }
    }

    /**
     * Marks all currently loaded messages (or up to [upToId]) as seen both locally and on the server.
     */
    suspend fun markAllSeen(api: ApiClient, upToId: Int? = _messages.value.maxOfOrNull { it.id }) {
        if (upToId != null) {
            markSeenLocally(upToId)
        } else {
            _unseenCount.value = 0
        }
        runCatching {
            api.markMessagesSeen(upToId)
        }
    }

    companion object {
        const val PREFS_NAME = "flyfun_whats_new"
        const val KEY_LAST_SEEN_ID = "last_seen_message_id"
    }
}
