package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.cache.LocalStore
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.Request
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class RuntimeCapability(
    val id: String = "",
    val title: String = "",
    val description: String = "",
    val state: String = "unavailable",
    val reason: String = "",
    val enabled: Boolean = false,
    val wired: Boolean = false,
    @SerialName("requires_desktop") val requiresDesktop: Boolean = false,
    @SerialName("active_tools") val activeTools: List<String> = emptyList(),
    @SerialName("missing_tools") val missingTools: List<String> = emptyList(),
    val surfaces: Map<String, String> = emptyMap(),
    val entrypoints: List<String> = emptyList(),
    val availability: String = "unavailable",
    val visibility: String = "diagnostic",
    @SerialName("diagnostic_only") val diagnosticOnly: Boolean = true,
    @SerialName("production_ready") val productionReady: Boolean = false,
)

@Serializable
data class RuntimeCapabilitiesSnapshot(
    val contract: String = "",
    @SerialName("truth_contract") val truthContract: String = "",
    val revision: String = "",
    @SerialName("chat_tool_count") val chatToolCount: Int = 0,
    @SerialName("ready_count") val readyCount: Int = 0,
    @SerialName("available_count") val availableCount: Int = 0,
    @SerialName("degraded_count") val degradedCount: Int = 0,
    @SerialName("unavailable_count") val unavailableCount: Int = 0,
    @SerialName("total_count") val totalCount: Int = 0,
    val capabilities: List<RuntimeCapability> = emptyList(),
    val notice: String? = null,
    val error: String? = null,
    /** Client-side trust state; never persisted as server evidence. */
    @Transient val freshness: String = "unknown",
    @Transient val verifiedAtEpochMs: Long = 0L,
)

private val capabilityJson = Json { ignoreUnknownKeys = true; encodeDefaults = true }

/** Pure parser kept public to regression-test the server/App wire contract. */
fun parseRuntimeCapabilities(raw: String): RuntimeCapabilitiesSnapshot? =
    runCatching { capabilityJson.decodeFromString<RuntimeCapabilitiesSnapshot>(raw) }
        .getOrNull()
        ?.takeIf { it.contract == "hashmm.runtime-capabilities.v1" }

