package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** V219 原生三页（审计/质量/高级能力）的数据源。
 *  端点与桌面端 lib/api.ts 完全同款（audit/tools · admin/quality/dashboard ·
 *  dispatch · credentials · kb/quarantine · images），只读 v1；操作仍在桌面端。 */

data class AuditItem(val tool: String, val user: String, val tsSec: Long)
data class QualityData(val stats: List<Pair<String, String>> = emptyList(), val error: String? = null)
data class AdvTask(val kind: String, val status: String, val runner: String, val createdSec: Long)
data class AdvancedData(
    val dispatchStats: Map<String, Int> = emptyMap(),
    val recentTasks: List<AdvTask> = emptyList(),
    val credentials: List<String> = emptyList(),
    val quarantineCount: Int = -1,
    val quarantineTop: List<String> = emptyList(),
    val imageCount: Int = -1,
    val imageBytes: Long = 0,
    val error: String? = null,
)

@Singleton
class AdminToolsRepository @Inject constructor(
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
        return when { isIp -> "http://$schemeless"; b.startsWith("http") -> b; else -> "https://$b" }
    }

    private fun sec(v: Double): Long = when { v <= 0 -> 0L; v > 1e12 -> (v / 1000.0).toLong(); else -> v.toLong() }

    private suspend fun getJson(path: String): Pair<Int, JSONObject?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext -1 to null
        try {
            val req = Request.Builder().url("$base$path").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                val body = resp.body?.string()
                resp.code to (if (body.isNullOrBlank()) null else runCatching { JSONObject(body) }.getOrNull())
            }
        } catch (e: Exception) { -2 to null }
    }

    private fun errFor(code: Int): String = when (code) {
        -1 -> "未连客户端后端或未登录"
        -2 -> "网络错误"
        401 -> "登录已过期，请重新登录"
        403 -> "需要管理员账号"
        404 -> "后端过旧：该接口为较新版本提供，unzip -o 新包并重启后端即可"
        else -> "加载失败（$code）"
    }

    /** 权限审计：最近工具调用（/api/admin/audit/tools）。 */
    suspend fun audit(limit: Int = 30): Pair<List<AuditItem>, String?> {
        val (code, o) = getJson("/api/admin/audit/tools?limit=$limit")
        if (o == null || code !in 200..299) return emptyList<AuditItem>() to errFor(code)
        val arr: JSONArray = o.optJSONArray("items") ?: o.optJSONArray("events") ?: o.optJSONArray("audit") ?: JSONArray()
        val out = mutableListOf<AuditItem>()
        for (i in 0 until arr.length()) {
            val e = arr.optJSONObject(i) ?: continue
            out.add(AuditItem(
                tool = e.optString("tool", e.optString("action", "?")),
                user = e.optString("user", e.optString("uid", "")),
                tsSec = sec(e.optDouble("ts", e.optDouble("created", 0.0))),
            ))
        }
        return out to null
    }

    /** 质量看板：/api/admin/quality/dashboard 标量字段泛化渲染（后端字段原样，不造数）。 */
    suspend fun quality(days: Int = 7): QualityData {
        val (code, o) = getJson("/api/admin/quality/dashboard?days=$days")
        if (o == null || code !in 200..299) return QualityData(error = errFor(code))
        val stats = mutableListOf<Pair<String, String>>()
        fun walk(prefix: String, obj: JSONObject, depth: Int) {
            if (depth > 1) return
            for (k in obj.keys()) {
                val v = obj.opt(k) ?: continue
                when (v) {
                    is Number -> stats.add((prefix + k) to (if (v.toDouble() % 1.0 == 0.0) v.toLong().toString() else String.format("%.2f", v.toDouble())))
                    is String -> if (v.isNotBlank() && v.length <= 40) stats.add((prefix + k) to v)
                    is Boolean -> stats.add((prefix + k) to if (v) "是" else "否")
                    is JSONObject -> walk("$k · ", v, depth + 1)
                }
            }
        }
        walk("", o, 0)
        return QualityData(stats = stats.take(24))
    }

    /** 高级能力概览：派活统计+最近任务 / 凭据 / 隔离 / 图库，逐块降级。 */
    suspend fun advanced(): AdvancedData {
        var out = AdvancedData()
        val (dc, d) = getJson("/api/dispatch?limit=20")
        if (d != null && dc in 200..299) {
            val stats = mutableMapOf<String, Int>()
            d.optJSONObject("stats")?.let { st -> for (k in st.keys()) stats[k] = st.optInt(k, 0) }
            val tasks = mutableListOf<AdvTask>()
            d.optJSONArray("items")?.let { arr ->
                for (i in 0 until minOf(arr.length(), 5)) {
                    val t = arr.optJSONObject(i) ?: continue
                    tasks.add(AdvTask(t.optString("kind", "?"), t.optString("status", ""),
                        t.optString("runner", ""), sec(t.optDouble("created", 0.0))))
                }
            }
            out = out.copy(dispatchStats = stats, recentTasks = tasks)
        } else if (out.error == null && dc !in 200..299) out = out.copy(error = errFor(dc))
        val (cc, c) = getJson("/api/credentials")
        if (c != null && cc in 200..299) {
            val names = mutableListOf<String>()
            c.optJSONArray("items")?.let { arr ->
                for (i in 0 until arr.length()) arr.optJSONObject(i)?.optString("connector")?.takeIf { it.isNotBlank() }?.let(names::add)
            }
            out = out.copy(credentials = names, error = null)
        }
        val (qc, q) = getJson("/api/kb/quarantine")
        if (q != null && qc in 200..299) {
            val tops = mutableListOf<String>()
            q.optJSONArray("items")?.let { arr ->
                for (i in 0 until minOf(arr.length(), 3)) arr.optJSONObject(i)?.optString("filename")?.takeIf { it.isNotBlank() }?.let(tops::add)
            }
            out = out.copy(quarantineCount = q.optInt("count", q.optJSONArray("items")?.length() ?: 0), quarantineTop = tops, error = null)
        }
        val (ic, im) = getJson("/api/images?limit=1")
        if (im != null && ic in 200..299) {
            out = out.copy(imageCount = im.optInt("count", -1), imageBytes = im.optLong("bytes", 0L), error = null)
        }
        // 四块全空且带错 → 保留错误；有任一块成功已把 error 清掉
        return out
    }
}
