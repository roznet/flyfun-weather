package aero.flyfun.common

import java.io.File

/**
 * Storage abstraction for the session Bearer JWT.
 */
interface TokenStore {
    fun readToken(): String?
    fun writeToken(token: String)
    fun clearToken()

    fun getToken(): String? = readToken()
    fun setToken(token: String) = writeToken(token)
}

/**
 * Thread-safe in-memory [TokenStore], useful for tests and ephemeral sessions.
 */
class InMemoryTokenStore(initialToken: String? = null) : TokenStore {
    private var currentToken: String? = initialToken

    @Synchronized
    override fun readToken(): String? = currentToken

    @Synchronized
    override fun writeToken(token: String) {
        currentToken = token
    }

    @Synchronized
    override fun clearToken() {
        currentToken = null
    }
}

/**
 * File-backed [TokenStore] that persists the Bearer JWT on disk.
 */
class FileTokenStore(private val file: File) : TokenStore {

    @Synchronized
    override fun readToken(): String? {
        if (!file.exists() || !file.isFile) return null
        return runCatching {
            file.readText(Charsets.UTF_8).trim().takeIf { it.isNotEmpty() }
        }.getOrNull()
    }

    @Synchronized
    override fun writeToken(token: String) {
        file.parentFile?.mkdirs()
        file.writeText(token, Charsets.UTF_8)
    }

    @Synchronized
    override fun clearToken() {
        if (file.exists()) {
            file.delete()
        }
    }
}
