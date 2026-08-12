package com.hashmm.app.ui.remote

import com.hashmm.app.data.remote.SharedHttp

import android.content.Context
import android.graphics.BitmapFactory
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.hashmm.app.data.settings.SettingsStore
import com.hashmm.app.data.remote.IceServerConfig
import com.hashmm.app.data.remote.RemoteDevice
import com.hashmm.app.data.remote.RemoteRegistration
import com.hashmm.app.data.remote.RemoteSignalListener
import com.hashmm.app.data.remote.RemoteSignalingClient
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import io.github.jan.supabase.SupabaseClient
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
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
import org.webrtc.DataChannel
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
import org.webrtc.VideoSink
import org.webrtc.VideoFrame
import timber.log.Timber
import javax.inject.Inject
import java.util.concurrent.TimeUnit
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicBoolean

enum class RemoteStage {
    IDLE, CONNECTING, PICK_DEVICE, APPROVAL_PENDING, SESSION_AUTHORIZED,
    NEGOTIATING, WAITING_FIRST_FRAME, STREAMING, ERROR,
}

enum class RemoteNetworkHealth { GOOD, FAIR, POOR, UNKNOWN }

internal fun remoteNetworkHealth(latencyMs: Int, packetLossPct: Double): RemoteNetworkHealth = when {
    (packetLossPct >= 5.0) || (latencyMs >= 250) -> RemoteNetworkHealth.POOR
    (packetLossPct >= 1.0) || (latencyMs >= 120) -> RemoteNetworkHealth.FAIR
    packetLossPct >= 0.0 || latencyMs >= 0 -> RemoteNetworkHealth.GOOD
    else -> RemoteNetworkHealth.UNKNOWN
}