/** A capability cache is evidence about one backend, not every server a user may configure. */
internal fun capabilityCacheScope(normalizedBaseUrl: String): String =
    MessageDigest.getInstance("SHA-256")
        .digest(normalizedBaseUrl.trim().lowercase().toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
        .take(20)

/**
 * Offline-first projection of the capabilities the server can actually expose to Chat.
 * It is not a second feature registry: the backend remains the only source of truth.
 */
@Singleton
class CapabilityRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
    private val local: LocalStore,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(10, TimeUnit.SECONDS).build()
    private val mutex = Mutex()
    @Volatile private var memory: RuntimeCapabilitiesSnapshot? = null
    @Volatile private var memoryAtMs = 0L
    @Volatile private var memoryBackendScope = ""

    suspend fun fetch(force: Boolean = false): RuntimeCapabilitiesSnapshot {
        val uid = auth.currentUserId() ?: return RuntimeCapabilitiesSnapshot(error = "请先登录")
        val now = System.currentTimeMillis()
        val backend = base()
        val backendScope = capabilityCacheScope(backend)
        memory?.takeIf {
            !force && memoryBackendScope == backendScope && now - memoryAtMs < MEMORY_TTL_MS
        }?.let { return it }
        return mutex.withLock {
            val lockedNow = System.currentTimeMillis()
            memory?.takeIf {
                !force && memoryBackendScope == backendScope && lockedNow - memoryAtMs < MEMORY_TTL_MS
            }?.let { return@withLock it }
            val sameBackend = backend.isNotBlank() && local.getLastSync(uid, CACHE_BACKEND_KEY) == backendScope
            val persisted = if (sameBackend) {
                local.getCapabilitySnapshot(uid)?.let(::parseRuntimeCapabilities)
            } else null
            val persistedAt = local.getLastSync(uid, CACHE_AT_KEY).toLongOrNull() ?: 0L
            if (!force && persisted != null && lockedNow - persistedAt < DISK_TTL_MS) {
                val recent = persisted.copy(
                    notice = "显示最近一次已验证状态；后台将在缓存到期后复核",
                    freshness = "cached",
                    verifiedAtEpochMs = persistedAt,
                )
                memory = recent
                memoryAtMs = lockedNow
                memoryBackendScope = backendScope
                return@withLock recent
            }
            val fresh = fetchRemote(uid, persisted, backend)
            if (fresh.error == null) {
                val verified = fresh.copy(
                    notice = null,
                    freshness = "live",
                    verifiedAtEpochMs = lockedNow,
                )
                local.putCapabilitySnapshot(uid, capabilityJson.encodeToString(verified.copy(notice = null)))
                local.setLastSync(uid, CACHE_AT_KEY, lockedNow.toString())
                local.setLastSync(uid, CACHE_BACKEND_KEY, backendScope)
                memory = verified
                memoryAtMs = lockedNow
                memoryBackendScope = backendScope
                verified
            } else if (persisted != null) {
                persisted.copy(
                    notice = "服务器暂时不可达，显示最近一次已验证状态",
                    freshness = "stale",
                    verifiedAtEpochMs = persistedAt,
                )
                    .also { memory = it; memoryAtMs = lockedNow; memoryBackendScope = backendScope }
            } else fresh
        }
    }

    private suspend fun base(): String {
        val raw = settings.clientUrl.first().trim().trimEnd('/')
        if (raw.isBlank()) return ""
        val schemeless = raw.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore('/').substringBefore(':')
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when {
            isIp -> "http://$schemeless"
            raw.startsWith("http://") || raw.startsWith("https://") -> raw
            else -> "https://$raw"
        }
    }

    private suspend fun fetchRemote(
        uid: String,
        cached: RuntimeCapabilitiesSnapshot?,
        backend: String,
    ): RuntimeCapabilitiesSnapshot = withContext(Dispatchers.IO) {
        val token = auth.currentToken()
        if (backend.isBlank() || token.isNullOrBlank()) {
            return@withContext RuntimeCapabilitiesSnapshot(error = "尚未连接服务器或登录已失效")
        }
        try {
            val builder = Request.Builder()
                .url("$backend/api/runtime/capabilities")
                .header("Authorization", "Bearer $token")
            if (cached != null) {
                local.getLastSync(uid, ETAG_KEY).takeIf { it.startsWith('"') }
                    ?.let { builder.header("If-None-Match", it) }
            }
            http.newCall(builder.get().build()).execute().use { response ->
                if (response.code == 304 && cached != null) return@withContext cached
                if (response.code == 404) {
                    return@withContext RuntimeCapabilitiesSnapshot(error = "服务器版本尚未提供统一能力状态")
                }
                if (!response.isSuccessful) {
                    return@withContext RuntimeCapabilitiesSnapshot(error = "能力状态读取失败（${response.code}）")
                }
                val parsed = parseRuntimeCapabilities(response.body?.string().orEmpty())
                    ?: return@withContext RuntimeCapabilitiesSnapshot(error = "服务器返回了不兼容的能力状态")
                response.header("ETag")?.takeIf { it.isNotBlank() }
                    ?.let { local.setLastSync(uid, ETAG_KEY, it) }
                parsed
            }
        } catch (_: Exception) {
            RuntimeCapabilitiesSnapshot(error = "网络连接失败")
        }
    }

    private companion object {
        const val MEMORY_TTL_MS = 60_000L
        const val DISK_TTL_MS = 5 * 60_000L
        const val CACHE_AT_KEY = "runtime_capabilities_at"
        const val ETAG_KEY = "runtime_capabilities_etag"
        const val CACHE_BACKEND_KEY = "runtime_capabilities_backend"
    }
}
