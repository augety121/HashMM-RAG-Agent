package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import com.hashmm.app.data.sync.ChatMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import java.util.concurrent.TimeUnit
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 直连桌面客户端拉某会话的消息（含正在流式生成的实时内容）。
 * 调 GET {clientUrl}/api/conversations/{id}/messages（带 Supabase token）。
 * 用于接管时的实时流式展示：客户端可达→拿到逐字增长的最新内容；不可达→返回 null，由上层回退到 Supabase 缓存。
 */
@Singleton
/**
 * V308 可测性重构：抽出 LiveChatManager 真正依赖的【最小接口】。
 *
 * LiveChatManager 只用到 streamMessage 这一个方法，却依赖整个 ChatLiveRepository 具体类
 * （内部要构造 OkHttp/SettingsStore/AuthRepository），导致单测【无法注入 Fake】——
 * 这正是原 LiveChatManagerTest 沦为"假测试"（只能测 StringBuilder）的直接原因。
 * 抽出接口后，测试可用一个纯内存 Fake 实现它，真正测到并发与状态逻辑。
 */
data class ChatStreamEvent(val type: String, val data: String)
data class ActiveTurnState(
    val turnId: String,
    val conversationId: String,
    val steerable: Boolean,
    val status: String,
    val mode: String,
)
data class SteerTurnResult(val accepted: Boolean, val duplicate: Boolean, val messageId: String)

interface LiveStreamSource {
    suspend fun streamMessage(convId: String, message: String, onToken: (String) -> Unit): Boolean

    /** Semantic event path. Old/fake sources remain compatible through the default implementation. */
    suspend fun streamMessageWithEvents(
        convId: String,
        message: String,
        onToken: (String) -> Unit,
        onEvent: (ChatStreamEvent) -> Unit,
    ): Boolean = streamMessage(convId, message, onToken)

    /** Cancel the real transport for this conversation when supported. */
    fun cancelStream(convId: String) = Unit

    suspend fun activeTurn(convId: String): ActiveTurnState? = null
    suspend fun steerTurn(convId: String, turnId: String, content: String, clientMessageId: String): SteerTurnResult? = null
    suspend fun interruptTurn(convId: String, turnId: String): Boolean = false
}

class ChatLiveRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) : LiveStreamSource {
    private val messageFetchGate = MessageFetchGate()
    private val activeStreams = ConcurrentHashMap<String, okhttp3.Call>()

    /**
     * The phone is the observe/confirm/handoff surface.  It requests automatic
     * routing and never claims browser, shell or desktop authority by itself.
     * The server still compiles the authoritative operating contract.
     */
    private fun mobileWorkMethod(): JSONObject = JSONObject()
        .put("retrieval", "auto")
        .put("effort", "standard")
        .put("run_mode", "auto")

    private val http = SharedHttp.base.newBuilder()
        .callTimeout(6, TimeUnit.SECONDS)
        .build()

    // 发消息要等 Agent 跑完（RAG + 工具），给足超时
    private val chatHttp = SharedHttp.base.newBuilder()
        .callTimeout(180, TimeUnit.SECONDS)
        .build()

