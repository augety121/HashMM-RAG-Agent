package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/* ── 数据模型（V259：三工坊 App 原生化）───────────────────────────────── */
data class AgentInfo(val id: String, val name: String, val skill: String)
data class TeamRole(
    val role: String, var task: String, val agentId: String = "",
    val state: String = "wait", val finding: String = "", val err: String = "",
)
data class TeamStatusA(
    val teamId: String, val goal: String, val status: String,
    val roles: List<TeamRole>, val final: String, val convId: String = "",
    val graphNodes: Int = 0, val graphEdges: Int = 0, val graphBlockers: Int = 0,
    val graphNextAction: String = "",
    val frontierUnresolved: Int = 0, val frontierReady: Int = 0,
    val frontierScopeBlocked: Int = 0, val frontierNextAction: String = "",
    val gateStatus: String = "", val gateRequired: Int = 0, val gatePassed: Int = 0,
    val gateFailed: Int = 0, val gateReview: Int = 0, val gateNextAction: String = "",
)
data class DocActionA(val id: String, val name: String, val desc: String)
data class DocResult(
    val convId: String,
    val file: String,
    val content: String,
    val note: String,
    val kind: String = "",
)
data class GwFocus(val goal: String, val source: String)
data class GwModule(val id: String, val name: String, val state: String, val detail: String)
data class GwBufferItem(val module: String, val summary: String)
data class GwSnapshotA(val focus: GwFocus?, val modules: List<GwModule>, val buffer: List<GwBufferItem>)
data class LoopInfoA(
    val id: String, val type: String, val status: String, val label: String,
    val progress: String, val score: Int, val lastNote: String,
    val networkMode: String = "deny", val allowSubagents: Boolean = false,
    val allowedToolCount: Int = 0, val acceptance: String = "",
    val graphNodes: Int = 0, val graphEdges: Int = 0, val graphBlockers: Int = 0,
    val graphNextAction: String = "",
    val frontierUnresolved: Int = 0, val frontierReady: Int = 0,
    val frontierScopeBlocked: Int = 0, val frontierNextAction: String = "",
    val gateStatus: String = "", val gateRequired: Int = 0, val gatePassed: Int = 0,
    val gateFailed: Int = 0, val gateReview: Int = 0, val gateNextAction: String = "",
    val awaitingUserAcceptance: Boolean = false, val acceptanceAccepted: Boolean? = null,
)
data class EventRuleA(val id: String, val name: String, val on: Boolean)
data class StudioListResult<T>(val items: List<T> = emptyList(), val error: String = "")

/**
 * 三工坊仓库：智能体工坊 / 文档工坊 / 总控中枢（含循环工程、事件规则、问工作区）。
 * 与桌面端同一套 API；所有方法失败返回 null / 空并把错误吞成可展示文案，绝不抛出。
 */
