package com.hashmm.app.data.settings

import android.content.Context
import android.util.Log
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.hashmm.app.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore by preferencesDataStore(name = "hashmm_settings")

/** 应用设置：客户端地址。
 *  优先级：用户手动填写 > Supabase 云端同步（零配置）> 编译期默认值。 */
@Singleton
class SettingsStore @Inject constructor(
    @param:ApplicationContext private val ctx: Context,
    private val secureKeys: SecureKeyStore,
) {

    private val keyClientUrl = stringPreferencesKey("client_url")          // 用户手动填写（最高优先）
    private val keySyncedUrl = stringPreferencesKey("synced_client_url")   // Supabase 云端同步
    private val keyRemoteConfigCheckedAt = longPreferencesKey("remote_config_checked_at")
    private val keyRemoteConfigLastSuccessAt = longPreferencesKey("remote_config_last_success_at")
    private val keyRemoteConfigLastFailureAt = longPreferencesKey("remote_config_last_failure_at")
    private val keyRemoteConfigLastFailure = stringPreferencesKey("remote_config_last_failure")
    private val keyRemoteConfigVerifiedUrl = stringPreferencesKey("remote_config_verified_url")
    private val keyClientInstanceId = stringPreferencesKey("client_instance_id")

    // 修 FIX-03：串行化遗留明文密钥迁移，避免多个 Flow collector 并发触发迁移副作用。
    private val migrationMutex = Mutex()

    /** 生效的客户端地址：手动 > 云端同步 > 编译默认。 */
    val clientUrl: Flow<String> = ctx.dataStore.data.map { prefs ->
        prefs[keyClientUrl]?.takeIf { it.isNotBlank() }
            ?: prefs[keySyncedUrl]?.takeIf { it.isNotBlank() }
            ?: BuildConfig.DEFAULT_CLIENT_URL
    }.onEach { url ->
        // V309：生效后端地址一变就登记主机名，供 CredentialHostGuard 判定「凭证只发后端」。
        com.hashmm.app.data.remote.BackendHostHolder.setBackendUrl(url)
    }

    /** 该地址是否来自云端自动同步（用于 UI 展示「已自动配置」）。 */
    val isAutoConfigured: Flow<Boolean> = ctx.dataStore.data.map { prefs ->
        val manual = prefs[keyClientUrl]?.takeIf { it.isNotBlank() }
        val synced = prefs[keySyncedUrl]?.takeIf { it.isNotBlank() }
        manual == null && synced != null
    }

    /** 用户可见的地址来源，避免旧手动地址无声遮挡云端入口。 */
    val clientUrlSource: Flow<String> = ctx.dataStore.data.map { prefs ->
        when {
            !prefs[keyClientUrl].isNullOrBlank() -> "manual"
            !prefs[keySyncedUrl].isNullOrBlank() -> "cloud"
            else -> "compiled_default"
        }
    }

    val remoteConfigLastFailure: Flow<String> = ctx.dataStore.data.map {
        it[keyRemoteConfigLastFailure].orEmpty()
    }

    /** 用户手动设置（最高优先级）。 */
    /** 设置手动客户端地址。非法地址（畸形/含 userinfo/非 http(s)）拒绝写入，返回是否成功。 */
    suspend fun setClientUrl(url: String): Boolean {
        val normalized = ClientUrlValidator.normalizeOrNull(url) ?: run {
            Log.w("SettingsStore", "拒绝写入非法客户端地址（畸形/含账号密码/非 http(s)）")
            return false
        }
        ctx.dataStore.edit { it[keyClientUrl] = normalized }
        return true
    }

    /** Supabase 云端同步写入（零配置；不覆盖用户手动值）。 */
    /** 设置云端同步下发的地址。同样校验——云端下发也可能被污染，不可无条件信任。 */
    suspend fun setSyncedClientUrl(url: String): Boolean {
        val normalized = ClientUrlValidator.normalizeOrNull(url) ?: run {
            Log.w("SettingsStore", "拒绝写入非法云同步地址")
            return false
        }
        ctx.dataStore.edit {
            it[keySyncedUrl] = normalized
            // V1400 migration: the old campus-network IP was once persisted as
            // a manual value and therefore shadowed the new HTTPS public
            // endpoint forever.  Retire only that known legacy endpoint; keep
            // every genuinely user-selected address untouched.
            val manual = it[keyClientUrl].orEmpty().trimEnd('/')
            if (manual == "http://111.115.7.14:20014" || manual == "111.115.7.14:20014") {
                it.remove(keyClientUrl)
            }
        }
        return true
    }

    suspend fun shouldRefreshRemoteConfig(nowMs: Long, ttlMs: Long): Boolean {
        val last = ctx.dataStore.data.first()[keyRemoteConfigCheckedAt] ?: 0L
        return last <= 0L || nowMs - last >= ttlMs
    }

    suspend fun markRemoteConfigChecked(nowMs: Long) {
        ctx.dataStore.edit { it[keyRemoteConfigCheckedAt] = nowMs }
    }

    suspend fun markRemoteConfigSucceeded(url: String, nowMs: Long) {
        ctx.dataStore.edit {
            it[keyRemoteConfigLastSuccessAt] = nowMs
            it[keyRemoteConfigVerifiedUrl] = url
            it.remove(keyRemoteConfigLastFailure)
            it.remove(keyRemoteConfigLastFailureAt)
        }
    }

    suspend fun markRemoteConfigFailed(reason: String, nowMs: Long) {
        ctx.dataStore.edit {
            it[keyRemoteConfigLastFailure] = reason.take(80)
            it[keyRemoteConfigLastFailureAt] = nowMs
        }
    }

    /** 恢复云端自动地址；不会删除云端 last-known-good。 */
    suspend fun clearManualClientUrl() {
        ctx.dataStore.edit { it.remove(keyClientUrl) }
    }

    suspend fun clientInstanceId(): String {
        val current = ctx.dataStore.data.first()[keyClientInstanceId]
        if (!current.isNullOrBlank()) return current
        val created = java.util.UUID.randomUUID().toString()
        ctx.dataStore.edit { prefs ->
            if (prefs[keyClientInstanceId].isNullOrBlank()) prefs[keyClientInstanceId] = created
        }
        return ctx.dataStore.data.first()[keyClientInstanceId] ?: created
    }

    // ── V251 执行体（Marvis 式全局切换）：auto=后端优先直连兜底 / backend=仅桌面端 / direct=仅手机直连 ──
    private val keyExecMode = stringPreferencesKey("exec_mode")
    val execMode: Flow<String> = ctx.dataStore.data.map { it[keyExecMode] ?: "auto" }
    suspend fun setExecMode(mode: String) {
        ctx.dataStore.edit { it[keyExecMode] = mode }
    }

    // ── V249 直连模型（离线兜底）：手机直接调 OpenAI 兼容端点继续 agent 问答 ──
    // V305 安全加固（审计 P0-1）：API Key 不再明文进 DataStore——密钥存 Keystore 加密存储(SecureKeyStore)，
    // DataStore 只留 keyDirectApiKeySet 标记。keyDirectApiKey 仅用于识别并**迁移**老版本遗留的明文密钥。
    private val keyDirectBaseUrl = stringPreferencesKey("direct_base_url")
    private val keyDirectModel = stringPreferencesKey("direct_model")
    private val keyDirectApiKey = stringPreferencesKey("direct_api_key")        // 仅遗留迁移用
    private val keyDirectApiKeySet = booleanPreferencesKey("direct_api_key_set") // 是否已配置密钥

    val directBaseUrl: Flow<String> = ctx.dataStore.data.map { it[keyDirectBaseUrl] ?: "" }
    val directModel: Flow<String> = ctx.dataStore.data.map { it[keyDirectModel] ?: "" }

    /** 生效密钥：从 Keystore 加密存储读取。若检测到老版本明文密钥，一次性迁移到加密存储再清除明文。
     *  修 FIX-03：迁移用 Mutex 串行化（多个 collector 不会并发迁移），且**仅在加密写入校验成功后**
     *  才删除明文——写入失败则保留明文、下次重试，杜绝"加密写失败却已抹掉明文"导致的永久丢 Key。 */
    val directApiKey: Flow<String> = ctx.dataStore.data.map { prefs ->
        val legacy = prefs[keyDirectApiKey]?.takeIf { it.isNotBlank() }
        if (legacy != null && !secureKeys.hasApiKey()) {
            migrationMutex.withLock {
                if (!secureKeys.hasApiKey()) {                     // 锁内二次确认，避免重复迁移
                    if (secureKeys.putApiKey(legacy)) {            // 写入并校验成功
                        ctx.dataStore.edit { it.remove(keyDirectApiKey); it[keyDirectApiKeySet] = true }
                    } else {
                        Log.w("SettingsStore", "密钥迁移失败，保留明文以便下次重试（不删除旧值）")
                    }
                }
            }
        }
        // 优先返回加密存储里的值；迁移失败时明文仍可用（app 不中断），都没有则空串
        secureKeys.getApiKey() ?: legacy ?: ""
    }

    suspend fun setDirectLlm(baseUrl: String, model: String, apiKey: String) {
        val k = apiKey.trim()
        // 空值不覆盖旧密钥（审计要求：设置页留空/只显示掩码时，保存不应抹掉已存密钥）
        val wrote = if (k.isNotEmpty()) secureKeys.putApiKey(k) else false
        if (k.isNotEmpty() && !wrote) {
            Log.w("SettingsStore", "直连密钥写入失败，未改动存储（请重试）")
            return                                                 // 写失败不继续，避免留下不一致状态
        }
        ctx.dataStore.edit {
            it[keyDirectBaseUrl] = baseUrl.trim().trimEnd('/')
            it[keyDirectModel] = model.trim()
            it.remove(keyDirectApiKey)                 // 确保任何明文残留被清除
            if (k.isNotEmpty() || secureKeys.hasApiKey()) it[keyDirectApiKeySet] = true
        }
    }

    /** 退出/切换账号/清数据时清除密钥（审计要求）。 */
    suspend fun clearDirectApiKey() {
        secureKeys.clearApiKey()
        ctx.dataStore.edit { it.remove(keyDirectApiKey); it[keyDirectApiKeySet] = false }
    }

    // ── 远程控制 · 安全（真实生效的两项；均默认关）──
    private val keyRemoteLockOnEnd = booleanPreferencesKey("remote_lock_on_end")
    private val keyRemoteWipeClipOnEnd = booleanPreferencesKey("remote_wipe_clip_on_end")

    /** 断开远程后自动锁定被控电脑（发 win+l）。 */
    val remoteLockOnEnd: Flow<Boolean> = ctx.dataStore.data.map { it[keyRemoteLockOnEnd] ?: false }

    /** 断开远程后清空被控电脑剪贴板（推过去的密码等不留在对方机器上）。 */
    val remoteWipeClipOnEnd: Flow<Boolean> = ctx.dataStore.data.map { it[keyRemoteWipeClipOnEnd] ?: false }

    suspend fun setRemoteLockOnEnd(on: Boolean) { ctx.dataStore.edit { it[keyRemoteLockOnEnd] = on } }

    suspend fun setRemoteWipeClipOnEnd(on: Boolean) { ctx.dataStore.edit { it[keyRemoteWipeClipOnEnd] = on } }
}
