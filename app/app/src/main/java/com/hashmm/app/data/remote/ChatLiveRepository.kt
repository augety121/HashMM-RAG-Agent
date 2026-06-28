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
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 直连桌面客户端拉某会话的消息（含正在流式生成的实时内容）。
 * 调 GET {clientUrl}/api/conversations/{id}/messages（带 Supabase token）。
 * 用于接管时的实时流式展示：客户端可达→拿到逐字增长的最新内容；不可达→返回 null，由上层回退到 Supabase 缓存。
 */
@Singleton
class ChatLiveRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder()
        .callTimeout(6, TimeUnit.SECONDS)
        .build()

    // 发消息要等 Agent 跑完（RAG + 工具），给足超时
    private val chatHttp = OkHttpClient.Builder()
        .callTimeout(180, TimeUnit.SECONDS)
        .build()

    // 流式：不设整体 callTimeout（回答可能较长），只限单次读取间隔
    private val streamHttp = OkHttpClient.Builder()
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

        // 3) 已是绝对 URL：本后端的补 token，否则原样返回
        if (u.startsWith("http")) {
            return@withContext if (u.contains("token="))
                u
            else
                u + (if (u.contains("?")) "&" else "?") + "token=$encToken"
        }
        null
    }

    suspend fun getMessages(convId: String): List<ChatMessage>? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            val url = "$base/api/conversations/$convId/messages"
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
                            files = jsonElOrNull(m.optJSONArray("files")?.toString()),
                            sources = jsonElOrNull(m.optJSONArray("sources")?.toString()),
                            status = m.optString("status", "complete"),
                            createdAt = "",
                        )
                    )
                }
                out
            }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 原生发消息：POST {base}/api/chat，session_id=convId，让后端 Agent 跑完并把消息写进该会话。
     * 返回 assistant 的答复文本；失败返回 null。
     */
    suspend fun sendMessage(convId: String, message: String): String? = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext null
        try {
            val payload = JSONObject()
                .put("message", message)
                .put("session_id", convId)
            val body = payload.toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/chat")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            chatHttp.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val s = resp.body?.string() ?: return@withContext null
                JSONObject(s).optString("answer", "")
            }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 流式发消息：POST {base}/api/chat/stream，按 SSE 逐行读，`token` 事件把 content 回调出去。
     * 返回是否成功（连上并读到流）。onToken 在 IO 线程调用，调用方需自行切回主线程更新 UI（StateFlow 线程安全）。
     */
    suspend fun streamMessage(convId: String, message: String, onToken: (String) -> Unit): Boolean = withContext(Dispatchers.IO) {
        val base = normBase(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        try {
            val payload = JSONObject().put("message", message).put("session_id", convId)
            val body = payload.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/chat/stream")
                .header("Authorization", "Bearer $token")
                .header("Accept", "text/event-stream")
                .post(body)
                .build()
            streamHttp.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext false
                val source = resp.body?.source() ?: return@withContext false
                var curEvent = ""
                var got = false
                while (!source.exhausted()) {
                    val line = source.readUtf8Line() ?: break
                    when {
                        line.startsWith("event:") -> curEvent = line.substringAfter("event:").trim()
                        line.startsWith("data:") -> {
                            val data = line.substringAfter("data:").trim()
                            if (curEvent == "token" && data.isNotEmpty()) {
                                try {
                                    val c = JSONObject(data).optString("content", "")
                                    if (c.isNotEmpty()) { onToken(c); got = true }
                                } catch (_: Exception) {}
                            }
                        }
                        line.isEmpty() -> curEvent = ""
                    }
                }
                got || true   // 即便没有 token 事件，也算连上了（可能直接 done）
            }
        } catch (e: Exception) {
            false
        }
    }
}

/** org.json 的数组字符串 → kotlinx JsonElement（供 ChatMessage.files/sources 用）。空/异常→null。 */
private fun jsonElOrNull(s: String?): JsonElement? =
    if (s.isNullOrBlank() || s == "null") null
    else try { Json.parseToJsonElement(s) } catch (_: Exception) { null }
