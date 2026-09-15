package com.hashmm.app.data.remote

import com.hashmm.app.BuildConfig
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class AdminUser(
    val id: String,
    val username: String,
    val displayName: String,
    val role: String,
    val createdAt: String,
    val identitySource: String = "local",
    val email: String = "",
    val lastSignInAt: String = "",
)

data class AuditEntry(
    val ts: Double,
    val actor: String,
    val tool: String,
    val risk: String,
    val ok: Boolean,
    val latencyMs: Int,
)

data class AuditResult(
    val enabled: Boolean = false,
    val entries: List<AuditEntry> = emptyList(),
    val error: String? = null,
)

data class AdminDirectoryResult(
    val users: List<AdminUser> = emptyList(),
    val notice: String? = null,
    val error: String? = null,
)

private data class DirectorySourceResult(
    val users: List<AdminUser> = emptyList(),
    val error: String? = null,
)

private val adminWireJson = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }

private fun JsonObject.text(key: String): String =
    this[key]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()

/**
 * Parse the shared desktop/App member wire format row by row.
 *
 * Local HashMM accounts historically expose ``created_at`` as a numeric Unix
 * timestamp, while Supabase exposes ISO-8601 strings.  Treating the complete
 * response as ``List<AdminUserWire>`` made one legacy numeric row invalidate
 * the whole directory.  JSON primitives are deliberately normalised to text
 * here and malformed rows are skipped independently.
 */
internal fun parseAdminUsers(
    body: String,
    defaultIdentitySource: String = "local",
    prefixSupabaseIds: Boolean = false,
): List<AdminUser> {
    val root = adminWireJson.parseToJsonElement(body) as? JsonArray
        ?: throw IllegalArgumentException("成员目录不是 JSON 数组")
    return root.mapNotNull { element ->
        val o = element as? JsonObject ?: return@mapNotNull null
        var id = o.text("id")
        if (id.isBlank()) return@mapNotNull null
        if (prefixSupabaseIds && !id.startsWith("sb_")) id = "sb_$id"
        val email = o.text("email")
        val displayName = o.text("display_name")
        val username = o.text("username")
            .ifBlank { displayName }
            .ifBlank { email.substringBefore('@') }
            .ifBlank { "用户" }
        val isAdmin = o["is_admin"]?.jsonPrimitive?.booleanOrNull == true
        val role = if (isAdmin) "admin" else o.text("role").ifBlank { "user" }
        AdminUser(
            id = id,
            username = username,
            displayName = displayName,
            role = role,
            createdAt = o.text("created_at"),
            identitySource = o.text("identity_source").ifBlank { defaultIdentitySource },
            email = email,
            lastSignInAt = o.text("last_sign_in_at"),
        )
    }
}

internal fun mergeAdminUsers(vararg sources: List<AdminUser>): List<AdminUser> {
    val merged = linkedMapOf<String, AdminUser>()
    for (source in sources) {
        for (incoming in source) {
            val key = incoming.id.lowercase()
            val current = merged[key]
            merged[key] = if (current == null) incoming else AdminUser(
                id = current.id,
                username = incoming.username.ifBlank { current.username },
                displayName = incoming.displayName.ifBlank { current.displayName },
                role = if (current.role.equals("admin", true) || incoming.role.equals("admin", true)) "admin" else incoming.role,
                createdAt = incoming.createdAt.ifBlank { current.createdAt },
                identitySource = if (current.identitySource == "supabase" || incoming.identitySource == "supabase") "supabase" else incoming.identitySource,
                email = incoming.email.ifBlank { current.email },
                lastSignInAt = incoming.lastSignInAt.ifBlank { current.lastSignInAt },
            )
        }
    }
    return merged.values.sortedByDescending { it.lastSignInAt.ifBlank { it.createdAt } }
}

