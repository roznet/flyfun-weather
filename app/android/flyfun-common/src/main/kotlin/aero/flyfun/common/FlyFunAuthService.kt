package aero.flyfun.common

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Configuration for [FlyFunAuthService].
 *
 * Note: `flyfun-common`'s `/auth/login/{provider}` endpoint checks `platform == "ios"` along with
 * `scheme` (`"flyfunweather"`) and `state` to trigger the native mobile custom-scheme auth-code
 * redirect (`flyfunweather://auth/callback?code=...&state=...`) instead of setting a web cookie
 * and redirecting to `/`.
 */
data class AuthConfig(
    val baseUrl: String,
    val callbackScheme: String = "flyfunweather",
    val platform: String = "ios",
)

/**
 * Exception thrown when an authentication step fails.
 */
class AuthException(
    message: String,
    val statusCode: Int? = null,
    cause: Throwable? = null,
) : Exception(message, cause)

/**
 * Parsed parameters from an OAuth or deep-link auth callback URI.
 */
data class AuthCallbackParams(
    val code: String? = null,
    val state: String? = null,
    val token: String? = null,
    val error: String? = null,
)

/**
 * Unverified JWT claim inspection helpers used strictly for client-side routing and cache scoping.
 * Server signature verification remains the sole authority for authorization.
 */
object JwtClaims {
    private val json = Json { ignoreUnknownKeys = true }

    /**
     * Decode the middle (payload) segment of a JWT without verifying its signature.
     * Returns `null` if [token] is not a 3-part JWT or the payload is not a valid JSON object.
     */
    fun unverifiedPayload(token: String): JsonObject? {
        val parts = token.split('.')
        if (parts.size != 3) return null
        val payloadSegment = parts[1]
        if (payloadSegment.isEmpty()) return null

        var b64 = payloadSegment
            .replace('-', '+')
            .replace('_', '/')
        while (b64.length % 4 != 0) {
            b64 += "="
        }

        val decodedBytes = runCatching { Base64.getDecoder().decode(b64) }.getOrNull() ?: return null
        val payloadText = String(decodedBytes, Charsets.UTF_8)
        return runCatching { json.parseToJsonElement(payloadText).jsonObject }.getOrNull()
    }

    /**
     * Reads the unverified `scope` claim as a [String], or `null` if absent or non-string.
     */
    fun unverifiedScope(token: String): String? {
        val primitive = unverifiedPayload(token)?.get("scope") as? JsonPrimitive ?: return null
        if (!primitive.isString) return null
        return primitive.content
    }

    /**
     * Reads the unverified `sub` claim as a non-empty [String], coercing numeric subjects if needed.
     */
    fun unverifiedSubject(token: String): String? {
        val primitive = unverifiedPayload(token)?.get("sub") as? JsonPrimitive ?: return null
        return primitive.content.takeIf { it.isNotEmpty() }
    }

    /**
     * Filesystem-safe, non-reversible 16-hex-char SHA-256 prefix for per-user cache scoping.
     * Mirrors `AppState.cacheScope(forToken:)` on iOS.
     */
    fun cacheScope(token: String): String {
        val sub = unverifiedSubject(token) ?: return "anon"
        val digest = MessageDigest.getInstance("SHA-256").digest(sub.toByteArray(Charsets.UTF_8))
        return digest.take(8).joinToString("") { "%02x".format(it) }
    }

    /**
     * Two-guard routing gate for bare-token deep links (`flyfunweather://auth?token=<jwt>`):
     * 1. Rejects when [isAuthenticated] is true (prevents forced-logout / session overwrite).
     * 2. Accepts only when the unverified `scope` claim equals `"review"` (blocks login-CSRF).
     */
    fun shouldAcceptDeepLinkToken(token: String, isAuthenticated: Boolean): Boolean {
        if (isAuthenticated) return false
        return unverifiedScope(token) == "review"
    }
}

/**
 * OkHttp DNS resolver that maps `localhost.ro-z.me` to IPv4 loopback (`127.0.0.1`)
 * and the Android Emulator host-loopback alias (`10.0.2.2`) so TLS connections to
 * `https://localhost.ro-z.me:8443` work seamlessly on both host JVM and Android emulators
 * while preserving `localhost.ro-z.me` SNI and Let's Encrypt certificate verification.
 */
object LocalhostRozMeDns : okhttp3.Dns {
    override fun lookup(hostname: String): List<java.net.InetAddress> {
        if (hostname.equals("localhost.ro-z.me", ignoreCase = true)) {
            return listOf(
                java.net.InetAddress.getByAddress(hostname, byteArrayOf(127, 0, 0, 1)),
                java.net.InetAddress.getByAddress(hostname, byteArrayOf(10, 0, 2, 2)),
            )
        }
        return okhttp3.Dns.SYSTEM.lookup(hostname)
    }
}

/**
 * Shared authentication service for FlyFun apps:
 * - Native OAuth authorization-code flow with `state` nonce and `POST /auth/exchange`
 * - Email magic-link & 6-digit OTP code flow
 * - Debug `/auth/dev-token` fetch
 */