@Singleton
class StudioRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder()
        .callTimeout(120, TimeUnit.SECONDS)   // preview/run 走 LLM，宽超时
        .connectTimeout(10, TimeUnit.SECONDS)
        .build()

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

    private suspend fun getResult(path: String): Pair<JSONObject?, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext null to "尚未连接 HashMM 服务或登录已失效"
        }
        try {
            val req = Request.Builder().url("$base$path").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) {
                    val detail = runCatching { JSONObject(text).optString("detail") }.getOrDefault("")
                    val fallback = when (r.code) {
                        401 -> "登录已失效，请重新登录"
                        403 -> "当前账号没有访问这项能力的权限"
                        404 -> "服务器版本尚未提供这项能力"
                        else -> "读取失败（${r.code}）"
                    }
                    return@withContext null to detail.ifBlank { fallback }
                }
                if (text.isBlank()) return@withContext null to "服务返回了空响应"
                val parsed = runCatching { JSONObject(text) }.getOrNull()
                    ?: return@withContext null to "服务返回的数据格式不兼容"
                parsed to ""
            }
        } catch (_: Exception) { null to "暂时无法连接 HashMM 服务" }
    }

    private suspend fun get(path: String): JSONObject? = getResult(path).first

    private suspend fun post(path: String, body: JSONObject): Pair<JSONObject?, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null to "未连客户端后端或未登录"
        try {
            val rb = body.toString().toRequestBody("application/json".toMediaType())
            val req = Request.Builder().url("$base$path").header("Authorization", "Bearer $token").post(rb).build()
            http.newCall(req).execute().use { r ->
                val txt = r.body?.string() ?: ""
                if (!r.isSuccessful) {
                    val msg = try { JSONObject(txt).optString("detail", "") } catch (_: Exception) { "" }
                    return@withContext null to (msg.ifBlank { "请求失败（${r.code}）" })
                }
                (try { JSONObject(txt) } catch (_: Exception) { JSONObject() }) to ""
            }
        } catch (_: Exception) { null to "网络错误" }
    }

    /* ── 智能体工坊 ── */
    suspend fun agents(): List<AgentInfo> {
        return agentsResult().items
    }

    suspend fun agentsResult(): StudioListResult<AgentInfo> {
        val (root, error) = getResult("/api/team/agents")
        if (root == null) return StudioListResult(error = error)
        val arr = root.optJSONArray("agents")
            ?: return StudioListResult(error = "服务器返回的成员库格式不兼容")
        val items = (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            AgentInfo(o.optString("id"), o.optString("name"), o.optString("skill"))
        }
        return StudioListResult(items = items)
    }

    suspend fun preview(goal: String, agentIds: List<String>?): Pair<List<TeamRole>, String> {
        val body = JSONObject().put("goal", goal)
        if (agentIds != null) body.put("agent_ids", JSONArray(agentIds))
        val (o, err) = post("/api/team/preview", body)
        if (o == null) return emptyList<TeamRole>() to err
        val arr = o.optJSONArray("roles") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            val r = arr.getJSONObject(i)
            TeamRole(r.optString("role"), r.optString("task"), r.optString("agent_id"))
        } to ""
    }

    suspend fun startTeam(goal: String, convId: String, roles: List<TeamRole>, mode: String = "parallel"): Pair<String, String> {
        val arr = JSONArray()
        roles.forEach { arr.put(JSONObject().put("role", it.role).put("task", it.task).put("agent_id", it.agentId)) }
        val (o, err) = post("/api/team/start", JSONObject().put("goal", goal).put("conv_id", convId).put("roles", arr).put("mode", mode))
        return (o?.optString("team_id") ?: "") to err
    }

    suspend fun teamStatus(teamId: String): TeamStatusA? {
        val o = get("/api/team/status/$teamId") ?: return null
        val arr = o.optJSONArray("roles") ?: JSONArray()
        val graph = o.optJSONObject("evidence_graph")
        val graphSummary = graph?.optJSONObject("summary")
        val graphActions = graph?.optJSONArray("next_actions")
        val frontier = o.optJSONObject("execution_frontier")
        val frontierSummary = frontier?.optJSONObject("summary")
        val frontierItems = frontier?.optJSONArray("items")
        val gate = o.optJSONObject("completion_gate")
        val gateSummary = gate?.optJSONObject("summary")
        return TeamStatusA(
            teamId = o.optString("team_id"), goal = o.optString("goal"),
            status = o.optString("status"), final = o.optString("final"),
            convId = o.optString("conv_id"),
            graphNodes = graphSummary?.optInt("nodes", 0) ?: 0,
            graphEdges = graphSummary?.optInt("edges", 0) ?: 0,
            graphBlockers = graphSummary?.optInt("blockers", 0) ?: 0,
            graphNextAction = graphActions?.optString(0, "") ?: "",
            frontierUnresolved = frontierSummary?.optInt("unresolved", 0) ?: 0,
            frontierReady = frontierSummary?.optInt("ready_routes", 0) ?: 0,
            frontierScopeBlocked = frontierSummary?.optInt("scope_blocked", 0) ?: 0,
            frontierNextAction = frontierItems?.optJSONObject(0)?.optString("minimum_action", "") ?: "",
            gateStatus = gate?.optString("status", "") ?: "",
            gateRequired = gateSummary?.optInt("required", 0) ?: 0,
            gatePassed = gateSummary?.optInt("passed", 0) ?: 0,
            gateFailed = gateSummary?.optInt("failed", 0) ?: 0,
            gateReview = (gateSummary?.optInt("review", 0) ?: 0) + (gateSummary?.optInt("missing", 0) ?: 0),
            gateNextAction = gate?.optString("next_action", "") ?: "",
            roles = (0 until arr.length()).map { i ->
                val r = arr.getJSONObject(i)
                TeamRole(r.optString("role"), r.optString("task"), r.optString("agent_id"),
                    r.optString("state"), r.optString("finding"), r.optString("err"))
            },
        )
    }

    suspend fun routeMode(): String = get("/api/team/route")?.optString("mode", "auto") ?: "auto"
    suspend fun setRouteMode(mode: String): Boolean =
        post("/api/team/route", JSONObject().put("mode", mode)).first != null

    /* ── 文档工坊 ── */
    suspend fun docActions(): List<DocActionA> {
        return docActionsResult().items
    }

    suspend fun docActionsResult(): StudioListResult<DocActionA> {
        val (root, error) = getResult("/api/docstudio/actions")
        if (root == null) return StudioListResult(error = error)
        val arr = root.optJSONArray("actions")
            ?: return StudioListResult(error = "服务器返回的文档动作格式不兼容")
        val items = (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            DocActionA(o.optString("id"), o.optString("name"), o.optString("desc"))
        }
        return StudioListResult(items = items)
    }

    /** 上传文件到会话（analyze=0 只落盘），返回 (服务器文件名, 错误)。 */
    suspend fun uploadFile(convId: String, name: String, bytes: ByteArray): Pair<String, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext "" to "未连客户端后端或未登录"
        try {
            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", name, bytes.toRequestBody("application/octet-stream".toMediaType()))
                .build()
            val req = Request.Builder().url("$base/api/conversations/$convId/upload")
                .header("Authorization", "Bearer $token").post(body).build()
            http.newCall(req).execute().use { r ->
                val txt = r.body?.string() ?: ""
                if (!r.isSuccessful) return@withContext "" to "上传失败（${r.code}）"
                (JSONObject(txt).optString("filename", name)) to ""
            }
        } catch (_: Exception) { "" to "网络错误" }
    }

    suspend fun docRun(convId: String, filename: String?, text: String?, action: String,
                       target: String, instruction: String): Pair<DocResult?, String> {
        val body = JSONObject().put("conv_id", convId).put("action", action).put("target", target)
        if (!filename.isNullOrBlank()) body.put("filename", filename)
        if (!text.isNullOrBlank()) body.put("text", text)
        if (instruction.isNotBlank()) body.put("instruction", instruction)
        val (o, err) = post("/api/docstudio/run", body)
        if (o == null) return null to err
        val artifact = o.optJSONObject("artifact")
        return DocResult(
            convId = o.optString("conv_id", convId),
            file = o.optString("file"),
            content = o.optString("content"),
            note = o.optString("note"),
            kind = artifact?.optString("kind").orEmpty(),
        ) to ""
    }

    /** 新建会话（工坊产出落画布用）。 */
    suspend fun createConversation(convId: String, title: String): Boolean =
        post("/api/conversations", JSONObject().put("id", convId).put("title", title)).first != null

    /* ── 总控中枢 ── */
    suspend fun gwState(): GwSnapshotA? {
        val o = get("/api/gw/state?history_n=0") ?: return null
        val f = o.optJSONObject("focus")
        val mods = o.optJSONArray("modules") ?: JSONArray()
        val buf = o.optJSONArray("buffer") ?: JSONArray()
        return GwSnapshotA(
            focus = f?.let { GwFocus(it.optString("goal"), it.optString("source")) },
            modules = (0 until mods.length()).map { i ->
                val m = mods.getJSONObject(i)
                GwModule(m.optString("id"), m.optString("name"), m.optString("state", "idle"), m.optString("detail"))
            },
            buffer = (0 until buf.length()).map { i ->
                val b = buf.getJSONObject(i)
                GwBufferItem(b.optString("module"), b.optString("summary"))
            }.reversed(),
        )
    }

    suspend fun gwAsk(question: String): Pair<String, String> {
        val (o, err) = post("/api/gw/ask", JSONObject().put("question", question))
        if (o == null) return "" to err
        return o.optString("answer") to o.optString("model")
    }

    suspend fun health(): Pair<String, String> {   // (release, deepsearch mode)
        val h = get("/api/health")?.optString("release", "") ?: ""
        val ds = get("/api/deepsearch/status")
        val mode = when {
            ds == null -> ""
            ds.optBoolean("full_model_dir") -> "full"
            ds.optBoolean("lite") -> "lite"
            else -> "off"
        }
        return h to mode
    }

    suspend fun loops(): List<LoopInfoA> {
        return loopsResult().items
    }

    suspend fun loopsResult(): StudioListResult<LoopInfoA> {
        val (root, error) = getResult("/api/loops")
        if (root == null) return StudioListResult(error = error)
        val arr = root.optJSONArray("loops")
            ?: return StudioListResult(error = "服务器返回的持续任务格式不兼容")
        val items = (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            val goal = o.optString("goal"); val prompt = o.optString("prompt")
            val isGoal = o.optString("type") == "goal"
            val hist = o.optJSONArray("history")
            val scope = o.optJSONObject("execution_scope")
            val network = scope?.optJSONObject("network")
            val graph = o.optJSONObject("evidence_graph")
            val graphSummary = graph?.optJSONObject("summary")
            val graphActions = graph?.optJSONArray("next_actions")
            val frontier = o.optJSONObject("execution_frontier")
            val frontierSummary = frontier?.optJSONObject("summary")
            val frontierItems = frontier?.optJSONArray("items")
            val gate = o.optJSONObject("completion_gate")
            val gateSummary = gate?.optJSONObject("summary")
            val gateCriteria = gate?.optJSONArray("criteria")
            var awaitingUserAcceptance = false
            if (gateCriteria != null) for (index in 0 until gateCriteria.length()) {
                val criterion = gateCriteria.optJSONObject(index) ?: continue
                if (criterion.optString("check_id") == "user_acceptance" && criterion.optString("status") != "passed") {
                    awaitingUserAcceptance = true
                    break
                }
            }
            val acceptanceConfirmation = o.optJSONObject("acceptance_confirmation")
            val lastNote = if (hist != null && hist.length() > 0)
                hist.getJSONObject(hist.length() - 1).optString("note", "") else ""
            LoopInfoA(
                id = o.optString("id"), type = o.optString("type"), status = o.optString("status"),
                label = if (isGoal) goal else prompt,
                progress = if (isGoal) "${o.optInt("rounds")}/${o.optInt("max_rounds")} 轮"
                           else "${o.optInt("runs")}/${o.optInt("max_runs")} 次 · 每 ${o.optInt("interval_min")} 分",
                score = o.optInt("score", 0), lastNote = lastNote,
                networkMode = network?.optString("mode", "deny") ?: "deny",
                allowSubagents = scope?.optBoolean("allow_subagents", false) ?: false,
                allowedToolCount = scope?.optJSONArray("allowed_tools")?.length() ?: 0,
                acceptance = o.optString("acceptance", ""),
                graphNodes = graphSummary?.optInt("nodes", 0) ?: 0,
                graphEdges = graphSummary?.optInt("edges", 0) ?: 0,
                graphBlockers = graphSummary?.optInt("blockers", 0) ?: 0,
                graphNextAction = graphActions?.optString(0, "") ?: "",
                frontierUnresolved = frontierSummary?.optInt("unresolved", 0) ?: 0,
                frontierReady = frontierSummary?.optInt("ready_routes", 0) ?: 0,
                frontierScopeBlocked = frontierSummary?.optInt("scope_blocked", 0) ?: 0,
                frontierNextAction = frontierItems?.optJSONObject(0)?.optString("minimum_action", "") ?: "",
                gateStatus = gate?.optString("status", "") ?: "",
                gateRequired = gateSummary?.optInt("required", 0) ?: 0,
                gatePassed = gateSummary?.optInt("passed", 0) ?: 0,
                gateFailed = gateSummary?.optInt("failed", 0) ?: 0,
                gateReview = (gateSummary?.optInt("review", 0) ?: 0) + (gateSummary?.optInt("missing", 0) ?: 0),
                gateNextAction = gate?.optString("next_action", "") ?: "",
                awaitingUserAcceptance = awaitingUserAcceptance,
                acceptanceAccepted = acceptanceConfirmation?.optBoolean("accepted"),
            )
        }
        return StudioListResult(items = items)
    }

    suspend fun loopGoal(
        goal: String,
        acceptance: String,
        rounds: Int,
        approvalMode: String,
        networkMode: String,
        allowSubagents: Boolean,
    ): String = post(
        "/api/loops/goal",
        JSONObject()
            .put("goal", goal)
            .put("acceptance", acceptance)
            .put("max_rounds", rounds)
            .put("approval_mode", approvalMode)
            .put("network_mode", networkMode)
            .put("allowed_origins", JSONArray())
            .put("allow_subagents", allowSubagents),
    ).second

    suspend fun loopStop(id: String): Boolean = post("/api/loops/$id/stop", JSONObject()).first != null

    suspend fun loopAcceptance(id: String, accepted: Boolean, note: String): String = post(
        "/api/loops/$id/acceptance",
        JSONObject().put("accepted", accepted).put("note", note.take(500)),
    ).second

    suspend fun rules(): List<EventRuleA> {
        return rulesResult().items
    }

    suspend fun rulesResult(): StudioListResult<EventRuleA> {
        val (root, error) = getResult("/api/gw/rules")
        if (root == null) return StudioListResult(error = error)
        val arr = root.optJSONArray("rules")
            ?: return StudioListResult(error = "服务器返回的自动处理规则格式不兼容")
        val items = (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            EventRuleA(o.optString("id"), o.optString("name"), o.optBoolean("on", true))
        }
        return StudioListResult(items = items)
    }

    suspend fun setRule(id: String, on: Boolean): Boolean =
        post("/api/gw/rules", JSONObject().put("id", id).put("on", on)).first != null
}