@Singleton
class AdminRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()

    private suspend fun base(): String {
        val b = settings.clientUrl.first().trim().trimEnd('/')
        if (b.isBlank()) return ""
        val schemeless = b.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore("/").substringBefore(":")
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when {
            isIp -> "http://$schemeless"
            b.startsWith("http") -> b
            else -> "https://$b"
        }
    }

    private fun listBackendUsers(base: String, token: String): DirectorySourceResult {
        if (base.isBlank()) return DirectorySourceResult(error = "HashMM 服务地址尚未配置")
        return try {
            val req = Request.Builder().url("$base/api/admin/users")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                when {
                    resp.code == 401 -> DirectorySourceResult(error = "HashMM 服务登录已失效")
                    resp.code == 403 -> DirectorySourceResult(error = "HashMM 服务尚未同步当前账号的管理员角色")
                    !resp.isSuccessful -> DirectorySourceResult(error = "HashMM 成员目录读取失败（${resp.code}）")
                    else -> {
                        val body = resp.body?.string()
                            ?: return DirectorySourceResult(error = "HashMM 服务返回了空响应")
                        try {
                            DirectorySourceResult(users = parseAdminUsers(body))
                        } catch (_: Exception) {
                            DirectorySourceResult(error = "HashMM 服务返回的成员数据格式不兼容")
                        }
                    }
                }
            }
        } catch (_: Exception) {
            DirectorySourceResult(error = "暂时无法连接 HashMM 服务")
        }
    }

    private fun listSupabaseUsers(token: String): DirectorySourceResult {
        val url = BuildConfig.SUPABASE_URL.trim().trimEnd('/')
        val key = BuildConfig.SUPABASE_KEY.trim()
        if (url.isBlank() || key.isBlank()) {
            return DirectorySourceResult(error = "App 尚未配置 Supabase 团队目录")
        }
        return try {
            val body = "{}".toRequestBody("application/json; charset=utf-8".toMediaType())
            val req = Request.Builder()
                .url("$url/rest/v1/rpc/list_all_profiles")
                .header("apikey", key)
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            http.newCall(req).execute().use { resp ->
                when {
                    resp.code == 401 -> DirectorySourceResult(error = "Supabase 会话已失效，请重新登录")
                    resp.code == 403 -> DirectorySourceResult(error = "Supabase 尚未授予当前账号团队目录权限")
                    resp.code == 404 -> DirectorySourceResult(error = "Supabase 尚未部署 list_all_profiles 团队目录函数")
                    !resp.isSuccessful -> DirectorySourceResult(error = "Supabase 团队目录读取失败（${resp.code}）")
                    else -> {
                        val responseBody = resp.body?.string()
                            ?: return DirectorySourceResult(error = "Supabase 返回了空响应")
                        try {
                            DirectorySourceResult(
                                users = parseAdminUsers(
                                    responseBody,
                                    defaultIdentitySource = "supabase",
                                    prefixSupabaseIds = true,
                                )
                            )
                        } catch (_: Exception) {
                            DirectorySourceResult(error = "Supabase 返回的成员数据格式不兼容")
                        }
                    }
                }
            }
        } catch (_: Exception) {
            DirectorySourceResult(error = "暂时无法连接 Supabase 团队目录")
        }
    }

    suspend fun listUsers(): AdminDirectoryResult = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (token.isNullOrBlank()) {
            return@withContext AdminDirectoryResult(error = "登录会话尚未恢复，请重新登录后再读取团队成员")
        }
        coroutineScope {
            // The desktop client already has these two independently secured
            // sources.  Run them concurrently so a slow GPU backend never blocks
            // the Supabase identity directory, and retain whichever succeeds.
            val backendDeferred = async { listBackendUsers(base, token) }
            val cloudDeferred = async { listSupabaseUsers(token) }
            val backend = backendDeferred.await()
            val cloud = cloudDeferred.await()
            val users = mergeAdminUsers(backend.users, cloud.users)
            val sourceErrors = listOfNotNull(backend.error, cloud.error)
            if (users.isEmpty()) {
                AdminDirectoryResult(
                    error = sourceErrors.joinToString("；").ifBlank { "团队目录当前没有可显示的成员" }
                )
            } else {
                AdminDirectoryResult(
                    users = users,
                    notice = sourceErrors.takeIf { it.isNotEmpty() }?.joinToString("；", postfix = "。已保留成功读取的成员，可稍后重试同步。"),
                )
            }
        }
    }

    suspend fun auditTools(limit: Int = 60): AuditResult = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext AuditResult(error = "尚未连接 HashMM 服务或登录已失效")
        try {
            val req = Request.Builder().url("$base/api/admin/audit/tools?limit=$limit")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext AuditResult(error = "当前账号没有安全记录查看权限")
                if (!resp.isSuccessful) return@withContext AuditResult(error = "安全记录加载失败（${resp.code}）")
                val o = JSONObject(resp.body?.string() ?: return@withContext AuditResult(error = "服务返回了空响应"))
                val enabled = o.optBoolean("enabled", false)
                val arr = o.optJSONArray("entries")
                val entries = if (arr == null) emptyList() else (0 until arr.length()).mapNotNull { i ->
                    val e = arr.optJSONObject(i) ?: return@mapNotNull null
                    AuditEntry(
                        ts = e.optDouble("ts", 0.0),
                        actor = e.optString("actor", "system"),
                        tool = e.optString("tool", "未标注操作"),
                        risk = e.optString("risk", "low"),
                        ok = e.optBoolean("ok", true),
                        latencyMs = e.optInt("latency_ms", 0),
                    )
                }.reversed()
                AuditResult(
                    enabled = enabled,
                    entries = entries,
                    error = if (entries.isEmpty()) {
                        if (enabled) "还没有安全记录" else "服务端尚未开启工具审计"
                    } else null,
                )
            }
        } catch (_: Exception) {
            AuditResult(error = "暂时无法连接服务，请稍后重试")
        }
    }
}
