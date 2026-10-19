package aero.flyfun.weather.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import aero.flyfun.common.FileTokenStore
import aero.flyfun.common.TokenStore
import java.io.File
import java.security.KeyStore
import java.security.Security
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Android Keystore-backed [TokenStore] that encrypts the session Bearer JWT at rest
 * using 256-bit `AES/GCM/NoPadding` (`filesDir/flyfun_auth_token.enc`).
 *
 * Automatically migrates and deletes any legacy plaintext `flyfun_auth_token.jwt` file on first read.
 * Falls back to [FileTokenStore] **only** in plain JVM unit test environments where the
 * `AndroidKeyStore` security provider is not registered; on Android devices, an encryption failure
 * keeps the previously stored token (never a plaintext copy of the new one), so a transient Keystore
 * error during an `X-Renewed-Token` rotation doesn't sign the pilot out.
 */
class AndroidTokenStore(
    private val storageDir: File,
) : TokenStore {

    constructor(context: Context) : this(context.filesDir)

    private val encryptedFile = File(storageDir, ENCRYPTED_FILE_NAME)
    private val tempEncryptedFile = File(storageDir, "$ENCRYPTED_FILE_NAME.tmp")
    private val legacyFileStore = FileTokenStore(File(storageDir, LEGACY_FILE_NAME))

    @Synchronized
    override fun readToken(): String? {
        if (encryptedFile.isFile) {
            decryptFromFile()?.let { return it }
        }
        // Migrate legacy plaintext file if present
        val legacyToken = legacyFileStore.readToken()
        if (!legacyToken.isNullOrBlank()) {
            if (isAndroidKeyStoreAvailable()) {
                if (encryptToFile(legacyToken)) {
                    legacyFileStore.clearToken()
                } else {
                    logWarning("Legacy token migration to AndroidKeyStore failed; will retry on next read")
                }
            }
            return legacyToken
        }
        return null
    }

    @Synchronized
    override fun writeToken(token: String) {
        val trimmed = token.trim()
        if (trimmed.isEmpty()) {
            clearToken()
            return
        }
        if (isAndroidKeyStoreAvailable()) {
            if (encryptToFile(trimmed)) {
                legacyFileStore.clearToken()
            } else {
                // Keep the previous (still encrypted) token rather than signing the pilot out;
                // the server keeps renewing, so the next rotation retries the write.
                logWarning("AndroidKeyStore encryption failed; keeping previous token, refusing plaintext fallback")
            }
        } else {
            // Pure JVM unit test environment without AndroidKeyStore provider
            legacyFileStore.writeToken(trimmed)
        }
    }

    @Synchronized
    override fun clearToken() {
        if (tempEncryptedFile.exists()) {
            tempEncryptedFile.delete()
        }
        if (encryptedFile.exists()) {
            encryptedFile.delete()
        }
        legacyFileStore.clearToken()
    }

    private fun isAndroidKeyStoreAvailable(): Boolean {
        return Security.getProvider(ANDROID_KEYSTORE) != null
    }

    private fun logWarning(message: String, error: Throwable? = null) {
        // android.util.Log is a stub in plain JVM unit tests
        runCatching { Log.w(TAG, message, error) }
    }

    private fun encryptToFile(plaintext: String): Boolean = runCatching {
        val secretKey = getOrCreateSecretKey() ?: return false
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey)
        val iv = cipher.iv
        if (iv == null || iv.isEmpty() || iv.size > 255) return false
        val cipherBytes = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))

        storageDir.mkdirs()
        val payload = ByteArray(1 + iv.size + cipherBytes.size)
        payload[0] = iv.size.toByte()
        System.arraycopy(iv, 0, payload, 1, iv.size)
        System.arraycopy(cipherBytes, 0, payload, 1 + iv.size, cipherBytes.size)

        tempEncryptedFile.writeBytes(payload)
        if (!tempEncryptedFile.renameTo(encryptedFile)) {
            encryptedFile.writeBytes(payload)
            tempEncryptedFile.delete()
        }
        true
    }.onFailure { logWarning("Token encryption failed", it) }.getOrDefault(false)

    private fun decryptFromFile(): String? = runCatching {
        val payload = encryptedFile.readBytes()
        if (payload.size < 2) return null
        val ivLength = payload[0].toInt() and 0xFF
        if (ivLength <= 0 || payload.size <= 1 + ivLength) return null

        val iv = payload.copyOfRange(1, 1 + ivLength)
        val cipherBytes = payload.copyOfRange(1 + ivLength, payload.size)

        val secretKey = getSecretKey() ?: return null
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey, GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
        val plainBytes = cipher.doFinal(cipherBytes)
        String(plainBytes, Charsets.UTF_8).trim().takeIf { it.isNotEmpty() }
    }.onFailure { logWarning("Token decryption failed", it) }.getOrNull()

    private fun getSecretKey(): SecretKey? = runCatching {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val entry = keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
        entry?.secretKey
    }.getOrNull()

    private fun getOrCreateSecretKey(): SecretKey? = runCatching {
        getSecretKey()?.let { return it }
        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()
        keyGenerator.init(spec)
        keyGenerator.generateKey()
    }.onFailure { logWarning("AndroidKeyStore key creation failed", it) }.getOrNull()

    companion object {
        private const val TAG = "AndroidTokenStore"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "aero.flyfun.weather.jwt_key_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_TAG_LENGTH_BITS = 128
        internal const val ENCRYPTED_FILE_NAME = "flyfun_auth_token.enc"
        internal const val LEGACY_FILE_NAME = "flyfun_auth_token.jwt"
    }
}
