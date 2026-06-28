package com.hashmm.app.data.remote

import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

data class UsageStat(val requests: Int, val tokens: Long, val cost: Double, val error: String? = null)

/**
 * 个人用量：GET {base}/api/admin/usage/me?days=N → {requests, tokens, cost}。
 */
@Singleton
class UsageRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    private val http = OkHttpClient.Builder().callTimeout(12, TimeUnit.SECONDS).build()

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
        val base = base(); val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext UsageStat(0, 0, 0.0, "未连客户端后端或未登录")
        try {
            val req = Request.Builder().url("$base/api/admin/usage/me?days=$days").header("Authorization", "Bearer $token").get().build()
            http.newCall(req).execute().use { resp ->
                if (resp.code == 403) return@withContext UsageStat(0, 0, 0.0, "需要管理员权限")
                if (!resp.isSuccessful) return@withContext UsageStat(0, 0, 0.0, "加载失败（${resp.code}）")
                val o = JSONObject(resp.body?.string() ?: return@withContext UsageStat(0, 0, 0.0, "空响应"))
                UsageStat(
                    requests = o.optInt("requests", 0),
                    tokens = o.optLong("tokens", 0L),
                    cost = o.optDouble("cost", 0.0),
                )
            }
        } catch (e: Exception) {
            UsageStat(0, 0, 0.0, "网络错误")
        }
    }
}
