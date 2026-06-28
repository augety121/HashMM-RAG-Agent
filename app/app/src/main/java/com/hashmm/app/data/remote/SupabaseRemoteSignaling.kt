package com.hashmm.app.data.remote

import java.time.Instant
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.postgrest.postgrest
import io.github.jan.supabase.realtime.PostgresAction
import io.github.jan.supabase.realtime.channel
import io.github.jan.supabase.realtime.postgresChangeFlow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonPrimitive
import org.json.JSONObject
import java.util.UUID

@Serializable
private data class PresenceRow(val host_id: String, val name: String? = null, val platform: String? = null, val updated_at: String? = null)

@Serializable
private data class SignalInsert(
    val room: String,
    val recipient: String,
    val sender: String,
    val kind: String,
    val payload: JsonElement? = null,
)

/**
 * 远程信令 —— Supabase 版（P2P，无需隧道）。
 *
 * 与 [RemoteSignalingClient]（后端 WS 版）实现相同的对外方法与 [RemoteSignalListener] 回调，
 * 因此 RemoteControlViewModel 可无缝切换。建连信令(offer/answer/ICE)经 Supabase 表
 * `remote_signals` 中转，屏幕视频与（后续）输入走 viewer↔host 的 WebRTC P2P 直连。
 *
 * 房间 = 当前登录用户 uid，保证同账号隔离。viewer 自生成 id，host 来自 `remote_presence`。
 */
class SupabaseRemoteSignaling(
    private val supabase: SupabaseClient,
    private val room: String,
    private val listener: RemoteSignalListener,
) {
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val viewerId = "v-" + UUID.randomUUID().toString().take(8)
    private val me = "viewer:$viewerId"
    private var targetHost: String? = null   // "host:<id>"
    private var sub: Job? = null
    private var closed = false

    /** 免费公共 STUN；如需穿透对称 NAT 可在此追加 TURN。 */
    private val ice = listOf(
        IceServerConfig("stun:stun.l.google.com:19302"),
        IceServerConfig("stun:stun1.l.google.com:19302"),
    )

    fun connect(token: String, name: String = "Android") {
        // 订阅发给本 viewer 的信令
        sub = scope.launch {
            try {
                val ch = supabase.channel("remote-$room-$viewerId")
                val flow = ch.postgresChangeFlow<PostgresAction.Insert>(schema = "public") {
                    table = "remote_signals"
                }
                ch.subscribe(blockUntilSubscribed = false)
                flow.collect { action -> handleRow(action.record) }
            } catch (_: Exception) {
                if (!closed) listener.onClosed("realtime_error")
            }
        }
        // 无后端，直接给 ICE 配置 + 拉设备列表
        listener.onAuthOk(ice)
    }

    fun listDevices() {
        scope.launch {
            try {
                val rows = supabase.postgrest.from("remote_presence").select {
                    filter { eq("room", room) }
                }.decodeList<PresenceRow>()
                fun ms(s: String?): Long? = s?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() }
                val now = System.currentTimeMillis()
                val devices = rows
                    // 45s 内有心跳才算在线（host 每 5s 上报；关掉的会陈旧自动消失）；解析失败则保留
                    .filter { val t = ms(it.updated_at); t == null || now - t < 45_000 }
                    // 同名去重：一台机器多次启动会产生多条不同 host_id，保留心跳最新的那条
                    .sortedByDescending { ms(it.updated_at) ?: 0L }
                    .distinctBy { (it.name ?: "").ifBlank { it.host_id } }
                    .map { RemoteDevice(it.host_id, it.name ?: "电脑端", it.platform ?: "") }
                listener.onDevices(devices)
            } catch (_: Exception) {
                listener.onDevices(emptyList())
            }
        }
    }

    fun connectToHost(hostId: String) {
        targetHost = "host:$hostId"
        // 通知 host 有 viewer 接入 → host 会回 offer
        send("connect", null)
        // 无服务器 ready 消息，本地直接进入协商（VM 建 peer 等 offer）
        listener.onReady()
    }

    fun sendRtcSignal(kind: String, data: JSONObject) = send(kind, data)

    /** v1：输入经 Supabase 中转（有一定延迟）；后续可改走 WebRTC 数据通道降延迟。 */
    fun sendInput(input: JSONObject) = send("input", input)

    /** 会话控制（声音 / 画质 / 安全中转）下发到被控端。 */
    fun sendControl(data: JSONObject) = send("control", data)

    fun setStreaming(on: Boolean) = send(if (on) "rtcOn" else "rtcOff", null)

    fun close() {
        if (closed) return
        closed = true
        send("bye", null)
        sub?.cancel()
    }

    // ── 内部 ──────────────────────────────────────────────
    private fun send(kind: String, data: JSONObject?) {
        val to = targetHost ?: return
        scope.launch {
            try {
                val payload = data?.let { Json.parseToJsonElement(it.toString()) }
                supabase.postgrest.from("remote_signals").insert(
                    SignalInsert(room = room, recipient = to, sender = me, kind = kind, payload = payload)
                )
            } catch (_: Exception) {
                // 单条信令失败不致命
            }
        }
    }

    private fun handleRow(rec: kotlinx.serialization.json.JsonObject) {
        try {
            val recipient = rec["recipient"]?.jsonPrimitive?.content ?: return
            if (recipient != me) return  // 只处理发给本 viewer 的
            val kind = rec["kind"]?.jsonPrimitive?.content ?: return
            val payloadEl = rec["payload"]
            when (kind) {
                "offer", "ice" -> {
                    val obj = if (payloadEl != null) JSONObject(payloadEl.toString()) else JSONObject()
                    listener.onRtcSignal(kind, obj)
                }
                "bye" -> listener.onClosed("host_left")
            }
        } catch (_: Exception) {
            // 忽略坏消息
        }
    }
}
