package com.hashmm.app.data.cache

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.security.crypto.EncryptedFile
import androidx.security.crypto.MasterKey
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.StandardCopyOption
import java.security.KeyStore
import java.util.concurrent.ConcurrentHashMap
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 账号隔离的本地加密副本。
 *
 * 数据写入 noBackupFilesDir，不进入云备份/设备迁移；AES-256-GCM 密钥只存在于
 * Android Keystore。每个文件以账号和逻辑名称作为 AAD，密文即使在账号目录间
 * 被替换也无法通过认证。写入采用同目录临时文件 + 原子替换，不再先删旧副本。
 */
@Singleton
class SecureCache @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private companion object {
        const val KEY_ALIAS = "hashmm_secure_cache_v2"
        val MAGIC = byteArrayOf('H'.code.toByte(), 'M'.code.toByte(), 'C'.code.toByte(), '2'.code.toByte())
        const val MAX_CIPHERTEXT_BYTES = 64 * 1024 * 1024
    }

    private val locks = ConcurrentHashMap<String, Any>()

    private val secretKey: SecretKey by lazy {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey) ?: KeyGenerator
            .getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setKeySize(256)
                        .setRandomizedEncryptionRequired(true)
                        .build(),
                )
            }
            .generateKey()
    }

    private fun safe(name: String): String = name.replace(Regex("[^A-Za-z0-9_.-]"), "_")

    private fun userDir(userId: String): File =
        File(context.noBackupFilesDir, "hashmm-cache/${safe(userId)}").apply { mkdirs() }

    private fun legacyUserDir(userId: String): File =
        File(context.filesDir, "cache/${safe(userId)}")

    private fun cacheFile(userId: String, name: String): File =
        File(userDir(userId), "${safe(name)}.enc")

    private fun aad(userId: String, name: String): ByteArray =
        "${safe(userId)}:${safe(name)}:v2".toByteArray(Charsets.UTF_8)

    private fun lockFor(userId: String, name: String): Any =
        locks.getOrPut("${safe(userId)}/${safe(name)}") { Any() }

    fun write(userId: String, name: String, content: String) {
        synchronized(lockFor(userId, name)) {
            runCatching {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.ENCRYPT_MODE, secretKey)
                cipher.updateAAD(aad(userId, name))
                val encrypted = cipher.doFinal(content.toByteArray(Charsets.UTF_8))
                val payload = MAGIC + byteArrayOf(cipher.iv.size.toByte()) + cipher.iv + encrypted
                val target = cacheFile(userId, name)
                val temporary = File(target.parentFile, ".${target.name}.${System.nanoTime()}.tmp")
                try {
                    FileOutputStream(temporary).use { output ->
                        output.write(payload)
                        output.fd.sync()
                    }
                    try {
                        Files.move(
                            temporary.toPath(),
                            target.toPath(),
                            StandardCopyOption.ATOMIC_MOVE,
                            StandardCopyOption.REPLACE_EXISTING,
                        )
                    } catch (_: AtomicMoveNotSupportedException) {
                        Files.move(
                            temporary.toPath(),
                            target.toPath(),
                            StandardCopyOption.REPLACE_EXISTING,
                        )
                    }
                } finally {
                    temporary.delete()
                }
            }
        }
    }

    fun read(userId: String, name: String): String? = synchronized(lockFor(userId, name)) {
        val current = cacheFile(userId, name)
        if (current.exists()) return@synchronized readV2(current, userId, name)

        // One-time compatibility migration from releases that used EncryptedFile.
        val legacy = readLegacy(userId, name) ?: return@synchronized null
        write(userId, name, legacy)
        File(legacyUserDir(userId), "${safe(name)}.enc").delete()
        legacy
    }

    private fun readV2(file: File, userId: String, name: String): String? = runCatching {
        if (file.length() !in 6..MAX_CIPHERTEXT_BYTES.toLong()) return@runCatching null
        val payload = file.readBytes()
        if (!payload.copyOfRange(0, MAGIC.size).contentEquals(MAGIC)) return@runCatching null
        val ivSize = payload[MAGIC.size].toInt() and 0xff
        if (ivSize !in 12..32 || MAGIC.size + 1 + ivSize >= payload.size) return@runCatching null
        val start = MAGIC.size + 1
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, secretKey, GCMParameterSpec(128, payload.copyOfRange(start, start + ivSize)))
        cipher.updateAAD(aad(userId, name))
        String(cipher.doFinal(payload.copyOfRange(start + ivSize, payload.size)), Charsets.UTF_8)
    }.getOrNull()

    @Suppress("DEPRECATION")
    private fun readLegacy(userId: String, name: String): String? = runCatching {
        val file = File(legacyUserDir(userId), "${safe(name)}.enc")
        if (!file.exists()) return@runCatching null
        val legacyKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        val encryptedFile = EncryptedFile.Builder(
            context,
            file,
            legacyKey,
            EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB,
        ).build()
        encryptedFile.openFileInput().use { String(it.readBytes(), Charsets.UTF_8) }
    }.getOrNull()

    fun delete(userId: String, name: String) {
        synchronized(lockFor(userId, name)) {
            cacheFile(userId, name).delete()
            File(legacyUserDir(userId), "${safe(name)}.enc").delete()
        }
    }

    fun clear(userId: String) {
        userDir(userId).deleteRecursively()
        legacyUserDir(userId).deleteRecursively()
        val prefix = "${safe(userId)}/"
        locks.keys.removeAll { it.startsWith(prefix) }
    }
}
