package com.hashmm.app.ui.remote

import android.content.Context
import android.graphics.BitmapFactory
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.hashmm.app.data.settings.SettingsStore
import com.hashmm.app.data.remote.IceServerConfig
import com.hashmm.app.data.remote.RemoteDevice
import com.hashmm.app.data.remote.RemoteSignalListener
import com.hashmm.app.data.remote.RemoteSignalingClient
import com.hashmm.app.data.remote.SupabaseRemoteSignaling
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import org.json.JSONObject
import okhttp3.OkHttpClient
import okhttp3.Request
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.VideoTrack
import timber.log.Timber
import javax.inject.Inject
import java.util.concurrent.TimeUnit

enum class RemoteStage { IDLE, CONNECTING, PICK_DEVICE, NEGOTIATING, STREAMING, ERROR }

data class RemoteUiState(
    val stage: RemoteStage = RemoteStage.IDLE,
    val devices: List<RemoteDevice> = emptyList(),
    val message: String = "",
    val remoteW: Int = 0,
    val remoteH: Int = 0,
    val latencyMs: Int = -1,   // WebRTC 选中候选对 RTT（毫秒）；-1 = 未知
    val audioOn: Boolean = false,   // 是否转发被控电脑声音
    val quality: Int = 1,           // 画质档：0 流畅 / 1 高清 / 2 极清
    val relayOn: Boolean = false,   // 安全中转(Beta)：强制经 TURN
)

/**
 * 远程控制 ViewModel：以 viewer 身份建立到桌面客户端（host）的 WebRTC P2P 投屏。
 *
 * 与官方参考实现 `desktop/remote-viewer.html` 的信令/数据格式严格一致：
 *  收 offer(data={type,sdp}) → setRemoteDescription → createAnswer → 发 answer；
 *  ice 双向 data={candidate,sdpMid,sdpMLineIndex}；ontrack 拿远端视频轨。
 * token 用 App 当前 Supabase 会话（后端 verify_any_token 已支持）。
 */
