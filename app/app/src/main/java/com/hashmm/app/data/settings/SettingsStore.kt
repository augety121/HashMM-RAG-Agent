package com.hashmm.app.data.settings

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.hashmm.app.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore by preferencesDataStore(name = "hashmm_settings")

/** 应用设置：客户端地址。
 *  优先级：用户手动填写 > Supabase 云端同步（零配置）> 编译期默认值。 */
@Singleton
class SettingsStore @Inject constructor(@ApplicationContext private val ctx: Context) {

    private val keyClientUrl = stringPreferencesKey("client_url")          // 用户手动填写（最高优先）
    private val keySyncedUrl = stringPreferencesKey("synced_client_url")   // Supabase 云端同步

    /** 生效的客户端地址：手动 > 云端同步 > 编译默认。 */
    val clientUrl: Flow<String> = ctx.dataStore.data.map { prefs ->
        prefs[keyClientUrl]?.takeIf { it.isNotBlank() }
            ?: prefs[keySyncedUrl]?.takeIf { it.isNotBlank() }
            ?: BuildConfig.DEFAULT_CLIENT_URL
    }

    /** 该地址是否来自云端自动同步（用于 UI 展示「已自动配置」）。 */
    val isAutoConfigured: Flow<Boolean> = ctx.dataStore.data.map { prefs ->
        val manual = prefs[keyClientUrl]?.takeIf { it.isNotBlank() }
        val synced = prefs[keySyncedUrl]?.takeIf { it.isNotBlank() }
        manual == null && synced != null
    }

    /** 用户手动设置（最高优先级）。 */
    suspend fun setClientUrl(url: String) {
        ctx.dataStore.edit { it[keyClientUrl] = url.trim().trimEnd('/') }
    }

    /** Supabase 云端同步写入（零配置；不覆盖用户手动值）。 */
    suspend fun setSyncedClientUrl(url: String) {
        ctx.dataStore.edit { it[keySyncedUrl] = url.trim().trimEnd('/') }
    }
}
