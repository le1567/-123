package com.opsdesk.console

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * 与 Python 端完全一致的规范 JSON 序列化：
 * 键排序、紧凑分隔符、UTF-8、不转义非 ASCII。
 */
object CanonicalJson {
    fun of(value: Any?): String {
        if (value === JSONObject.NULL) return "null"
        return ofValue(value)
    }

    private fun ofValue(value: Any?): String = when (value) {
        null -> "null"
        is JSONObject -> value.keys().asSequence().sorted()
            .joinToString(",", "{", "}") { key -> quote(key) + ":" + of(value.get(key)) }
        is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { index -> of(value.get(index)) }
        is String -> quote(value)
        is Boolean -> value.toString()
        is Int, is Long, is Short, is Byte -> value.toString()
        is Double -> if (value % 1.0 == 0.0 && !value.isInfinite()) value.toString() else value.toString()
        is Float -> value.toString()
        else -> quote(value.toString())
    }

    private fun quote(text: String): String {
        val builder = StringBuilder("\"")
        for (ch in text) {
            when (ch) {
                '"' -> builder.append("\\\"")
                '\\' -> builder.append("\\\\")
                '\n' -> builder.append("\\n")
                '\r' -> builder.append("\\r")
                '\t' -> builder.append("\\t")
                '\b' -> builder.append("\\b")
                '\u000C' -> builder.append("\\f")
                else -> if (ch.code < 0x20) {
                    builder.append("\\u").append(ch.code.toString(16).padStart(4, '0'))
                } else {
                    builder.append(ch)
                }
            }
        }
        return builder.append("\"").toString()
    }
}

/** 管理端签名密钥：私钥保存在 Android Keystore，无法导出。 */
object AdminKeys {
    private const val ALIAS = "opsdesk_admin_ec_v1"
    private const val PROVIDER = "AndroidKeyStore"

    private fun keyStore(): KeyStore {
        val store = KeyStore.getInstance(PROVIDER)
        store.load(null)
        return store
    }

    fun hasKey(): Boolean = try {
        keyStore().containsAlias(ALIAS)
    } catch (_: Exception) {
        false
    }

    fun ensureKey(): ByteArray {
        val store = keyStore()
        if (!store.containsAlias(ALIAS)) {
            val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, PROVIDER)
            generator.initialize(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .setUserAuthenticationRequired(false)
                    .build()
            )
            generator.generateKeyPair()
        }
        val certificate = store.getCertificate(ALIAS) ?: error("管理密钥生成失败")
        return certificate.publicKey.encoded
    }

    fun publicDer(): ByteArray = ensureKey()

    fun publicPem(): String {
        val encoded = Base64.encodeToString(publicDer(), Base64.NO_WRAP)
        val wrapped = encoded.chunked(64).joinToString("\n")
        return "-----BEGIN PUBLIC KEY-----\n$wrapped\n-----END PUBLIC KEY-----\n"
    }

    fun fingerprint(): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(publicDer())
        return digest.take(8).joinToString("") { "%02X".format(it) }
    }

    fun sign(document: JSONObject): String {
        val store = keyStore()
        val privateKey = store.getKey(ALIAS, null) as? PrivateKey ?: error("管理密钥不存在")
        val payload = CanonicalJson.of(document).toByteArray(Charsets.UTF_8)
        val signature = Signature.getInstance("SHA256withECDSA")
        signature.initSign(privateKey)
        signature.update(payload)
        return Base64.encodeToString(signature.sign(), Base64.NO_WRAP)
    }

    fun reset() {
        val store = keyStore()
        if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS)
        ensureKey()
    }
}
