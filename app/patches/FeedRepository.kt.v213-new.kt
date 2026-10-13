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
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** 动态流数据（V213：对接后端 V218 新增的 GET /api/feed，一次拉齐动态页全部素材）。 */
data class FeedFile(
    val convId: String,
    val convTitle: String,
    val filename: String,
    val size: Long,
    val mtimeSec: Long,
    val viewUrl: String,      // 后端手机适配预览页（/view?token=），InAppFileViewer 直接打开
)
data class FeedConv(val id: String, val title: String, val updatedAtSec: Long)
data class FeedRunner(val name: String, val online: Boolean)
data class FeedScheduled(val name: String, val enabled: Boolean, val lastRunSec: Long, val lastResult: String)
data class FeedEpisode(val summary: String, val createdAtSec: Long)
data class FeedData(
    val release: String = "",
    val files: List<FeedFile> = emptyList(),
    val convs: List<FeedConv> = emptyList(),
    val runners: List<FeedRunner> = emptyList(),
    val scheduled: List<FeedScheduled> = emptyList(),
    val scheduledCount: Int = -1,      // 普通用户只拿数量；-1=未知
    val episodes: List<FeedEpisode> = emptyList(),
    val error: String? = null,         // 非空=拉取失败（含"后端过旧无 /api/feed"）
)

/**
 * 动态流：GET {base}/api/feed（后端 V218+）。
 * 与 UsageRepository 同一套 base()/token 范式；404 视为"后端代码过旧"，
 * 返回带 error 的 FeedData，UI 据此显示升级提示条而不是空白。
 */
@Singleton
class FeedRepository @Inject constructor(
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

    private fun sec(v: Double): Long = when {
        v <= 0 -> 0L
        v > 1e12 -> (v / 1000.0).toLong()   // 毫秒 → 秒
        else -> v.toLong()
    }

    suspend fun feed(): FeedData = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext FeedData(error = "未连客户端后端或未登录")
        try {
            val req = Request.Builder().url("$base/api/feed").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 404) return@withContext FeedData(error = "后端过旧：缺 /api/feed（V218+）。请同步后端源码并重启。")
                if (!resp.isSuccessful) return@withContext FeedData(error = "动态加载失败（${resp.code}）")
                val o = JSONObject(resp.body?.string() ?: return@withContext FeedData(error = "空响应"))

                val files = mutableListOf<FeedFile>()
                o.optJSONArray("recent_files")?.forEach { f ->
                    val cid = f.optString("conv_id"); val fn = f.optString("filename")
                    if (cid.isNotBlank() && fn.isNotBlank()) files.add(
                        FeedFile(
                            convId = cid,
                            convTitle = f.optString("conv_title", "对话"),
                            filename = fn,
                            size = f.optLong("size", 0L),
                            mtimeSec = sec(f.optDouble("mtime", 0.0)),
                            viewUrl = "$base/api/conversations/$cid/files/" +
                                URLEncoder.encode(fn, "UTF-8") + "/view?token=" +
                                URLEncoder.encode(token, "UTF-8"),
                        )
                    )
                }
                val convs = mutableListOf<FeedConv>()
                o.optJSONArray("recent_conversations")?.forEach { c ->
                    val id = c.optString("id")
                    if (id.isNotBlank()) convs.add(
                        FeedConv(id, c.optString("title", "对话"), sec(c.optDouble("updated_at", 0.0)))
                    )
                }
                val runners = mutableListOf<FeedRunner>()
                o.optJSONArray("runners")?.forEach { r ->
                    runners.add(
                        FeedRunner(
                            name = r.optString("runner", r.optString("name", r.optString("id", "电脑端"))),
                            online = r.optBoolean("online", false),
                        )
                    )
                }
                var schedCount = -1
                val sched = mutableListOf<FeedScheduled>()
                o.optJSONArray("scheduled")?.forEach { s ->
                    if (s.has("count")) schedCount = s.optInt("count", 0)
                    else sched.add(
                        FeedScheduled(
                            name = s.optString("name", "任务"),
                            enabled = s.optBoolean("enabled", true),
                            lastRunSec = sec(s.optDouble("last_run", 0.0)),
                            lastResult = s.optString("last_result", ""),
                        )
                    )
                }
                val eps = mutableListOf<FeedEpisode>()
                o.optJSONArray("episodes")?.forEach { e ->
                    val sum = e.optString("summary", "")
                    if (sum.isNotBlank()) eps.add(FeedEpisode(sum, sec(e.optDouble("created_at", 0.0))))
                }
                FeedData(
                    release = o.optString("release", ""),
                    files = files, convs = convs, runners = runners,
                    scheduled = sched, scheduledCount = schedCount, episodes = eps,
                )
            }
        } catch (e: Exception) {
            FeedData(error = "网络错误")
        }
    }
}

/** JSONArray 便捷遍历（只取对象元素）。 */
private inline fun JSONArray.forEach(block: (JSONObject) -> Unit) {
    for (i in 0 until length()) (optJSONObject(i) ?: continue).let(block)
}
