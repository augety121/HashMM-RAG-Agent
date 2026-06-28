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
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class ModelInfo(
    val id: String,
    val name: String,
    val provider: String,
    val modelName: String,
    val isDefault: Boolean,
)

/** 新增/测试模型用的配置体。 */
data class ModelCreate(
    val name: String,
    val provider: String,
    val baseUrl: String,
    val apiKey: String,
    val modelName: String,
    val temperature: Double = 0.1,
    val maxTokens: Int = 4096,
)

/** 测试连接结果。 */
data class TestResult(val ok: Boolean, val message: String, val latencyMs: Int)

/**
 * 模型配置：直连客户端后端管理 LLM（管理员）。
 * GET {base}/api/admin/models 列表；POST {base}/api/admin/models/{id}/default 设默认。
 */
@Singleton
class ModelRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder().callTimeout(12, TimeUnit.SECONDS).build()
    // 测试连接会真的发一次 LLM 请求，给足超时
    private val testHttp = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .build()

    private fun ModelCreate.toJson(): JSONObject = JSONObject().apply {
        put("name", name)
        put("provider", provider)
        put("base_url", baseUrl)
        put("api_key", apiKey)
        put("model_name", modelName)
        put("temperature", temperature)
        put("max_tokens", maxTokens)
    }

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

    /** 返回 (模型列表, 错误信息)；列表为空 + 错误非空表示失败。 */
    suspend fun listModels(): Pair<List<ModelInfo>, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList<ModelInfo>() to "未连客户端后端或未登录"
        try {
            val req = Request.Builder().url("$base/api/admin/models").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext emptyList<ModelInfo>() to "需要管理员权限"
                if (!resp.isSuccessful) return@withContext emptyList<ModelInfo>() to "加载失败（${resp.code}）"
                val s = resp.body?.string() ?: return@withContext emptyList<ModelInfo>() to "空响应"
                val arr = JSONArray(s)
                val out = (0 until arr.length()).mapNotNull { i ->
                    val o = arr.optJSONObject(i) ?: return@mapNotNull null
                    ModelInfo(
                        id = o.optString("id"),
                        name = o.optString("name", o.optString("model_name", "模型")),
                        provider = o.optString("provider", ""),
                        modelName = o.optString("model_name", ""),
                        isDefault = o.optInt("is_default", 0) == 1,
                    )
                }
                out to null
            }
        } catch (e: Exception) {
            emptyList<ModelInfo>() to "网络错误"
        }
    }

    suspend fun setDefault(id: String): Boolean = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        try {
            val empty = "".toRequestBody("application/json".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/admin/models/$id/default")
                .header("Authorization", "Bearer $token")
                .post(empty)
                .build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }

    /** 测试一份模型配置能否连通。POST {base}/api/admin/models/test。 */
    suspend fun testModel(c: ModelCreate): TestResult = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext TestResult(false, "未连客户端后端或未登录", 0)
        try {
            val body = c.toJson().toString().toRequestBody("application/json".toMediaTypeOrNull())
            val req = Request.Builder().url("$base/api/admin/models/test").header("Authorization", "Bearer $token").post(body).build()
            testHttp.newCall(req).execute().use { resp ->
                val txt = resp.body?.string().orEmpty()
                if (resp.code == 403) return@withContext TestResult(false, "需要管理员权限", 0)
                if (!resp.isSuccessful) return@withContext TestResult(false, "测试失败（${resp.code}）", 0)
                val o = JSONObject(txt)
                TestResult(o.optBoolean("ok", false), o.optString("message", if (o.optBoolean("ok")) "连接成功" else "连接失败"), o.optInt("latency_ms", 0))
            }
        } catch (e: Exception) {
            TestResult(false, "测试出错：网络或超时", 0)
        }
    }

    /** 新增模型。POST {base}/api/admin/models。 */
    suspend fun createModel(c: ModelCreate): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false to "未连客户端后端或未登录"
        try {
            val body = c.toJson().toString().toRequestBody("application/json".toMediaTypeOrNull())
            val req = Request.Builder().url("$base/api/admin/models").header("Authorization", "Bearer $token").post(body).build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext false to "需要管理员权限"
                if (!resp.isSuccessful) return@withContext false to "新增失败（${resp.code}）"
                true to "已新增模型「${c.name}」"
            }
        } catch (e: Exception) {
            false to "新增出错：网络错误"
        }
    }

    /** 删除模型。DELETE {base}/api/admin/models/{id}。 */
    suspend fun deleteModel(id: String): Boolean = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        try {
            val req = Request.Builder().url("$base/api/admin/models/$id").header("Authorization", "Bearer $token").delete().build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }
}
