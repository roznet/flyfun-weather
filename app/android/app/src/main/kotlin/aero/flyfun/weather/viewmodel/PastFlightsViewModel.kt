package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.model.PastArchiveFacets
import aero.flyfun.weather.model.PastArchiveRow
import aero.flyfun.weather.model.PastFilter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The Past flights screen (#1032): search + chips over `GET /api/flights/archive`. Filtering,
 * paging and every count are the server's; this only holds the filter, the loaded rows and the
 * page cursor. Mirrors iOS `PastFlightsViewModel`.
 */
class PastFlightsViewModel(
    private val repository: BriefingRepository,
    initialFilter: PastFilter = PastFilter(),
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    data class MonthSection(val title: String, val rows: List<PastArchiveRow>)

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    private val _rows = MutableStateFlow<List<PastArchiveRow>>(emptyList())
    val rows: StateFlow<List<PastArchiveRow>> = _rows.asStateFlow()

    private val _total = MutableStateFlow(0)
    val total: StateFlow<Int> = _total.asStateFlow()

    private val _facets = MutableStateFlow<PastArchiveFacets?>(null)
    val facets: StateFlow<PastArchiveFacets?> = _facets.asStateFlow()

    private val _filter = MutableStateFlow(initialFilter)
    val filter: StateFlow<PastFilter> = _filter.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    /** Newest request wins: a page or filter change that lands late is dropped. */
    private var loadToken = 0
    private var searchJob: Job? = null

    val hasMore: Boolean get() = _rows.value.size < _total.value

    fun reload() {
        val token = ++loadToken
        _isLoading.value = true
        val query = _filter.value.queryItems(limit = PAGE_SIZE, offset = 0)
        scope.launch {
            try {
                val resp = repository.pastArchive(query)
                if (token != loadToken) return@launch
                _rows.value = resp.rows
                _total.value = resp.total
                _facets.value = resp.facets
                _error.value = null
            } catch (e: Exception) {
                if (token != loadToken) return@launch
                _error.value = e.message ?: "Couldn't load past flights"
            } finally {
                if (token == loadToken) _isLoading.value = false
            }
        }
    }

    /** Next page when [row] is the last one loaded. */
    fun loadMoreIfNeeded(row: PastArchiveRow) {
        if (row.id != _rows.value.lastOrNull()?.id || !hasMore || _isLoading.value) return
        val token = ++loadToken
        _isLoading.value = true
        val query = _filter.value.queryItems(limit = PAGE_SIZE, offset = _rows.value.size)
        scope.launch {
            try {
                val resp = repository.pastArchive(query)
                if (token != loadToken) return@launch
                val seen = _rows.value.mapTo(HashSet()) { it.id }
                _rows.value = _rows.value + resp.rows.filterNot { it.id in seen }
                _total.value = resp.total
                _facets.value = resp.facets
                _error.value = null
            } catch (e: Exception) {
                if (token != loadToken) return@launch
                _error.value = e.message ?: "Couldn't load past flights"
            } finally {
                if (token == loadToken) _isLoading.value = false
            }
        }
    }

    fun update(change: (PastFilter) -> PastFilter) {
        val next = change(_filter.value)
        if (next == _filter.value) return
        _filter.value = next
        reload()
    }

    fun toggleTag(tag: String) = update { it.copy(tags = PastFilter.toggled(it.tags, tag)) }
    fun toggleOutcome(key: String) = update { it.copy(outcomes = PastFilter.toggled(it.outcomes, key)) }

    /** Debounced: each keystroke would otherwise be a request. */
    fun setQuery(query: String, debounceMs: Long = 300) {
        searchJob?.cancel()
        searchJob = scope.launch {
            delay(debounceMs)
            update { it.copy(query = query) }
        }
    }

    companion object {
        const val PAGE_SIZE = 30

        private val MONTH_FMT: DateTimeFormatter =
            DateTimeFormatter.ofPattern("LLLL yyyy", Locale.getDefault()).withZone(ZoneOffset.UTC)

        /** Rows grouped by month (newest first, as served) for the filtered view. */
        fun monthSections(rows: List<PastArchiveRow>): List<MonthSection> {
            val out = mutableListOf<MonthSection>()
            for (row in rows) {
                val title = row.departureEpochMs?.let { MONTH_FMT.format(Instant.ofEpochMilli(it)) } ?: ""
                val last = out.lastOrNull()
                if (last != null && last.title == title) {
                    out[out.size - 1] = last.copy(rows = last.rows + row)
                } else {
                    out += MonthSection(title, listOf(row))
                }
            }
            return out
        }
    }
}
