package com.hashmm.app.data.remote

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import timber.log.Timber
import java.util.concurrent.TimeUnit

/** 远程可控设备（后端 host）。 */
data class RemoteDevice(val id: String, val name: String, val platform: String)

/** ICE 服务器配置（来自后端 authOk.iceServers，格式 {"urls": "stun:..."}）。 */
data class IceServerConfig(val urls: String, val username: String? = null, val credential: String? = null)

interface RemoteSignalListener {
    fun onAuthOk(iceServers: List<IceServerConfig>)
    fun onDevices(devices: List<RemoteDevice>)
    fun onReady()
    fun onRtcSignal(kind: String, data: JSONObject)
    fun onMeta(remoteW: Int, remoteH: Int)
    fun onPairFail(reason: String)
    fun onClosed(reason: String)
}

/**
 * 远程控制信令客户端：连后端 `WS /api/remote/ws`，以 viewer 身份中继 WebRTC 信令与输入。
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
    private val listener: RemoteSignalListener
) {
    private var ws: WebSocket? = null
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    @Volatile private var authToken: String = ""
    @Volatile private var viewerName: String = "Android"

    /** 连接并以 viewer 身份认证。 */
    fun connect(token: String, name: String = "Android") {
        authToken = token
        viewerName = name
        val url = wsUrl(baseUrl)
        val req = Request.Builder().url(url).build()
        ws = http.newWebSocket(req, Listener())
    }

    fun listDevices() = sendJson(JSONObject().put("type", "listDevices"))

    fun connectToHost(hostId: String) =
        sendJson(JSONObject().put("type", "connect").put("target", hostId))

    /** 发 WebRTC 信令（kind: offer/answer/ice）。viewer → 它所连的 host。 */
    fun sendRtcSignal(kind: String, data: JSONObject) =
        sendJson(JSONObject().put("type", "rtcSignal").put("kind", kind).put("data", data))

    /** 发输入事件（触摸/点击映射）。viewer → host。 */
    fun sendInput(input: JSONObject) =
        sendJson(input.put("type", "input"))

    /** 告知 host 开/关推流。 */
    fun setStreaming(on: Boolean) =
        sendJson(JSONObject().put("type", if (on) "rtcOn" else "rtcOff"))

    fun close() {
        try { ws?.close(1000, "bye") } catch (_: Exception) {}
        ws = null
    }

    private fun sendJson(obj: JSONObject) {
        val sock = ws
        if (sock == null) { Timber.tag(TAG).w("ws not connected"); return }
        sock.send(obj.toString())
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            // 首条必须是 auth
            val auth = JSONObject()
                .put("type", "auth")
                .put("token", authToken)
                .put("role", "viewer")
                .put("name", viewerName)
                .put("platform", "android")
            webSocket.send(auth.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val m = try { JSONObject(text) } catch (e: Exception) { Timber.tag(TAG).w(e); return }
            when (m.optString("type")) {
                "authOk", "hello" -> {
                    listener.onAuthOk(parseIceServers(m.optJSONArray("iceServers")))
                }
                "authFail" -> listener.onClosed("认证失败：" + m.optString("reason"))
                "devices" -> listener.onDevices(parseDevices(m.optJSONArray("list")))
                "ready" -> listener.onReady()
                "meta" -> listener.onMeta(m.optInt("sw", 0), m.optInt("sh", 0))
                "pairFail" -> listener.onPairFail(m.optString("reason"))
                "rtcSignal" -> listener.onRtcSignal(m.optString("kind"), m.optJSONObject("data") ?: JSONObject())
                "viewerLeft", "viewerJoined" -> { /* host 侧消息，viewer 忽略 */ }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Timber.tag(TAG).w(t, "ws failure")
            listener.onClosed(t.message ?: "连接断开")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            listener.onClosed(reason.ifBlank { "连接已关闭" })
        }
    }

    private fun parseIceServers(arr: JSONArray?): List<IceServerConfig> {
        val out = mutableListOf<IceServerConfig>()
        if (arr != null) for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val urls = o.optString("urls").ifBlank { o.optString("url") }
            if (urls.isNotBlank()) out.add(
                IceServerConfig(urls, o.optString("username").ifBlank { null }, o.optString("credential").ifBlank { null })
            )
        }
        if (out.isEmpty()) out.add(IceServerConfig("stun:stun.l.google.com:19302"))
        return out
    }

    private fun parseDevices(arr: JSONArray?): List<RemoteDevice> {
        val out = mutableListOf<RemoteDevice>()
        if (arr != null) for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            out.add(RemoteDevice(o.optString("id"), o.optString("name"), o.optString("platform")))
        }
        return out
    }

    companion object {
        private const val TAG = "RemoteSignal"
        /** http(s) → ws(s)，并补 /api/remote/ws 路径。 */
        fun wsUrl(base: String): String {
            var b = base.trim()
            if (!b.startsWith("http")) b = "https://$b"
            b = b.trimEnd('/')
            val wsBase = when {
                b.startsWith("https://") -> "wss://" + b.removePrefix("https://")
                b.startsWith("http://") -> "ws://" + b.removePrefix("http://")
                else -> b
            }
            return "$wsBase/api/remote/ws"
        }
    }
}
