package com.hashmm.app.data.remote

import com.hashmm.app.BuildConfig
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.settings.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.URI
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 客户端后端可达性 + 在线被控端数。
 *
 * V309 修「同账号仍显示未连接」：
 *   旧实现把 online 绑定到 GET /api/remote/status 的返回，但那个端点的 online 字段语义是
 *   「当前账号有没有【被控端 host】在线」——而手机 App 自己不是 host（host 是桌面端通过
 *   WebSocket 注册的）。于是只要桌面端没开着注册成 host，哪怕后端完全正常、账号完全正确，
 *   手机也永远显示「未连接」。这与用户直觉（后端在跑=已连接）完全不符。
 *
 *   正确语义拆成两件事：
 *     · online（已连接）= 后端【可达】。用匿名可访问的 GET /api/health 判定（status 字段），
 *       不依赖被控端、不依赖 token 是否有权限，纯粹测「这个后端地址通不通」。
 *     · hosts（被控端数）= GET /api/remote/status 的 count，仅用于远程控制入口的展示，
 *       与「是否已连接」解耦。
 *
 * 关键：IP 地址强制 http://（无有效证书），与其它 repo 一致，避免 SSL 直接失败误判离线。
 */
@Singleton
class ClientStatusRepository @Inject constructor(
    private val settings: SettingsStore,
    private val auth: AuthRepository,
) {
    data class Status(
        val online: Boolean = false,
        val transportOnline: Boolean = false,
        val desktops: Int = 0,
        val remoteHosts: Int = 0,
        val failureClass: String? = null,
    )

    private val http = SharedHttp.base.newBuilder().callTimeout(8, TimeUnit.SECONDS).build()

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
        if (base.isBlank()) return@withContext Status()
        // ① 传输可达不等于账号已连接。livez 只证明隧道和进程可达。
        val transportOnline = try {
            val req = Request.Builder().url("$base/api/livez").get().build()
            http.newCall(req).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
        if (!transportOnline) return@withContext Status(failureClass = "network_error")

        // ② 带 Supabase access token 的 bootstrap 才证明 App 与该部署属于同一身份边界。
        var token = auth.currentToken()?.takeIf { it.isNotBlank() }
            ?: return@withContext Status(transportOnline = true, failureClass = "authentication_required")
        suspend fun fetchBootstrap(accessToken: String): Pair<Int, String> = try {
            val request = Request.Builder().url("$base/api/client/bootstrap")
                .header("Authorization", "Bearer $accessToken")
                .header("Accept", "application/json")
                .header("X-HashMM-Client", "android")
                .header("X-App-Version", BuildConfig.VERSION_NAME)
                .header("X-Request-ID", UUID.randomUUID().toString())
                .get().build()
            http.newCall(request).execute().use { it.code to it.body?.string().orEmpty() }
        } catch (_: Exception) { -1 to "" }
        var bootstrapResult = fetchBootstrap(token)
        if (bootstrapResult.first == 401) {
            token = auth.refreshAccessToken().orEmpty()
            if (token.isNotBlank()) bootstrapResult = fetchBootstrap(token)
        }
        if (bootstrapResult.first !in 200..299) {
            return@withContext Status(
                transportOnline = true,
                failureClass = if (bootstrapResult.first == 401) "authentication_expired" else "bootstrap_failed",
            )
        }
        val identity = parseBootstrapIdentity(bootstrapResult.second)
        val expectedProject = runCatching {
            URI(BuildConfig.SUPABASE_URL).host.orEmpty().lowercase().removeSuffix(".supabase.co")
        }.getOrDefault("")
        val expectedFingerprint = auth.currentUserId()?.let { raw ->
            MessageDigest.getInstance("SHA-256").digest(raw.toByteArray())
                .joinToString("") { "%02x".format(it) }.take(12)
        }.orEmpty()
        if (identity.first != expectedProject || identity.second != expectedFingerprint) {
            return@withContext Status(transportOnline = true, failureClass = "identity_mismatch")
        }

        // ③ V1400 unified projection: dispatch and remote capabilities are
        // merged by the backend into one owner-scoped device list.  The App no
        // longer races two endpoints and invents a third local truth.
        val deviceStatus = if (token.isBlank()) (0 to 0) else try {
            val req = Request.Builder()
                .url("$base/api/remote/status")
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) 0 to 0
                else parseUnifiedDeviceStatus(resp.body?.string().orEmpty())
            }
        } catch (e: Exception) {
            0 to 0
        }
        Status(online = true, transportOnline = true, desktops = deviceStatus.first, remoteHosts = deviceStatus.second)
    }
}

internal fun parseBootstrapIdentity(payload: String): Pair<String, String> = runCatching {
    val root = Json.parseToJsonElement(payload) as? JsonObject ?: return@runCatching "" to ""
    val project = (root["supabase_project_ref"] as? JsonPrimitive)?.contentOrNull.orEmpty()
    val fingerprint = (root["user_sub_fingerprint"] as? JsonPrimitive)?.contentOrNull.orEmpty()
    project to fingerprint
}.getOrDefault("" to "")

internal fun parseUnifiedDeviceStatus(payload: String): Pair<Int, Int> = runCatching {
    val root = Json.parseToJsonElement(payload) as? JsonObject ?: return@runCatching 0 to 0
    val devices = (root["devices"] as? JsonArray).orEmpty()
    val onlineDevices = (root["desktop_count"] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()
        ?: devices.count { element ->
            val item = element as? JsonObject ?: return@count false
            (item["online"] as? JsonPrimitive)?.booleanOrNull == true
        }
    val remoteReady = devices.count { element ->
        val item = element as? JsonObject ?: return@count false
        (item["remote_ready"] as? JsonPrimitive)?.booleanOrNull == true
    }
    onlineDevices to remoteReady
}.getOrDefault(0 to 0)

internal fun parseDesktopRunnerCount(payload: String): Int = runCatching {
    val root = Json.parseToJsonElement(payload) as? JsonObject ?: return@runCatching 0
    val runners = root["runners"] as? JsonArray ?: return@runCatching 0
    runners.count { element ->
        val item = element as? JsonObject ?: return@count false
        val queue = (item["runner"] as? JsonPrimitive)?.contentOrNull
            ?: (item["name"] as? JsonPrimitive)?.contentOrNull
        val online = (item["online"] as? JsonPrimitive)?.booleanOrNull == true
        online && queue == "desktop"
    }
}.getOrDefault(0)
