package aero.flyfun.weather.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.CachingBriefingRepository
import aero.flyfun.weather.data.OnlineBriefingRepository
import aero.flyfun.weather.model.*
import aero.flyfun.weather.model.rules.DebriefSectionRules
import aero.flyfun.weather.service.FlightTrackingService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for the 5-tab Briefing viewer (`Advisory`, `Observed`, `Discussion`, `Cross-Section`, `Map`).
 * Mirrors `BriefingViewModel` on iOS.
 */
class BriefingViewModel(
    val repository: BriefingRepository,
    val flightId: String? = null,
    initialFlight: FlightResponse? = null,
    val helpStore: aero.flyfun.weather.data.HelpCatalogStore? = null,
    val apiClient: ApiClient? = (repository as? OnlineBriefingRepository)?.api
        ?: ((repository as? CachingBriefingRepository)?.online as? OnlineBriefingRepository)?.api,
    private val mainDispatcher: CoroutineDispatcher = Dispatchers.Main,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope = scopeOverride ?: viewModelScope

    private inline fun <T> runSuspendCatching(block: () -> T): Result<T> = try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (t: Throwable) {
        Result.failure(t)
    }

    private val _flight = MutableStateFlow<FlightResponse?>(initialFlight)
    val flight: StateFlow<FlightResponse?> = _flight.asStateFlow()

    private val _packs = MutableStateFlow<List<PackMetaResponse>>(emptyList())
    val packs: StateFlow<List<PackMetaResponse>> = _packs.asStateFlow()

    private val _selectedPack = MutableStateFlow<PackMetaResponse?>(null)
    val selectedPack: StateFlow<PackMetaResponse?> = _selectedPack.asStateFlow()

    private val _selectedTab = MutableStateFlow(BriefingTab.ADVISORY)
    val selectedTab: StateFlow<BriefingTab> = _selectedTab.asStateFlow()

    /**
     * Demo mode's simulated tracking (fixture repository only): one flag for the toolbar's Start
     * and the Observed tab's tracking bar, which both read it (#996).
     */
    val demoTrackingActive = MutableStateFlow(false)

    private val _availableTabs = MutableStateFlow(BriefingTab.tabs(showsObserved = false))
    val availableTabs: StateFlow<List<BriefingTab>> = _availableTabs.asStateFlow()

    private val _advisories = MutableStateFlow<AdvisoriesResponse?>(null)
    val advisories: StateFlow<AdvisoriesResponse?> = _advisories.asStateFlow()

    private val _digest = MutableStateFlow<DigestResponse?>(null)
    val digest: StateFlow<DigestResponse?> = _digest.asStateFlow()

    private val _snapshot = MutableStateFlow<SnapshotResponse?>(null)
    val snapshot: StateFlow<SnapshotResponse?> = _snapshot.asStateFlow()

    private val _routeAnalyses = MutableStateFlow<RouteAnalysesResponse?>(null)
    val routeAnalyses: StateFlow<RouteAnalysesResponse?> = _routeAnalyses.asStateFlow()

    private val _elevation = MutableStateFlow<ElevationResponse?>(null)
    val elevation: StateFlow<ElevationResponse?> = _elevation.asStateFlow()

    /** The pack's front-detection artifact (#196), or null when it has none —
     *  the usual case, since "Auto Front Detection" is an opt-in preference. */
    private val _routeFronts = MutableStateFlow<RouteFrontsResponse?>(null)
    val routeFronts: StateFlow<RouteFrontsResponse?> = _routeFronts.asStateFlow()

    private val _alternates = MutableStateFlow<AlternatesResponse?>(null)
    val alternates: StateFlow<AlternatesResponse?> = _alternates.asStateFlow()

    private val _timeOptions = MutableStateFlow<TimeOptionsResponse?>(null)
    val timeOptions: StateFlow<TimeOptionsResponse?> = _timeOptions.asStateFlow()

    private val _debrief = MutableStateFlow<DebriefResponse?>(null)
    val debrief: StateFlow<DebriefResponse?> = _debrief.asStateFlow()

    /** The Debrief section's report (#1024), once the flight is due it; null before. */
    private val _debriefReport = MutableStateFlow<DebriefReport?>(null)
    val debriefReport: StateFlow<DebriefReport?> = _debriefReport.asStateFlow()
    private val _debriefBusy = MutableStateFlow(false)
    val debriefBusy: StateFlow<Boolean> = _debriefBusy.asStateFlow()
    private val _debriefError = MutableStateFlow<String?>(null)
    val debriefError: StateFlow<String?> = _debriefError.asStateFlow()
    /** "I changed level or route" ticked before its question had an answer. */
    private val _debriefPendingAvoided = MutableStateFlow<Map<String, Boolean>>(emptyMap())
    val debriefPendingAvoided: StateFlow<Map<String, Boolean>> = _debriefPendingAvoided.asStateFlow()

    private val _liveLayer = MutableStateFlow<LiveLayerResponse?>(null)
    val liveLayer: StateFlow<LiveLayerResponse?> = _liveLayer.asStateFlow()

    /**
     * The last `/live` read failed (#883 offline banner). [liveLayer] then keeps the
     * previous layer, so the Observed tab shows it as cached data, not as nothing.
     * Cleared by the next successful read. Also true for a flight that simply has no
     * live layer (the read fails the same way), so read it together with [liveLayer].
     */
    private val _liveFetchFailed = MutableStateFlow(false)
    val liveFetchFailed: StateFlow<Boolean> = _liveFetchFailed.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()

    private val _refreshProgressText = MutableStateFlow<String?>(null)
    val refreshProgressText: StateFlow<String?> = _refreshProgressText.asStateFlow()

    private val _isOfflineCached = MutableStateFlow(false)
    val isOfflineCached: StateFlow<Boolean> = _isOfflineCached.asStateFlow()

    private val _isDownloadingPack = MutableStateFlow(false)
    val isDownloadingPack: StateFlow<Boolean> = _isDownloadingPack.asStateFlow()

    private val _focusIntent = MutableStateFlow<FocusIntent?>(null)
    val focusIntent: StateFlow<FocusIntent?> = _focusIntent.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    private val _selectedModel = MutableStateFlow("ecmwf")
    val selectedModel: StateFlow<String> = _selectedModel.asStateFlow()

    private val _availableModels = MutableStateFlow<List<String>>(emptyList())
    val availableModels: StateFlow<List<String>> = _availableModels.asStateFlow()

    private val _activePointIndex = MutableStateFlow<Int?>(null)
    val activePointIndex: StateFlow<Int?> = _activePointIndex.asStateFlow()

    private var currentFlightId: String? = flightId
    private var openObservedRequested: Boolean = false
    private var hasStartedInitialLoad: Boolean = false

    fun loadInitialBriefing(preferredTimestamp: String? = null) {
        val id = currentFlightId ?: _flight.value?.id ?: return
        hasStartedInitialLoad = true
        load(
            flightId = id,
            initialPackTimestamp = preferredTimestamp,
            openObserved = _selectedTab.value == BriefingTab.OBSERVED,
        )
    }

    /**
     * Apply launch/deep-link parameters to either a newly created or an already-open [BriefingViewModel].
     *
     * Each open gets a fresh instance (`MainActivity` clears the briefing's `ViewModelStore` on
     * close), so a repeat call only comes from rotation or a deep link into the open briefing.
     */
    fun applyLaunchOptions(
        preferredTimestamp: String? = null,
        openObserved: Boolean = false,
    ) {
        if (openObserved) {
            selectTab(BriefingTab.OBSERVED)
        }
        if (!hasStartedInitialLoad) {
            loadInitialBriefing(preferredTimestamp = preferredTimestamp)
            return
        }
        if (!preferredTimestamp.isNullOrBlank() && _selectedPack.value?.fetchTimestamp != preferredTimestamp) {
            val matchingPack = _packs.value.firstOrNull { it.fetchTimestamp == preferredTimestamp }
            if (matchingPack != null) {
                selectPack(matchingPack)
            } else {
                val id = currentFlightId ?: _flight.value?.id ?: return
                load(flightId = id, initialPackTimestamp = preferredTimestamp, openObserved = openObserved)
            }
        }
    }

    fun load(
        flightId: String,
        initialPackTimestamp: String? = null,
        openObserved: Boolean = false,
    ) {
        currentFlightId = flightId
        openObservedRequested = openObserved
        hasStartedInitialLoad = true
        scope.launch {
            loadInternal(flightId, initialPackTimestamp, openObserved)
        }
    }

    private suspend fun loadInternal(
        flightId: String,
        initialPackTimestamp: String?,
        openObserved: Boolean,
    ) {
        _isLoading.value = true
        _errorMessage.value = null
        try {
            val (loadedFlight, packList, latest) = coroutineScope {
                val flightDeferred = async {
                    runSuspendCatching { repository.getFlight(flightId) }.getOrElse {
                        runSuspendCatching { repository.listFlights().firstOrNull { it.id == flightId } }.getOrNull()
                    }
                }
                val packsDeferred = async {
                    runSuspendCatching { repository.listPacks(flightId) }.getOrDefault(emptyList())
                }
                val latestDeferred = async {
                    runSuspendCatching { repository.latestPack(flightId) }.getOrNull()
                }
                Triple(flightDeferred.await(), packsDeferred.await(), latestDeferred.await())
            }
            _flight.value = loadedFlight
            // #1024: the Debrief section, off the pack load path.
            scope.launch { loadDebriefReportIfDue(openObserved) }

            val mergedPacks = buildList {
                if (latest != null && packList.none { it.fetchTimestamp == latest.fetchTimestamp }) {
                    add(latest)
                }
                addAll(packList)
            }
            _packs.value = mergedPacks

            val targetPack = when {
                !initialPackTimestamp.isNullOrBlank() ->
                    mergedPacks.firstOrNull { it.fetchTimestamp == initialPackTimestamp }
                        ?: latest
                        ?: mergedPacks.firstOrNull()
                else -> latest ?: mergedPacks.firstOrNull()
            }

            if (targetPack != null) {
                loadPackContents(flightId, targetPack, openObserved = openObserved)
                // A refresh started elsewhere (web, another device) is still running:
                // show its progress and load the new pack when it lands (iOS `checkActiveRefresh`).
                // Checked off the load path so the shown pack never waits on it.
                scope.launch {
                    if (activeRefreshStatus(flightId) != null) {
                        pollActiveRefresh(flightId)
                    }
                }
            } else {
                // No pack yet: the normal state right after creation (POST /api/flights only
                // saves the flight). Adopt a refresh already in flight, else start one.
                reconcileTabs(openObserved = openObserved)
                if (activeRefreshStatus(flightId) != null) {
                    pollActiveRefresh(flightId)
                } else {
                    refreshBriefingInternal(flightId)
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            _errorMessage.value = e.message ?: "Failed to load briefing"
        } finally {
            _isLoading.value = false
        }
    }

    private suspend fun loadPackContents(
        flightId: String,
        pack: PackMetaResponse,
        openObserved: Boolean = false,
    ) {
        _selectedPack.value = pack
        val ts = pack.fetchTimestamp

        // Fetch all 9 pack/flight endpoints concurrently instead of sequentially
        var adv: AdvisoriesResponse? = null
        var dig: DigestResponse? = null
        var snap: SnapshotResponse? = null
        var analyses: RouteAnalysesResponse? = null
        var elev: ElevationResponse? = null
        var fronts: RouteFrontsResponse? = null
        var alts: AlternatesResponse? = null
        var times: TimeOptionsResponse? = null
        var deb: DebriefResponse? = null
        var live: LiveLayerResponse? = null

        coroutineScope {
            val advD = async { runSuspendCatching { repository.advisories(flightId, ts) }.getOrNull() }
            val digD = async { runSuspendCatching { repository.digest(flightId, ts) }.getOrNull() }
            val snapD = async { runSuspendCatching { repository.snapshot(flightId, ts) }.getOrNull() }
            val analysesD = async { runSuspendCatching { repository.routeAnalyses(flightId, ts) }.getOrNull() }
            val elevD = async { runSuspendCatching { repository.elevation(flightId, ts) }.getOrNull() }
            val frontsD = async { runSuspendCatching { repository.routeFronts(flightId, ts) }.getOrNull() }
            val altsD = async { runSuspendCatching { repository.alternates(flightId, ts) }.getOrNull() }
            val timesD = async { runSuspendCatching { repository.timeOptions(flightId) }.getOrNull() }
            val debD = async { runSuspendCatching { repository.debrief(flightId) }.getOrNull() }
            val liveD = async { runSuspendCatching { repository.liveLayer(flightId) }.getOrNull() }

            adv = advD.await()
            dig = digD.await()
            snap = snapD.await()
            analyses = analysesD.await()
            elev = elevD.await()
            fronts = frontsD.await()
            alts = altsD.await() ?: adv?.alternates
            times = timesD.await()
            deb = debD.await()
            live = liveD.await()
        }

        val resolvedSnap = snap
        // A failed read keeps the layer we already had (shown under the offline banner).
        _liveFetchFailed.value = live == null
        val resolvedLive = live ?: _liveLayer.value?.takeIf { it.flightId.isEmpty() || it.flightId == flightId }
        // Overlay live observation blocks onto snapshot when live layer is newer
        val effectiveSnapshot = if (resolvedSnap != null && resolvedLive != null && resolvedLive.hasObservationOverlay) {
            resolvedSnap.copy(
                routeObservations = resolvedLive.routeObservations ?: resolvedSnap.routeObservations,
                routeSigmets = resolvedLive.routeSigmets ?: resolvedSnap.routeSigmets,
                observedConditions = resolvedLive.observedConditions ?: resolvedSnap.observedConditions,
            )
        } else {
            resolvedSnap
        }

        _advisories.value = adv
        _digest.value = dig
        _snapshot.value = effectiveSnapshot
        _routeAnalyses.value = analyses
        _elevation.value = elev
        _routeFronts.value = fronts
        _alternates.value = alts
        _timeOptions.value = times
        _debrief.value = deb
        _liveLayer.value = resolvedLive

        val models = buildList {
            analyses?.models?.let { addAll(it) }
            adv?.models?.forEach { if (it !in this) add(it) }
            pack.modelInitTimes.keys.forEach { if (it !in this) add(it) }
        }
        _availableModels.value = models
        if (models.isNotEmpty() && _selectedModel.value !in models) {
            _selectedModel.value = if ("ecmwf" in models) "ecmwf" else models.first()
        }

        val cachingRepo = repository as? CachingBriefingRepository
        _isOfflineCached.value = cachingRepo?.cache?.isPackCached(flightId, ts) ?: false

        reconcileTabs(openObserved = openObserved)
    }

    private fun reconcileTabs(openObserved: Boolean = false) {
        val showsObserved = openObserved ||
            // #1024: after the window the tab opens on the Debrief section.
            _debriefReport.value != null ||
            hasObservedContent(_snapshot.value, _liveLayer.value) ||
            (_flight.value?.let { f ->
                // A flight being tracked is started and not landed: its window runs on (#874).
                val started = FlightTrackingService.isTracking.value &&
                    FlightTrackingService.activeFlightId.value == f.id
                f.isInLiveObservationWindow(started = started)
            } == true)
        val tabs = BriefingTab.tabs(showsObserved = showsObserved)
        _availableTabs.value = tabs

        if (openObserved && BriefingTab.OBSERVED in tabs) {
            _selectedTab.value = BriefingTab.OBSERVED
        } else if (_selectedTab.value !in tabs) {
            _selectedTab.value = BriefingTab.ADVISORY
        }
    }

    /**
     * Re-evaluate the tabs when tracking starts or stops: a tracked flight's Observed tab stays
     * past its planned window (#874), and [reconcileTabs] otherwise reads the tracking state only
     * when the briefing loads. Called by the screen, which observes the service's state.
     */
    fun onTrackingStateChanged() {
        if (_flight.value != null) reconcileTabs()
    }

    fun selectTab(tab: BriefingTab) {
        if (tab == BriefingTab.OBSERVED && tab !in _availableTabs.value) {
            _availableTabs.value = BriefingTab.tabs(showsObserved = true)
        }
        _selectedTab.value = tab
    }

    fun selectPack(pack: PackMetaResponse) {
        val flightId = currentFlightId ?: _flight.value?.id ?: pack.flightId.takeIf { it.isNotEmpty() } ?: return
        _selectedPack.value = pack
        scope.launch {
            _isLoading.value = true
            try {
                loadPackContents(flightId, pack, openObserved = false)
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to switch pack"
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun selectModel(model: String) {
        _selectedModel.value = model
    }

    fun setActivePointIndex(index: Int?) {
        _activePointIndex.value = index
    }

    fun applyFocusIntent(intent: FocusIntent) {
        _focusIntent.value = intent
        _selectedTab.value = intent.tab
        val analyses = _routeAnalyses.value?.analyses.orEmpty()
        if (intent.waypointIcao != null) {
            val match = analyses.firstOrNull { it.waypointIcao.equals(intent.waypointIcao, ignoreCase = true) }
            if (match != null) {
                _activePointIndex.value = match.pointIndex
            }
        } else if (intent.distanceNm != null && analyses.isNotEmpty()) {
            val nearest = analyses.minByOrNull { kotlin.math.abs(it.distanceFromOriginNm - intent.distanceNm) }
            if (nearest != null) {
                _activePointIndex.value = nearest.pointIndex
            }
        }
    }

    fun clearFocusIntent() {
        _focusIntent.value = null
    }

    fun refreshBriefing() {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        scope.launch {
            refreshBriefingInternal(flightId)
        }
    }

    private var resumeJob: Job? = null

    /**
     * Called when the briefing screen returns to `ON_RESUME` (e.g. after the phone screen
     * turned off during a refresh and Android aborted the background SSE socket, or after
     * switching back from another app). Adopts an active server refresh or newly completed
     * pack and clears any stale background disconnect error.
     */
    fun onForegroundResume() {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        if (!hasStartedInitialLoad || _isLoading.value || _isRefreshing.value) return
        if (resumeJob?.isActive == true) return
        resumeJob = scope.launch {
            val needsRecovery = _selectedPack.value == null ||
                _errorMessage.value != null ||
                _advisories.value == null ||
                _snapshot.value == null
            val maxAttempts = if (needsRecovery) 3 else 1
            for (attempt in 0 until maxAttempts) {
                if (_isLoading.value || _isRefreshing.value) return@launch
                if (attempt > 0) {
                    delay(1200L * attempt)
                }
                val active = activeRefreshStatus(flightId)
                if (active != null) {
                    _errorMessage.value = null
                    _refreshProgressText.value = refreshProgressLabel(
                        active.label ?: active.stage,
                        active.detail,
                        active.progress,
                    )
                    pollActiveRefresh(flightId)
                    return@launch
                }
                val latest = runSuspendCatching { repository.latestPack(flightId) }.getOrNull()
                if (latest != null) {
                    val currentTs = _selectedPack.value?.fetchTimestamp
                    val needsContentReload = _advisories.value == null || _snapshot.value == null
                    if (currentTs == null || _errorMessage.value != null || needsContentReload || latest.fetchTimestamp != currentTs) {
                        _errorMessage.value = null
                        adoptNewPack(flightId, latest)
                    }
                    return@launch
                }
            }
        }
    }

    private suspend fun refreshBriefingInternal(flightId: String) {
        if (_isRefreshing.value) return
        // A resume check still between its status read and its poll would overlap this run.
        resumeJob?.cancel()
        _isRefreshing.value = true
        _refreshProgressText.value = "Starting briefing refresh…"
        _errorMessage.value = null
        var completedPack: PackMetaResponse? = null
        var provisionalPack: PackMetaResponse? = null
        var streamErrorEvent: String? = null
        var streamException: Throwable? = null
        try {
            try {
                repository.refreshPack(flightId, force = false, notifyEmail = false).collect { event ->
                    when (event.type) {
                        "progress", "stage" -> {
                            _refreshProgressText.value = refreshProgressLabel(
                                event.label ?: event.stage,
                                event.detail,
                                event.progress,
                            )
                        }
                        "briefing_ready" -> {
                            if (event.pack != null) {
                                provisionalPack = event.pack
                            }
                            _refreshProgressText.value = refreshProgressLabel(
                                event.label ?: event.stage,
                                event.detail,
                                event.progress,
                            )
                        }
                        "complete" -> {
                            completedPack = event.pack
                            _refreshProgressText.value = null
                        }
                        "error" -> {
                            streamErrorEvent = event.message ?: event.detail ?: "Refresh failed"
                        }
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                streamException = e
            }

            val finalPack = completedPack
            val errorEvent = streamErrorEvent
            val provisional = provisionalPack
            when {
                finalPack != null -> {
                    adoptNewPack(flightId, finalPack)
                }
                errorEvent != null -> {
                    if (provisional != null) {
                        adoptNewPack(flightId, provisional)
                    }
                    _errorMessage.value = errorEvent
                }
                else -> {
                    // If the SSE connection dropped while the server pipeline is still running in the
                    // background (or returned 409 because a refresh was already active), follow the
                    // server's run via `/refresh/status` polling instead of failing the screen.
                    val statusResult = runSuspendCatching { repository.refreshStatus(flightId) }
                    val active = statusResult.getOrNull()?.takeIf { it.active }
                    if (active != null) {
                        _refreshProgressText.value = refreshProgressLabel(
                            active.label ?: active.stage,
                            active.detail,
                            active.progress,
                        )
                        pollActiveRefreshLoop(flightId)
                    } else if (statusResult.isFailure && provisional == null) {
                        // A status-check failure means the server state is unknown (e.g. socket was
                        // aborted when the screen turned off); hand off to polling which retries with
                        // backoff before concluding the run was lost.
                        pollActiveRefreshLoop(flightId)
                    } else {
                        // Server is no longer active — check whether the refresh already produced a pack.
                        val latest = runSuspendCatching { repository.latestPack(flightId) }.getOrNull()
                        val recoveredPack = when {
                            latest != null && latest.fetchTimestamp != _selectedPack.value?.fetchTimestamp -> latest
                            provisional != null -> provisional
                            else -> null
                        }
                        if (recoveredPack != null) {
                            adoptNewPack(flightId, recoveredPack)
                        } else if (streamException != null) {
                            _errorMessage.value = streamException.message ?: "Failed to refresh briefing"
                        } else if (_selectedPack.value == null) {
                            _errorMessage.value = "The briefing refresh did not produce a briefing. Tap refresh to try again."
                        }
                    }
                }
            }
        } finally {
            _isRefreshing.value = false
            _refreshProgressText.value = null
        }
    }

    private suspend fun adoptNewPack(flightId: String, newPack: PackMetaResponse) {
        val updatedList = runSuspendCatching { repository.listPacks(flightId) }.getOrDefault(listOf(newPack))
        _packs.value = if (updatedList.none { it.fetchTimestamp == newPack.fetchTimestamp }) {
            listOf(newPack) + updatedList
        } else {
            updatedList
        }
        loadPackContents(flightId, newPack, openObserved = false)
        _errorMessage.value = null
    }

    /** The server's refresh state for this flight when one is running, else null (also on failure). */
    private suspend fun activeRefreshStatus(flightId: String): ActiveRefreshResponse? =
        runSuspendCatching { repository.refreshStatus(flightId) }.getOrNull()?.takeIf { it.active }

    /**
     * Follow a refresh this screen did not start (web, another device) by polling
     * `/refresh/status`, driving the same banner as the SSE stream, then load the new pack.
     * Mirrors iOS `pollRefreshStatus`: 3 s cadence, 100 polls (~5 min) cap.
     *
     * Any end without a new pack (lost status, cap reached, run produced nothing) is reported
     * when no pack is on screen, so a new flight never sits on a blank briefing.
     */
    private suspend fun pollActiveRefresh(flightId: String) {
        if (_isRefreshing.value) return
        _isRefreshing.value = true
        try {
            pollActiveRefreshLoop(flightId)
        } finally {
            _isRefreshing.value = false
            _refreshProgressText.value = null
        }
    }

    private suspend fun pollActiveRefreshLoop(flightId: String) {
        var failure: String? = "Briefing is still generating. Tap refresh to check again."
        var consecutiveStatusFailures = 0
        for (attempt in 0 until ACTIVE_REFRESH_MAX_POLLS) {
            val status = runSuspendCatching { repository.refreshStatus(flightId) }.getOrNull()
            if (status == null) {
                consecutiveStatusFailures++
                if (consecutiveStatusFailures <= 3) {
                    delay(ACTIVE_REFRESH_POLL_MS)
                    continue
                }
                val pack = runSuspendCatching { repository.latestPack(flightId) }.getOrNull()
                if (pack != null && pack.fetchTimestamp != _selectedPack.value?.fetchTimestamp) {
                    adoptNewPack(flightId, pack)
                    failure = null
                } else {
                    failure = "Lost track of the briefing refresh. Tap refresh to try again."
                }
                break
            }
            consecutiveStatusFailures = 0
            if (!status.active) {
                val pack = runSuspendCatching { repository.latestPack(flightId) }.getOrNull()
                failure = if (pack != null) {
                    // A run that produced nothing new leaves the reader where they are.
                    if (pack.fetchTimestamp != _selectedPack.value?.fetchTimestamp) {
                        adoptNewPack(flightId, pack)
                    }
                    null
                } else {
                    "The briefing refresh did not produce a briefing. Tap refresh to try again."
                }
                break
            }
            _refreshProgressText.value = refreshProgressLabel(status.label ?: status.stage, status.detail, status.progress)
            delay(ACTIVE_REFRESH_POLL_MS)
        }
        if (failure != null && _selectedPack.value == null) {
            _errorMessage.value = failure
        }
    }

    fun downloadCurrentPack() {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        val pack = _selectedPack.value ?: return
        scope.launch {
            _isDownloadingPack.value = true
            try {
                val caching = repository as? CachingBriefingRepository
                if (caching != null) {
                    caching.downloadPack(
                        flightId = flightId,
                        timestamp = pack.fetchTimestamp,
                        flightTitle = _flight.value?.shortTitle ?: flightId,
                        assessment = pack.assessment,
                        includeGramet = pack.hasGramet,
                        departureTime = _flight.value?.departureTime,
                        packMeta = pack,
                    )
                }
                _isOfflineCached.value = true
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to download pack for offline use"
            } finally {
                _isDownloadingPack.value = false
            }
        }
    }

    // ========================================================================
    // Debrief section (#1024): the server's report, the pilot's taps sent back
    // ========================================================================

    private suspend fun loadDebriefReportIfDue(openObserved: Boolean) {
        val f = _flight.value ?: return
        val started = FlightTrackingService.isTracking.value && FlightTrackingService.activeFlightId.value == f.id
        if (_debriefReport.value != null || !DebriefSectionRules.isDue(f, started = started)) return
        val report = runSuspendCatching { repository.debriefReport(f.id) }.getOrNull() ?: return
        _debriefReport.value = report
        reconcileTabs(openObserved = openObserved)
        // Open questions land the pilot on them, once per open (a deep link off Advisory wins).
        val decision = DebriefSectionRules.decision(_debrief.value, report)
        if (_selectedTab.value == BriefingTab.ADVISORY && DebriefSectionRules.openQuestions(report) > 0 &&
            decision != DebriefSectionRules.Decision.CANCELLED
        ) {
            _selectedTab.value = BriefingTab.OBSERVED
        }
    }

    private fun debriefSave(work: suspend (String) -> Unit) {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        if (_debriefBusy.value) return
        _debriefBusy.value = true
        _debriefError.value = null
        scope.launch {
            try {
                work(flightId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                _debriefError.value = "Could not save. Check your connection and try again."
            } finally {
                _debriefBusy.value = false
            }
        }
    }

    private fun saveDebriefDecision(decision: DebriefSectionRules.Decision, reasons: List<String>, note: String?) =
        debriefSave { flightId ->
            val saved = repository.saveDebrief(
                flightId,
                DebriefRequest(decision = decision.wire, reasons = reasons, note = note?.trim()?.takeIf { it.isNotEmpty() }),
            )
            _debrief.value = saved
            // The report reads the decision: a cancelled flight asks nothing.
            _debriefReport.value = repository.debriefReport(flightId)
        }

    fun chooseDebriefDecision(decision: DebriefSectionRules.Decision) {
        if (DebriefSectionRules.decision(_debrief.value, _debriefReport.value) == decision) return
        saveDebriefDecision(decision, emptyList(), _debrief.value?.note)
    }

    fun answerDebriefQuestion(question: DebriefQuestion, option: String) =
        sendDebriefAnswer(DebriefSectionRules.answerTap(question, option, _debriefPendingAvoided.value))

    fun setDebriefAvoided(question: DebriefQuestion, avoided: Boolean) {
        val stored = question.answer
        if (stored != null) {
            sendDebriefAnswer(DebriefAnswerItem(questionId = question.id, answer = stored.answer, avoided = avoided))
        } else {
            _debriefPendingAvoided.value = _debriefPendingAvoided.value + (question.id to avoided)
        }
    }

    fun toggleDebriefCancelReason(tag: String) {
        val current = _debrief.value?.takeIf { it.decision == "cancelled" }?.reasons.orEmpty()
        saveDebriefDecision(DebriefSectionRules.Decision.CANCELLED, DebriefSectionRules.toggledReasons(current, tag), _debrief.value?.note)
    }

    fun saveDebriefNote(note: String) {
        val decision = DebriefSectionRules.decision(_debrief.value, _debriefReport.value) ?: return
        if (note.length > DebriefSectionRules.NOTE_MAX_LENGTH) return
        val reasons = if (decision == DebriefSectionRules.Decision.CANCELLED) _debrief.value?.reasons.orEmpty() else emptyList()
        saveDebriefDecision(decision, reasons, note)
    }

    private fun sendDebriefAnswer(item: DebriefAnswerItem) = debriefSave { flightId ->
        _debriefReport.value = repository.saveDebriefAnswers(flightId, DebriefAnswersRequest(answers = listOf(item)))
        _debriefPendingAvoided.value = _debriefPendingAvoided.value - item.questionId
        // The server stored the flight as flown with this answer.
        _debrief.value = _debrief.value?.copy(decision = "flown", reasons = emptyList(), form = "questions")
    }

    fun submitDigestFeedback(thumbsUp: Boolean, comment: String? = null) {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        val packTs = _selectedPack.value?.fetchTimestamp ?: return
        scope.launch {
            try {
                apiClient?.submitFeedback(
                    FeedbackRequest.digestRating(flightId, packTs, thumbsUp, comment.orEmpty())
                )
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to submit feedback"
            }
        }
    }

    /** Thumb on the Observed tab's live highlight (target `live_highlight`, keyed to the rated line). */
    fun submitHighlightFeedback(thumbsUp: Boolean) {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        val live = _liveLayer.value ?: return
        val highlight = live.glance?.highlight ?: live.glance?.highlights?.firstOrNull() ?: return
        val packTs = live.packTimestamp ?: _selectedPack.value?.fetchTimestamp ?: return
        val request = FeedbackRequest.highlightRating(flightId, packTs, highlight, thumbsUp) ?: return
        scope.launch {
            try {
                apiClient?.submitFeedback(request)
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to submit feedback"
            }
        }
    }

    fun confirmTimeOption(departureTime: String) {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        scope.launch {
            _isLoading.value = true
            try {
                val updatedFlight = repository.confirmTimeOption(
                    flightId,
                    ConfirmTimeOptionRequest(
                        departureTime = departureTime,
                        lockToFixed = true,
                        refreshNow = true,
                    ),
                )
                _flight.value = updatedFlight
                refreshBriefingInternal(flightId)
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to confirm departure time"
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun recalculateAdvisories(cruiseAltitudeFt: Int?) {
        val flightId = currentFlightId ?: _flight.value?.id ?: return
        val packTs = _selectedPack.value?.fetchTimestamp ?: return
        scope.launch {
            try {
                if (cruiseAltitudeFt != null && cruiseAltitudeFt != _flight.value?.cruiseAltitudeFt) {
                    val updated = runCatching {
                        repository.updateFlight(flightId, UpdateFlightRequest(cruiseAltitudeFt = cruiseAltitudeFt))
                    }.getOrNull()
                    if (updated != null) {
                        _flight.value = updated.flight
                    }
                }
                val recalculated = repository.recalculateAdvisories(flightId, packTs)
                _advisories.value = recalculated
                recalculated.alternates?.let { _alternates.value = it }
            } catch (e: Throwable) {
                _errorMessage.value = e.message ?: "Failed to recalculate advisories"
            }
        }
    }

    suspend fun loadChartImage(chart: PackChart): ByteArray? =
        runCatching { repository.chartImage(chart) }.getOrNull()

    suspend fun loadChartOverlay(source: PackChartSource): ChartOverlay? {
        val overlayUrl = source.overlayUrl?.takeIf { it.isNotBlank() } ?: return null
        val flightId = currentFlightId ?: _flight.value?.id ?: return null
        val packTs = _selectedPack.value?.fetchTimestamp ?: return null
        return runCatching {
            repository.chartOverlay(
                urlPath = overlayUrl,
                flightId = flightId,
                timestamp = packTs,
                source = source.source,
            )
        }.getOrNull()
    }

    suspend fun loadGrametDocument(): ByteArray? {
        val flightId = currentFlightId ?: _flight.value?.id ?: return null
        val packTs = _selectedPack.value?.fetchTimestamp ?: return null
        return runCatching { repository.gramet(flightId, packTs) }.getOrNull()
    }

    suspend fun loadSoundingProfile(
        pointIndex: Int,
        model: String = _selectedModel.value,
    ): SoundingProfileResponse? {
        val flightId = currentFlightId ?: _flight.value?.id ?: return null
        val packTs = _selectedPack.value?.fetchTimestamp ?: return null
        return runCatching { repository.soundingProfile(flightId, packTs, pointIndex, model) }.getOrNull()
    }

    suspend fun loadAdvisoryDetail(
        advisoryId: String,
        alt: Boolean = false,
    ): AdvisoryDetailResponse? {
        val flightId = currentFlightId ?: _flight.value?.id ?: return null
        val packTs = _selectedPack.value?.fetchTimestamp ?: return null
        return runCatching { repository.advisoryDetail(flightId, packTs, advisoryId, alt) }.getOrNull()
    }

    fun clearError() {
        _errorMessage.value = null
    }

    companion object {
        const val ACTIVE_REFRESH_POLL_MS = 3_000L
        const val ACTIVE_REFRESH_MAX_POLLS = 100

        /** Refresh banner text, shared by the SSE stream and the status poll. */
        fun refreshProgressLabel(stage: String?, detail: String?, progress: Double?): String {
            val pct = progress?.let { "${(it * 100).toInt()}%" }
            return listOfNotNull(stage ?: "Refreshing", detail?.takeIf { it.isNotBlank() }, pct)
                .joinToString(" · ")
        }

        fun isLiveLayerMeaningful(live: LiveLayerResponse?): Boolean {
            if (live == null) return false
            return !live.liveUpdatedAt.isNullOrBlank() ||
                live.hasObservationOverlay ||
                (live.changes?.changes?.isNotEmpty() == true) ||
                live.glance != null ||
                live.ribbon != null ||
                live.storms != null
        }

        fun hasObservedContent(
            snapshot: SnapshotResponse?,
            liveLayer: LiveLayerResponse? = null,
        ): Boolean {
            if (isLiveLayerMeaningful(liveLayer)) return true
            if (snapshot == null) return false
            val hasObsAirports = snapshot.routeObservations?.airports?.any { it.hasMetar || it.hasTaf } == true
            val hasSigmets = (snapshot.routeSigmets?.sigmets?.isNotEmpty() == true) ||
                ((snapshot.routeSigmets?.count ?: 0) > 0)
            val hasObservedCond = snapshot.observedConditions != null && (
                snapshot.observedConditions.radar != null ||
                    snapshot.observedConditions.cloudTops != null ||
                    snapshot.observedConditions.lightning != null ||
                    !snapshot.observedConditions.summaryEntries.isNullOrEmpty()
                )
            return hasObsAirports || hasSigmets || hasObservedCond
        }
    }
}
