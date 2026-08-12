package com.hashmm.app.data.remote

import com.hashmm.app.data.settings.SettingsStore
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.postgrest.postgrest
import kotlinx.serialization.Serializable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class AppConfigRow(val key: String = "", val value: String = "")

sealed interface RemoteConfigSyncResult {
    data class Success(val url: String) : RemoteConfigSyncResult
    data object Skipped : RemoteConfigSyncResult
    data class Failure(val reason: String) : RemoteConfigSyncResult
}

/** 零配置：从 Supabase app_config 读后端公网地址，写入本地设置。
 *  后端启动时把地址写进 app_config，App 登录后读到即可自动连接，无需手填。 */
@Singleton
class RemoteConfigRepository @Inject constructor(
    private val supabase: SupabaseClient,
    private val settings: SettingsStore,
) {
    private val refreshMutex = Mutex()

    /** 只同步公开后端地址。个人 API Key 永不经过 app_config 下发或跨用户共享。 */
    suspend fun syncBackendUrl(force: Boolean = false): RemoteConfigSyncResult = refreshMutex.withLock {
        val now = System.currentTimeMillis()
        if (!force && !settings.shouldRefreshRemoteConfig(now, 6 * 60 * 60 * 1000L)) {
            return@withLock RemoteConfigSyncResult.Skipped
        }
        return@withLock try {
            val rows = supabase.postgrest.from("app_config").select {
                filter { eq("key", "backend_url") }
            }.decodeList<AppConfigRow>()
            val url = rows.firstOrNull { it.key == "backend_url" }
                ?.value?.trim()?.trimEnd('/')?.takeIf { it.isNotBlank() }
                ?: return@withLock RemoteConfigSyncResult.Failure("missing_backend_url")
            if (!settings.setSyncedClientUrl(url)) {
                return@withLock RemoteConfigSyncResult.Failure("invalid_backend_url")
            }
            // Only a validated row is a successful check. A provider outage
            // must never suppress retries for six hours.
            settings.markRemoteConfigChecked(now)
            settings.markRemoteConfigSucceeded(url, now)
            RemoteConfigSyncResult.Success(url)
        } catch (_: Throwable) {
            settings.markRemoteConfigFailed("provider_unavailable", now)
            RemoteConfigSyncResult.Failure("provider_unavailable")
        }
    }
}