class FlyFunAuthService(
    val config: AuthConfig,
    private val httpClient: OkHttpClient = OkHttpClient.Builder().dns(LocalhostRozMeDns).build(),
) {
    private val secureRandom = SecureRandom()
    private val json = Json { ignoreUnknownKeys = true }

    @Serializable
    private data class CodeExchangeRequest(
        val code: String,
        val state: String,
        val code_verifier: String? = null,
    )

    @Serializable
    private data class MagicLinkRequest(
        val email: String,
    )

    @Serializable
    private data class MagicLinkConsumeCodeRequest(
        val email: String,
        val code: String,
    )

    @Serializable
    private data class TokenResponse(
        val token: String,
    )

    /**
     * Generates a cryptographically random URL-safe `state` nonce (without Base64 padding).
     */
    fun generateStateNonce(byteLength: Int = 24): String {
        val bytes = ByteArray(byteLength)
        secureRandom.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    /**
     * Generates a 32-byte PKCE `code_verifier` and its S256 `code_challenge`.
     */
    fun generatePkcePair(): Pair<String, String> {
        val verifierBytes = ByteArray(32)
        secureRandom.nextBytes(verifierBytes)
        val verifier = Base64.getUrlEncoder().withoutPadding().encodeToString(verifierBytes)
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
        val challenge = Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
        return verifier to challenge
    }

    /**
     * Builds the OAuth authorization URL for [provider], [state], and optional PKCE [codeChallenge].
     * Includes `platform=${config.platform}&scheme=${config.callbackScheme}&state=$state` so
     * `flyfun-common` redirects back to `${config.callbackScheme}://auth/callback?code=...&state=...`.
     */
    fun buildAuthorizeUrl(
        provider: String = "google",
        state: String = generateStateNonce(),
        codeChallenge: String? = null,
    ): String {
        val base = config.baseUrl.trimEnd('/')
        val baseQuery = "$base/auth/login/$provider?platform=${config.platform}&scheme=${config.callbackScheme}&state=$state"
        return if (!codeChallenge.isNullOrBlank()) {
            "$baseQuery&code_challenge=$codeChallenge&code_challenge_method=S256"
        } else {
            baseQuery
        }
    }

    /**
     * Alias for [buildAuthorizeUrl].
     */
    fun authorizationUrl(
        provider: String = "google",
        state: String = generateStateNonce(),
        codeChallenge: String? = null,
    ): String = buildAuthorizeUrl(provider = provider, state = state, codeChallenge = codeChallenge)

    /**
     * Parses an inbound OAuth callback URI (`flyfunweather://auth/callback?code=...&state=...`,
     * `https://weather.flyfun.aero/auth/callback?code=...&state=...`, or reviewer deep link
     * `flyfunweather://auth?token=...`).
     *
     * Returns `null` if the URI is malformed or does not match an auth callback shape.
     */
    fun parseCallbackUri(uriString: String): AuthCallbackParams? {
        val uri = runCatching { URI(uriString) }.getOrNull() ?: return null
        return parseCallbackUri(uri)
    }

    fun parseCallbackUri(uri: URI): AuthCallbackParams? {
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: ""
        val path = uri.path ?: ""

        val isCustomSchemeAuth = scheme == config.callbackScheme.lowercase() &&
            (host == "auth" || path.startsWith("/auth"))
        val isHttpsCallback = (scheme == "https" || scheme == "http") &&
            (path == "/auth/callback" || path.endsWith("/auth/callback"))

        if (!isCustomSchemeAuth && !isHttpsCallback) return null

        val queryParams = parseQueryParameters(uri.rawQuery)
        return AuthCallbackParams(
            code = queryParams["code"],
            state = queryParams["state"],
            token = queryParams["token"],
            error = queryParams["error"],
        )
    }

    /**
     * Parses [callbackUri], verifies that `state == expectedState`, and exchanges `code` + `state`
     * (and optional PKCE [codeVerifier]) for a session JWT via `POST /auth/exchange`.
     */
    suspend fun handleCallback(
        callbackUri: String,
        expectedState: String,
        codeVerifier: String? = null,
    ): String {
        val params = parseCallbackUri(callbackUri)
            ?: throw AuthException("Invalid auth callback URI")
        if (!params.error.isNullOrBlank()) {
            throw AuthException("OAuth error: ${params.error}")
        }
        val state = params.state
        if (state.isNullOrBlank() || state != expectedState) {
            throw AuthException("OAuth state mismatch")
        }
        val code = params.code
        if (code.isNullOrBlank()) {
            throw AuthException("Missing authorization code in callback URI")
        }
        return exchangeCode(code = code, state = state, codeVerifier = codeVerifier)
    }

    /**
     * Exchanges a one-time OAuth [code] and [state] for a session JWT via `POST /auth/exchange`.
     */
    suspend fun exchangeCode(
        code: String,
        state: String,
        codeVerifier: String? = null,
    ): String = withContext(Dispatchers.IO) {
        val url = "${config.baseUrl.trimEnd('/')}/auth/exchange"
        val bodyJson = json.encodeToString(
            CodeExchangeRequest.serializer(),
            CodeExchangeRequest(code = code, state = state, code_verifier = codeVerifier),
        )
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        httpClient.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw AuthException(
                    "Token exchange failed with HTTP ${response.code}",
                    statusCode = response.code,
                )
            }
            val tokenResponse = runCatching {
                json.decodeFromString(TokenResponse.serializer(), responseBody)
            }.getOrElse { err ->
                throw AuthException("Malformed token response from /auth/exchange", cause = err)
            }
            if (tokenResponse.token.isBlank()) {
                throw AuthException("Empty token in /auth/exchange response")
            }
            tokenResponse.token
        }
    }

    /**
     * Requests a magic-link email via `POST /auth/magic-link`.
     */
    suspend fun requestMagicLink(email: String) = withContext(Dispatchers.IO) {
        val normalized = email.trim().lowercase()
        val url = "${config.baseUrl.trimEnd('/')}/auth/magic-link"
        val bodyJson = json.encodeToString(MagicLinkRequest.serializer(), MagicLinkRequest(normalized))
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw AuthException(
                    "Magic link request failed with HTTP ${response.code}",
                    statusCode = response.code,
                )
            }
        }
    }

    /**
     * Requests a 6-digit magic-link sign-in code via `POST /auth/magic-link/request`.
     */
    suspend fun requestMagicLinkCode(email: String) = withContext(Dispatchers.IO) {
        val normalized = email.trim().lowercase()
        val url = "${config.baseUrl.trimEnd('/')}/auth/magic-link/request"
        val bodyJson = json.encodeToString(MagicLinkRequest.serializer(), MagicLinkRequest(normalized))
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw AuthException(
                    "Magic link code request failed with HTTP ${response.code}",
                    statusCode = response.code,
                )
            }
        }
    }

    /**
     * Exchanges a 6-digit email sign-in code for a session JWT via `POST /auth/magic-link/consume-code`.
     */
    suspend fun consumeMagicLinkCode(email: String, code: String): String = withContext(Dispatchers.IO) {
        val normalizedEmail = email.trim().lowercase()
        val normalizedCode = code.trim()
        val url = "${config.baseUrl.trimEnd('/')}/auth/magic-link/consume-code"
        val bodyJson = json.encodeToString(
            MagicLinkConsumeCodeRequest.serializer(),
            MagicLinkConsumeCodeRequest(email = normalizedEmail, code = normalizedCode),
        )
        val request = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        httpClient.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw AuthException(
                    "Magic link code consumption failed with HTTP ${response.code}",
                    statusCode = response.code,
                )
            }
            val tokenResponse = runCatching {
                json.decodeFromString(TokenResponse.serializer(), responseBody)
            }.getOrElse { err ->
                throw AuthException("Malformed token response from /auth/magic-link/consume-code", cause = err)
            }
            if (tokenResponse.token.isBlank()) {
                throw AuthException("Empty token in /auth/magic-link/consume-code response")
            }
            tokenResponse.token
        }
    }

    /**
     * Fetches a development JWT from `GET /auth/dev-token` (for local dev/emulator use only).
     */
    suspend fun fetchDevToken(): String = withContext(Dispatchers.IO) {
        val url = "${config.baseUrl.trimEnd('/')}/auth/dev-token"
        val request = Request.Builder()
            .url(url)
            .get()
            .build()

        httpClient.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw AuthException(
                    "Dev token fetch failed with HTTP ${response.code}",
                    statusCode = response.code,
                )
            }
            val tokenResponse = runCatching {
                json.decodeFromString(TokenResponse.serializer(), responseBody)
            }.getOrElse { err ->
                throw AuthException("Malformed token response from /auth/dev-token", cause = err)
            }
            if (tokenResponse.token.isBlank()) {
                throw AuthException("Empty token in /auth/dev-token response")
            }
            tokenResponse.token
        }
    }

    private fun parseQueryParameters(rawQuery: String?): Map<String, String> {
        if (rawQuery.isNullOrEmpty()) return emptyMap()
        val result = mutableMapOf<String, String>()
        for (pair in rawQuery.split('&')) {
            if (pair.isEmpty()) continue
            val idx = pair.indexOf('=')
            if (idx < 0) {
                val key = URLDecoder.decode(pair, Charsets.UTF_8)
                result[key] = ""
            } else {
                val key = URLDecoder.decode(pair.substring(0, idx), Charsets.UTF_8)
                val value = URLDecoder.decode(pair.substring(idx + 1), Charsets.UTF_8)
                result[key] = value
            }
        }
        return result
    }

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

        fun shouldAcceptDeepLinkToken(token: String, isAuthenticated: Boolean): Boolean =
            JwtClaims.shouldAcceptDeepLinkToken(token = token, isAuthenticated = isAuthenticated)
    }
}
