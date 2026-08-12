package com.hashmm.app.data.remote

import com.hashmm.app.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONObject
import timber.log.Timber
import java.util.UUID
import java.io.IOException
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.ScheduledFuture

/** 远程可控设备（后端 host）。 */
data class RemoteDevice(
    val id: String,
    val name: String,
    val platform: String,
    val online: Boolean = true,
    val remoteReady: Boolean = false,
    val busy: Boolean = false,
    val lastSeen: Long = 0L,
    val appVersion: String = "",
    val deviceFingerprint: String = "",
)

data class RemoteRegistration(
    val protocol: String,
    val stableDeviceId: String,
    val ownerFingerprint: String,
    val deviceFingerprint: String,
    val generation: Int,
    val leaseExpiresIn: Int,
)

/** ICE 服务器配置（来自后端 authOk.iceServers，格式 {"urls": "stun:..."}）。 */
data class IceServerConfig(val urls: String, val username: String? = null, val credential: String? = null)

interface RemoteSignalListener {
    fun onTransportStage(stage: String, detail: String = "") {}
    fun onAuthOk(iceServers: List<IceServerConfig>)
    fun onRegistered(registration: RemoteRegistration) {}
    fun onDevices(devices: List<RemoteDevice>)
    fun onPermissionPending() {}
    fun onReady(sessionId: String, ticket: String, scopes: Set<String>, legacyRelayAfterMs: Long)
    fun onRemoteTicket(sessionId: String, ticket: String) {}
    fun onRemoteCompleted(sessionId: String) {}
    fun onRtcSignal(kind: String, data: JSONObject)
    fun onMeta(remoteW: Int, remoteH: Int)
    fun onPairFail(reason: String)
    fun onClosed(reason: String)
}

/**
 * 远程控制信令客户端：使用 V4 一次性票据连接账号控制面；屏幕媒体仍优先走 WebRTC。
 *
 * 与后端 `hashmm/api/routes/remote_signal.py` + `remote_hub.py` 协议严格对齐：
 *  发：auth / listDevices / connect / rtcSignal / input
 *  收：authOk / devices / ready / rtcSignal / pairFail / authFail
 *
 * token 用 App 当前 Supabase 会话的 access token（后端 verify_any_token 已支持 Supabase 身份）。
 * 真实屏幕视频走 WebRTC P2P 直连，不经此信令通道。
 */
