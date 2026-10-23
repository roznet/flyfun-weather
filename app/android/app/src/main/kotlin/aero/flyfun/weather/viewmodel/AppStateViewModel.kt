package aero.flyfun.weather.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import aero.flyfun.common.AuthConfig
import aero.flyfun.common.FileTokenStore
import aero.flyfun.common.FlyFunAuthService
import aero.flyfun.common.InMemoryTokenStore
import aero.flyfun.common.JwtClaims
import aero.flyfun.common.TokenStore
import aero.flyfun.weather.data.AirportDatabaseService
import aero.flyfun.weather.data.AndroidTokenStore
import aero.flyfun.weather.data.ApiClient
import aero.flyfun.weather.data.BriefingCache
import aero.flyfun.weather.data.BriefingRepository
import aero.flyfun.weather.data.CachingBriefingRepository
import aero.flyfun.weather.data.FixtureBriefingRepository
import aero.flyfun.weather.data.FixtureTripRepository
import aero.flyfun.weather.data.HelpCatalogStore
import aero.flyfun.weather.data.OnlineBriefingRepository
import aero.flyfun.weather.data.OnlineTripRepository
import aero.flyfun.weather.data.PirepOfflineStore
import aero.flyfun.weather.data.TripRepository
import aero.flyfun.weather.data.WhatsNewStore
import aero.flyfun.weather.model.NavigationTarget
import aero.flyfun.weather.model.PendingNavigation
import aero.flyfun.weather.model.UniversalLinkRouter
import aero.flyfun.weather.model.UserPreferencesResponse
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Auth state and global app dependencies equivalent to iOS `AppState`.
 */
