package com.hashmm.app.data.remote

import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/** Deterministic fallback compaction for phone-direct mode (no extra LLM call). */
internal object DirectContextWindow {
    private const val RecentMessages = 10
    private const val CheckpointChars = 3_500
    private const val RecentMessageChars = 4_000

    fun compact(history: List<Pair<String, String>>): List<Pair<String, String>> {
        val clean = history.filter { it.second.isNotBlank() }
        if (clean.size <= RecentMessages + 2) return clean
        val recent = clean.takeLast(RecentMessages)
        val older = clean.dropLast(RecentMessages)
        val firstGoal = clean.firstOrNull { it.first == "user" }?.second.orEmpty()
        val latestRequirements = older.asSequence()
            .filter { it.first == "user" }
            .map { it.second.replace(Regex("\\s+"), " ").take(240) }
            .toList()
            .takeLast(6)
        val reportedProgress = older.asSequence()
            .filter { it.first == "assistant" }
            .map { it.second.replace(Regex("\\s+"), " ").take(240) }
            .toList()
            .takeLast(4)
        val checkpoint = buildString {
            append("[会话压缩检查点；完整原文仍保留在本机/云端]\n")
            if (firstGoal.isNotBlank()) append("原始目标：").append(firstGoal.take(600)).append('\n')
            if (latestRequirements.isNotEmpty()) {
                append("阶段要求：\n")
                latestRequirements.forEach { append("- ").append(it).append('\n') }
            }
            if (reportedProgress.isNotEmpty()) {
                append("已报告进展（仍需核验）：\n")
                reportedProgress.forEach { append("- ").append(it).append('\n') }
            }
        }.take(CheckpointChars)
        return listOf("system" to checkpoint) + recent.map { it.first to it.second.take(RecentMessageChars) }
    }
}

/**
 * 直连模型（V249 离线兜底，Marvis 式独立 agent）：
 * 桌面端/自建后端不在线时，手机直接调 **OpenAI 兼容** /chat/completions（stream=true）
 * 继续问答——DeepSeek / Moonshot / 通义 / OpenAI / 任何兼容端点皆可。
 * Key 只存本机 DataStore、只发给你自己填的端点；本仓库不经手任何第三方中转。
 */
@Singleton
class DirectLlmRepository @Inject constructor(
    private val settings: SettingsStore,
) {
    // 流式：整体不设上限，只限单次读取间隔
    private val http = SharedHttp.base.newBuilder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()
    private val activeCalls = ConcurrentHashMap<String, okhttp3.Call>()

    data class Cfg(val baseUrl: String, val model: String, val apiKey: String) {
        val ok: Boolean get() = baseUrl.isNotBlank() && model.isNotBlank() && apiKey.isNotBlank()
    }

    suspend fun cfg(): Cfg = Cfg(
        baseUrl = settings.directBaseUrl.first().trim().trimEnd('/'),
        model = settings.directModel.first().trim(),
        apiKey = settings.directApiKey.first().trim(),
    )

    /** 是否已配置齐（兜底可用）。 */
    suspend fun configured(): Boolean = cfg().ok

    /**
     * 流式对话。history 为 (role, content) 列表（含本条 user）。
     * 返回错误信息：空 = 成功走完流。onToken 在 IO 线程回调。
     */
    suspend fun streamChat(
        history: List<Pair<String, String>>,
        requestKey: String = "",
        onToken: (String) -> Unit,
    ): String =
        withContext(Dispatchers.IO) {
            val c = cfg()
            if (!c.ok) return@withContext "直连模型未配置"
            try {
                val msgs = JSONArray()
                // 轻系统提示：保持与后端 agent 一致的中文习惯
                msgs.put(JSONObject().put("role", "system")
                    .put("content", "你是 HashMM 的手机端直连助手。用中文、简洁直接地回答；这是离线兜底模式，无法使用知识库与工具。"))
                DirectContextWindow.compact(history).forEach { (role, content) ->
                    if (content.isNotBlank()) msgs.put(JSONObject().put("role", role).put("content", content))
                }
                val payload = JSONObject()
                    .put("model", c.model)
                    .put("messages", msgs)
                    .put("stream", true)
                    .put("temperature", 0.5)
                val req = Request.Builder()
                    .url(c.baseUrl + "/chat/completions")
                    .header("Authorization", "Bearer ${c.apiKey}")
                    .header("Accept", "text/event-stream")
                    .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
                    .build()
                val call = http.newCall(req)
                if (requestKey.isNotBlank()) activeCalls.put(requestKey, call)?.cancel()
                try { call.execute().use { resp ->
                    if (!resp.isSuccessful) {
                        val hint = when (resp.code) {
                            401, 403 -> "API Key 无效"
                            404 -> "端点不对（需 OpenAI 兼容 /v1）"
                            else -> "HTTP ${resp.code}"
                        }
                        return@withContext "直连失败：$hint"
                    }
                    val source = resp.body?.source() ?: return@withContext "直连失败：空响应"
                    var got = false
                    while (!source.exhausted()) {
                        val line = source.readUtf8Line() ?: break
                        if (!line.startsWith("data:")) continue
                        val data = line.substringAfter("data:").trim()
                        if (data == "[DONE]") break
                        try {
                            val delta = JSONObject(data).optJSONArray("choices")
                                ?.optJSONObject(0)?.optJSONObject("delta")
                                ?.optString("content", "") ?: ""
                            if (delta.isNotEmpty()) { onToken(delta); got = true }
                        } catch (_: Exception) { /* 心跳/非 JSON 行忽略 */ }
                    }
                    if (got) "" else "直连失败：模型没有返回内容"
                } } finally {
                    if (requestKey.isNotBlank()) activeCalls.remove(requestKey, call)
                }
            } catch (e: Exception) {
                "直连失败：${e.message ?: "网络错误"}"
            }
        }

    /** Stop the actual direct-provider transport, not only its UI collector. */
    fun cancel(requestKey: String) {
        if (requestKey.isNotBlank()) activeCalls.remove(requestKey)?.cancel()
    }

    /** 连通性测试：让模型回一个 OK。返回 (成功, 消息)。 */
    suspend fun test(): Pair<Boolean, String> {
        val sb = StringBuilder()
        val err = streamChat(listOf("user" to "只回复两个字母：OK")) { sb.append(it) }
        return if (err.isBlank()) true to "连接成功，模型回复：${sb.toString().trim().take(30)}"
        else false to err
    }
}
