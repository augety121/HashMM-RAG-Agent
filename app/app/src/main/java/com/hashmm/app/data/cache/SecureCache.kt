package com.hashmm.app.data.cache

import android.content.Context
import androidx.security.crypto.EncryptedFile
import androidx.security.crypto.MasterKey
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 账号隔离的加密本地缓存（对标大厂的本地静态加密）。
 *  · 文件用 AES256-GCM 加密，密钥由 Android Keystore 托管（设备外无法解密）。
 *  · 每个账号一个独立目录；退出账号时整目录清空——别的账号读不到上一个账号的数据。
 *  · App 私有存储本就与其它 App 隔离；叠加 Keystore 加密 + 退登清缓存，达到「仅本账号可读」。
 */
@Singleton
class SecureCache @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val masterKey: MasterKey by lazy {
        MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
    }

    private fun userDir(userId: String): File =
        File(context.filesDir, "cache/${safe(userId)}").apply { mkdirs() }

    private fun safe(name: String): String = name.replace(Regex("[^A-Za-z0-9_.-]"), "_")

    @Synchronized
    fun write(userId: String, name: String, content: String) {
        try {
            val file = File(userDir(userId), "${safe(name)}.enc")
            if (file.exists()) file.delete()  // EncryptedFile 不支持覆盖已存在文件，先删
            val ef = EncryptedFile.Builder(
                context, file, masterKey,
                EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB
            ).build()
            ef.openFileOutput().use { it.write(content.toByteArray(Charsets.UTF_8)) }
        } catch (e: Exception) {
            // 缓存写失败不致命（下次同步会重试）
        }
    }

    @Synchronized
    fun read(userId: String, name: String): String? {
        return try {
            val file = File(userDir(userId), "${safe(name)}.enc")
            if (!file.exists()) return null
            val ef = EncryptedFile.Builder(
                context, file, masterKey,
                EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB
            ).build()
            ef.openFileInput().use { String(it.readBytes(), Charsets.UTF_8) }
        } catch (e: Exception) {
            null
        }
    }

    /** 退出账号时清空该账号的本地缓存。 */
    fun clear(userId: String) {
        try {
            userDir(userId).deleteRecursively()
        } catch (e: Exception) {
            // ignore
        }
    }
}