data class RemoteUiState(
    val stage: RemoteStage = RemoteStage.IDLE,
    val devices: List<RemoteDevice> = emptyList(),
    val message: String = "",
    val remoteW: Int = 0,
    val remoteH: Int = 0,
    val packetLossPct: Double = -1.0,
    val availableBitrateKbps: Int = -1,
    val framesPerSecond: Int = -1,
    val latencyMs: Int = -1,   // WebRTC 选中候选对 RTT（毫秒）；-1 = 未知
    val audioOn: Boolean = false,   // 是否转发被控电脑声音
    val quality: Int = 1,           // 画质档：0 流畅 / 1 高清 / 2 极清
    val relayOn: Boolean = false,   // 安全中转(Beta)：强制经 TURN
    val legacyRelayActive: Boolean = false, // WebRTC/TURN 均失败后的应急 HTTPS 帧中继
    val protocol: String = "",
    val ownerFingerprint: String = "",
    val deviceFingerprint: String = "",
    val leaseSeconds: Int = 0,
    val lastSnapshotAt: Long = 0L,
    val reconnectAttempt: Int = 0,
    val transportStage: String = "idle",
    val transportDetail: String = "",
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
    @param:ApplicationContext private val appContext: Context,
    private val settings: SettingsStore,
    private val supabase: SupabaseClient,
) : ViewModel(), RemoteSignalListener {

    private val _ui = MutableStateFlow(RemoteUiState())
    val ui: StateFlow<RemoteUiState> = _ui.asStateFlow()

    val eglBase: EglBase = EglBase.create()
    private var factory: PeerConnectionFactory? = null
    private var pc: PeerConnection? = null
    private var signaling: RemoteSignalingClient? = null
    private var remoteSessionId: String = ""
    private var remoteRelayTicket: String = ""
    private var grantedScopes: Set<String> = emptySet()
    private var controlChannel: DataChannel? = null
    private var pointerChannel: DataChannel? = null
    private val controlSequence = AtomicLong(0)
    private val firstFrameRendered = AtomicBoolean(false)
    private var firstFrameSink: VideoSink? = null

    private var iceServers: List<IceServerConfig> = emptyList()
    private val pendingIce = mutableListOf<IceCandidate>()
    private var statsJob: Job? = null
    private var lastTransportReportAt: Long = 0L
    private var devicePollJob: Job? = null     // PICK_DEVICE 时自动轮询在线设备，免手动刷新
    private var negotiateJob: Job? = null      // NEGOTIATING 超时兜底，避免一直卡在「正在协商」
    private var reconnectJob: Job? = null      // 断线后自动重连
    private var connectTimeoutJob: Job? = null
    private var reconnectTries = 0
    private var authRefreshAttempted = false
    private var legacyRelayAfterMs = 8_000L
    private var intentionalClose = false
    private var currentHostId: String? = null  // 当前所连被控端，用于断线重连
    private val connectGeneration = AtomicLong(0)
    private val mediaGeneration = AtomicLong(0)

    private val _remoteTrack = MutableStateFlow<VideoTrack?>(null)
    val remoteTrack: StateFlow<VideoTrack?> = _remoteTrack.asStateFlow()

    // 帧中继兜底：relayOn 时轮询后端最新 JPEG 帧并显示（P2P/TURN 打不通也能看画面）。
    private val _relayFrame = MutableStateFlow<ImageBitmap?>(null)
    val relayFrame: StateFlow<ImageBitmap?> = _relayFrame.asStateFlow()
    private var relayPollJob: Job? = null
    private val relayHttp = SharedHttp.base.newBuilder().callTimeout(8, TimeUnit.SECONDS).build()

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
    fun connect() = connectInternal(resetAttempts = true)

    private fun connectInternal(resetAttempts: Boolean) {
        val generation = connectGeneration.incrementAndGet()
        viewModelScope.launch {
            if (resetAttempts) reconnectTries = 0
            intentionalClose = true
            try { signaling?.close() } catch (_: Exception) {}
            signaling = null
            intentionalClose = false
            val token = supabase.auth.currentSessionOrNull()?.accessToken
            val base = settings.clientUrl.first().trim()
            if (generation != connectGeneration.get()) return@launch
            if (token.isNullOrBlank()) { setError("请先登录账号"); return@launch }
            if (base.isBlank()) { setError("请先连接 HashMM 服务"); return@launch }
            _ui.value = _ui.value.copy(stage = RemoteStage.CONNECTING, message = "正在验证账号与设备…",
                transportStage = "identity", transportDetail = "正在读取当前账号与服务地址")
            // Use an app-owned installation identity. ANDROID_ID is neither
            // necessary nor stable across every Android profile/reset path.
            val installationId = settings.clientInstanceId()
            signaling = RemoteSignalingClient(
                base, this@RemoteControlViewModel, "android-$installationId"
            ).also { it.connect(token) }
            connectTimeoutJob?.cancel()
            connectTimeoutJob = launch {
                delay(12_000)
                if (_ui.value.stage == RemoteStage.CONNECTING) {
                    setError("连接超时：公网入口可达，但设备信令未完成注册")
                }
            }
        }
    }

    fun refreshDevices() { signaling?.listDevices() }

    fun selectDevice(hostId: String) {
        devicePollJob?.cancel()
        currentHostId = hostId
        reconnectTries = 0
        reconnectJob?.cancel(); reconnectJob = null
        mediaGeneration.incrementAndGet()
        remoteSessionId = ""
        remoteRelayTicket = ""
        grantedScopes = emptySet()
        negotiateJob?.cancel(); negotiateJob = null
        _ui.value = _ui.value.copy(
            stage = RemoteStage.APPROVAL_PENDING,
            message = "正在向电脑发送连接请求…",
            transportStage = "approval-pending",
            transportDetail = "媒体协商将在电脑批准并签发会话票据后开始",
        )
        signaling?.connectToHost(hostId, turnOnly = _ui.value.relayOn)
    }

    /** 转发输入事件（坐标已由 UI 归一化到 0..1000）。 */
    fun sendInput(input: JSONObject) {
        val action = input.optString("action")
        val channel = if (action == "mouse_move" || action == "scroll") pointerChannel ?: controlChannel else controlChannel
        if (channel != null && channel.state() == DataChannel.State.OPEN && "control" in grantedScopes
            && action !in setOf("device", "system", "clipboard_set")) {
            val message = JSONObject(input.toString()).put("type", "input").put("sessionId", remoteSessionId)
                .put("seq", controlSequence.incrementAndGet()).put("timestamp", System.currentTimeMillis())
            if (channel.send(DataChannel.Buffer(ByteBuffer.wrap(message.toString().toByteArray(Charsets.UTF_8)), false))) return
        }
        signaling?.sendInput(input)
    }

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
        _ui.value = _ui.value.copy(
            relayOn = on,
            message = if (_ui.value.stage == RemoteStage.STREAMING || _ui.value.stage == RemoteStage.NEGOTIATING)
                "安全 TURN 偏好将在下次连接时生效" else _ui.value.message,
        )
    }

    private data class CompatFrameResult(val status: Int, val bytes: ByteArray? = null, val failure: String = "")

    /**
     * 兼容画面预览：仅使用批准会话签发的短时 viewer ticket。
     * 它不是与 TURN 并列的“高速中继”，只用于帮助用户看到画面并诊断 WebRTC；
     * 8 秒内没有首帧必须结束为稳定错误，禁止无限转圈。
     */
    private fun startRelayPoll() {
        if (relayPollJob?.isActive == true) return
        val generation = mediaGeneration.get()
        val sessionId = remoteSessionId
        val ticket = remoteRelayTicket
        if (sessionId.isBlank() || ticket.isBlank()) {
            setError("授权已通过，但媒体凭据签发失败，请重试本次连接", "TICKET_ISSUE_FAILED")
            return
        }
        _ui.value = _ui.value.copy(
            legacyRelayActive = true,
            stage = RemoteStage.WAITING_FIRST_FRAME,
            transportStage = "compat-preview",
            transportDetail = "端到端路径尚未出画面，正在启动兼容预览",
            message = "正在等待兼容预览首帧…",
        )
        signaling?.sendMilestone("fallback_started", JSONObject().put("stage", "compat_preview"))
        signaling?.requestCompatPreview()
        relayPollJob = viewModelScope.launch {
            val endpoint = settings.clientUrl.first().trim().trimEnd('/')
            if (endpoint.isBlank()) {
                setError("HashMM 服务地址不可用", "REMOTE_ENDPOINT_MISSING")
                return@launch
            }
            var refreshAt = System.currentTimeMillis() + 90_000
            val deadline = System.currentTimeMillis() + 8_000L
            var lastStatus = 0
            var firstDelivered = false
            while (isActive) {
                if (generation != mediaGeneration.get() || sessionId != remoteSessionId) return@launch
                if (System.currentTimeMillis() >= refreshAt) { signaling?.refreshTicket(); refreshAt = System.currentTimeMillis() + 90_000 }
                val currentTicket = remoteRelayTicket
                if (currentTicket.isBlank()) {
                    setError("媒体凭据已失效，无法继续兼容预览", "RELAY_TICKET_EXPIRED")
                    return@launch
                }
                val rb = Request.Builder().url("$endpoint/api/remote/relay/$sessionId/frame").get()
                    .header("Authorization", "Remote $currentTicket")
                val result = withContext(Dispatchers.IO) {
                    try {
                        relayHttp.newCall(rb.build()).execute().use { resp ->
                            CompatFrameResult(resp.code, if (resp.isSuccessful) resp.body?.bytes() else null)
                        }
                    } catch (e: Exception) { CompatFrameResult(0, failure = e.javaClass.simpleName) }
                }
                lastStatus = result.status
                if (result.status == 401 || result.status == 403) {
                    setError(if (result.status == 401) "兼容预览票据已失效" else "当前会话无权读取兼容预览",
                        if (result.status == 401) "RELAY_HTTP_401" else "RELAY_HTTP_403")
                    return@launch
                }
                val bytes = result.bytes
                if (bytes != null) {
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bmp != null) {
                        _relayFrame.value = bmp.asImageBitmap()
                        if (!firstDelivered) {
                            firstDelivered = true
                            signaling?.sendMilestone("fallback_first_frame", JSONObject().put("stage", "compat_preview")
                                .put("width", bmp.width).put("height", bmp.height))
                            _ui.value = _ui.value.copy(stage = RemoteStage.STREAMING, message = "",
                                transportStage = "compat-preview", transportDetail = "兼容预览已出画面")
                        }
                    }
                }
                if (!firstDelivered && System.currentTimeMillis() >= deadline) {
                    val code = when (lastStatus) {
                        404 -> "RELAY_HTTP_404"
                        0 -> "RELAY_REQUEST_TIMEOUT"
                        else -> "RELAY_HOST_NOT_PUSHING"
                    }
                    setError("兼容预览在 8 秒内没有收到首帧，请检查电脑抓屏权限和 TURN 配置", code)
                    return@launch
                }
                delay(250)
            }
        }
    }

    private fun stopRelayPoll() {
        relayPollJob?.cancel()
        relayPollJob = null
        _relayFrame.value = null
        _ui.value = _ui.value.copy(legacyRelayActive = false)
    }

    private fun detachRemoteTrack() {
        val sink = firstFrameSink
        val track = _remoteTrack.value
        if (sink != null && track != null) runCatching { track.removeSink(sink) }
        firstFrameSink = null
        firstFrameRendered.set(false)
        _remoteTrack.value = null
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
        // V254: 重启/关机改走协议认可的 device 动作（旧 action:"system" 被被控端白名单拒收
        // ——这就是"按钮点了没作用"的根因）。被控端 3 秒缓冲执行。
        "reboot" -> sendInput(JSONObject().put("action", "device").put("cmd", "restart"))
        "shutdown" -> sendInput(JSONObject().put("action", "device").put("cmd", "shutdown"))
        else -> sendInput(JSONObject().put("action", "device").put("cmd", cmd))
    }

    /** 把本机剪贴板文本推到远端（host 设置其剪贴板）。 */
    fun pushClipboard() {
        try {
            val cm = appContext.getSystemService(Context.CLIPBOARD_SERVICE) as? android.content.ClipboardManager
            val txt = cm?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(appContext)?.toString().orEmpty()
            if (txt.isNotBlank()) sendInput(JSONObject().put("action", "clipboard_set").put("text", txt))
        } catch (_: Exception) {}
    }

    // ── 远控安全偏好（真实生效）：断开后锁屏 / 断开后清空被控端剪贴板 ──
    val lockOnEnd: StateFlow<Boolean> = settings.remoteLockOnEnd
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)
    val wipeClipOnEnd: StateFlow<Boolean> = settings.remoteWipeClipOnEnd
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    fun setLockOnEnd(on: Boolean) { viewModelScope.launch { settings.setRemoteLockOnEnd(on) } }

    fun setWipeClipOnEnd(on: Boolean) { viewModelScope.launch { settings.setRemoteWipeClipOnEnd(on) } }

    /** 用户主动退出投屏：按安全偏好善后（清剪贴板/锁屏），再回设备列表。 */
    fun exitStreaming() {
        try {
            if (wipeClipOnEnd.value) sendInput(JSONObject().put("action", "clipboard_set").put("text", ""))
            if (lockOnEnd.value) sendKey("win+l")
        } catch (_: Exception) {}
        backToDeviceList()
    }

    /** 拖选/拖动：一次完整的 left_click_drag（起点→终点，0..1000 内容区坐标）。 */
    fun sendDrag(x: Int, y: Int, x2: Int, y2: Int) = sendInput(
        JSONObject().put("action", "left_click_drag").put("x", x).put("y", y).put("x2", x2).put("y2", y2)
    )

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

    /** 直连与 TURN 在服务端给定的宽限期内均未出画面，才启用旧 HTTPS 帧中继。 */
    private fun armNegotiateTimeout() {
        negotiateJob?.cancel()
        val generation = mediaGeneration.get()
        val sessionId = remoteSessionId
        if (sessionId.isBlank() || remoteRelayTicket.isBlank() || "view" !in grantedScopes) {
            setError("授权已通过，但媒体凭据不完整", "TICKET_ISSUE_FAILED")
            return
        }
        negotiateJob = viewModelScope.launch {
            delay(legacyRelayAfterMs)
            if (generation == mediaGeneration.get() && sessionId == remoteSessionId &&
                (_ui.value.stage == RemoteStage.NEGOTIATING || _ui.value.stage == RemoteStage.WAITING_FIRST_FRAME)) {
                startRelayPoll()
            }
        }
    }

    fun disconnect() {
        connectGeneration.incrementAndGet()
        intentionalClose = true
        connectTimeoutJob?.cancel(); connectTimeoutJob = null
        try { reconnectJob?.cancel() } catch (_: Exception) {}
        reconnectJob = null; reconnectTries = 0; currentHostId = null
        try { stopRelayPoll() } catch (_: Exception) {}
        try { devicePollJob?.cancel() } catch (_: Exception) {}
        try { negotiateJob?.cancel() } catch (_: Exception) {}
        devicePollJob = null; negotiateJob = null
        try { statsJob?.cancel() } catch (_: Exception) {}
        statsJob = null
        try { signaling?.completeSession("user_finished_remote_work") } catch (_: Exception) {}
        try { signaling?.setStreaming(false) } catch (_: Exception) {}
        try { pc?.dispose() } catch (_: Exception) {}
        pc = null
        try { signaling?.close() } catch (_: Exception) {}
        signaling = null
        try { controlChannel?.dispose() } catch (_: Exception) {}
        try { pointerChannel?.dispose() } catch (_: Exception) {}
        controlChannel = null; pointerChannel = null; grantedScopes = emptySet(); remoteSessionId = ""; remoteRelayTicket = ""; controlSequence.set(0)
        detachRemoteTrack()
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
        try { signaling?.completeSession("user_returned_to_device_list") } catch (_: Exception) {}
        try { signaling?.setStreaming(false) } catch (_: Exception) {}
        try { pc?.dispose() } catch (_: Exception) {}
        pc = null; controlChannel = null; pointerChannel = null; controlSequence.set(0)
        detachRemoteTrack()
        pendingIce.clear()
        if (signaling != null) {
            // 信令还在 → 直接回到设备列表并重新轮询在线设备
            _ui.value = _ui.value.copy(stage = RemoteStage.PICK_DEVICE, message = "", latencyMs = -1, devices = _ui.value.devices)
            try { signaling?.listDevices() } catch (_: Exception) {}
            startDevicePoll()
        } else {
            // 信令断了 → 重新建立（会再走到 PICK_DEVICE）
            connectInternal(resetAttempts = false)
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
                        var bitrateKbps = -1
                        var fps = -1
                        var receivedPackets = 0L
                        var lostPackets = 0L
                        var selectedPair: Map<String, Any>? = null
                        val statsById = report.statsMap
                        for (s in report.statsMap.values) {
                            if (s.type == "candidate-pair") {
                                val m = s.members
                                val nominated = (m["nominated"] as? Boolean) ?: false
                                val st = m["state"] as? String
                                val rtt = (m["currentRoundTripTime"] as? Number)?.toDouble()
                                if (rtt != null && (nominated || st == "succeeded")) {
                                    selectedPair = m
                                    rttMs = (rtt * 1000).toInt().coerceIn(0, 60_000)
                                    val bitrate = (m["availableIncomingBitrate"] as? Number)?.toDouble()
                                        ?: (m["availableOutgoingBitrate"] as? Number)?.toDouble()
                                    if (bitrate != null && bitrate.isFinite()) {
                                        bitrateKbps = (bitrate / 1000.0).toInt().coerceIn(0, 10_000_000)
                                    }
                                }
                            }
                            if (s.type == "inbound-rtp") {
                                val m = s.members
                                val media = (m["kind"] ?: m["mediaType"])?.toString()
                                if (media == "video") {
                                    receivedPackets += ((m["packetsReceived"] as? Number)?.toLong() ?: 0L).coerceAtLeast(0L)
                                    lostPackets += ((m["packetsLost"] as? Number)?.toLong() ?: 0L).coerceAtLeast(0L)
                                    val measuredFps = (m["framesPerSecond"] as? Number)?.toDouble()
                                    if (measuredFps != null && measuredFps.isFinite()) {
                                        fps = measuredFps.toInt().coerceIn(0, 240)
                                    }
                                }
                            }
                        }
                        val totalPackets = receivedPackets + lostPackets
                        val lossPct = if (totalPackets > 0L) {
                            (lostPackets.toDouble() * 100.0 / totalPackets.toDouble()).coerceIn(0.0, 100.0)
                        } else -1.0
                        _ui.value = _ui.value.copy(
                            latencyMs = rttMs,
                            packetLossPct = lossPct,
                            availableBitrateKbps = bitrateKbps,
                            framesPerSecond = fps,
                        )
                        val now = System.currentTimeMillis()
                        if (now - lastTransportReportAt >= 60_000L) {
                            val pair = selectedPair
                            val localId = pair?.get("localCandidateId")?.toString()
                            val local = localId?.let { statsById[it]?.members }
                            val candidateType = local?.get("candidateType")?.toString() ?: "unknown"
                            val protocol = (local?.get("protocol") ?: local?.get("relayProtocol"))?.toString() ?: "unknown"
                            val payload = JSONObject().put("candidateType", candidateType).put("protocol", protocol)
                            if (rttMs >= 0) payload.put("rttMs", rttMs)
                            if (lossPct >= 0) payload.put("packetLossPct", lossPct)
                            (pair?.get("bytesSent") as? Number)?.toLong()?.let { payload.put("bytesSent", it) }
                            (pair?.get("bytesReceived") as? Number)?.toLong()?.let { payload.put("bytesReceived", it) }
                            signaling?.sendTransportReport(payload)
                            if (lastTransportReportAt == 0L) {
                                signaling?.sendMilestone("candidate_pair_selected", JSONObject(payload.toString())
                                    .put("stage", "ice"))
                            }
                            lastTransportReportAt = now
                        }
                    }
                } catch (_: Exception) {}
                delay(2000)
            }
        }
    }

    // ── RemoteSignalListener ──
    override fun onAuthOk(iceServers: List<IceServerConfig>) {
        connectTimeoutJob?.cancel(); connectTimeoutJob = null
        reconnectTries = 0
        authRefreshAttempted = false
        this.iceServers = iceServers
        _ui.value = _ui.value.copy(stage = RemoteStage.PICK_DEVICE, message = "")
        signaling?.listDevices()
        startDevicePoll()
    }

    override fun onDevices(devices: List<RemoteDevice>) {
        _ui.value = _ui.value.copy(devices = devices, lastSnapshotAt = System.currentTimeMillis())
    }

    override fun onRegistered(registration: RemoteRegistration) {
        _ui.value = _ui.value.copy(
            protocol = registration.protocol,
            ownerFingerprint = registration.ownerFingerprint,
            deviceFingerprint = registration.deviceFingerprint,
            leaseSeconds = registration.leaseExpiresIn,
            reconnectAttempt = 0,
            transportStage = "registered",
            transportDetail = "查看端已注册，正在读取同账号电脑",
        )
    }

    override fun onTransportStage(stage: String, detail: String) {
        _ui.value = _ui.value.copy(transportStage = stage, transportDetail = detail)
    }

    override fun onPermissionPending() {
        negotiateJob?.cancel(); negotiateJob = null
        _ui.value = _ui.value.copy(
            stage = RemoteStage.APPROVAL_PENDING,
            message = "等待电脑确认本次连接权限…",
            transportStage = "approval-pending",
            transportDetail = "尚未开始画面协商，也不会提前启动兼容预览",
        )
    }

    override fun onReady(sessionId: String, ticket: String, scopes: Set<String>, legacyRelayAfterMs: Long) {
        if (sessionId.isBlank() || ticket.isBlank() || "view" !in scopes) {
            setError("授权已通过，但媒体凭据签发失败，请重新发起连接", "TICKET_ISSUE_FAILED")
            return
        }
        mediaGeneration.incrementAndGet()
        remoteSessionId = sessionId; remoteRelayTicket = ticket; grantedScopes = scopes; controlSequence.set(0); lastTransportReportAt = 0L
        this.legacyRelayAfterMs = legacyRelayAfterMs.coerceIn(3_000L, 30_000L)
        signaling?.sendMilestone("permission_approved", JSONObject().put("stage", "permission_approved"))
        // host 已配对，建 PeerConnection 等待 host 的 offer
        createPeer()
        _ui.value = _ui.value.copy(
            stage = RemoteStage.SESSION_AUTHORIZED,
            message = "连接已获授权，正在准备画面协商…",
            transportStage = "session-authorized",
            transportDetail = "会话票据已签发",
        )
        _ui.value = _ui.value.copy(stage = RemoteStage.NEGOTIATING, message = "正在协商画面…")
        armNegotiateTimeout()
    }

    override fun onRemoteTicket(sessionId: String, ticket: String) {
        if (sessionId == remoteSessionId) remoteRelayTicket = ticket
    }

    override fun onMeta(remoteW: Int, remoteH: Int) {
        if (remoteW > 0 && remoteH > 0) _ui.value = _ui.value.copy(remoteW = remoteW, remoteH = remoteH)
    }

    override fun onRtcSignal(kind: String, data: JSONObject) {
        val peer = pc ?: run { createPeer(); pc!! }
        when (kind) {
            "offer" -> {
                signaling?.sendMilestone("offer_received", JSONObject().put("stage", "signaling"))
                val sdp = data.optString("sdp")
                peer.setRemoteDescription(object : SimpleSdp() {
                    override fun onSetSuccess() {
                        flushPendingIce()
                        peer.createAnswer(object : SimpleSdp() {
                            override fun onCreateSuccess(desc: SessionDescription) {
                                peer.setLocalDescription(SimpleSdp(), desc)
                                signaling?.sendMilestone("answer_created", JSONObject().put("stage", "signaling"))
                                val out = JSONObject().put("type", "answer").put("sdp", desc.description)
                                signaling?.sendRtcSignal("answer", out)
                            }
                            override fun onCreateFailure(error: String?) {
                                setError("无法创建远程应答", "ANSWER_NOT_DELIVERED")
                            }
                        }, MediaConstraints())
                    }
                    override fun onSetFailure(error: String?) {
                        setError("无法应用电脑端画面协商", "OFFER_NOT_DELIVERED")
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
        connectTimeoutJob?.cancel(); connectTimeoutJob = null
        if (intentionalClose || _ui.value.stage == RemoteStage.IDLE) return
        if (reconnectJob?.isActive == true) return
        val authenticationFailure = reason.startsWith("REMOTE_BOOTSTRAP_401") ||
            reason.startsWith("socket_ticket_401") || reason.contains("invalid_token") ||
            reason.contains("REMOTE_AUTH_REJECTED")
        if (authenticationFailure && !authRefreshAttempted) {
            authRefreshAttempted = true
            _ui.value = _ui.value.copy(
                stage = RemoteStage.CONNECTING,
                message = "登录状态正在安全续期…",
                transportStage = "identity-refresh",
                transportDetail = "远程控制面收到 401，正在单次刷新 Supabase 会话",
            )
            reconnectJob = viewModelScope.launch {
                val refreshed = runCatching {
                    supabase.auth.refreshCurrentSession()
                    supabase.auth.currentSessionOrNull()?.accessToken
                }.getOrNull()
                reconnectJob = null
                if (refreshed.isNullOrBlank()) {
                    setError("登录凭据已失效，请重新登录")
                } else {
                    connectInternal(resetAttempts = false)
                }
            }
            return
        }
        val normalized = when {
            authenticationFailure -> "登录凭据已失效，请重新登录"
            reason.startsWith("socket_ticket_403") -> "当前账号无权使用远程设备"
            reason.contains("secure_transport_required") -> "服务器要求 HTTPS/WSS 安全连接"
            reason.contains("deviceReplaced") -> "本设备的旧连接已被新连接替换"
            else -> "远程信令已断开：$reason"
        }
        if (reconnectTries >= 5 || normalized.startsWith("登录凭据") || normalized.contains("无权")) {
            setError(normalized)
            return
        }
        reconnectTries += 1
        val attempt = reconnectTries
        _ui.value = _ui.value.copy(
            stage = RemoteStage.CONNECTING,
            message = "连接中断，正在第 $attempt 次恢复…",
            reconnectAttempt = attempt,
        )
        reconnectJob = viewModelScope.launch {
            delay(listOf(1_000L, 2_000L, 4_000L, 8_000L, 15_000L)[attempt - 1])
            try { signaling?.close() } catch (_: Exception) {}
            signaling = null
            connectInternal(resetAttempts = false)
        }
    }

    // ── WebRTC ──
    private fun createPeer() {
        if (pc != null) return
        // ICE 只接受 HashMM 后端的受控配置；不再内置第三方公共 TURN 账号。
        val rtcIce = ArrayList<PeerConnection.IceServer>()
        iceServers.forEach {
            val b = PeerConnection.IceServer.builder(it.urls)
            if (!it.username.isNullOrBlank()) b.setUsername(it.username)
            if (!it.credential.isNullOrBlank()) b.setPassword(it.credential)
            rtcIce.add(b.createIceServer())
        }
        if (rtcIce.isEmpty()) rtcIce.add(PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer())
        val config = PeerConnection.RTCConfiguration(rtcIce).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_CONTINUALLY
            iceCandidatePoolSize = 2
            // 默认按 host→srflx→relay 的 ICE 顺序选路；用户显式开启
            // “安全中转”时只发布 TURN 候选。旧 HTTPS/JPEG 是另一条应急路径。
            iceTransportsType = if (_ui.value.relayOn) PeerConnection.IceTransportsType.RELAY
                else PeerConnection.IceTransportsType.ALL
        }
        val candidateCounts = mutableMapOf("host" to 0, "srflx" to 0, "prflx" to 0, "relay" to 0)
        pc = factory?.createPeerConnection(config, object : PeerObserver() {
            override fun onDataChannel(dataChannel: DataChannel?) {
                when (dataChannel?.label()) {
                    "hashmm-control-v1" -> controlChannel = dataChannel
                    "hashmm-pointer-v1" -> pointerChannel = dataChannel
                }
            }
            override fun onIceCandidate(candidate: IceCandidate) {
                Regex(" typ (host|srflx|prflx|relay)(?: |$)").find(candidate.sdp)?.groupValues?.getOrNull(1)?.let {
                    candidateCounts[it] = (candidateCounts[it] ?: 0) + 1
                }
                val out = JSONObject()
                    .put("candidate", candidate.sdp)
                    .put("sdpMid", candidate.sdpMid)
                    .put("sdpMLineIndex", candidate.sdpMLineIndex)
                signaling?.sendRtcSignal("ice", out)
            }
            override fun onIceGatheringChange(newState: PeerConnection.IceGatheringState?) {
                if (newState == PeerConnection.IceGatheringState.COMPLETE) {
                    val counts = JSONObject()
                    candidateCounts.forEach { (kind, count) -> counts.put(kind, count) }
                    signaling?.sendMilestone("candidates_gathered", JSONObject().put("stage", "ice")
                        .put("candidateCounts", counts))
                }
            }
            override fun onAddTrack(receiver: RtpReceiver, streams: Array<out MediaStream>) {
                val track = receiver.track()
                if (track is VideoTrack) {
                    detachRemoteTrack()
                    _remoteTrack.value = track
                    signaling?.sendMilestone("track_received", JSONObject().put("stage", "media"))
                    _ui.value = _ui.value.copy(stage = RemoteStage.WAITING_FIRST_FRAME,
                        message = "媒体通道已连接，正在等待首帧…", transportStage = "first-frame",
                        transportDetail = "已收到视频轨，等待解码后的首帧")
                    val sink = object : VideoSink {
                        override fun onFrame(frame: VideoFrame) {
                            if (!firstFrameRendered.compareAndSet(false, true)) return
                            viewModelScope.launch {
                                negotiateJob?.cancel()
                                stopRelayPoll()
                                signaling?.setStreaming(true)
                                signaling?.sendMilestone("first_frame_rendered", JSONObject().put("stage", "media")
                                    .put("width", frame.rotatedWidth).put("height", frame.rotatedHeight))
                                _ui.value = _ui.value.copy(stage = RemoteStage.STREAMING, message = "",
                                    transportStage = "direct", transportDetail = "端到端首帧已渲染")
                            }
                        }
                    }
                    firstFrameSink = sink
                    track.addSink(sink)
                    startStats()
                }
            }
            override fun onConnectionChange(newState: PeerConnection.PeerConnectionState) {
                when (newState) {
                    PeerConnection.PeerConnectionState.CONNECTED -> {
                        signaling?.sendMilestone("dtls_connected", JSONObject().put("stage", "dtls"))
                        // 连上（含重连成功）：清掉重连状态，恢复投屏
                        reconnectTries = 0
                        reconnectJob?.cancel(); reconnectJob = null
                        if (_remoteTrack.value != null && !firstFrameRendered.get()) {
                            _ui.value = _ui.value.copy(stage = RemoteStage.WAITING_FIRST_FRAME,
                                message = "媒体通道已连接，正在等待首帧…")
                        } else if (_ui.value.message.isNotEmpty() && _ui.value.stage == RemoteStage.STREAMING) {
                            _ui.value = _ui.value.copy(message = "")
                        }
                    }
                    PeerConnection.PeerConnectionState.DISCONNECTED -> {
                        // DISCONNECTED 多为短暂网络波动，先给 ICE 自愈窗口；
                        // 未恢复时沿用当前批准会话启动应急画面，避免重新审批/忙碌冲突。
                        if (reconnectJob?.isActive != true) {
                            _ui.value = _ui.value.copy(message = "网络波动，正在恢复连接…")
                            reconnectJob = viewModelScope.launch {
                                delay(3000)
                                if (pc?.connectionState() != PeerConnection.PeerConnectionState.CONNECTED) startRelayPoll()
                            }
                        }
                    }
                    PeerConnection.PeerConnectionState.FAILED -> {
                        // ICE 已穷尽：保持当前权限会话，用应急 HTTPS 帧中继恢复画面。
                        signaling?.sendMilestone("terminal_error", JSONObject().put("stage", "ice")
                            .put("errorCode", "ICE_ALL_PAIRS_FAILED"))
                        reconnectJob?.cancel(); reconnectJob = null
                        startRelayPoll()
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
        detachRemoteTrack()
        pendingIce.clear()
        mediaGeneration.incrementAndGet()
        remoteSessionId = ""; remoteRelayTicket = ""; grantedScopes = emptySet()
        negotiateJob?.cancel(); negotiateJob = null
        _ui.value = _ui.value.copy(
            stage = RemoteStage.APPROVAL_PENDING,
            message = "连接断开，正在重新申请本次连接权限（第 $reconnectTries/3 次）…",
            transportStage = "approval-pending",
        )
        signaling?.connectToHost(host)   // 重新配对；仅在新的 onReady 之后启动媒体计时器
    }

    private fun flushPendingIce() {
        val peer = pc ?: return
        val it = pendingIce.iterator()
        while (it.hasNext()) { try { peer.addIceCandidate(it.next()) } catch (_: Exception) {}; it.remove() }
    }

    private fun setError(msg: String, errorCode: String = "REMOTE_INTERNAL_ERROR") {
        Timber.tag("RemoteVM").w(msg)
        signaling?.sendMilestone("terminal_error", JSONObject().put("stage", _ui.value.transportStage)
            .put("errorCode", errorCode))
        _ui.value = _ui.value.copy(stage = RemoteStage.ERROR, message = msg)
    }

    override fun onCleared() {
        super.onCleared()
        disconnect()
        try { factory?.dispose() } catch (_: Exception) {}
        try { eglBase.release() } catch (_: Exception) {}
    }
}
