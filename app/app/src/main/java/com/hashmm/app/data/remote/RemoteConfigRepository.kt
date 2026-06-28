package com.hashmm.app.data.remote

import com.hashmm.app.data.settings.SettingsStore
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.postgrest.postgrest
import kotlinx.serialization.Serializable
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class AppConfigRow(val key: String = "", val value: String = "")

/** 零配置：从 Supabase app_config 读后端公网地址，写入本地设置。
 *  后端启动时把地址写进 app_config，App 登录后读到即可自动连接，无需手填。 */
@Singleton
class RemoteConfigRepository @Inject constructor(
    private val supabase: SupabaseClient,
    private val settings: SettingsStore,
) {
    /** 拉取并写入后端地址。永不抛错（表不存在/离线时静默退回本地默认）。 */
    suspend fun syncBackendUrl() {
        try {
            val rows = supabase.postgrest.from("app_config").select {
                filter { eq("key", "backend_url") }
            }.decodeList<AppConfigRow>()
            val url = rows.firstOrNull()?.value?.trim()?.trimEnd('/')
            if (!url.isNullOrBlank()) {
                settings.setSyncedClientUrl(url)
            }
        } catch (_: Throwable) {
        }
    }
}
