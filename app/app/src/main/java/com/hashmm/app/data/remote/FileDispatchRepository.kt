package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class DesktopDispatchResult(
    val ok: Boolean,
    val message: String,
    val requestId: String = "",
    val status: String = "",
)

data class DesktopRequestState(
    val id: String,
    val query: String,
    val status: String,
    val createdAt: Long,
    val updatedAt: Long,
)

/**
 * App「从电脑取文件」：往后端写一条 file_requests(target=desktop)，桌面客户端常驻轮询器接走，
 * 在 桌面/下载/文档 里按文件名匹配并把文件上传回本会话（像普通文件卡片一样出现在对话里）。
 * 复用桌面已有投送链路，零新增后端逻辑。失败返回 (false, 原因)，绝不抛错（锦上添花，不影响 App 其它部分）。
 */
@Singleton
class FileDispatchRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS)
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

    /** 返回 (ok, 给用户看的提示)。 */
    suspend fun requestFile(convId: String, query: String): DesktopDispatchResult = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext DesktopDispatchResult(false, "未连接电脑端后端或未登录")
        if (convId.isBlank() || query.isBlank()) return@withContext DesktopDispatchResult(false, "缺少会话或文件名")
        try {
            val payload = JSONObject().put("query", query).toString()
            val body = payload.toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/request-file")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            http.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) {
                    val data = runCatching { JSONObject(resp.body?.string().orEmpty()) }.getOrNull()
                    DesktopDispatchResult(
                        true,
                        "任务已进入电脑队列，文件找到后会回到当前对话",
                        data?.optString("request_id").orEmpty(),
                        data?.optString("status", "pending").orEmpty(),
                    )
                } else {
                    DesktopDispatchResult(false, "下发失败（${resp.code}）")
                }
            }
        } catch (_: Exception) {
            DesktopDispatchResult(false, "下发失败：电脑端后端未连接或超时")
        }
    }

    /** 让电脑执行一条任务。公开类型使用 browser_use / computer_use；服务端兼容旧 runner 前缀。 */
    suspend fun requestComputerTask(convId: String, task: String, kind: String = "computer_use"): DesktopDispatchResult = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext DesktopDispatchResult(false, "未连接电脑端后端或未登录")
        if (convId.isBlank() || task.isBlank()) return@withContext DesktopDispatchResult(false, "缺少任务")
        try {
            val payload = JSONObject().put("task", task).put("kind", kind).toString()
            val body = payload.toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/computer-task")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            http.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) {
                    val data = runCatching { JSONObject(resp.body?.string().orEmpty()) }.getOrNull()
                    DesktopDispatchResult(
                        true,
                        "任务已进入电脑队列，进度和结果会回到当前对话",
                        data?.optString("request_id").orEmpty(),
                        data?.optString("status", "pending").orEmpty(),
                    )
                } else DesktopDispatchResult(false, "下发失败（${resp.code}）")
            }
        } catch (_: Exception) {
            DesktopDispatchResult(false, "下发失败：电脑端后端未连接或超时")
        }
    }

    suspend fun requests(convId: String, limit: Int = 20): List<DesktopRequestState> = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || convId.isBlank()) return@withContext emptyList()
        try {
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/computer-requests?limit=${limit.coerceIn(1, 100)}")
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@use emptyList()
                val arr = JSONObject(resp.body?.string().orEmpty()).optJSONArray("requests")
                    ?: return@use emptyList()
                (0 until arr.length()).mapNotNull { index ->
                    val item = arr.optJSONObject(index) ?: return@mapNotNull null
                    val id = item.optString("id")
                    if (id.isBlank()) return@mapNotNull null
                    DesktopRequestState(
                        id = id,
                        query = item.optString("query"),
                        status = item.optString("status", "pending"),
                        createdAt = item.optLong("created_at", 0L),
                        updatedAt = item.optLong("updated_at", 0L),
                    )
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** 语音编排决策结果（路线图阶段 D）。action = local | dispatch | clarify。 */
    data class Orchestration(val action: String, val dispatchKind: String, val question: String)

    /**
     * 语音任务编排（阶段 D）：把语音转写文本交给后端意图层，拿到「本地直答 / 派桌面端 / 先澄清」决策。
     * 网络失败返回 null —— 调用方回退普通发送，语音功能绝不因编排层故障而失效。
     */
    suspend fun orchestrate(text: String, convId: String): Orchestration? = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank() || text.isBlank()) return@withContext null
        try {
            val payload = JSONObject()
                .put("text", text)
                .put("has_desktop", true)   // App 走桌面通道下发，离线时任务在通道里等桌面上线（既有语义）
                .put("conv_id", convId)
                .toString()
            val body = payload.toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/voice/orchestrate")
                .header("Authorization", "Bearer $token")
                .post(body)
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@use null
                val j = JSONObject(resp.body?.string() ?: return@use null)
                Orchestration(
                    action = j.optString("action", "local"),
                    dispatchKind = j.optString("dispatch_kind", ""),
                    question = j.optString("clarifying_question", ""),
                )
            }
        } catch (_: Exception) {
            null
        }
    }
}
