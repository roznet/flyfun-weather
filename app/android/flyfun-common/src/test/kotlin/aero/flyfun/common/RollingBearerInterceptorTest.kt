package aero.flyfun.common

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.nio.file.Files

class RollingBearerInterceptorTest {

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

    @Test
    fun attachesBearerHeaderWhenTokenPresent() {
        val store = InMemoryTokenStore("initial-jwt-token")
        val client = OkHttpClient.Builder()
            .addInterceptor(RollingBearerInterceptor(store))
            .build()

        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))

        val request = Request.Builder().url(server.url("/api/flights")).build()
        client.newCall(request).execute().use { response ->
            assertEquals(200, response.code)
        }

        val recorded = server.takeRequest()
        assertEquals("Bearer initial-jwt-token", recorded.getHeader("Authorization"))
    }

    @Test
    fun omitsAuthorizationHeaderWhenTokenStoreEmpty() {
        val store = InMemoryTokenStore(null)
        val client = OkHttpClient.Builder()
            .addInterceptor(RollingBearerInterceptor(store))
            .build()

        server.enqueue(MockResponse().setResponseCode(200))

        val request = Request.Builder().url(server.url("/api/help/catalog")).build()
        client.newCall(request).execute().close()

        val recorded = server.takeRequest()
        assertNull(recorded.getHeader("Authorization"))
    }

    @Test
    fun preservesExplicitAuthorizationHeader() {
        val store = InMemoryTokenStore("store-jwt")
        val client = OkHttpClient.Builder()
            .addInterceptor(RollingBearerInterceptor(store))
            .build()

        server.enqueue(MockResponse().setResponseCode(200))

        val request = Request.Builder()
            .url(server.url("/api/flights"))
            .header("Authorization", "Bearer explicit-jwt")
            .build()
        client.newCall(request).execute().close()

        val recorded = server.takeRequest()
        assertEquals("Bearer explicit-jwt", recorded.getHeader("Authorization"))
    }

    @Test
    fun updatesTokenStoreOnXRenewedTokenHeaderCaseInsensitive() {
        val store = InMemoryTokenStore("old-jwt")
        val client = OkHttpClient.Builder()
            .addInterceptor(RollingBearerInterceptor(store))
            .build()

        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .addHeader("x-renewed-token", "rotated-jwt-token")
                .setBody("{}")
        )

        val request = Request.Builder().url(server.url("/api/flights")).build()
        client.newCall(request).execute().close()

        assertEquals("rotated-jwt-token", store.readToken())
    }

    @Test
    fun clearsTokenStoreAndInvokesCallbackOn401() {
        val store = InMemoryTokenStore("expired-jwt")
        var unauthorizedCount = 0
        val client = OkHttpClient.Builder()
            .addInterceptor(
                RollingBearerInterceptor(store) {
                    unauthorizedCount += 1
                }
            )
            .build()

        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"Expired"}"""))

        val request = Request.Builder().url(server.url("/api/flights")).build()
        client.newCall(request).execute().use { response ->
            assertEquals(401, response.code)
        }

        assertNull(store.readToken())
        assertEquals(1, unauthorizedCount)
    }

    @Test
    fun fileTokenStorePersistsAndClearsToken() {
        val tempDir = Files.createTempDirectory("token-store-test").toFile()
        try {
            val tokenFile = tempDir.resolve("nested/session.jwt")
            val store = FileTokenStore(tokenFile)

            assertNull(store.readToken())

            store.writeToken("persisted-jwt-123")
            assertTrue(tokenFile.exists())
            assertEquals("persisted-jwt-123", store.readToken())

            store.clearToken()
            assertFalse(tokenFile.exists())
            assertNull(store.readToken())
        } finally {
            tempDir.deleteRecursively()
        }
    }
}