class RemoteSignalingClient(
    private val baseUrl: String,
    private val listener: RemoteSignalListener,
    private val deviceId: String,
) {
    private var ws: WebSocket? = null
    private var ticketCall: Call? = null
    private val http = SharedHttp.base.newBuilder()
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    @Volatile private var authToken: String = ""
    @Volatile private var viewerName: String = "Android"
    @Volatile private var socketTicket: String = ""
    @Volatile private var socketAttemptId: String = ""
    @Volatile private var socketTraceId: String = ""
    @Volatile private var controlWss: String = ""
    @Volatile var activeSessionId: String = ""
        private set
    @Volatile var relayTicket: String = ""
        private set
    private val sequence = java.util.concurrent.atomic.AtomicLong(0)
    private val connectRequestIds = ConcurrentHashMap<String, String>()
    private var heartbeat: ScheduledFuture<*>? = null
    private val connectionGeneration = java.util.concurrent.atomic.AtomicLong(0)

    /** 连接并以 viewer 身份认证。 */
    fun connect(token: String, name: String = "Android") {
        val generation = connectionGeneration.incrementAndGet()
        heartbeat?.cancel(false); heartbeat = null
        try { ticketCall?.cancel() } catch (_: Exception) {}
        try { ws?.cancel() } catch (_: Exception) {}
        ws = null
        authToken = token
        viewerName = name
        socketTraceId = "rt_" + UUID.randomUUID().toString().replace("-", "")
        listener.onTransportStage("bootstrap", "正在校验远程服务入口")
        val req = Request.Builder().url(baseUrl.trimEnd('/') + "/api/remote/v4/bootstrap")
            .header("Authorization", "Bearer $token").get().build()
        val call = http.newCall(req)
        ticketCall = call
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (generation != connectionGeneration.get() || call.isCanceled()) return
                listener.onClosed("REMOTE_BOOTSTRAP_UNAVAILABLE")
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (generation != connectionGeneration.get()) return
                    if (!it.isSuccessful) { listener.onClosed("REMOTE_BOOTSTRAP_${it.code}"); return }
                    val parsed = runCatching { JSONObject(it.body?.string().orEmpty()) }.getOrNull()
                    val bootstrap = validateBootstrap(parsed)
                    if (bootstrap == null) listener.onClosed("REMOTE_BOOTSTRAP_INVALID")
                    else requestTicket(bootstrap, generation)
                }
            }
        })
    }

    private data class Bootstrap(val apiBase: String, val controlWss: String, val revision: String)

    private fun validateBootstrap(body: JSONObject?): Bootstrap? {
        if (body == null || body.optString("schema") != "hashmm.remote-bootstrap.v4" ||
            body.optString("protocol") != REMOTE_PROTOCOL) return null
        val api = body.optString("api_base").toHttpUrlOrNull() ?: return null
        val signal = runCatching { URI(body.optString("control_wss")) }.getOrNull() ?: return null
        val loopback = api.host in setOf("127.0.0.1", "localhost", "::1")
        if (api.scheme != "https" && !loopback) return null
        if (signal.scheme != "wss" && !(loopback && signal.scheme == "ws")) return null
        val signalPort = if (signal.port > 0) signal.port else if (signal.scheme == "wss") 443 else 80
        if (api.host != signal.host || api.port != signalPort || signal.rawPath != "/api/remote/v4/ws") return null
        return Bootstrap(api.newBuilder().encodedPath("/").query(null).fragment(null).build().toString().trimEnd('/'),
            signal.toString(), body.optString("config_revision"))
    }

    private fun requestTicket(bootstrap: Bootstrap, generation: Long) {
        listener.onTransportStage("ticket", "正在申请一次性信令票据")
        val body = JSONObject().put("role", "viewer").put("device_id", deviceId).toString()
        val payload = JSONObject(body).put("trace_id", socketTraceId)
            .put("protocol", REMOTE_PROTOCOL)
            .put("endpoint_host", runCatching { URI(bootstrap.controlWss).authority }.getOrDefault(""))
            .toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url(bootstrap.apiBase + "/api/remote/v4/socket-ticket")
            .header("Authorization", "Bearer $authToken").post(payload).build()
        val call = http.newCall(req)
        ticketCall = call
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (generation != connectionGeneration.get() || call.isCanceled()) return
                listener.onClosed("socket_ticket_unavailable")
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (generation != connectionGeneration.get()) return
                    if (!it.isSuccessful) {
                        listener.onClosed("socket_ticket_${it.code}")
                        return
                    }
                    val parsed = runCatching { JSONObject(it.body?.string().orEmpty()) }.getOrNull()
                    val ticket = parsed?.optString("ticket").orEmpty()
                    if (ticket.isBlank()) listener.onClosed("socket_ticket_missing")
                    else {
                        socketAttemptId = parsed?.optString("attempt_id").orEmpty()
                        socketTraceId = parsed?.optString("trace_id", socketTraceId).orEmpty()
                        controlWss = parsed?.optString("control_wss", bootstrap.controlWss).orEmpty()
                        openSocket(ticket, generation)
                    }
                }
            }
        })
    }

    private fun openSocket(ticket: String, generation: Long) {
        if (generation != connectionGeneration.get()) return
        socketTicket = ticket
        listener.onTransportStage("socket", "正在建立 WSS 信令连接")
        val endpoint = controlWss + (if (controlWss.contains('?')) "&" else "?") +
            "attempt_id=${urlEncode(socketAttemptId)}&trace_id=${urlEncode(socketTraceId)}&protocol=${urlEncode(REMOTE_PROTOCOL)}"
        val req = runCatching { Request.Builder().url(endpoint).build() }.getOrNull()
        if (req == null) { listener.onClosed("REMOTE_BOOTSTRAP_INVALID"); return }
        ws = http.newWebSocket(req, Listener(generation))
    }

    fun listDevices() = sendJson(JSONObject().put("type", "listDevices"))

    fun connectToHost(hostId: String, turnOnly: Boolean = false) =
        sendJson(JSONObject().put("type", "connect").put("target", hostId)
            .put("transportMode", if (turnOnly) "turn-only" else "auto")
            .put("clientRequestId", connectRequestIds.computeIfAbsent(hostId) { UUID.randomUUID().toString() })
            .put("scopes", JSONArray()
            .put("view").put("control").put("clipboard").put("file_write").put("audio").put("power")))

    /** 发 WebRTC 信令（kind: offer/answer/ice）。viewer → 它所连的 host。 */
    fun sendRtcSignal(kind: String, data: JSONObject) =
        sendJson(JSONObject().put("type", "rtcSignal").put("kind", kind).put("data", data))

    /** 发输入事件（触摸/点击映射）。viewer → host。 */
    fun sendInput(input: JSONObject) =
        sendJson(input.put("type", "input").put("sessionId", activeSessionId)
            .put("seq", sequence.incrementAndGet()).put("timestamp", System.currentTimeMillis()))

    fun sendControl(input: JSONObject) =
        sendJson(input.put("type", "control").put("sessionId", activeSessionId)
            .put("seq", sequence.incrementAndGet()).put("timestamp", System.currentTimeMillis()))

    /** 上报浏览器实际选中的 ICE 传输摘要；不包含候选地址、SDP 或账号令牌。 */
    fun sendTransportReport(report: JSONObject) =
        sendJson(JSONObject().put("type", "transportReport").put("sessionId", activeSessionId).put("report", report))

    /** 上报有界连接里程碑；不得包含 SDP、候选地址、令牌或屏幕内容。 */
    fun sendMilestone(milestone: String, detail: JSONObject = JSONObject()) {
        if (activeSessionId.isBlank()) return
        sendJson(JSONObject().put("type", "milestone").put("sessionId", activeSessionId)
            .put("milestone", milestone).put("detail", detail))
    }

    /** 告知 host：viewer 已进入兼容预览，host 此时才开始低帧率 JPEG 推送。 */
    fun requestCompatPreview() {
        if (activeSessionId.isBlank()) return
        sendJson(JSONObject().put("type", "compatReady").put("sessionId", activeSessionId))
    }

    /** 正常结束会话并写入服务端完成回执；与掉线/撤销语义分开。 */
    fun completeSession(summary: String = "remote_session_completed") {
        if (activeSessionId.isBlank()) return
        sendJson(JSONObject().put("type", "complete").put("sessionId", activeSessionId)
            .put("summary", summary.take(120)))
    }

    /** 告知 host 开/关推流。 */
    fun setStreaming(on: Boolean) =
        sendJson(JSONObject().put("type", if (on) "rtcOn" else "rtcOff"))

    fun close() {
        connectionGeneration.incrementAndGet()
        try { ticketCall?.cancel() } catch (_: Exception) {}
        ticketCall = null
        heartbeat?.cancel(false); heartbeat = null
        try { ws?.close(1000, "bye") } catch (_: Exception) {}
        ws = null
        activeSessionId = ""; relayTicket = ""; sequence.set(0)
    }

    private fun sendJson(obj: JSONObject) {
        val sock = ws
        if (sock == null) { Timber.tag(TAG).w("ws not connected"); return }
        sock.send(obj.toString())
    }

    private inner class Listener(private val generation: Long) : WebSocketListener() {
        private fun current(webSocket: WebSocket): Boolean =
            generation == connectionGeneration.get() && ws === webSocket

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!current(webSocket)) { webSocket.cancel(); return }
            listener.onTransportStage("auth", "WSS 已连接，正在绑定账号与设备")
            // 首条必须是 auth
            val auth = JSONObject()
                .put("type", "auth")
                .put("token", if (socketTicket.isBlank()) authToken else "")
                .put("ticket", socketTicket)
                .put("role", "viewer")
                .put("deviceId", deviceId)
                .put("name", viewerName)
                .put("platform", "android")
                .put("appVersion", BuildConfig.VERSION_NAME)
                .put("protocol", REMOTE_PROTOCOL)
                .put("attemptId", socketAttemptId)
                .put("traceId", socketTraceId)
            webSocket.send(auth.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!current(webSocket)) return
            val m = try { JSONObject(text) } catch (e: Exception) { Timber.tag(TAG).w(e); return }
            when (m.optString("type")) {
                "authOk", "hello" -> {
                    listener.onTransportStage("registered", "查看端已注册")
                    listener.onRegistered(
                        RemoteRegistration(
                            protocol = m.optString("protocol", "hashmm.remote.v1"),
                            stableDeviceId = m.optString("stableDeviceId", m.optString("deviceId")),
                            ownerFingerprint = m.optString("ownerFingerprint"),
                            deviceFingerprint = m.optString("deviceFingerprint"),
                            generation = m.optInt("generation", 0),
                            leaseExpiresIn = m.optInt("leaseExpiresIn", 0),
                        )
                    )
                    heartbeat?.cancel(false)
                    heartbeat = HEARTBEAT_EXECUTOR.scheduleAtFixedRate(
                        { sendJson(JSONObject().put("type", "heartbeat").put("at", System.currentTimeMillis())) },
                        10, 10, TimeUnit.SECONDS,
                    )
                    listener.onAuthOk(parseIceServers(m.optJSONArray("iceServers")))
                }
                "authFail" -> listener.onClosed(m.optString("errorCode", "REMOTE_AUTH_REJECTED") + "：" + m.optString("reason"))
                "devices" -> listener.onDevices(parseDevices(m.optJSONArray("list")))
                "deviceChanged" -> listDevices()
                "deviceReplaced", "leaseRejected" -> listener.onClosed(m.optString("type"))
                "heartbeatAck" -> Unit
                "permissionPending" -> {
                    activeSessionId = m.optString("sessionId")
                    listener.onPermissionPending()
                }
                "ready" -> {
                    val nextSessionId = m.optString("sessionId", activeSessionId)
                    val nextTicket = m.optString("ticket")
                    sequence.set(0)
                    connectRequestIds.clear()
                    val scopes = mutableSetOf<String>()
                    val arr = m.optJSONArray("scopes")
                    if (arr != null) for (i in 0 until arr.length()) scopes.add(arr.optString(i))
                    if (nextSessionId.isBlank() || nextTicket.isBlank() || "view" !in scopes) {
                        activeSessionId = ""
                        relayTicket = ""
                        listener.onPairFail("ticket_issue_failed")
                    } else {
                        activeSessionId = nextSessionId
                        relayTicket = nextTicket
                        listener.onReady(activeSessionId, relayTicket, scopes,
                            m.optLong("legacyRelayAfterMs", 12_000L).coerceIn(3_000L, 30_000L))
                    }
                }
                "ticket" -> {
                    if (m.optString("sessionId") == activeSessionId) {
                        relayTicket = m.optString("ticket", relayTicket)
                        listener.onRemoteTicket(activeSessionId, relayTicket)
                    }
                }
                "remoteCompleted" -> {
                    val completedId = m.optString("sessionId", activeSessionId)
                    activeSessionId = ""; relayTicket = ""; sequence.set(0)
                    listener.onRemoteCompleted(completedId)
                }
                "meta" -> listener.onMeta(m.optInt("sw", 0), m.optInt("sh", 0))
                "pairFail" -> {
                    // A server response ends this admission attempt. A later
                    // explicit tap gets a fresh work request, while transport
                    // retries before a response reuse the same identifier.
                    connectRequestIds.clear()
                    listener.onPairFail(m.optString("reason"))
                }
                "rtcSignal" -> listener.onRtcSignal(m.optString("kind"), m.optJSONObject("data") ?: JSONObject())
                "viewerLeft", "viewerJoined" -> { /* host 侧消息，viewer 忽略 */ }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!current(webSocket)) return
            heartbeat?.cancel(false); heartbeat = null
            Timber.tag(TAG).w(t, "ws failure")
            listener.onClosed(t.message ?: "连接断开")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!current(webSocket)) return
            heartbeat?.cancel(false); heartbeat = null
            listener.onClosed(reason.ifBlank { "连接已关闭" })
        }
    }

    fun refreshTicket() = sendJson(JSONObject().put("type", "refreshTicket"))

    private fun parseIceServers(arr: JSONArray?): List<IceServerConfig> {
        val out = mutableListOf<IceServerConfig>()
        if (arr != null) for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val username = o.optString("username").ifBlank { null }
            val credential = o.optString("credential").ifBlank { null }
            val urlArray = o.optJSONArray("urls")
            if (urlArray != null) {
                for (j in 0 until urlArray.length()) {
                    val url = urlArray.optString(j)
                    if (url.isNotBlank()) out.add(IceServerConfig(url, username, credential))
                }
            } else {
                val url = o.optString("urls").ifBlank { o.optString("url") }
                if (url.isNotBlank()) out.add(IceServerConfig(url, username, credential))
            }
        }
        if (out.isEmpty()) out.add(IceServerConfig("stun:stun.l.google.com:19302"))
        return out
    }

    private fun parseDevices(arr: JSONArray?): List<RemoteDevice> {
        val out = mutableListOf<RemoteDevice>()
        if (arr != null) for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("device_id", o.optString("id"))
            if (id.isBlank()) continue
            out.add(
                RemoteDevice(
                    id = id,
                    name = o.optString("name"),
                    platform = o.optString("platform"),
                    online = o.optBoolean("online", true),
                    remoteReady = o.optBoolean("remote_ready", false),
                    busy = o.optBoolean("busy", false),
                    lastSeen = (o.optDouble("last_seen", 0.0) * 1000.0).toLong(),
                    appVersion = o.optString("app_version"),
                    deviceFingerprint = o.optString("device_fingerprint"),
                )
            )
        }
        return out
    }

    companion object {
        private const val TAG = "RemoteSignal"
        const val REMOTE_PROTOCOL = "hashmm.remote.v4"
        private val HEARTBEAT_EXECUTOR = java.util.concurrent.Executors.newSingleThreadScheduledExecutor { runnable ->
            Thread(runnable, "hashmm-remote-heartbeat").apply { isDaemon = true }
        }
        private fun urlEncode(value: String): String =
            URLEncoder.encode(value, StandardCharsets.UTF_8.toString())
    }
}
