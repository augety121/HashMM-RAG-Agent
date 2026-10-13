package com.hashmm.app.data.settings

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * V305 安全加固（源码审计 P0-1）：直连模型 API Key 的**加密存储**。
 *
 * 之前 API Key 明文存在普通 Preferences DataStore，Root/调试/取证/错误备份都可能读到。这里改用
 * Android Keystore 托管的 EncryptedSharedPreferences（AES256-SIV 键 + AES256-GCM 值，主密钥由
 * 硬件/软件 Keystore 保管），密钥落盘即密文。DataStore 里只留"是否已配置"标记与掩码，不再存明文。
 *
 * 失败可观测（对齐审计 P1-9）：不再完全静默——加解密异常记脱敏日志（只记异常类型，不记密钥内容）。
 */
@Singleton
class SecureKeyStore @Inject constructor(@ApplicationContext private val context: Context) {

    private val prefs by lazy {
        val master = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "hashmm_secure_keys",
            master,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun getApiKey(): String? = try {
        prefs.getString(K_API, null)
    } catch (e: Exception) {
        Log.w("SecureKeyStore", "读取加密密钥失败：${e.javaClass.simpleName}")
        null
    }

    /** 写入密钥。空串不写（避免"清空输入框保存"意外抹掉已存密钥）——空值不覆盖由调用方保证。 */
    fun putApiKey(value: String) {
        try {
            prefs.edit().putString(K_API, value).apply()
        } catch (e: Exception) {
            Log.w("SecureKeyStore", "写入加密密钥失败：${e.javaClass.simpleName}")
        }
    }

    fun clearApiKey() {
        try {
            prefs.edit().remove(K_API).apply()
        } catch (e: Exception) {
            Log.w("SecureKeyStore", "清除加密密钥失败：${e.javaClass.simpleName}")
        }
    }

    fun hasApiKey(): Boolean = !getApiKey().isNullOrBlank()

    companion object {
        private const val K_API = "direct_api_key"
    }
}
