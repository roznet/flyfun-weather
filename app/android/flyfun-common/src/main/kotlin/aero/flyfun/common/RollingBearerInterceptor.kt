package aero.flyfun.common

import okhttp3.Interceptor
import okhttp3.Response

/**
 * OkHttp [Interceptor] mirroring `RollingBearerSession` from iOS `FlyFunCommon`:
 * - Attaches `Authorization: Bearer <token>` from [tokenStore] if present and not already set.
 * - Updates [tokenStore] when the server responds with a non-blank `X-Renewed-Token` header.
 * - Clears [tokenStore] and invokes [onUnauthorized] when the server responds with HTTP 401.
 */
class RollingBearerInterceptor(
    private val tokenStore: TokenStore,
    private val onUnauthorized: () -> Unit = {},
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val originalRequest = chain.request()
        val token = tokenStore.readToken()?.trim()

        val request = if (!token.isNullOrEmpty() && originalRequest.header(HEADER_AUTHORIZATION) == null) {
            originalRequest.newBuilder()
                .header(HEADER_AUTHORIZATION, "Bearer $token")
                .build()
        } else {
            originalRequest
        }

        val response = chain.proceed(request)

        val renewedToken = response.header(HEADER_RENEWED_TOKEN)?.trim()
        if (!renewedToken.isNullOrEmpty()) {
            tokenStore.writeToken(renewedToken)
        }

        if (response.code == HTTP_UNAUTHORIZED) {
            tokenStore.clearToken()
            onUnauthorized()
        }

        return response
    }

    companion object {
        const val HEADER_AUTHORIZATION = "Authorization"
        const val HEADER_RENEWED_TOKEN = "X-Renewed-Token"
        private const val HTTP_UNAUTHORIZED = 401
    }
}
