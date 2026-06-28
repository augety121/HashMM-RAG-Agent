package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class AdminUser(
    val id: String,
    val username: String,
    val displayName: String,
    val role: String,
    val createdAt: String,
)

/** 一条工具审计记录。 */
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

/**
 * 管理后台：GET {base}/api/admin/users（管理员）。
 * 返回 {id,username,display_name,role,created_at}。
 */
@Singleton
class AdminRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder().callTimeout(12, TimeUnit.SECONDS).build()

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

    suspend fun listUsers(): Pair<List<AdminUser>, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList<AdminUser>() to "未连客户端后端或未登录"
        try {
            val req = Request.Builder().url("$base/api/admin/users").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext emptyList<AdminUser>() to "需要管理员权限"
                if (!resp.isSuccessful) return@withContext emptyList<AdminUser>() to "加载失败（${resp.code}）"
                val arr = JSONArray(resp.body?.string() ?: return@withContext emptyList<AdminUser>() to "空响应")
                val out = (0 until arr.length()).mapNotNull { i ->
                    val o = arr.optJSONObject(i) ?: return@mapNotNull null
                    AdminUser(
                        id = o.optString("id"),
                        username = o.optString("username", o.optString("display_name", "用户")),
                        displayName = o.optString("display_name", ""),
                        role = o.optString("role", "user"),
                        createdAt = o.optString("created_at", ""),
                    )
                }
                out to null
            }
        } catch (e: Exception) {
            emptyList<AdminUser>() to "网络错误"
        }
    }

    /** 工具审计流。GET {base}/api/admin/audit/tools?limit=N → {enabled, entries}。 */
    suspend fun auditTools(limit: Int = 60): AuditResult = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext AuditResult(error = "未连客户端后端或未登录")
        try {
            val req = Request.Builder().url("$base/api/admin/audit/tools?limit=$limit").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext AuditResult(error = "需要管理员权限")
                if (!resp.isSuccessful) return@withContext AuditResult(error = "加载失败（${resp.code}）")
                val o = org.json.JSONObject(resp.body?.string() ?: return@withContext AuditResult(error = "空响应"))
                val enabled = o.optBoolean("enabled", false)
                val arr = o.optJSONArray("entries")
                val entries = if (arr == null) emptyList() else (0 until arr.length()).mapNotNull { i ->
                    val e = arr.optJSONObject(i) ?: return@mapNotNull null
                    AuditEntry(
                        ts = e.optDouble("ts", 0.0),
                        actor = e.optString("actor", "system"),
                        tool = e.optString("tool", "?"),
                        risk = e.optString("risk", "low"),
                        ok = e.optBoolean("ok", true),
                        latencyMs = e.optInt("latency_ms", 0),
                    )
                }.reversed() // 最新在前
                AuditResult(enabled = enabled, entries = entries, error = if (entries.isEmpty()) (if (enabled) "暂无审计记录" else "审计未开启（HASHMM_AUDIT_TOOLS）") else null)
            }
        } catch (e: Exception) {
            AuditResult(error = "网络错误")
        }
    }
}
