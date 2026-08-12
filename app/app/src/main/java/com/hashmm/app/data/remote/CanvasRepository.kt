package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 工作画布（V248 App 侧）：读/写会话产物 HTML——与桌面端画布同一后端链路。
 *   读：GET  {base}{downloadUrl}                     （downloadUrl 来自 feed/会话文件）
 *   写：PUT  {base}/api/conversations/{cid}/files/{fname}  body {content}
 * 与 FeedRepository 同一套 base()/token 范式。
 */
@Singleton
class CanvasRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = SharedHttp.base.newBuilder().callTimeout(20, TimeUnit.SECONDS).build()

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

    /** 读画布 HTML。返回 (html, baseUrl, error)：error 非空＝失败原因。 */
    suspend fun load(downloadUrl: String): Triple<String, String, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext Triple("", "", "未连客户端后端或未登录")
        try {
            val target = resolveCanvasUrl(base, downloadUrl)
            if (target.isBlank()) return@withContext Triple("", "", "画布地址无效")
            val req = Request.Builder().url(target)
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext Triple("", "", "读取失败（HTTP ${resp.code}）")
                Triple(resp.body?.string() ?: "", base, "")
            }
        } catch (e: Exception) { Triple("", "", "读取失败：${e.message ?: "网络错误"}") }
    }

    /** Feed/message payloads historically used relative URLs, while newer
     * deployments may return an absolute URL. Do not concatenate an absolute
     * URL onto the API base (which produces an invalid double-host address). */
    /** 保存画布 HTML（就地编辑落盘）。返回错误信息，空＝成功。 */
    suspend fun save(convId: String, filename: String, content: String): String = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext "未连客户端后端或未登录"
        try {
            val body = JSONObject().put("content", content).toString()
                .toRequestBody("application/json; charset=utf-8".toMediaType())
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/files/" + java.net.URLEncoder.encode(filename, "UTF-8"))
                .header("Authorization", "Bearer $token").put(body).build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext "保存失败（HTTP ${resp.code}）"
                ""
            }
        } catch (e: Exception) { "保存失败：${e.message ?: "网络错误"}" }
    }

    /**
     * Create and persist a blank canvas for the current conversation.
     * A conflict from the idempotent conversation create is safe to ignore;
     * the owner-checked file write is the authoritative operation.
     */
    suspend fun createBlank(
        convId: String,
        title: String,
        filename: String,
        content: String,
    ): Pair<String, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext "" to "Backend is not connected or the account is not signed in"
        try {
            val createBody = JSONObject().put("id", convId).put("title", title)
                .toString().toRequestBody("application/json; charset=utf-8".toMediaType())
            val createReq = Request.Builder().url("$base/api/conversations")
                .header("Authorization", "Bearer $token").post(createBody).build()
            http.newCall(createReq).execute().use { response ->
                if (!response.isSuccessful && response.code != 409) {
                    return@withContext "" to "Failed to create conversation (HTTP ${response.code})"
                }
            }

            val body = JSONObject().put("content", content)
                .toString().toRequestBody("application/json; charset=utf-8".toMediaType())
            val encoded = java.net.URLEncoder.encode(filename, "UTF-8")
            val req = Request.Builder()
                .url("$base/api/conversations/$convId/files/$encoded")
                .header("Authorization", "Bearer $token").put(body).build()
            http.newCall(req).execute().use { response ->
                val payload = response.body?.string().orEmpty()
                if (!response.isSuccessful) return@withContext "" to "Failed to create canvas (HTTP ${response.code})"
                val returned = runCatching { JSONObject(payload).optString("download_url") }.getOrDefault("")
                (returned.ifBlank { "/api/conversations/$convId/download/$encoded" }) to ""
            }
        } catch (e: Exception) {
            "" to "Failed to create canvas: ${e.message ?: "network error"}"
        }
    }
}

/** Canvas HTML receives a native save bridge, so an off-origin document must
 * never be treated as a trusted artifact even when the global HTTP interceptor
 * would strip its Authorization header. */
internal fun resolveCanvasUrl(base: String, raw: String): String {
    val cleanBase = base.trim().trimEnd('/')
    val value = raw.trim()
    if (cleanBase.isBlank() || value.isBlank()) return ""
    val target = when {
        value.startsWith("https://", ignoreCase = true) || value.startsWith("http://", ignoreCase = true) -> value
        value.startsWith("/") -> cleanBase + value
        else -> "$cleanBase/$value"
    }
    return target.takeIf {
        com.hashmm.app.data.settings.UrlSecurity.isSameOrigin(it, cleanBase)
    }.orEmpty()
}