@HiltViewModel
class RemoteControlViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val settings: SettingsStore,
    private val supabase: SupabaseClient,
) : ViewModel(), RemoteSignalListener {

    private val _ui = MutableStateFlow(RemoteUiState())
    val ui: StateFlow<RemoteUiState> = _ui.asStateFlow()

    val eglBase: EglBase = EglBase.create()
    private var factory: PeerConnectionFactory? = null
    private var pc: PeerConnection? = null
    private var signaling: SupabaseRemoteSignaling? = null

    private var iceServers: List<IceServerConfig> = emptyList()
    private val pendingIce = mutableListOf<IceCandidate>()
    private var statsJob: Job? = null
    private var devicePollJob: Job? = null     // PICK_DEVICE 时自动轮询在线设备，免手动刷新
    private var negotiateJob: Job? = null      // NEGOTIATING 超时兜底，避免一直卡在「正在协商」
    private var reconnectJob: Job? = null      // 断线后自动重连
    private var reconnectTries = 0
    private var currentHostId: String? = null  // 当前所连被控端，用于断线重连

    private val _remoteTrack = MutableStateFlow<VideoTrack?>(null)
    val remoteTrack: StateFlow<VideoTrack?> = _remoteTrack.asStateFlow()

    // 帧中继兜底：relayOn 时轮询后端最新 JPEG 帧并显示（P2P/TURN 打不通也能看画面）。
    private val _relayFrame = MutableStateFlow<ImageBitmap?>(null)
    val relayFrame: StateFlow<ImageBitmap?> = _relayFrame.asStateFlow()
    private var relayPollJob: Job? = null
    private val relayHttp = OkHttpClient.Builder().callTimeout(8, TimeUnit.SECONDS).build()

    init { initFactory() }

    private fun initFactory() {
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(appContext)
                .createInitializationOptions()
        )
        factory = PeerConnectionFactory.builder()
            .setVideoEncoderFactory(DefaultVideoEncoderFactory(eglBase.eglBaseContext, true, true))
            .setVideoDecoderFactory(DefaultVideoDecoderFactory(eglBase.eglBaseContext))
            .createPeerConnectionFactory()
    }

    /** 连接信令并以当前 Supabase 身份认证。 */
    fun connect() {
        viewModelScope.launch {
            // Supabase 信令 P2P：同账号 + 联网即可，无需客户端公网地址/隧道。
            val uid = supabase.auth.currentUserOrNull()?.id
            val token = supabase.auth.currentSessionOrNull()?.accessToken
            if (uid.isNullOrBlank() || token.isNullOrBlank()) { setError("请先登录 Supabase 账号"); return@launch }
            _ui.value = _ui.value.copy(stage = RemoteStage.CONNECTING, message = "正在连接…")
            signaling = SupabaseRemoteSignaling(supabase, uid, this@RemoteControlViewModel).also { it.connect(token) }
        }
    }

    fun refreshDevices() { signaling?.listDevices() }

    fun selectDevice(hostId: String) {
        devicePollJob?.cancel()
        currentHostId = hostId
        reconnectTries = 0
        reconnectJob?.cancel(); reconnectJob = null
        _ui.value = _ui.value.copy(stage = RemoteStage.NEGOTIATING, message = "正在连接所选设备…")
        signaling?.connectToHost(hostId)
        armNegotiateTimeout()
    }

    /** 转发输入事件（坐标已由 UI 归一化到 0..1000）。 */
    fun sendInput(input: JSONObject) { signaling?.sendInput(input) }

    // ── 会话控制：声音 / 画质 / 安全中转（下发被控端，立即更新本地 UI）──
    fun setRemoteAudio(on: Boolean) {
        signaling?.sendControl(JSONObject().put("action", "audio").put("on", on))
        _ui.value = _ui.value.copy(audioOn = on)
    }

    fun setRemoteQuality(level: Int) {
        val (fps, jpeg) = when (level) {
            0 -> 8 to 55      // 流畅
            2 -> 30 to 90     // 极清
            else -> 20 to 75  // 高清
        }
        signaling?.sendControl(JSONObject().put("action", "quality").put("fps", fps).put("jpeg", jpeg))
        _ui.value = _ui.value.copy(quality = level)
    }

    fun setRemoteRelay(on: Boolean) {
        signaling?.sendControl(JSONObject().put("action", "relay").put("on", on))
        _ui.value = _ui.value.copy(relayOn = on)
        if (on) startRelayPoll() else stopRelayPoll()
    }

    /** 中继拉帧：轮询后端最新帧并显示。room=账号 uid（被控端推到同一 uid）。出帧即进入投屏界面。 */
    private fun startRelayPoll() {
        if (relayPollJob?.isActive == true) return
        relayPollJob = viewModelScope.launch {
            val base = settings.clientUrl.first().trim().trimEnd('/')
            val uid = supabase.auth.currentUserOrNull()?.id
            if (base.isBlank() || uid.isNullOrBlank()) return@launch
            while (isActive) {
                try {
                    val token = supabase.auth.currentSessionOrNull()?.accessToken
                    val rb = Request.Builder().url("$base/api/remote/relay/$uid/frame").get()
                    if (!token.isNullOrBlank()) rb.header("Authorization", "Bearer $token")
                    val bytes = withContext(Dispatchers.IO) {
                        try {
                            relayHttp.newCall(rb.build()).execute().use { resp ->
                                if (resp.isSuccessful) resp.body?.bytes() else null
                            }
                        } catch (_: Exception) { null }
                    }
                    if (bytes != null) {
                        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        if (bmp != null) {
                            _relayFrame.value = bmp.asImageBitmap()
                            if (_ui.value.stage != RemoteStage.STREAMING) {
                                _ui.value = _ui.value.copy(stage = RemoteStage.STREAMING, message = "")
                            }
                        }
                    }
                } catch (_: Exception) {}
                delay(200)
            }
        }
    }

    private fun stopRelayPoll() {
        relayPollJob?.cancel()
        relayPollJob = null
        _relayFrame.value = null
    }

    /** 单个/组合按键。host 端 computer 链用 `keys` 字段（如 "enter"、"ctrl+c"、"alt+tab"）。 */
    fun sendKey(keys: String) = sendInput(JSONObject().put("action", "key").put("keys", keys))

    /** 键入一段文本。 */
    fun sendText(text: String) { if (text.isNotEmpty()) sendInput(JSONObject().put("action", "type").put("text", text)) }

    /**
     * 系统操作。锁屏/显示桌面/任务管理器走系统组合键（无需 host 改动，直接经 computer 链）；
     * 重启/关机走 host 端 system 处理（需桌面端支持）。
     */
    fun sendSystem(cmd: String) = when (cmd) {
        "lock" -> sendKey("win+l")
        "show_desktop" -> sendKey("win+d")
        "task_manager" -> sendKey("ctrl+shift+escape")
        else -> sendInput(JSONObject().put("action", "system").put("cmd", cmd)) // reboot / shutdown
    }

    /** 把本机剪贴板文本推到远端（host 设置其剪贴板）。 */
    fun pushClipboard() {
        try {
            val cm = appContext.getSystemService(Context.CLIPBOARD_SERVICE) as? android.content.ClipboardManager
            val txt = cm?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(appContext)?.toString().orEmpty()
            if (txt.isNotBlank()) sendInput(JSONObject().put("action", "clipboard_set").put("text", txt))
        } catch (_: Exception) {}
    }

    /** 虚拟鼠标：移动光标到归一化坐标（0..1000）。 */
    fun sendMouseMove(x: Int, y: Int) = sendInput(JSONObject().put("action", "mouse_move").put("x", x).put("y", y))

    /** 在指定坐标点击（left_click / right_click / middle_click / double_click）。 */
    fun sendClickAt(action: String, x: Int, y: Int) = sendInput(JSONObject().put("action", action).put("x", x).put("y", y))

    /** 滚动：direction = up/down/left/right，amount = 步数。 */
    fun sendScroll(direction: String, amount: Int = 3) =
        sendInput(JSONObject().put("action", "scroll").put("scroll_direction", direction).put("scroll_amount", amount))

    /** 进入设备选择后自动轮询在线设备，host 上线即出现，无需手动刷新。 */
    private fun startDevicePoll() {
        devicePollJob?.cancel()
        devicePollJob = viewModelScope.launch {
            var n = 0
            while (_ui.value.stage == RemoteStage.PICK_DEVICE) {
                // 立刻查一次（不先等 3s），前几次加密轮询——进入界面即出在线设备（对标 UU）。
                if (_ui.value.stage == RemoteStage.PICK_DEVICE) try { signaling?.listDevices() } catch (_: Exception) {}
                n++
                delay(if (n < 6) 1500 else 3000)
            }
        }
    }

    /** 协商超时兜底：25s 内没出画面，自动切到中继模式并提示重试（重试即强制走 TURN，校园网最可能通的路径）。 */
    private fun armNegotiateTimeout() {
        negotiateJob?.cancel()
        negotiateJob = viewModelScope.launch {
            delay(25000)
            if (_ui.value.stage == RemoteStage.NEGOTIATING) {
                _ui.value = _ui.value.copy(relayOn = true)
                startRelayPoll()
                setError("协商超时：网络可能受限（对称 NAT，校园/公司网常见，P2P 直连打不通）。已自动切到「中继模式」：画面改经后端中转，正在尝试拉取被控端画面…")
            }
        }
    }

    fun disconnect() {
        try { reconnectJob?.cancel() } catch (_: Exception) {}
        reconnectJob = null; reconnectTries = 0; currentHostId = null
        try { stopRelayPoll() } catch (_: Exception) {}
        try { devicePollJob?.cancel() } catch (_: Exception) {}
        try { negotiateJob?.cancel() } catch (_: Exception) {}
        devicePollJob = null; negotiateJob = null
        try { statsJob?.cancel() } catch (_: Exception) {}
        statsJob = null
        try { signaling?.setStreaming(false) } catch (_: Exception) {}
        try { pc?.dispose() } catch (_: Exception) {}
        pc = null
        try { signaling?.close() } catch (_: Exception) {}
        signaling = null
        _remoteTrack.value = null
        pendingIce.clear()
        _ui.value = RemoteUiState(stage = RemoteStage.IDLE)
    }

    /** 从投屏「返回」：只断开当前被控设备，保留信令连接，回到「可控设备」列表（而不是退回 App 首页）。 */
    fun backToDeviceList() {
        try { reconnectJob?.cancel() } catch (_: Exception) {}
        reconnectJob = null; reconnectTries = 0; currentHostId = null
        try { stopRelayPoll() } catch (_: Exception) {}
        try { negotiateJob?.cancel() } catch (_: Exception) {}
        negotiateJob = null
        try { statsJob?.cancel() } catch (_: Exception) {}
        statsJob = null
        try { signaling?.setStreaming(false) } catch (_: Exception) {}
        try { pc?.dispose() } catch (_: Exception) {}
        pc = null
        _remoteTrack.value = null
        pendingIce.clear()
        if (signaling != null) {
            // 信令还在 → 直接回到设备列表并重新轮询在线设备
            _ui.value = _ui.value.copy(stage = RemoteStage.PICK_DEVICE, message = "", latencyMs = -1, devices = _ui.value.devices)
            try { signaling?.listDevices() } catch (_: Exception) {}
            startDevicePoll()
        } else {
            // 信令断了 → 重新建立（会再走到 PICK_DEVICE）
            connect()
        }
    }

    /** 周期性读取 WebRTC 选中候选对的 RTT，回填到 UI 当延迟显示（对标 UU「网络状态 5ms」）。 */
    private fun startStats() {
        statsJob?.cancel()
        statsJob = viewModelScope.launch {
            while (true) {
                val peer = pc ?: break
                try {
                    peer.getStats { report ->
                        var rttMs = -1
                        for (s in report.statsMap.values) {
                            if (s.type == "candidate-pair") {
                                val m = s.members
                                val nominated = (m["nominated"] as? Boolean) ?: false
                                val st = m["state"] as? String
                                val rtt = m["currentRoundTripTime"] as? Double
                                if (rtt != null && (nominated || st == "succeeded")) {
                                    rttMs = (rtt * 1000).toInt()
                                }
                            }
                        }
                        if (rttMs >= 0) _ui.value = _ui.value.copy(latencyMs = rttMs)
                    }
                } catch (_: Exception) {}
                delay(2000)
            }
        }
    }

    // ── RemoteSignalListener ──
    override fun onAuthOk(ice: List<IceServerConfig>) {
        iceServers = ice
        _ui.value = _ui.value.copy(stage = RemoteStage.PICK_DEVICE, message = "")
        signaling?.listDevices()
        startDevicePoll()
    }

    override fun onDevices(devices: List<RemoteDevice>) {
        _ui.value = _ui.value.copy(devices = devices)
    }

    override fun onReady() {
        // host 已配对，建 PeerConnection 等待 host 的 offer
        createPeer()
        _ui.value = _ui.value.copy(stage = RemoteStage.NEGOTIATING, message = "正在协商画面…")
        armNegotiateTimeout()
    }

    override fun onMeta(remoteW: Int, remoteH: Int) {
        if (remoteW > 0 && remoteH > 0) _ui.value = _ui.value.copy(remoteW = remoteW, remoteH = remoteH)
    }

    override fun onRtcSignal(kind: String, data: JSONObject) {
        val peer = pc ?: run { createPeer(); pc!! }
        when (kind) {
            "offer" -> {
                val sdp = data.optString("sdp")
                peer.setRemoteDescription(object : SimpleSdp() {
                    override fun onSetSuccess() {
                        flushPendingIce()
                        peer.createAnswer(object : SimpleSdp() {
                            override fun onCreateSuccess(desc: SessionDescription) {
                                peer.setLocalDescription(SimpleSdp(), desc)
                                val out = JSONObject().put("type", "answer").put("sdp", desc.description)
                                signaling?.sendRtcSignal("answer", out)
                            }
                        }, MediaConstraints())
                    }
                }, SessionDescription(SessionDescription.Type.OFFER, sdp))
            }
            "answer" -> {
                val sdp = data.optString("sdp")
                peer.setRemoteDescription(SimpleSdp(), SessionDescription(SessionDescription.Type.ANSWER, sdp))
            }
            "ice" -> {
                val cand = IceCandidate(
                    data.optString("sdpMid"),
                    data.optInt("sdpMLineIndex"),
                    data.optString("candidate"),
                )
                if (peer.remoteDescription != null) peer.addIceCandidate(cand) else pendingIce.add(cand)
            }
        }
    }

    override fun onPairFail(reason: String) {
        val r = when (reason) {
            "offline" -> "对方设备不在线"; "busy" -> "对方正忙"; else -> reason
        }
        setError(r)
    }

    override fun onClosed(reason: String) {
        if (_ui.value.stage != RemoteStage.IDLE) setError(reason)
    }

    // ── WebRTC ──
    private fun createPeer() {
        if (pc != null) return
        // 合并 ICE：后端下发 + 内置兜底（公共 STUN + 免费 TURN 中继）。
        // 校园网/公司网多为对称 NAT，P2P 直连必失败；有 TURN 中继才能通，
        // 否则就会无限卡「正在协商」。自有 TURN 可在「TURN/ICE」里覆盖（更稳）。
        val rtcIce = ArrayList<PeerConnection.IceServer>()
        iceServers.forEach {
            val b = PeerConnection.IceServer.builder(it.urls)
            if (!it.username.isNullOrBlank()) b.setUsername(it.username)
            if (!it.credential.isNullOrBlank()) b.setPassword(it.credential)
            rtcIce.add(b.createIceServer())
        }
        rtcIce.add(
            PeerConnection.IceServer.builder(
                listOf("stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302")
            ).createIceServer()
        )
        rtcIce.add(
            PeerConnection.IceServer.builder(
                listOf("turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443", "turns:openrelay.metered.ca:443?transport=tcp")
            ).setUsername("openrelayproject").setPassword("openrelayproject").createIceServer()
        )
        val config = PeerConnection.RTCConfiguration(rtcIce).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
            // 始终 ALL（P2P + STUN + 可用 TURN）。不再因"中继"强制 relay-only——
            // 没有 TURN 时强制 relay-only 会连不上变黑屏。中继(JPEG)是独立兜底，不破坏 P2P。
            iceTransportsType = PeerConnection.IceTransportsType.ALL
        }
        pc = factory?.createPeerConnection(config, object : PeerObserver() {
            override fun onIceCandidate(c: IceCandidate) {
                val out = JSONObject()
                    .put("candidate", c.sdp)
                    .put("sdpMid", c.sdpMid)
                    .put("sdpMLineIndex", c.sdpMLineIndex)
                signaling?.sendRtcSignal("ice", out)
            }
            override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) {
                val track = receiver.track()
                if (track is VideoTrack) {
                    _remoteTrack.value = track
                    signaling?.setStreaming(true)
                    negotiateJob?.cancel()
                    _ui.value = _ui.value.copy(stage = RemoteStage.STREAMING, message = "")
                    startStats()
                }
            }
            override fun onConnectionChange(newState: PeerConnection.PeerConnectionState) {
                when (newState) {
                    PeerConnection.PeerConnectionState.CONNECTED -> {
                        // 连上（含重连成功）：清掉重连状态，恢复投屏
                        reconnectTries = 0
                        reconnectJob?.cancel(); reconnectJob = null
                        if (_remoteTrack.value != null && _ui.value.stage != RemoteStage.STREAMING) {
                            _ui.value = _ui.value.copy(stage = RemoteStage.STREAMING, message = "")
                        } else if (_ui.value.message.isNotEmpty()) {
                            _ui.value = _ui.value.copy(message = "")
                        }
                    }
                    PeerConnection.PeerConnectionState.DISCONNECTED -> {
                        // DISCONNECTED 多为短暂网络波动，WebRTC 常能自愈：先提示 + 6s 宽限，超时仍没连上才主动重连
                        if (reconnectJob?.isActive != true) {
                            _ui.value = _ui.value.copy(message = "网络波动，正在恢复连接…")
                            reconnectJob = viewModelScope.launch {
                                delay(6000)
                                if (pc?.connectionState() != PeerConnection.PeerConnectionState.CONNECTED) reconnect()
                            }
                        }
                    }
                    PeerConnection.PeerConnectionState.FAILED -> {
                        // 彻底失败：立刻重连
                        reconnectJob?.cancel(); reconnectJob = null
                        reconnect()
                    }
                    else -> {}
                }
            }
        })
    }

    /** 断线自动重连：关掉旧 pc，向被控端重新请求一路新 offer；最多 3 次，仍失败则报错（其间 25s 协商超时会自动切中继兜底）。 */
    private fun reconnect() {
        val host = currentHostId
        if (host.isNullOrBlank()) { setError("连接已断开，请返回设备列表重连"); return }
        if (reconnectTries >= 3) { setError("多次重连失败，请返回设备列表重新连接"); return }
        reconnectTries++
        _ui.value = _ui.value.copy(stage = RemoteStage.NEGOTIATING, message = "连接断开，正在重连（第 $reconnectTries/3 次）…")
        try { pc?.dispose() } catch (_: Exception) {}
        pc = null
        _remoteTrack.value = null
        pendingIce.clear()
        signaling?.connectToHost(host)   // 触发被控端重新配对并发新 offer（onReady→createPeer）
        armNegotiateTimeout()
    }

    private fun flushPendingIce() {
        val peer = pc ?: return
        val it = pendingIce.iterator()
        while (it.hasNext()) { try { peer.addIceCandidate(it.next()) } catch (_: Exception) {}; it.remove() }
    }

    private fun setError(msg: String) {
        Timber.tag("RemoteVM").w(msg)
        _ui.value = _ui.value.copy(stage = RemoteStage.ERROR, message = msg)
    }

    override fun onCleared() {
        super.onCleared()
        disconnect()
        try { factory?.dispose() } catch (_: Exception) {}
        try { eglBase.release() } catch (_: Exception) {}
    }
}