    // 流式：不设整体 callTimeout（回答可能较长），只限单次读取间隔
    private val streamHttp = SharedHttp.base.newBuilder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    /** 规整 base：IP 一律 http（无有效证书），域名默认 https。 */
    private fun normBase(raw: String): String {
        val b = raw.trim().trimEnd('/')
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

    /** App 内置查看页 URL：{base}/api/conversations/{conv}/files/{name}/view?token=（用 App 自己的新 token，
     *  不依赖卡片里桌面端可能已过期的 token）。conv 从下载 URL 里解析。失败返回 null。 */
    suspend fun fileViewUrl(downloadUrl: String, filename: String): String? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        val encToken = java.net.URLEncoder.encode(token, "UTF-8")
        val u = downloadUrl.trim()

        // 1) 会话级文件：/api/conversations/{conv}/files/{name} → 会话内查看端点（Word/Excel/PPT 分页预览）
        if (u.contains("/conversations/")) {
            val conv = u.substringAfter("/conversations/").substringBefore("/")
            if (conv.isNotBlank()) {
                val encName = java.net.URLEncoder.encode(filename, "UTF-8").replace("+", "%20")
                return@withContext "$base/api/conversations/$conv/files/$encName/view?token=$encToken"
            }
        }

        // 2) 全局生成文件（PPT/Word/Excel 等由文档生成器产出）：download_url 形如
        //    "/api/files/ppt_xxxx.pptx"——相对路径、既无后端 host 也无 token，App 直接打不开，
        //    这正是"做了 PPT 但文件给不出来"的根因。补全 host + token：能内联预览的类型
        //    走 /preview（渲染 HTML），其余（pptx/xlsx/pdf）走原始下载端点，由 WebView 的
        //    下载监听落到手机下载目录。
        val globalName = when {
            u.contains("/api/files/") ->
                u.substringAfter("/api/files/").substringBefore("?").substringBefore("/")
            u.isNotBlank() && !u.contains("/") && !u.startsWith("http") -> u   // 裸文件名兜底
            else -> ""
        }
        if (globalName.isNotBlank()) {
            val encName = java.net.URLEncoder.encode(globalName, "UTF-8").replace("+", "%20")
            val ext = globalName.substringAfterLast('.', "").lowercase()
            val htmlPreviewable = setOf("docx", "txt", "md", "csv", "json", "html", "htm", "py", "yaml", "yml", "log")
            return@withContext if (ext in htmlPreviewable)
                "$base/api/files/$encName/preview?token=$encToken"
            else
                "$base/api/files/$encName?token=$encToken"
        }

        // 3) 已是绝对 URL：仅当它与【已配置的可信后端同源】时才追加登录 token；
        //    指向任意外部域名的 URL【绝不】附带 token（否则服务端一旦返回攻击者控制的下载
        //    地址，App 就会把用户凭证主动发过去——这是明确的凭证泄露）。
        if (u.startsWith("http")) {
            // 用共享的 UrlSecurity 做同源判定（单一事实源，可 JVM 单测）
            return@withContext com.hashmm.app.data.settings.UrlSecurity
                .appendTokenIfSameOrigin(u, base, encToken)
        }
        null
    }

    suspend fun getMessages(convId: String): List<ChatMessage>? =
        messageFetchGate.fetch(convId) { loadMessages(convId) }

    fun invalidateMessages(convId: String) {
        messageFetchGate.invalidate(convId)
    }