class AppStateViewModel(
    private val context: Context? = null,
    val baseUrl: String = ApiClient.DEFAULT_BASE_URL,
    tokenStoreOverride: TokenStore? = null,
    private val cacheRootOverride: File? = null,
    val authService: FlyFunAuthService = FlyFunAuthService(
        AuthConfig(baseUrl = baseUrl, callbackScheme = "flyfunweather"),
    ),
    initialMockMode: Boolean = false,
    private val scopeOverride: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope
        get() = scopeOverride ?: viewModelScope

    val tokenStore: TokenStore = tokenStoreOverride ?: run {
        if (context != null) {
            AndroidTokenStore(context)
        } else {
            InMemoryTokenStore()
        }
    }

    val apiClient: ApiClient = ApiClient(
        baseUrl = baseUrl,
        tokenStore = tokenStore,
        onUnauthorized = { handleUnauthorized() },
    )

    private val _isAuthenticated = MutableStateFlow(false)
    val isAuthenticated: StateFlow<Boolean> = _isAuthenticated.asStateFlow()

    private val _isGuestBrowsing = MutableStateFlow(false)
    val isGuestBrowsing: StateFlow<Boolean> = _isGuestBrowsing.asStateFlow()

    private val _isMockMode = MutableStateFlow(initialMockMode)
    val isMockMode: StateFlow<Boolean> = _isMockMode.asStateFlow()

    private val _isOfflineMode = MutableStateFlow(false)
    val isOfflineMode: StateFlow<Boolean> = _isOfflineMode.asStateFlow()

    private val _authError = MutableStateFlow<String?>(null)
    val authError: StateFlow<String?> = _authError.asStateFlow()

    private val _isAuthLoading = MutableStateFlow(false)
    val isAuthLoading: StateFlow<Boolean> = _isAuthLoading.asStateFlow()

    private val _magicLinkSent = MutableStateFlow(false)
    val magicLinkSent: StateFlow<Boolean> = _magicLinkSent.asStateFlow()

    private val _magicLinkEmail = MutableStateFlow("")
    val magicLinkEmail: StateFlow<String> = _magicLinkEmail.asStateFlow()

    private val _pendingOAuthState = MutableStateFlow<String?>(null)
    val pendingOAuthState: StateFlow<String?> = _pendingOAuthState.asStateFlow()

    private val _pendingNavTarget = MutableStateFlow<NavigationTarget?>(null)
    val pendingNavTarget: StateFlow<NavigationTarget?> = _pendingNavTarget.asStateFlow()

    private val _autorouterLinked = MutableStateFlow(false)
    val autorouterLinked: StateFlow<Boolean> = _autorouterLinked.asStateFlow()

    private val _userEmail = MutableStateFlow<String?>(null)
    val userEmail: StateFlow<String?> = _userEmail.asStateFlow()

    private val _userPreferences = MutableStateFlow<UserPreferencesResponse?>(null)
    val userPreferences: StateFlow<UserPreferencesResponse?> = _userPreferences.asStateFlow()

    private var pendingOAuthVerifier: String? = null

    private val _autorouterLinkError = MutableStateFlow<String?>(null)
    /** Why the last Autorouter link attempt failed (callback `status=error`, or the redeem call). */
    val autorouterLinkError: StateFlow<String?> = _autorouterLinkError.asStateFlow()

    private val cacheRootDir: File?
        get() = cacheRootOverride ?: context?.filesDir?.let { File(it, "BriefingCache") }

    private val _cacheScope = MutableStateFlow("anon")
    val cacheScope: StateFlow<String> = _cacheScope.asStateFlow()

    @Volatile
    var briefingCache: BriefingCache? = null
        private set

    private val fixtureBriefingRepo = FixtureBriefingRepository(mockLive = true)
    private val fixtureTripRepo = FixtureTripRepository()

    @Volatile
    private var onlineBriefingRepo: BriefingRepository = OnlineBriefingRepository(apiClient)

    @Volatile
    private var onlineTripRepo: TripRepository = OnlineTripRepository(apiClient, null)

    val repository: BriefingRepository
        get() = if (_isMockMode.value || _isGuestBrowsing.value) fixtureBriefingRepo else onlineBriefingRepo

    val tripRepository: TripRepository
        get() = if (_isMockMode.value || _isGuestBrowsing.value) fixtureTripRepo else onlineTripRepo

    val airportDb: AirportDatabaseService? = context?.let { AirportDatabaseService(it) }
    val helpStore: HelpCatalogStore? = context?.let { HelpCatalogStore(it) }
    val whatsNewStore: WhatsNewStore? = context?.let { WhatsNewStore(it) }

    init {
        // Clean up legacy cacheDir-based briefing cache directories if present from older builds
        context?.cacheDir?.let { legacyCacheParent ->
            scope.launch(Dispatchers.IO) {
                runCatching {
                    File(legacyCacheParent, "briefing_cache").takeIf { it.exists() }?.deleteRecursively()
                    File(legacyCacheParent, "BriefingCache").takeIf { it.exists() }?.deleteRecursively()
                }
            }
        }
        val existingToken = tokenStore.getToken()
        if (!existingToken.isNullOrBlank()) {
            rebuildUserScopedRepositories(existingToken)
            _isAuthenticated.value = true
            _userEmail.value = extractEmailFromToken(existingToken)
            refreshUserSupportData()
        } else {
            rebuildUserScopedRepositories(null)
            if (initialMockMode) {
                _isAuthenticated.value = true
            }
        }
    }

    private fun rebuildUserScopedRepositories(token: String?) {
        val scopeKey = token?.takeIf { it.isNotBlank() }?.let { JwtClaims.cacheScope(it) } ?: "anon"
        _cacheScope.value = scopeKey
        val root = cacheRootDir
        val cache = if (root != null) BriefingCache(root, scope = scopeKey) else null
        briefingCache = cache
        val online = OnlineBriefingRepository(apiClient)
        onlineBriefingRepo = if (cache != null) {
            CachingBriefingRepository(
                online = online,
                cache = cache,
                isOnline = { !_isOfflineMode.value },
            )
        } else {
            online
        }
        onlineTripRepo = OnlineTripRepository(apiClient, cache)
    }

    private fun extractEmailFromToken(token: String): String? {
        val payload = JwtClaims.unverifiedPayload(token) ?: return null
        val str = payload["email"]?.toString()?.trim('"')
            ?: payload["sub"]?.toString()?.trim('"')
        return str?.takeIf { it.isNotBlank() }
    }

    fun refreshUserSupportData() {
        if (_isMockMode.value || _isGuestBrowsing.value || !_isAuthenticated.value) return
        scope.launch {
            runCatching { apiClient.getPreferences() }.onSuccess { prefs ->
                _userPreferences.value = prefs
                _autorouterLinked.value = prefs.autorouterConnected
            }
            airportDb?.let { db -> runCatching { db.ensureLoaded(apiClient) } }
            helpStore?.let { store -> runCatching { store.refreshIfNeeded(apiClient) } }
            whatsNewStore?.let { store -> runCatching { store.checkStatus(apiClient) } }
            // Position fixes a killed process left queued on disk (#889).
            context?.let { ctx ->
                runCatching {
                    aero.flyfun.weather.service.FlightTrackingService.uploadPersistedFixes(ctx, apiClient)
                }
            }
        }
    }

    /** Build the Google OAuth URL for Chrome Custom Tabs and store the state nonce + PKCE verifier. */
    fun startGoogleLogin(): String {
        _authError.value = null
        val state = authService.generateStateNonce()
        val (verifier, challenge) = authService.generatePkcePair()
        _pendingOAuthState.value = state
        pendingOAuthVerifier = verifier
        return authService.buildAuthorizeUrl(
            provider = "google",
            state = state,
            codeChallenge = challenge,
        )
    }

    /** Request an email magic link + 6-digit code. */
    fun requestMagicLink(email: String) {
        val trimmed = email.trim()
        if (trimmed.isBlank()) {
            _authError.value = "Please enter your email address."
            return
        }
        _authError.value = null
        _isAuthLoading.value = true
        _magicLinkEmail.value = trimmed
        scope.launch {
            runCatching {
                authService.requestMagicLinkCode(trimmed)
            }.onSuccess {
                _magicLinkSent.value = true
                _isAuthLoading.value = false
            }.onFailure { err ->
                // Fall back to standard magic link endpoint if code endpoint fails
                runCatching {
                    authService.requestMagicLink(trimmed)
                }.onSuccess {
                    _magicLinkSent.value = true
                    _isAuthLoading.value = false
                }.onFailure { fallbackErr ->
                    _authError.value = err.message ?: fallbackErr.message ?: "Failed to send login link"
                    _isAuthLoading.value = false
                }
            }
        }
    }

    /** Exchange a 6-digit magic link code for a JWT token. */
    fun verifyMagicLinkCode(email: String = _magicLinkEmail.value, code: String) {
        val trimmedEmail = email.trim()
        val trimmedCode = code.trim()
        if (trimmedEmail.isBlank() || trimmedCode.isBlank()) {
            _authError.value = "Please enter the verification code."
            return
        }
        _authError.value = null
        _isAuthLoading.value = true
        scope.launch {
            runCatching {
                authService.consumeMagicLinkCode(trimmedEmail, trimmedCode)
            }.onSuccess { jwt ->
                completeLogin(jwt)
            }.onFailure { err ->
                _authError.value = err.message ?: "Invalid verification code"
                _isAuthLoading.value = false
            }
        }
    }

    /** Dev login (local development server or mock fallback). */
    fun loginWithDevToken() {
        _authError.value = null
        _isAuthLoading.value = true
        scope.launch {
            runCatching {
                authService.fetchDevToken()
            }.onSuccess { jwt ->
                completeLogin(jwt)
            }.onFailure { err ->
                _authError.value = err.message ?: "Dev login failed"
                _isAuthLoading.value = false
            }
        }
    }

    /** Start guest browsing / demo mode with rich fixture data. */
    fun enterDemoMode() {
        _authError.value = null
        _isGuestBrowsing.value = true
        _isMockMode.value = true
        _isAuthenticated.value = true
    }

    fun setMockMode(enabled: Boolean) {
        _isMockMode.value = enabled
        if (enabled) {
            _isAuthenticated.value = true
        } else if (tokenStore.getToken().isNullOrBlank()) {
            _isAuthenticated.value = false
            _isGuestBrowsing.value = false
        }
    }

    fun setOfflineMode(offline: Boolean) {
        _isOfflineMode.value = offline
    }

    /** Directly accept a JWT token (e.g. from OAuth callback or deep link). */
    fun completeLogin(jwt: String) {
        tokenStore.setToken(jwt)
        rebuildUserScopedRepositories(jwt)
        _isGuestBrowsing.value = false
        _isMockMode.value = false
        _isAuthenticated.value = true
        _isAuthLoading.value = false
        _magicLinkSent.value = false
        _authError.value = null
        _userEmail.value = extractEmailFromToken(jwt)
        refreshUserSupportData()
    }

    /**
     * Handle incoming deep link or universal link (`flyfunweather://...` or `https://weather.flyfun.aero/...`).
     */
    fun handleIncomingUri(uriString: String) {
        // 1. Check OAuth callback (`flyfunweather://auth/callback?...`)
        val callbackParams = authService.parseCallbackUri(uriString)
        if (callbackParams != null) {
            handleAuthCallback(uriString)
            return
        }

        // 2. Autorouter link callback (`flyfunweather://autorouter/callback?status=authorized&code=...`,
        //    or `status=error&reason=...`). The code is redeemed with this app's bearer, so only the
        //    user who started the link can complete it (flyfun-common `autorouter.py`).
        if (uriString.startsWith("$AUTOROUTER_CALLBACK_SCHEME://autorouter/callback")) {
            val code = extractQueryParam(uriString, "code")
            if (extractQueryParam(uriString, "status") != "authorized" || code.isNullOrBlank()) {
                _autorouterLinkError.value = autorouterLinkErrorMessage(extractQueryParam(uriString, "reason"))
                return
            }
            scope.launch {
                runCatching { apiClient.completeAutorouterLink(code) }
                    .onSuccess {
                        _autorouterLinked.value = true
                        _autorouterLinkError.value = null
                    }
                    .onFailure { _autorouterLinkError.value = "Couldn't connect Autorouter: ${it.message}" }
            }
            return
        }

        // 3. Check Universal Links (briefings, trips, shares, maps)
        val navTarget = UniversalLinkRouter.navigationTarget(uriString)
        if (navTarget != null) {
            PendingNavigation.post(navTarget)
            _pendingNavTarget.value = navTarget
        }
    }

    fun consumePendingNavigation(): NavigationTarget? {
        val target = PendingNavigation.consume() ?: _pendingNavTarget.value
        _pendingNavTarget.value = null
        return target
    }

    private fun handleAuthCallback(uriString: String) {
        _isAuthLoading.value = true
        _authError.value = null
        val expectedState = _pendingOAuthState.value
        val codeVerifier = pendingOAuthVerifier
        scope.launch {
            runCatching {
                val params = authService.parseCallbackUri(uriString)
                // Scope guard for direct token callbacks (`flyfunweather://auth?token=...`)
                if (params?.token != null) {
                    if (!JwtClaims.shouldAcceptDeepLinkToken(params.token!!, _isAuthenticated.value)) {
                        throw IllegalStateException("Scoped token ignored while already authenticated")
                    }
                    return@runCatching params.token!!
                }
                authService.handleCallback(
                    callbackUri = uriString,
                    expectedState = expectedState.orEmpty(),
                    codeVerifier = codeVerifier,
                )
            }.onSuccess { jwt ->
                _pendingOAuthState.value = null
                pendingOAuthVerifier = null
                completeLogin(jwt)
            }.onFailure { err ->
                _isAuthLoading.value = false
                _authError.value = err.message ?: "Authentication failed"
            }
        }
    }

    /**
     * Start Autorouter account linking: the server returns a short-lived URL (a signed ticket, since
     * the browser can't send the app's bearer) that ends at the `flyfunweather://autorouter/callback`.
     */
    suspend fun buildAutorouterLinkUrl(): String? {
        _autorouterLinkError.value = null
        return apiClient.createAutorouterLinkTicket(AUTOROUTER_CALLBACK_SCHEME).url.takeIf { it.isNotBlank() }
    }

    fun unlinkAutorouter() {
        scope.launch {
            runCatching { apiClient.unlinkAutorouter() }.onSuccess {
                _autorouterLinked.value = false
            }
        }
    }

    fun resetMagicLinkState() {
        _magicLinkSent.value = false
        _authError.value = null
    }

    fun clearAuthError() {
        _authError.value = null
    }

    fun logout() {
        context?.let { ctx ->
            runCatching { aero.flyfun.weather.service.FlightTrackingService.stopTracking(ctx) }
            aero.flyfun.weather.service.FlightTrackingService.discardPersistedFixes(ctx)
        }
        tokenStore.clearToken()
        rebuildUserScopedRepositories(null)
        _isAuthenticated.value = false
        _isGuestBrowsing.value = false
        _isMockMode.value = false
        _magicLinkSent.value = false
        _userEmail.value = null
        _userPreferences.value = null
        _autorouterLinked.value = false
        _authError.value = null
    }

    /**
     * Delete the user's account on the server, then log out locally. Mirrors iOS
     * `AppState.deleteAccount()`: the account is gone, so this user's cache and the
     * offline PIREP queue go too rather than waiting for the janitor. Throws on a
     * server failure, leaving the user signed in.
     */
    suspend fun deleteAccount() {
        apiClient.deleteAccount()
        runCatching { briefingCache?.clearAll() }
        context?.let { ctx -> runCatching { PirepOfflineStore(ctx.filesDir).clear() } }
        logout()
    }

    private fun handleUnauthorized() {
        if (!_isMockMode.value && !_isGuestBrowsing.value) {
            logout()
        }
    }

    private fun extractQueryParam(uri: String, key: String): String? {
        val qIdx = uri.indexOf('?')
        if (qIdx < 0) return null
        val query = uri.substring(qIdx + 1).substringBefore('#')
        for (pair in query.split('&')) {
            val parts = pair.split('=', limit = 2)
            if (parts.size == 2 && parts[0] == key) {
                return java.net.URLDecoder.decode(parts[1], "UTF-8")
            }
        }
        return null
    }

    private fun autorouterLinkErrorMessage(reason: String?): String = when (reason) {
        "denied" -> "Autorouter access wasn't granted. Try again and choose Allow on autorouter.aero."
        "expired" -> "The connection request timed out. Please try again."
        else -> "Couldn't connect Autorouter. Please try again."
    }

    private companion object {
        /** Registered in the manifest and on the server's allowed callback schemes (as on iOS). */
        const val AUTOROUTER_CALLBACK_SCHEME = "flyfunweather"
    }
}
