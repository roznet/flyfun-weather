package aero.flyfun.common

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.Base64

class FlyFunAuthServiceTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun makeJwt(payloadJson: String): String {
        fun b64url(text: String): String =
            Base64.getUrlEncoder().withoutPadding().encodeToString(text.toByteArray(Charsets.UTF_8))

        val header = b64url("""{"alg":"HS256","typ":"JWT"}""")
        val body = b64url(payloadJson)
        return "$header.$body.signature-not-checked"
    }

    @Test
    fun generatesUrlSafeStateNonceAndAuthorizeUrl() {
        val service = FlyFunAuthService(
            AuthConfig(
                baseUrl = "https://weather.flyfun.aero/",
                callbackScheme = "flyfunweather",
            )
        )
        val nonce1 = service.generateStateNonce()
        val nonce2 = service.generateStateNonce()
        assertTrue(nonce1.length >= 32)
        assertNotEquals(nonce1, nonce2)
        assertFalse(nonce1.contains("="))
        assertFalse(nonce1.contains("+"))
        assertFalse(nonce1.contains("/"))

        val url = service.buildAuthorizeUrl(provider = "google", state = "state-123")
        assertEquals(
            "https://weather.flyfun.aero/auth/login/google?platform=ios&scheme=flyfunweather&state=state-123",
            url,
        )

        val (verifier, challenge) = service.generatePkcePair()
        assertTrue(verifier.length >= 43)
        assertTrue(challenge.isNotBlank())
        val pkceUrl = service.buildAuthorizeUrl(
            provider = "google",
            state = "state-123",
            codeChallenge = challenge,
        )
        assertEquals(
            "https://weather.flyfun.aero/auth/login/google?platform=ios&scheme=flyfunweather&state=state-123&code_challenge=$challenge&code_challenge_method=S256",
            pkceUrl,
        )
    }

    @Test
    fun parsesCustomSchemeAndHttpsCallbackUris() {
        val service = FlyFunAuthService(AuthConfig(baseUrl = "https://weather.flyfun.aero"))

        val customParams = service.parseCallbackUri("flyfunweather://auth/callback?code=abc-123&state=xyz-789")
        assertNotNull(customParams)
        assertEquals("abc-123", customParams?.code)
        assertEquals("xyz-789", customParams?.state)

        val httpsParams = service.parseCallbackUri("https://weather.flyfun.aero/auth/callback?code=code2&state=state2")
        assertNotNull(httpsParams)
        assertEquals("code2", httpsParams?.code)
        assertEquals("state2", httpsParams?.state)

        val reviewDeepLink = service.parseCallbackUri("flyfunweather://auth?token=review-jwt")
        assertNotNull(reviewDeepLink)
        assertEquals("review-jwt", reviewDeepLink?.token)

        assertNull(service.parseCallbackUri("https://weather.flyfun.aero/briefing.html?flight=123"))
    }

    @Test
    fun exchangesAuthorizationCodeAndVerifiesState() = runTest {
        val baseUrl = server.url("/").toString().trimEnd('/')
        val service = FlyFunAuthService(AuthConfig(baseUrl = baseUrl))

        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"token":"minted-session-jwt"}""")
        )

        val token = service.handleCallback(
            callbackUri = "flyfunweather://auth/callback?code=one-time-code&state=expected-nonce",
            expectedState = "expected-nonce",
            codeVerifier = "pkce-verifier-123",
        )
        assertEquals("minted-session-jwt", token)

        val recorded = server.takeRequest()
        assertEquals("POST", recorded.method)
        assertEquals("/auth/exchange", recorded.path)
        val bodyObj = Json.parseToJsonElement(recorded.body.readUtf8()).jsonObject
        assertEquals("one-time-code", bodyObj["code"]?.jsonPrimitive?.content)
        assertEquals("expected-nonce", bodyObj["state"]?.jsonPrimitive?.content)
        assertEquals("pkce-verifier-123", bodyObj["code_verifier"]?.jsonPrimitive?.content)
    }

    @Test
    fun rejectsCallbackWhenStateDoesNotMatch() = runTest {
        val baseUrl = server.url("/").toString().trimEnd('/')
        val service = FlyFunAuthService(AuthConfig(baseUrl = baseUrl))

        try {
            service.handleCallback(
                callbackUri = "flyfunweather://auth/callback?code=one-time-code&state=wrong-nonce",
                expectedState = "expected-nonce",
            )
            fail("Expected AuthException on state mismatch")
        } catch (e: AuthException) {
            assertTrue(e.message!!.contains("state", ignoreCase = true))
        }
    }

    @Test
    fun supportsMagicLinkAndDevTokenEndpoints() = runTest {
        val baseUrl = server.url("/").toString().trimEnd('/')
        val service = FlyFunAuthService(AuthConfig(baseUrl = baseUrl))

        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        service.requestMagicLink("  Pilot@Example.com ")
        val magicReq = server.takeRequest()
        assertEquals("/auth/magic-link", magicReq.path)
        val magicBody = Json.parseToJsonElement(magicReq.body.readUtf8()).jsonObject
        assertEquals("pilot@example.com", magicBody["email"]?.jsonPrimitive?.content)

        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"token":"otp-jwt"}"""))
        val otpToken = service.consumeMagicLinkCode("Pilot@Example.com", "654321")
        assertEquals("otp-jwt", otpToken)
        val consumeReq = server.takeRequest()
        assertEquals("/auth/magic-link/consume-code", consumeReq.path)

        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"token":"dev-jwt-token"}"""))
        val devToken = service.fetchDevToken()
        assertEquals("dev-jwt-token", devToken)
        val devReq = server.takeRequest()
        assertEquals("/auth/dev-token", devReq.path)
    }

    // --- Port of ReviewTokenScopeTests.swift ---

    @Test
    fun extractsReviewScope() {
        val token = makeJwt("""{"sub":"u1","scope":"review"}""")
        assertEquals("review", JwtClaims.unverifiedScope(token))
    }

    @Test
    fun missingScopeReturnsNull() {
        val token = makeJwt("""{"sub":"u1","email":"a@b.com"}""")
        assertNull(JwtClaims.unverifiedScope(token))
    }

    @Test
    fun nonReviewScopeReturnsItsValue() {
        val token = makeJwt("""{"sub":"u1","scope":"mcp"}""")
        assertEquals("mcp", JwtClaims.unverifiedScope(token))
    }

    @Test
    fun nonJwtShapedStringReturnsNull() {
        assertNull(JwtClaims.unverifiedScope("not-a-jwt"))
        assertNull(JwtClaims.unverifiedScope("only.two"))
    }

    @Test
    fun nonJsonPayloadReturnsNull() {
        val token = "header.bm90LWpzb24.sig" // "not-json" base64url, not JSON
        assertNull(JwtClaims.unverifiedScope(token))
    }

    @Test
    fun handlesBase64PayloadNeedingPadding() {
        val token = makeJwt("""{"scope":"review"}""")
        assertEquals("review", JwtClaims.unverifiedScope(token))
    }

    @Test
    fun scopeWrongTypeReturnsNull() {
        val token = makeJwt("""{"sub":"u1","scope":42}""")
        assertNull(JwtClaims.unverifiedScope(token))
    }

    @Test
    fun acceptsReviewTokenWhenSignedOut() {
        val reviewToken = makeJwt("""{"sub":"u1","scope":"review"}""")
        assertTrue(JwtClaims.shouldAcceptDeepLinkToken(reviewToken, isAuthenticated = false))
    }

    @Test
    fun rejectsReviewTokenWhenAlreadyAuthenticated() {
        val reviewToken = makeJwt("""{"sub":"u1","scope":"review"}""")
        assertFalse(JwtClaims.shouldAcceptDeepLinkToken(reviewToken, isAuthenticated = true))
    }

    @Test
    fun rejectsNonReviewTokenWhenSignedOut() {
        val realToken = makeJwt("""{"sub":"u1","email":"a@b.com"}""")
        assertFalse(JwtClaims.shouldAcceptDeepLinkToken(realToken, isAuthenticated = false))
    }

    @Test
    fun rejectsNonReviewTokenWhenAuthenticated() {
        val realToken = makeJwt("""{"sub":"u1","email":"a@b.com"}""")
        assertFalse(JwtClaims.shouldAcceptDeepLinkToken(realToken, isAuthenticated = true))
    }

    @Test
    fun computesStableCacheScopeFromSubject() {
        val token = makeJwt("""{"sub":"user-123"}""")
        val scope = JwtClaims.cacheScope(token)
        assertEquals(16, scope.length)
        assertEquals(scope, JwtClaims.cacheScope(token))
        assertEquals("anon", JwtClaims.cacheScope("invalid.jwt"))
    }
}
