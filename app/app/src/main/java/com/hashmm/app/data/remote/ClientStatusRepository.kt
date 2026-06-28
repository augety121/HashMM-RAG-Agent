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

/**
 * 客户端后端可达性 + 在线被控端数。
 * online = 后端 GET {base}/api/remote/status 返回 2xx（即后端可联网访问到）；
 * hosts = 同账号在线的被控端数量（用于远程控制）。
 * 关键：IP 地址强制 http://（无有效证书），与其它 repo 一致，避免 SSL 直接失败误判离线。
 */
@Singleton
class ClientStatusRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    data class Status(val online: Boolean = false, val hosts: Int = 0)

    private val http = OkHttpClient.Builder().callTimeout(8, TimeUnit.SECONDS).build()

    private fun normalize(raw: String): String {
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

    suspend fun fetch(): Status = withContext(Dispatchers.IO) {
        val base = normalize(settings.clientUrl.first())
        val token = auth.currentToken()
        if (base.isBlank() || token.isNullOrBlank()) return@withContext Status()
        try {
            val req = Request.Builder()
                .url("$base/api/remote/status")
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                // 2xx 即视为后端可达（在线）。被控端数另算。
                if (!resp.isSuccessful) return@withContext Status(online = false)
                val body = resp.body?.string().orEmpty()
                val hosts = runCatching { JSONObject(body).optInt("count", JSONObject(body).optInt("hosts", 0)) }.getOrDefault(0)
                Status(online = true, hosts = hosts)
            }
        } catch (e: Exception) {
            Status(online = false)
        }
    }
}
