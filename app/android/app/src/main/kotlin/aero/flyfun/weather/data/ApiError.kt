package aero.flyfun.weather.data

/**
 * Typed domain/network errors for FlyFun Weather API operations.
 * Mirrors `APIError` on iOS.
 */
sealed class ApiError(
    override val message: String,
    override val cause: Throwable? = null,
) : Exception(message, cause) {

    data object Unauthorized : ApiError("Session expired. Please sign in again.")

    /** [detail] is the server's `detail` string (e.g. why a refresh was refused). */
    data class Forbidden(
        val detail: String? = null,
    ) : ApiError(detail?.takeIf { it.isNotBlank() } ?: "Access denied.")

    data object NotFound : ApiError("Not found.")

    data class ServerError(
        val statusCode: Int,
        val detail: String? = null,
    ) : ApiError(detail?.takeIf { it.isNotBlank() } ?: "Server error ($statusCode)")

    data class NetworkError(
        override val cause: Throwable,
    ) : ApiError("Network error: ${cause.localizedMessage ?: cause.message ?: "unknown"}", cause)

    data class DecodingError(
        override val cause: Throwable,
    ) : ApiError("Data error: ${cause.localizedMessage ?: cause.message ?: "invalid format"}", cause)

    data class OfflineNotCached(
        val detail: String = "Briefing not available offline. Connect to the internet or download the pack first.",
    ) : ApiError(detail)

    data class InvalidResponse(
        val detail: String = "Invalid response from server.",
    ) : ApiError(detail)
}
