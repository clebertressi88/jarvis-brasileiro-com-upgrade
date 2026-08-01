package br.com.jarvis.remote

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureStore(context: Context) {
    private val preferences = context.getSharedPreferences("jarvis_remote", Context.MODE_PRIVATE)
    private val keyAlias = "jarvis_remote_pairing_key"

    val clientId: String
        get() {
            val stored = preferences.getString("client_id", null)
            if (stored != null) return stored
            return UUID.randomUUID().toString().also {
                preferences.edit().putString("client_id", it).apply()
            }
        }

    var serverUrl: String
        get() = preferences.getString("server_url", "") ?: ""
        set(value) {
            preferences.edit().putString("server_url", value).apply()
        }

    fun saveSecret(secret: ByteArray) {
        require(secret.size == 32) { "Segredo inválido" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val encrypted = cipher.doFinal(secret)
        val combined = cipher.iv + encrypted
        preferences.edit()
            .putString("paired_secret", Base64.encodeToString(combined, Base64.NO_WRAP))
            .apply()
    }

    fun loadSecret(): ByteArray? {
        val encoded = preferences.getString("paired_secret", null) ?: return null
        return try {
            val combined = Base64.decode(encoded, Base64.DEFAULT)
            if (combined.size <= 12) return null
            val iv = combined.copyOfRange(0, 12)
            val encrypted = combined.copyOfRange(12, combined.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(128, iv))
            cipher.doFinal(encrypted).takeIf { it.size == 32 }
        } catch (_: Exception) {
            null
        }
    }

    fun clearPairing() {
        preferences.edit().remove("paired_secret").apply()
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(keyAlias, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore",
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                keyAlias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }
}
