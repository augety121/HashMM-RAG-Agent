package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
    val wireApi: String = "chat_completions",
)

data class ModelProviderInfo(
    val id: String,
    val name: String,
    val baseUrl: String,
    val wireApis: List<String>,
    val defaultWireApi: String,
    val authOptional: Boolean,
    val local: Boolean,
    val endpointNote: String,
    val modelHints: List<String>,
)

internal fun parseModelProviders(payload: String): List<ModelProviderInfo> {
    val root = runCatching { Json.parseToJsonElement(payload.ifBlank { "{}" }).jsonObject }
        .getOrElse { return emptyList() }
    val array = root["providers"]?.let { runCatching { it.jsonArray }.getOrNull() }.orEmpty()
    return array.mapNotNull { element ->
        val item = runCatching { element.jsonObject }.getOrNull() ?: return@mapNotNull null
        fun text(key: String): String = item[key]?.jsonPrimitive?.contentOrNull.orEmpty()
        fun texts(key: String): List<String> = item[key]
            ?.let { runCatching { it.jsonArray }.getOrNull() }
            .orEmpty()
            .mapNotNull { it.jsonPrimitive.contentOrNull?.takeIf(String::isNotBlank) }
        val id = text("id")
        if (id.isBlank()) return@mapNotNull null
        ModelProviderInfo(
            id = id,
            name = text("name").ifBlank { id },
            baseUrl = text("base_url"),
            wireApis = texts("wire_apis"),
            defaultWireApi = text("default_wire_api").ifBlank { "chat_completions" },
            authOptional = text("auth") == "optional",
            local = item["local"]?.jsonPrimitive?.booleanOrNull ?: false,
            endpointNote = text("endpoint_note"),
            modelHints = texts("model_hints"),
        )
    }
}

internal fun parseMyModels(payload: String): List<ModelInfo> {
    val root = runCatching { Json.parseToJsonElement(payload.ifBlank { "{}" }) }
        .getOrElse { return emptyList() }
    val arr = when (root) {
        is kotlinx.serialization.json.JsonArray -> root
        is kotlinx.serialization.json.JsonObject -> root["models"]
            ?.let { runCatching { it.jsonArray }.getOrNull() }.orEmpty()
        else -> emptyList()
    }
    return arr.mapNotNull { element ->
        val o = runCatching { element.jsonObject }.getOrNull() ?: return@mapNotNull null
        fun text(key: String): String = o[key]?.jsonPrimitive?.contentOrNull.orEmpty()
        val configWire = runCatching {
            Json.parseToJsonElement(text("config_json").ifBlank { "{}" })
                .jsonObject["wire_api"]?.jsonPrimitive?.contentOrNull.orEmpty()
        }.getOrDefault("")
        ModelInfo(
            id = text("id"),
            name = text("name").ifBlank { text("model_name").ifBlank { "模型" } },
            provider = text("provider"),
            modelName = text("model_name"),
            isDefault = o["is_preferred"]?.jsonPrimitive?.booleanOrNull
                ?: (text("is_default") == "1"),
            wireApi = text("wire_api").ifBlank { configWire.ifBlank { "chat_completions" } },
        )
    }
}

/** 新增/测试模型用的配置体。 */
data class ModelCreate(
    val name: String,
    val provider: String,
    val baseUrl: String,
    val apiKey: String,
    val modelName: String,
    val temperature: Double = 0.1,
    val maxTokens: Int = 4096,
    val wireApi: String = "chat_completions",
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
    private val http = SharedHttp.base.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()
    // 测试连接会真的发一次 LLM 请求，给足超时
    private val testHttp = SharedHttp.base.newBuilder()
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
        put("wire_api", wireApi)
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

    /** 返回 (模型列表, 错误信息)；列表为空 + 错误非空表示失败。
     *  V306：GET 幂等 → 用 RetryPolicy 对 5xx/超时自动重试；错误文案统一走 HttpError。 */
    suspend fun listModels(): Pair<List<ModelInfo>, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList<ModelInfo>() to "未连客户端后端或未登录"
        val result = RetryPolicy.withRetry { _ ->
            try {
                val req = Request.Builder().url("$base/api/models/mine").header("Authorization", "Bearer $token").get().build()
                http.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        // 非 2xx → 交给 RetryableHttp，是否重试由 HttpError 分类决定（5xx/429 重试，4xx 立即止）
                        Result.failure(RetryPolicy.RetryableHttp(resp.code))
                    } else {
                        val s = resp.body?.string() ?: ""
                        Result.success(parseMyModels(s))
                    }
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }
        result.fold(
            onSuccess = { it to null },
            onFailure = { e ->
                val msg = when (e) {
                    is RetryPolicy.RetryableHttp -> if (e.code == 403) "需要管理员权限" else HttpError.fromCode(e.code).message
                    else -> HttpError.fromException(e).message
                }
                emptyList<ModelInfo>() to msg
            },
        )
    }

    /**
     * 厂商能力由后端运行时注册表提供。App 不维护第二套过期模型清单，
     * 只展示协议、端点规则和账号控制台里的精确模型 ID。
     */
    suspend fun listProviders(): Pair<List<ModelProviderInfo>, String?> = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext emptyList<ModelProviderInfo>() to "未连客户端后端或未登录"
        try {
            val request = Request.Builder().url("$base/api/models/providers")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(request).execute().use { response ->
                if (response.code == 403) return@withContext emptyList<ModelProviderInfo>() to "需要管理员权限"
                if (!response.isSuccessful) return@withContext emptyList<ModelProviderInfo>() to "厂商策略加载失败（${response.code}）"
                val result = parseModelProviders(response.body?.string().orEmpty())
                result to null
            }
        } catch (_: Exception) {
            emptyList<ModelProviderInfo>() to "厂商策略加载失败：网络错误"
        }
    }

    suspend fun setDefault(id: String): Boolean = withContext(Dispatchers.IO) {
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext false
        try {
            val body = JSONObject().put("model_id", id).toString()
                .toRequestBody("application/json".toMediaTypeOrNull())
            val req = Request.Builder()
                .url("$base/api/models/mine/prefer")
                .header("Authorization", "Bearer $token")
                .post(body)
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
            val req = Request.Builder().url("$base/api/models/test").header("Authorization", "Bearer $token").post(body).build()
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
            val req = Request.Builder().url("$base/api/models/mine").header("Authorization", "Bearer $token").post(body).build()
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
            val req = Request.Builder().url("$base/api/models/mine/$id").header("Authorization", "Bearer $token").delete().build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (e: Exception) { false }
    }
}
