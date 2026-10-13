package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** 动态流数据（V213：对接后端 V218 新增的 GET /api/feed，一次拉齐动态页全部素材）。 */
@Serializable data class FeedFile(
    val convId: String,
    val convTitle: String,
    val filename: String,
    val size: Long,
    val mtimeSec: Long,
    val viewUrl: String,      // 后端手机适配预览页（/view?token=），InAppFileViewer 直接打开
    val downloadUrl: String,  // 原始文件相对路径（/api/conversations/{cid}/download/{fn}），画布屏读写用
)
@Serializable data class FeedConv(val id: String, val title: String, val updatedAtSec: Long)
@Serializable data class FeedRunner(val name: String, val online: Boolean)
@Serializable data class FeedScheduled(val name: String, val enabled: Boolean, val lastRunSec: Long, val lastResult: String)
@Serializable data class FeedRun(
    val title: String,
    val status: String,
    val tsSec: Long,
    val convId: String = "",
    val convTitle: String = "",
    val runId: String = "",
    val taskType: String = "",
    val executionMode: String = "",
    val model: String = "",
    val elapsedMs: Long = 0,
    val stopReason: String = "",
    val iterations: Int = 0,
    val tokens: Long = 0,
    val failedChecks: List<String> = emptyList(),
)
@Serializable data class FeedEpisode(val summary: String, val createdAtSec: Long)
@Serializable data class FeedData(
    val release: String = "",
    val files: List<FeedFile> = emptyList(),
    val convs: List<FeedConv> = emptyList(),
    val runners: List<FeedRunner> = emptyList(),
    val scheduled: List<FeedScheduled> = emptyList(),
    val scheduledCount: Int = -1,      // 普通用户只拿数量；-1=未知
    val episodes: List<FeedEpisode> = emptyList(),
    val runs: List<FeedRun> = emptyList(),
    val error: String? = null,         // 非空=硬失败（连兼容模式都拉不到）
    val notice: String? = null,        // 非空=软提示（如兼容模式：老后端降级运行）
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
    private val local: com.hashmm.app.data.cache.LocalStore,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val feedMutex = Mutex()
    @Volatile private var memoryFeed: FeedData? = null
    @Volatile private var memoryAtMs: Long = 0L

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

    /** V219: 轻量健康探测（Hub 顶部健康条用）：可达性 + 后端 release（V218+ 才有）。 */
    suspend fun health(): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank()) return@withContext false to ""
        try {
            val rb = Request.Builder().url("$base/api/health")
            if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
            http.newCall(rb.get().build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext false to ""
                val o = JSONObject(resp.body?.string() ?: "{}")
                true to o.optString("release", "")
            }
        } catch (e: Exception) { false to "" }
    }

    suspend fun feed(force: Boolean = false): FeedData {
        val uid = auth.currentUserId() ?: return FeedData(error = "未登录")
        val now = System.currentTimeMillis()
        memoryFeed?.takeIf { !force && now - memoryAtMs < MEMORY_TTL_MS }?.let { return it }
        return feedMutex.withLock {
            val lockedNow = System.currentTimeMillis()
            memoryFeed?.takeIf { !force && lockedNow - memoryAtMs < MEMORY_TTL_MS }?.let { return@withLock it }
            val persisted = local.getFeedSnapshot(uid)?.let {
                runCatching { json.decodeFromString<FeedData>(it) }.getOrNull()
            }
            val persistedAt = local.getLastSync(uid, "feed_snapshot_at").toLongOrNull() ?: 0L
            if (!force && persisted != null && lockedNow - persistedAt < DISK_TTL_MS) {
                memoryFeed = persisted; memoryAtMs = lockedNow
                return@withLock persisted
            }
            val fresh = fetchFeed(uid, persisted)
            if (fresh.error == null) {
                local.putFeedSnapshot(uid, json.encodeToString(fresh))
                local.setLastSync(uid, "feed_snapshot_at", lockedNow.toString())
                memoryFeed = fresh; memoryAtMs = lockedNow
                fresh
            } else if (persisted != null) {
                val fallback = persisted.copy(notice = "正在显示本机缓存；服务器暂时不可达")
                memoryFeed = fallback; memoryAtMs = lockedNow
                fallback
            } else fresh
        }
    }

    private suspend fun fetchFeed(uid: String, cached: FeedData?): FeedData = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext FeedData(error = "未连客户端后端或未登录")
        try {
            val rb = Request.Builder().url("$base/api/feed").header("Authorization", "Bearer $token")
            local.getLastSync(uid, "feed_etag").takeIf { it.startsWith("\"") }?.let { rb.header("If-None-Match", it) }
            val req = rb.get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 304 && cached != null) return@withContext cached
                // V214: 老后端没有 /api/feed → 不再只给红条，回退到老端点拼一份"兼容模式动态"：
                // 最近对话 + 最新产物照样有；runner/定时任务/经验回放需 V218+，以软提示说明。
                if (resp.code == 404) return@withContext fallbackFeed(base, token)
                if (!resp.isSuccessful) return@withContext FeedData(error = "动态加载失败（${resp.code}）")
                val o = JSONObject(resp.body?.string() ?: return@withContext FeedData(error = "空响应"))
                resp.header("ETag")?.takeIf { it.isNotBlank() }?.let { local.setLastSync(uid, "feed_etag", it) }

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
                            downloadUrl = f.optString("download_url",
                                "/api/conversations/$cid/download/" + URLEncoder.encode(fn, "UTF-8")),
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
                val runs = mutableListOf<FeedRun>()
                o.optJSONArray("runs")?.forEach { r ->
                    val title = r.optString("title", "")
                    if (title.isNotBlank()) {
                        val failed = mutableListOf<String>()
                        r.optJSONArray("failed_checks")?.let { arr ->
                            for (i in 0 until arr.length()) arr.optString(i).takeIf { it.isNotBlank() }?.let(failed::add)
                        }
                        runs.add(FeedRun(
                            title = title,
                            status = r.optString("status", ""),
                            tsSec = sec(r.optDouble("ts", 0.0)),
                            convId = r.optString("conv_id", ""),
                            convTitle = r.optString("conv_title", ""),
                            runId = r.optString("run_id", ""),
                            taskType = r.optString("task_type", ""),
                            executionMode = r.optString("execution_mode", ""),
                            model = r.optString("model", ""),
                            elapsedMs = r.optLong("elapsed_ms", 0),
                            stopReason = r.optString("stop_reason", ""),
                            iterations = r.optInt("iterations", 0),
                            tokens = r.optLong("tokens", 0),
                            failedChecks = failed,
                        ))
                    }
                }
                FeedData(
                    release = o.optString("release", ""),
                    files = files, convs = convs, runners = runners,
                    scheduled = sched, scheduledCount = schedCount, episodes = eps, runs = runs,
                )
            }
        } catch (e: Exception) {
            FeedData(error = "网络错误")
        }
    }

    private companion object {
        const val MEMORY_TTL_MS = 15_000L
        const val DISK_TTL_MS = 60_000L
    }

    /** V214 兼容模式：老后端（无 /api/feed）用一直都有的两个端点拼动态。
     *  GET /api/conversations → 最近 8 会话；前 3 个会话各拉 /files → 合并按 mtime 取 10 个产物。 */
    private fun fallbackFeed(base: String, token: String): FeedData {
        val notice = "后端未升级到 V218+：动态为兼容模式（对话/产物可用；电脑端心跳、定时任务、经验回放需升级——源码包里 upgrade-server.sh 一键搞定）。"
        try {
            val creq = Request.Builder().url("$base/api/conversations").header("Authorization", "Bearer $token").get().build()
            http.newCall(creq).execute().use { cr ->
                if (!cr.isSuccessful) return FeedData(error = "动态加载失败（${cr.code}）")
                val co = JSONObject(cr.body?.string() ?: return FeedData(error = "空响应"))
                val convs = mutableListOf<FeedConv>()
                co.optJSONArray("conversations")?.forEach { c ->
                    val id = c.optString("id")
                    if (id.isNotBlank()) convs.add(
                        FeedConv(id, c.optString("title", "对话"),
                            sec(c.optDouble("updated_at", c.optDouble("created_at", 0.0))))
                    )
                }
                val top = convs.take(8)
                val files = mutableListOf<FeedFile>()
                for (c in top.take(3)) {
                    try {
                        val freq = Request.Builder().url("$base/api/conversations/${c.id}/files")
                            .header("Authorization", "Bearer $token").get().build()
                        http.newCall(freq).execute().use { fr ->
                            if (!fr.isSuccessful) return@use
                            val fo = JSONObject(fr.body?.string() ?: return@use)
                            fo.optJSONArray("files")?.forEach { f ->
                                val fn = f.optString("filename")
                                if (fn.isNotBlank()) files.add(
                                    FeedFile(
                                        convId = c.id, convTitle = c.title, filename = fn,
                                        size = f.optLong("size", 0L),
                                        mtimeSec = sec(f.optDouble("mtime", 0.0)),
                                        viewUrl = "$base/api/conversations/${c.id}/files/" +
                                            URLEncoder.encode(fn, "UTF-8") + "/view?token=" +
                                            URLEncoder.encode(token, "UTF-8"),
                                        downloadUrl = "/api/conversations/${c.id}/download/" +
                                            URLEncoder.encode(fn, "UTF-8"),
                                    )
                                )
                            }
                        }
                    } catch (_: Exception) { /* 单会话失败不拖垮整页 */ }
                }
                files.sortByDescending { it.mtimeSec }
                return FeedData(convs = top, files = files.take(10), notice = notice)
            }
        } catch (e: Exception) {
            return FeedData(error = "网络错误")
        }
    }
}

/** JSONArray 便捷遍历（只取对象元素）。 */
private inline fun JSONArray.forEach(block: (JSONObject) -> Unit) {
    for (i in 0 until length()) (optJSONObject(i) ?: continue).let(block)
}
