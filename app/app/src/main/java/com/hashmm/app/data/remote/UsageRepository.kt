package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class UsageModel(val model: String, val requests: Int, val tokens: Long, val cost: Double)
data class UsageMember(val username: String, val requests: Int, val tokens: Long, val cost: Double)

data class UsageStat(
    val requests: Int,
    val tokens: Long,
    val cost: Double,
    val tokensIn: Long = 0,
    val tokensOut: Long = 0,
    val currency: String = "CNY",
    val scope: String = "personal",
    val byModel: List<UsageModel> = emptyList(),
    val byMember: List<UsageMember> = emptyList(),
    val error: String? = null,
)

@Serializable
private data class UsageRowWire(
    val model: String = "",
    val username: String = "",
    val requests: Int = 0,
    val tokens: Long = 0,
    val cost: Double = 0.0,
)

@Serializable
private data class UsageOverviewWire(
    val contract: String = "",
    val scope: String = "personal",
    val requests: Int = 0,
    val tokens: Long = 0,
    val tokens_in: Long = 0,
    val tokens_out: Long = 0,
    val cost: Double = 0.0,
    val currency: String = "CNY",
    val by_model: List<UsageRowWire> = emptyList(),
    val by_user: List<UsageRowWire> = emptyList(),
)

private val usageWireJson = Json { ignoreUnknownKeys = true }

internal fun parseUsageOverview(body: String): UsageStat {
    val o = usageWireJson.decodeFromString<UsageOverviewWire>(body)
    require(o.contract == "hashmm.usage-overview.v1") { "不支持的用量响应" }
    val models = o.by_model.map { m ->
        UsageModel(m.model.ifBlank { "未标注模型" }, m.requests, m.tokens, m.cost)
    }
    val members = o.by_user.map { m ->
        UsageMember(m.username.ifBlank { "未标注成员" }, m.requests, m.tokens, m.cost)
    }
    return UsageStat(
        requests = o.requests,
        tokens = o.tokens,
        cost = o.cost,
        tokensIn = o.tokens_in,
        tokensOut = o.tokens_out,
        currency = o.currency,
        scope = o.scope,
        byModel = models,
        byMember = members,
    )
}

@Singleton
class UsageRepository @Inject constructor(
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
        return when {
            isIp -> "http://$schemeless"
            b.startsWith("http") -> b
            else -> "https://$b"
        }
    }

    suspend fun usage(days: Int): UsageStat = withContext(Dispatchers.IO) {
        val base = base()
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) {
            return@withContext UsageStat(0, 0, 0.0, error = "尚未连接 HashMM 服务或登录已失效")
        }
        try {
            val req = Request.Builder().url("$base/api/admin/usage/overview?days=$days")
                .header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 401) return@withContext UsageStat(0, 0, 0.0, error = "登录已失效，请重新登录")
                if (resp.code == 404) return@withContext UsageStat(0, 0, 0.0, error = "服务端版本过旧，请先更新并重启服务")
                if (!resp.isSuccessful) return@withContext UsageStat(0, 0, 0.0, error = "使用概览加载失败（${resp.code}）")
                val body = resp.body?.string() ?: return@withContext UsageStat(0, 0, 0.0, error = "服务返回了空响应")
                runCatching { parseUsageOverview(body) }.getOrElse {
                    UsageStat(0, 0, 0.0, error = "服务返回的数据格式不兼容，请更新两端版本")
                }
            }
        } catch (_: Exception) {
            UsageStat(0, 0, 0.0, error = "暂时无法连接服务，请稍后重试")
        }
    }
}
