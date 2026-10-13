package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/** V219 原生三页（审计/质量/高级能力）的数据源。
 *  端点与桌面端 lib/api.ts 完全同款（audit/tools · admin/quality/dashboard ·
 *  dispatch · credentials · kb/quarantine · images），只读 v1；操作仍在桌面端。 */

data class NotifItem(val type: String, val text: String, val by: String, val tsSec: Long, val read: Boolean, val shareId: String = "")
data class NotifData(val items: List<NotifItem> = emptyList(), val unread: Int = 0, val error: String? = null)
data class AuditItem(val tool: String, val user: String, val tsSec: Long)
data class QualityDay(val epochDay: Long, val samples: Int, val groundedRatio: Double?)
data class ToolQualityData(
    val runManifests: Int = 0,
    val evaluableRuns: Int = 0,
    val notEvaluableRuns: Int = 0,
    val passedRuns: Int = 0,
    val failedRuns: Int = 0,
    val toolCalls: Int = 0,
    val failedToolCalls: Int = 0,
    val executionSuccessRate: Double? = null,
    val wrongToolFeedback: Int = 0,
    val scope: String = "",
    val truncated: Boolean = false,
)
data class TaskGraphQualityData(
    val runManifests: Int = 0,
    val graphRuns: Int = 0,
    val missingGraphRuns: Int = 0,
    val traceCoverage: Double? = null,
    val blockedRuns: Int = 0,
    val openBlockers: Int = 0,
    val claimEvidenceCoverage: Double? = null,
    val claimEvaluableRuns: Int = 0,
    val integrityViolations: Int = 0,
    val scope: String = "",
)
data class ExecutionFrontierQualityData(
    val runManifests: Int = 0,
    val frontierRuns: Int = 0,
    val missingFrontierRuns: Int = 0,
    val traceCoverage: Double? = null,
    val convergedRuns: Int = 0,
    val actionableRuns: Int = 0,
    val unresolvedItems: Int = 0,
    val readyRoutes: Int = 0,
    val scopeBlockedItems: Int = 0,
    val waitingInputItems: Int = 0,
    val routeCoverage: Double? = null,
    val integrityViolations: Int = 0,
    val scope: String = "",
    val truncated: Boolean = false,
)
data class CompletionGateQualityData(
    val runManifests: Int = 0,
    val gateRuns: Int = 0,
    val missingGateRuns: Int = 0,
    val verifiedRuns: Int = 0,
    val deliveredWithLimitsRuns: Int = 0,
    val incompleteRuns: Int = 0,
    val blockedRuns: Int = 0,
    val verifiedRate: Double? = null,
    val repeatedToolCallRuns: Int = 0,
    val unsafeCompletionClaims: Int = 0,
    val integrityViolations: Int = 0,
    val scope: String = "",
)
data class QualityData(
    val days: Int = 7,
    val samples: Int = 0,
    val evaluableSamples: Int = 0,
    val notEvaluableSamples: Int = 0,
    val avgGroundedRatio: Double? = null,
    val evidenceCoverage: Double? = null,
    val avgSources: Double = 0.0,
    val avgCitations: Double = 0.0,
    val avgLatencyMs: Long = 0,
    val answersWithoutSources: Int = 0,
    val weaklyGrounded: Int = 0,
    val weakRate: Double = 0.0,
    val sampleRate: Int = 0,
    val daily: List<QualityDay> = emptyList(),
    val toolQuality: ToolQualityData = ToolQualityData(),
    val taskGraphQuality: TaskGraphQualityData = TaskGraphQualityData(),
    val executionFrontierQuality: ExecutionFrontierQualityData = ExecutionFrontierQualityData(),
    val completionGateQuality: CompletionGateQualityData = CompletionGateQualityData(),
    val error: String? = null,
)
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
    private val http = SharedHttp.base.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()

    private suspend fun base(): String {
        val b = settings.clientUrl.first().trim().trimEnd('/')
        if (b.isBlank()) return ""
        val schemeless = b.removePrefix("https://").removePrefix("http://")
        val host = schemeless.substringBefore("/").substringBefore(":")
        val isIp = Regex("""^\d{1,3}(\.\d{1,3}){3}$""").matches(host)
        return when { isIp -> "http://$schemeless"; b.startsWith("http") -> b; else -> "https://$b" }
    }

    private fun sec(v: Double): Long = when { v <= 0 -> 0L; v > 1e12 -> (v / 1000.0).toLong(); else -> v.toLong() }

    /** V273 把 App 原生测试报告 md 发到后端落盘——与桌面端同一端点 /api/selftest/save-report，
     *  服务器保存到 data/selftest_reports/，跑一次存一个日志。返回 (是否成功, 服务器相对路径或错误)。 */
    suspend fun saveSelftestReport(reportMd: String): Pair<Boolean, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false to "未连客户端后端或未登录"
        try {
            val body = JSONObject().put("report_md", reportMd)
            val req = Request.Builder().url("$base/api/selftest/save-report")
                .header("Authorization", "Bearer $token")
                .post(okhttp3.RequestBody.create("application/json".toMediaType(), body.toString()))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string()
                if (resp.isSuccessful) {
                    val path = text?.let { runCatching { JSONObject(it).optString("report_path") }.getOrNull() } ?: ""
                    true to path
                } else false to errFor(resp.code)
            }
        } catch (e: Exception) { false to "网络错误" }
    }

    private suspend fun getRaw(path: String): Pair<Int, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext -1 to null
        try {
            val req = Request.Builder().url("$base$path").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp -> resp.code to resp.body?.string() }
        } catch (e: Exception) { -2 to null }
    }

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
        422 -> "后端拒绝了请求参数（版本差异）——请用最新源码包升级后端"
        else -> "加载失败（$code）"
    }

    /** V230 站内通知：@提及 / 我发布的画布有新评论。 */
    suspend fun notifications(): NotifData {
        val (code, o) = getJson("/api/notifications")
        if (o == null || code !in 200..299) return NotifData(error = errFor(code))
        val arr = o.optJSONArray("items") ?: JSONArray()
        val out = mutableListOf<NotifItem>()
        for (i in 0 until arr.length()) {
            val e = arr.optJSONObject(i) ?: continue
            out.add(NotifItem(e.optString("type"), e.optString("text"), e.optString("by"),
                sec(e.optDouble("ts", 0.0)), e.optBoolean("read", false), e.optString("share_id", "")))
        }
        return NotifData(items = out, unread = o.optInt("unread", 0))
    }

    /** V232 通知直达：拼 Viewer 完整地址（带登录态），无 base/token 回 null。 */
    suspend fun viewerUrl(shareId: String): String? {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || shareId.isBlank()) return null
        return "$base/canvas/$shareId?token=" + java.net.URLEncoder.encode(token, "UTF-8")
    }

    suspend fun notificationsReadAll(): Boolean {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return false
        return try {
            val req = Request.Builder().url("$base/api/notifications/read")
                .header("Authorization", "Bearer $token")
                .post(okhttp3.RequestBody.create("application/json".toMediaType(), "{}")).build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }

    /** V228 审计高级筛选：直连 /audit/query（username / start_ts），响应根可为对象或数组。 */
    suspend fun auditQuery(username: String, startTs: Long, limit: Int = 100): Pair<List<AuditItem>, String?> {
        val q = buildString {
            append("/api/admin/audit/query?limit=").append(limit)
            if (username.isNotBlank()) append("&username=").append(java.net.URLEncoder.encode(username, "UTF-8"))
            if (startTs > 0) append("&start_ts=").append(startTs)
        }
        val (code, body) = getRaw(q)
        if (body == null || code !in 200..299) return emptyList<AuditItem>() to errFor(code)
        val arr: JSONArray = runCatching {
            val t = body.trim()
            if (t.startsWith("[")) JSONArray(t)
            else JSONObject(t).let { o -> o.optJSONArray("items") ?: o.optJSONArray("logs") ?: o.optJSONArray("entries") ?: JSONArray() }
        }.getOrElse { return emptyList<AuditItem>() to "响应格式无法解析" }
        val out = mutableListOf<AuditItem>()
        for (i in 0 until arr.length()) {
            val e = arr.optJSONObject(i) ?: continue
            out.add(AuditItem(
                tool = e.optString("action", e.optString("tool", "?")),
                user = e.optString("username", e.optString("user", e.optString("uid", ""))),
                tsSec = sec(e.optDouble("ts", e.optDouble("created", e.optDouble("created_at", 0.0)))),
            ))
        }
        return out to null
    }

    /** V220 一键派活：POST /api/dispatch（runner=desktop）。返回 (成功, 提示语)。 */
    suspend fun createDispatch(kind: String, payload: JSONObject): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false to "未连客户端后端或未登录"
        try {
            val body = JSONObject().put("runner", "desktop").put("kind", kind).put("payload", payload)
            val req = Request.Builder().url("$base/api/dispatch")
                .header("Authorization", "Bearer $token")
                .post(okhttp3.RequestBody.create("application/json".toMediaType(), body.toString()))
                .build()
            http.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) true to "已派活：电脑端认领后自动执行，结果回填任务列表"
                else false to errFor(resp.code)
            }
        } catch (e: Exception) { false to "网络错误" }
    }

    /** 权限审计（V221 双端点）：先 /audit/tools（工具流），非 2xx 自动回退 /audit/query
     *  （合规审计流，老/异构后端兜底）——422 之类的版本差异不再把页面打死。 */
    suspend fun audit(limit: Int = 30): Pair<List<AuditItem>, String?> {
        var (code, o) = getJson("/api/admin/audit/tools?limit=$limit")
        if (o == null || code !in 200..299) {
            val fb = getJson("/api/admin/audit/query?limit=$limit")
            if (fb.second != null && fb.first in 200..299) { code = fb.first; o = fb.second }
            else return emptyList<AuditItem>() to errFor(code)
        }
        val arr: JSONArray = o!!.optJSONArray("entries") ?: o!!.optJSONArray("items")
            ?: o!!.optJSONArray("logs") ?: o!!.optJSONArray("events") ?: JSONArray()
        val out = mutableListOf<AuditItem>()
        for (i in 0 until arr.length()) {
            val e = arr.optJSONObject(i) ?: continue
            out.add(AuditItem(
                tool = e.optString("tool", e.optString("action", "?")),
                user = e.optString("user", e.optString("username", e.optString("uid", ""))),
                tsSec = sec(e.optDouble("ts", e.optDouble("created", 0.0))),
            ))
        }
        return out to null
    }

    /** 质量看板：严格解析后端 quality_monitor.dashboard；null 表示不可评估，不造 0 分。 */
    suspend fun quality(days: Int = 7): QualityData {
        val (code, o) = getJson("/api/admin/quality/dashboard?days=$days")
        if (o == null || code !in 200..299) return QualityData(error = errFor(code))
        fun nullableDouble(key: String): Double? = if (!o.has(key) || o.isNull(key)) null else o.optDouble(key)
        val trend = mutableListOf<QualityDay>()
        o.optJSONArray("daily")?.let { arr ->
            for (i in 0 until arr.length()) {
                val day = arr.optJSONObject(i) ?: continue
                trend.add(QualityDay(
                    epochDay = day.optLong("day", 0),
                    samples = day.optInt("samples", 0),
                    groundedRatio = if (!day.has("grounded_ratio") || day.isNull("grounded_ratio")) null
                        else day.optDouble("grounded_ratio"),
                ))
            }
        }
        val tq = o.optJSONObject("tool_quality")
        val gq = o.optJSONObject("task_graph_quality")
        val fq = o.optJSONObject("execution_frontier_quality")
        val cq = o.optJSONObject("completion_gate_quality")
        return QualityData(
            days = o.optInt("days", days),
            samples = o.optInt("samples", 0),
            evaluableSamples = o.optInt("evaluable_samples", 0),
            notEvaluableSamples = o.optInt("not_evaluable_samples", 0),
            avgGroundedRatio = nullableDouble("avg_grounded_ratio"),
            evidenceCoverage = nullableDouble("evidence_coverage"),
            avgSources = o.optDouble("avg_sources", 0.0),
            avgCitations = o.optDouble("avg_citations", 0.0),
            avgLatencyMs = o.optLong("avg_latency_ms", 0),
            answersWithoutSources = o.optInt("answers_without_sources", 0),
            weaklyGrounded = o.optInt("weakly_grounded", 0),
            weakRate = o.optDouble("weak_rate", 0.0),
            sampleRate = o.optInt("sample_rate", 0),
            daily = trend,
            toolQuality = if (tq == null) ToolQualityData() else ToolQualityData(
                runManifests = tq.optInt("run_manifests", 0),
                evaluableRuns = tq.optInt("evaluable_runs", 0),
                notEvaluableRuns = tq.optInt("not_evaluable_runs", 0),
                passedRuns = tq.optInt("passed_runs", 0),
                failedRuns = tq.optInt("failed_runs", 0),
                toolCalls = tq.optInt("tool_calls", 0),
                failedToolCalls = tq.optInt("failed_tool_calls", 0),
                executionSuccessRate = if (tq.isNull("execution_success_rate")) null
                    else tq.optDouble("execution_success_rate"),
                wrongToolFeedback = tq.optInt("wrong_tool_feedback", 0),
                scope = tq.optString("scope", ""),
                truncated = tq.optBoolean("truncated", false),
            ),
            taskGraphQuality = if (gq == null) TaskGraphQualityData() else TaskGraphQualityData(
                runManifests = gq.optInt("run_manifests", 0),
                graphRuns = gq.optInt("graph_runs", 0),
                missingGraphRuns = gq.optInt("missing_graph_runs", 0),
                traceCoverage = if (gq.isNull("trace_coverage")) null else gq.optDouble("trace_coverage"),
                blockedRuns = gq.optInt("blocked_runs", 0),
                openBlockers = gq.optInt("open_blockers", 0),
                claimEvidenceCoverage = if (gq.isNull("claim_evidence_coverage")) null
                    else gq.optDouble("claim_evidence_coverage"),
                claimEvaluableRuns = gq.optInt("claim_evaluable_runs", 0),
                integrityViolations = gq.optInt("integrity_violations", 0),
                scope = gq.optString("scope", ""),
            ),
            executionFrontierQuality = if (fq == null) ExecutionFrontierQualityData() else ExecutionFrontierQualityData(
                runManifests = fq.optInt("run_manifests", 0),
                frontierRuns = fq.optInt("frontier_runs", 0),
                missingFrontierRuns = fq.optInt("missing_frontier_runs", 0),
                traceCoverage = if (fq.isNull("trace_coverage")) null else fq.optDouble("trace_coverage"),
                convergedRuns = fq.optInt("converged_runs", 0),
                actionableRuns = fq.optInt("actionable_runs", 0),
                unresolvedItems = fq.optInt("unresolved_items", 0),
                readyRoutes = fq.optInt("ready_routes", 0),
                scopeBlockedItems = fq.optInt("scope_blocked_items", 0),
                waitingInputItems = fq.optInt("waiting_input_items", 0),
                routeCoverage = if (fq.isNull("route_coverage")) null else fq.optDouble("route_coverage"),
                integrityViolations = fq.optInt("integrity_violations", 0),
                scope = fq.optString("scope", ""),
                truncated = fq.optBoolean("truncated", false),
            ),
            completionGateQuality = if (cq == null) CompletionGateQualityData() else CompletionGateQualityData(
                runManifests = cq.optInt("run_manifests", 0),
                gateRuns = cq.optInt("gate_runs", 0),
                missingGateRuns = cq.optInt("missing_gate_runs", 0),
                verifiedRuns = cq.optInt("verified_runs", 0),
                deliveredWithLimitsRuns = cq.optInt("delivered_with_limits_runs", 0),
                incompleteRuns = cq.optInt("incomplete_runs", 0),
                blockedRuns = cq.optInt("blocked_runs", 0),
                verifiedRate = if (cq.isNull("verified_rate")) null else cq.optDouble("verified_rate"),
                repeatedToolCallRuns = cq.optInt("repeated_tool_call_runs", 0),
                unsafeCompletionClaims = cq.optInt("unsafe_completion_claims", 0),
                integrityViolations = cq.optInt("integrity_violations", 0),
                scope = cq.optString("scope", ""),
            ),
        )
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

    // ── V267 测试中枢（对标桌面端 SelfTestView，同一后端 /api/selftest）─────
    /** 一个可勾选的自测套件（来自 GET /api/selftest/suites）。 */
    data class SelfTestSuite(val id: String, val name: String, val group: String, val slow: Boolean)

    /** 一条运行结果（来自 POST /api/selftest/run）。 */
    data class SelfTestResult(
        val id: String, val name: String, val group: String,
        val ok: Boolean, val skip: Boolean, val detail: String, val ms: Long,
    )

    data class SelfTestRun(
        val results: List<SelfTestResult> = emptyList(),
        val passCnt: Int = 0, val failCnt: Int = 0, val skipCnt: Int = 0,
        val error: String? = null,
    )

    /** 套件清单：分组勾选用。失败返回空表+错误串（不造数）。 */
    suspend fun selftestSuites(): Pair<List<SelfTestSuite>, String?> {
        val (code, o) = getJson("/api/selftest/suites")
        if (o == null || code !in 200..299) return emptyList<SelfTestSuite>() to errFor(code)
        val arr = o.optJSONArray("suites") ?: JSONArray()
        val out = mutableListOf<SelfTestSuite>()
        for (i in 0 until arr.length()) {
            val s = arr.optJSONObject(i) ?: continue
            val id = s.optString("id"); if (id.isBlank()) continue
            out.add(SelfTestSuite(id, s.optString("name", id), s.optString("group", "其他"), s.optBoolean("slow", false)))
        }
        return out to null
    }

    /** 只跑勾选的套件；后端逐项容错并回 PASS/FAIL/SKIP。 */
    suspend fun selftestRun(ids: List<String>): SelfTestRun = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext SelfTestRun(error = "未连客户端后端或未登录")
        try {
            val body = JSONObject().put("ids", JSONArray(ids))
            val req = Request.Builder().url("$base/api/selftest/run")
                .header("Authorization", "Bearer $token")
                .post(okhttp3.RequestBody.create("application/json".toMediaType(), body.toString()))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string()
                if (!resp.isSuccessful || text.isNullOrBlank()) return@use SelfTestRun(error = errFor(resp.code))
                val o = runCatching { JSONObject(text) }.getOrNull() ?: return@use SelfTestRun(error = "响应解析失败")
                val ra = o.optJSONArray("results") ?: JSONArray()
                val items = mutableListOf<SelfTestResult>()
                for (i in 0 until ra.length()) {
                    val r = ra.optJSONObject(i) ?: continue
                    items.add(SelfTestResult(
                        id = r.optString("id"), name = r.optString("name", r.optString("id")),
                        group = r.optString("group", ""), ok = r.optBoolean("ok", false),
                        skip = r.optBoolean("skip", false), detail = r.optString("detail", ""),
                        ms = r.optLong("ms", 0)))
                }
                val sm = o.optJSONObject("summary")
                SelfTestRun(results = items,
                    passCnt = sm?.optInt("pass") ?: items.count { it.ok },
                    failCnt = sm?.optInt("fail") ?: items.count { !it.ok && !it.skip },
                    skipCnt = sm?.optInt("skip") ?: items.count { it.skip })
            }
        } catch (e: Exception) { SelfTestRun(error = "网络错误") }
    }

    // ── V268 上下文透视（对标桌面端 contextInspect：GET /api/context/inspect）────
    /** 一个上下文组成块。 */
    data class CtxBlock(val id: String, val name: String, val present: Boolean,
                        val chars: Int, val preview: String, val note: String)

    data class CtxInspect(
        val blocks: List<CtxBlock> = emptyList(),
        val totalChars: Int = 0,
        val convId: String = "",
        val convTitle: String = "",
        val query: String = "",
        val resolvedLatest: Boolean = false,
        val tips: List<String> = emptyList(),
        val error: String? = null,
    )

    /** 看这轮 system 上下文由哪些块组成、各多少字符（排查"模型为何知道/不知道某事"）。 */
    suspend fun contextInspect(convId: String = ""): CtxInspect {
        val q = if (convId.isBlank()) "" else "?conv_id=" + java.net.URLEncoder.encode(convId, "UTF-8")
        val (code, o) = getJson("/api/context/inspect$q")
        if (o == null || code !in 200..299) return CtxInspect(error = errFor(code))
        val ba = o.optJSONArray("blocks") ?: JSONArray()
        val blocks = mutableListOf<CtxBlock>()
        for (i in 0 until ba.length()) {
            val b = ba.optJSONObject(i) ?: continue
            blocks.add(CtxBlock(
                id = b.optString("id"), name = b.optString("name", b.optString("id")),
                present = b.optBoolean("present", false), chars = b.optInt("chars", 0),
                preview = b.optString("preview", ""), note = b.optString("note", "")))
        }
        val ta = o.optJSONArray("tips") ?: JSONArray()
        val tips = mutableListOf<String>()
        for (i in 0 until ta.length()) ta.optString(i)?.takeIf { it.isNotBlank() }?.let { tips.add(it) }
        return CtxInspect(
            blocks = blocks,
            totalChars = o.optInt("total_chars", 0),
            convId = o.optString("conv_id", ""),
            convTitle = o.optString("conv_title", ""),
            query = o.optString("query", ""),
            resolvedLatest = o.optBoolean("resolved_latest", false),
            tips = tips,
        )
    }

    /** 手动建立长对话检查点；完整消息仍由后端 SQLite/Supabase 保存。 */
    suspend fun contextCompact(convId: String): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (convId.isBlank()) return@withContext false to "请先选择一条对话"
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false to "未连接后端或未登录"
        try {
            val encoded = java.net.URLEncoder.encode(convId, "UTF-8")
            val req = Request.Builder().url("$base/api/conversations/$encoded/compact")
                .header("Authorization", "Bearer $token")
                .post(okhttp3.RequestBody.create("application/json".toMediaType(), "{}"))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) return@use false to errFor(resp.code)
                val o = runCatching { JSONObject(text) }.getOrNull()
                    ?: return@use false to "响应解析失败"
                val compacted = o.optBoolean("compacted", false)
                val count = o.optJSONObject("state")?.optInt("compaction_count", 0) ?: 0
                if (compacted) true to "已建立第 $count 个检查点，完整原文仍保留"
                else true to o.optString("reason", "当前检查点已是最新")
            }
        } catch (e: Exception) { false to "网络错误" }
    }
}
