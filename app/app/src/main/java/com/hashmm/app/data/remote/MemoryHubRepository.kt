package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** 联邦记忆召回的一条结果（后端 V249 /api/memory/recall）。 */
data class MemoryHit(
    val kind: String,      // service | episodic | profile | entity
    val source: String,    // 中文来源名：长期记忆 / 经验回放 / 用户画像 / 知识图谱
    val text: String,
    val score: Double,
    val tsSec: Long,
)

/**
 * 记忆中枢（V246 配套后端 V249）：
 *   GET {base}/api/memory/recall?q=   四路联邦召回（MemoryService + 经验回放 + 画像 + 图谱实体）
 * 与 FeedRepository 同一套 base()/token 范式；旧后端 404 → 空列表 + note 提示升级。
 */
@Singleton
class MemoryHubRepository @Inject constructor(
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

    /** 联邦召回。返回 (结果, 提示)：提示非空＝未连后端/未登录/后端过旧。 */
    suspend fun recall(query: String, limit: Int = 20): Pair<List<MemoryHit>, String> =
        withContext(Dispatchers.IO) {
            val q = query.trim()
            if (q.isBlank()) return@withContext emptyList<MemoryHit>() to ""
            val base = base(); val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank()) {
                return@withContext emptyList<MemoryHit>() to "未连客户端后端或未登录"
            }
            try {
                val url = "$base/api/memory/recall?q=" + URLEncoder.encode(q, "UTF-8") + "&limit=$limit"
                val req = Request.Builder().url(url).header("Authorization", "Bearer $token").get().build()
                http.newCall(req).execute().use { resp ->
                    if (resp.code == 404) return@withContext emptyList<MemoryHit>() to "后端过旧（需 V249+）——联邦召回不可用"
                    if (!resp.isSuccessful) return@withContext emptyList<MemoryHit>() to "召回失败（HTTP ${resp.code}）"
                    val o = JSONObject(resp.body?.string() ?: "{}")
                    val arr = o.optJSONArray("items") ?: return@withContext emptyList<MemoryHit>() to ""
                    val out = ArrayList<MemoryHit>(arr.length())
                    for (i in 0 until arr.length()) {
                        val it = arr.optJSONObject(i) ?: continue
                        val text = it.optString("text", "")
                        if (text.isBlank()) continue
                        out.add(
                            MemoryHit(
                                kind = it.optString("kind", ""),
                                source = it.optString("source", "记忆"),
                                text = text,
                                score = it.optDouble("score", 0.0),
                                tsSec = it.optLong("ts", 0L),
                            )
                        )
                    }
                    out to ""
                }
            } catch (e: Exception) {
                emptyList<MemoryHit>() to "召回失败：${e.message ?: "网络错误"}"
            }
        }
}
