package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner

/**
 * Named [ViewModelStoreOwner]s for screens that outlive a recomposition but not their own
 * close: one per session (`session:<sessionKey>`) and one per open briefing
 * (`briefing:<sessionKey>:<flightId>`).
 *
 * Held in the activity `ViewModelStore`, so a rotation or dark-mode toggle keeps every owner
 * (and the ViewModels in it). [retainOnly] clears the owners no longer on screen, which runs
 * their ViewModels' `onCleared` and cancels their `viewModelScope` (pack loads, refresh polling).
 * Mirrors what a `NavHost` back-stack entry does, without moving the two-pane layouts to it.
 */
class ViewModelScopes : ViewModel() {

    private class ScopeOwner : ViewModelStoreOwner {
        override val viewModelStore = ViewModelStore()
    }

    private val owners = mutableMapOf<String, ScopeOwner>()

    val keys: Set<String> get() = owners.keys.toSet()

    /** The owner for [key], created on first use and kept until [clear] or [retainOnly] drops it. */
    fun owner(key: String): ViewModelStoreOwner = owners.getOrPut(key) { ScopeOwner() }

    /** Clear the owner for [key], if any; the next [owner] call for it starts empty. */
    fun clear(key: String) {
        owners.remove(key)?.viewModelStore?.clear()
    }

    /** Clear every owner whose key is not in [keep]. */
    fun retainOnly(keep: Set<String>) {
        (owners.keys - keep).forEach(::clear)
    }

    override fun onCleared() {
        retainOnly(emptySet())
    }

    companion object {
        fun sessionKey(sessionKey: String) = "session:$sessionKey"
        fun briefingKey(sessionKey: String, flightId: String) = "briefing:$sessionKey:$flightId"
    }
}