    private suspend fun loadMessages(convId: String): List<ChatMessage>? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            // The server returns the newest page in chronological order. Ask
            // for a substantial page so reopening a long desktop thread on the
            // phone does not look like the latest turns disappeared.
            val url = "$base/api/conversations/$convId/messages?limit=500"
            val req = Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val body = resp.body?.string() ?: return@withContext null
                val arr = JSONObject(body).optJSONArray("messages") ?: return@withContext emptyList()
                val out = mutableListOf<ChatMessage>()
                for (i in 0 until arr.length()) {
                    val m = arr.getJSONObject(i)
                    out.add(
                        ChatMessage(
                            id = m.optString("id"),
                            convId = convId,
                            role = m.optString("role", "user"),
                            content = m.optString("content", ""),
                            thinking = m.optString("thinking", ""),
                            toolCalls = jsonElOrNull(m.opt("tool_calls")?.toString()),
                            files = jsonElOrNull(m.optJSONArray("files")?.toString()),
                            sources = jsonElOrNull(m.optJSONArray("sources")?.toString()),
                            groundings = jsonElOrNull(m.opt("groundings")?.toString()),
                            runManifest = jsonElOrNull(m.opt("run_manifest")?.toString()),
                            suggestions = jsonElOrNull(m.opt("suggestions")?.toString()),
                            status = m.optString("status", "complete"),
                            feedback = m.optString("feedback", ""),
                            tokensIn = m.optInt("tokens_in", 0),
                            tokensOut = m.optInt("tokens_out", 0),
                            createdAt = m.optString("created_at", ""),
                            updatedAt = m.optString("updated_at", ""),
                        )
                    )
                }
                // The client endpoint is backed by SQLite and returns rows in
                // canonical chronological order. Keep that contract explicit
                // even when a deployment changes its SQL ordering.
                com.hashmm.app.ui.chat.ChatMessageOps.sortChronological(out)
            }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 原生发消息：POST {base}/api/chat，session_id=convId，让后端 Agent 跑完并把消息写进该会话。
     * 返回 assistant 的答复文本；失败返回 null。
     */
    /** 往对话追加一条消息但不触发 Agent（V202 澄清气泡）。role = user | assistant。成功返回 true。 */
    private suspend fun appendMessage(convId: String, role: String, content: String): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || content.isBlank()) return@withContext false
        try {
            val path = if (role == "assistant") "assistant-message" else "user-message"
            val body = JSONObject().put("content", content).toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/$path")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            val ok = http.newCall(req).execute().use { it.isSuccessful }
            if (ok) invalidateMessages(convId)
            ok
        } catch (_: Exception) {
            false
        }
    }

    /** 追加一条用户消息但不触发 Agent（电脑任务气泡持久化，桌面端历史完整）。 */
    suspend fun postUserMessage(convId: String, content: String): Boolean =
        appendMessage(convId, "user", content)

    /**
     * 澄清交换（阶段 D 体验升级）：用户原话 + 澄清问题都作为真实消息持久化——
     * 气泡出现在对话里（跨端同步、历史完整），而非一闪而过的 Toast。两条都成功才算成功。
     */
    suspend fun postClarifyExchange(convId: String, utterance: String, question: String): Boolean =
        appendMessage(convId, "user", utterance) && appendMessage(convId, "assistant", question)

    suspend fun sendMessage(convId: String, message: String): String? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            val payload = JSONObject()
                .put("message", message)
                .put("session_id", convId)
                .put("work_method", mobileWorkMethod())
            val body = payload.toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/chat")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            val answer = chatHttp.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val s = resp.body?.string() ?: return@withContext null
                JSONObject(s).optString("answer", "")
            }
            invalidateMessages(convId)
            answer
        } catch (e: Exception) {
            null
        }
    }

    /** Record a human decision for one exact Agent tool invocation.
     * The endpoint never executes the tool; a later guarded Chat turn consumes
     * the approval once. Returns the authoritative status, or null on failure. */
    suspend fun decideToolApproval(convId: String, requestId: String, approve: Boolean): String? =
        withContext(Dispatchers.IO) {
            val base = normBase(settings.clientUrl.first())
            val token = auth.currentToken()
            if (base.isBlank() || token.isNullOrBlank() || convId.isBlank() || requestId.isBlank()) {
                return@withContext null
            }
            try {
                val payload = JSONObject().put("decision", if (approve) "approve" else "decline")
                val body = payload.toString()
                    .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
                val req = Request.Builder()
                    .url("$base/api/conversations/$convId/tool-approvals/$requestId")
                    .header("Authorization", "Bearer $token")
                    .post(body)
                    .build()
                val status = http.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) return@withContext null
                    val json = JSONObject(resp.body?.string().orEmpty())
                    json.optJSONObject("approval_request")?.optString("status")
                }
                invalidateMessages(convId)
                status
            } catch (_: Exception) {
                null
            }
        }

    /** Structured EDD feedback. Query, answer and trajectory are captured by
     * the backend from the authoritative message; the App sends no answer text. */
    suspend fun submitMessageFeedback(
        convId: String,
        messageId: String,
        rating: String,
        reasonCode: String = "",
        comment: String = "",
    ): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || convId.isBlank() || messageId.isBlank()) {
            return@withContext false
        }
        try {
            val payload = JSONObject()
                .put("rating", rating)
                .put("reason_code", reasonCode)
                .put("comment", comment.take(1000))
            val body = payload.toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/messages/$messageId/review")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            val ok = http.newCall(req).execute().use { it.isSuccessful }
            if (ok) invalidateMessages(convId)
            ok
        } catch (_: Exception) {
            false
        }
    }

    /**
     * 流式发消息：POST {base}/api/chat/stream，按 SSE 逐行读，`token` 事件把 content 回调出去。
     * 返回是否成功（连上并读到流）。onToken 在 IO 线程调用，调用方需自行切回主线程更新 UI（StateFlow 线程安全）。
     */
    override suspend fun streamMessage(convId: String, message: String, onToken: (String) -> Unit): Boolean =
        streamMessageWithEvents(convId, message, onToken) { }

    override suspend fun streamMessageWithEvents(
        convId: String,
        message: String,
        onToken: (String) -> Unit,
        onEvent: (ChatStreamEvent) -> Unit,
    ): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        val success = try {
            // V360: App and desktop use the same conversation AgentLoop. The
            // former /api/chat/stream path bypassed planning, tool timelines,
            // active-turn steering and durable long-task state.
            val payload = JSONObject()
                .put("message", message)
                .put("work_method", mobileWorkMethod())
            val body = payload.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/stream")
                .header("Authorization", "Bearer $token")
                .header("Accept", "text/event-stream")
                .post(body)
                .build()
            val call = streamHttp.newCall(req)
            activeStreams.put(convId, call)?.cancel()
            try { call.execute().use { resp ->
                if (!resp.isSuccessful) return@withContext false
                val source = resp.body?.source() ?: return@withContext false
                var curEvent = ""
                var got = false            // 是否收到过非空 token
                var done = false           // 是否收到明确的完成事件
                var errored = false        // 是否收到错误事件
                while (!source.exhausted()) {
                    val line = source.readUtf8Line() ?: break
                    when {
                        line.startsWith("event:") -> curEvent = line.substringAfter("event:").trim()
                        line.startsWith("data:") -> {
                            val data = line.substringAfter("data:").trim()
                            if (curEvent in setOf(
                                    "turn_started", "turn_state", "steer_applied", "plan", "task_contract",
                                    "todo", "progress", "step_start", "step_done", "done"
                                )) {
                                onEvent(ChatStreamEvent(curEvent, data))
                            }
                            when (curEvent) {
                                "token" -> if (data.isNotEmpty()) {
                                    try {
                                        val c = JSONObject(data).optString("content", "")
                                        if (c.isNotEmpty()) { onToken(c); got = true }
                                    } catch (_: Exception) {}
                                }
                                "done", "end", "complete" -> done = true
                                "error" -> errored = true
                            }
                        }
                        line.isEmpty() -> curEvent = ""
                    }
                }
                // V308 修恒真 bug：原为 `got || true`（永远 true）——只要 HTTP 连接建立，
                // 即便服务端没返回任何 token、没有完成事件、流被异常截断，也判"成功"，
                // 导致空白助手消息 + 不触发兜底模型 + UI 显示已完成。现在按真实语义判定：
                // 收到过内容，或收到明确 done 事件，且未收到 error，才算成功；否则失败（让上层走兜底）。
                (got || done) && !errored
            } } finally { activeStreams.remove(convId, call) }
        } catch (e: Exception) {
            false
        }
        if (success) invalidateMessages(convId)
        success
    }

    override fun cancelStream(convId: String) {
        activeStreams.remove(convId)?.cancel()
    }

    override suspend fun activeTurn(convId: String): ActiveTurnState? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || convId.isBlank()) return@withContext null
        try {
            val req = Request.Builder().url("$base/api/conversations/$convId/active-turn")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val root = JSONObject(resp.body?.string().orEmpty())
                val turn = root.optJSONObject("turn") ?: return@withContext null
                ActiveTurnState(
                    turnId = turn.optString("turn_id"),
                    conversationId = turn.optString("conversation_id", convId),
                    steerable = turn.optBoolean("steerable", false),
                    status = turn.optString("status", "running"),
                    mode = turn.optString("mode", "initializing"),
                )
            }
        } catch (_: Exception) { null }
    }

    override suspend fun steerTurn(
        convId: String, turnId: String, content: String, clientMessageId: String,
    ): SteerTurnResult? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || content.isBlank()) return@withContext null
        try {
            val payload = JSONObject().put("content", content).put("client_message_id", clientMessageId)
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/turns/$turnId/steer")
                .header("Authorization", "Bearer $token")
                .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val json = JSONObject(resp.body?.string().orEmpty())
                SteerTurnResult(
                    accepted = json.optBoolean("accepted", false),
                    duplicate = json.optBoolean("duplicate", false),
                    messageId = json.optString("message_id"),
                )
            }
        } catch (_: Exception) { null }
    }

    override suspend fun interruptTurn(convId: String, turnId: String): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        try {
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/turns/$turnId/interrupt")
                .header("Authorization", "Bearer $token")
                .post("{}".toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
                .build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (_: Exception) { false }
    }

    // ───── 会话管理：重命名 / 置顶 / 删除（走后端 PATCH/DELETE，后端再同步 Supabase）─────
    private suspend fun patchConv(convId: String, body: JSONObject): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || convId.isBlank()) return@withContext false
        try {
            val req = Request.Builder()
                .url("$base/api/conversations/$convId")
                .header("Authorization", "Bearer $token")
                .patch(body.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
                .build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (_: Exception) { false }
    }

    suspend fun renameConversation(convId: String, title: String): Boolean =
        patchConv(convId, JSONObject().put("title", title))

    suspend fun setPinned(convId: String, pinned: Boolean): Boolean =
        patchConv(convId, JSONObject().put("pinned", pinned))

    suspend fun deleteConversation(convId: String): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || convId.isBlank()) return@withContext false
        try {
            val req = Request.Builder()
                .url("$base/api/conversations/$convId")
                .header("Authorization", "Bearer $token")
                .delete()
                .build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (_: Exception) { false }
    }

    /** 读取本地隐私模式状态：返回 (on, llmLocal, localModelAvailable)。失败返回 null。 */
    suspend fun getPrivacyMode(): Triple<Boolean, Boolean, Boolean>? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            val req = Request.Builder().url("$base/api/privacy-mode")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val o = JSONObject(resp.body?.string() ?: return@withContext null)
                Triple(o.optBoolean("on", false), o.optBoolean("llm_local", false), o.optBoolean("local_model_available", false))
            }
        } catch (_: Exception) { null }
    }

    /** 设置本地隐私模式。返回 (on, llmLocal, localModelAvailable)。 */
    suspend fun setPrivacyMode(on: Boolean): Triple<Boolean, Boolean, Boolean>? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            val body = JSONObject().put("on", on).toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder().url("$base/api/privacy-mode")
                .header("Authorization", "Bearer $token").post(body).build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val o = JSONObject(resp.body?.string() ?: return@withContext null)
                Triple(o.optBoolean("on", false), o.optBoolean("llm_local", false), o.optBoolean("local_model_available", false))
            }
        } catch (_: Exception) { null }
    }
}

// 把 JSON 字符串（如 files/sources 数组）解析成 JsonElement?；空或非法都返回 null
private fun jsonElOrNull(s: String?): JsonElement? = try {
    if (s.isNullOrBlank()) null else Json.parseToJsonElement(s)
} catch (_: Exception) { null }
