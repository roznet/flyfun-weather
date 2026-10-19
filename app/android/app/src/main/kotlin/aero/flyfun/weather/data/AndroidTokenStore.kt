package aero.flyfun.weather.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import aero.flyfun.common.FileTokenStore
import aero.flyfun.common.TokenStore
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Android Keystore-backed [TokenStore] that encrypts the session Bearer JWT at rest
 * using 256-bit `AES/GCM/NoPadding` (`filesDir/flyfun_auth_token.enc`).
 *
 * Automatically migrates any legacy plaintext `flyfun_auth_token.jwt` file on first read,
 * and falls back to [FileTokenStore] when running in plain JVM unit test environments
 * where `AndroidKeyStore` is not registered.
 */
class AndroidTokenStore(context: Context) : TokenStore {

    private val encryptedFile = File(context.filesDir, ENCRYPTED_FILE_NAME)
    private val legacyFileStore = FileTokenStore(File(context.filesDir, LEGACY_FILE_NAME))

    @Synchronized
    override fun readToken(): String? {
        if (encryptedFile.isFile) {
            decryptFromFile()?.let { return it }
        }
        // Migrate or fall back to legacy plaintext file if present
        val legacyToken = legacyFileStore.readToken()
        if (!legacyToken.isNullOrBlank()) {
            if (encryptToFile(legacyToken)) {
                legacyFileStore.clearToken()
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
        if (encryptToFile(trimmed)) {
            legacyFileStore.clearToken()
        } else {
            legacyFileStore.writeToken(trimmed)
        }
    }

    @Synchronized
    override fun clearToken() {
        if (encryptedFile.exists()) {
            encryptedFile.delete()
        }
        legacyFileStore.clearToken()
    }

    private fun encryptToFile(plaintext: String): Boolean = runCatching {
        val secretKey = getOrCreateSecretKey() ?: return false
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey)
        val iv = cipher.iv
        if (iv == null || iv.isEmpty() || iv.size > 255) return false
        val cipherBytes = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))

        encryptedFile.parentFile?.mkdirs()
        val payload = ByteArray(1 + iv.size + cipherBytes.size)
        payload[0] = iv.size.toByte()
        System.arraycopy(iv, 0, payload, 1, iv.size)
        System.arraycopy(cipherBytes, 0, payload, 1 + iv.size, cipherBytes.size)
        encryptedFile.writeBytes(payload)
        true
    }.getOrDefault(false)

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
    }.getOrNull()

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
    }.getOrNull()

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "aero.flyfun.weather.jwt_key_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_TAG_LENGTH_BITS = 128
        private const val ENCRYPTED_FILE_NAME = "flyfun_auth_token.enc"
        private const val LEGACY_FILE_NAME = "flyfun_auth_token.jwt"
    }
}
