package com.hashmm.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ChatLiveRepository
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID
import javax.inject.Inject

data class ChatHomeUiState(
    val convId: String? = null,        // null = 首页空态（小哈），非空 = 正在某个对话里
    val messages: List<ChatMessage> = emptyList(),
    val sending: Boolean = false,
    val loadingHistory: Boolean = false,
    val error: String? = null,
    // V251 执行体（全局持久，SettingsStore）+ 直连徽标
    val execMode: String = "auto",
    val directMode: Boolean = false,
    val directPersisted: Boolean = false,
    val taskContract: String = "",
    val liveTodo: String = "",
    val liveProgress: String = "",
    val liveStepCount: Int = 0,
    val activeTurnId: String = "",
    val turnSteerable: Boolean = false,
    val steering: Boolean = false,
    val interrupting: Boolean = false,
)

/**
 * 首页内联对话：发消息直接在首页内展开会话，不跳独立页（对标 Marvis）。
 * 新会话首发时本地生成 convId，后端首条消息时建库；历史会话可载入内联继续。
 */
@HiltViewModel
class ChatHomeViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val liveRepo: ChatLiveRepository,
    private val liveManager: com.hashmm.app.data.remote.LiveChatManager,
    private val sttRepo: com.hashmm.app.data.remote.SttRepository,
    private val direct: com.hashmm.app.data.remote.DirectLlmRepository,
    private val settings: com.hashmm.app.data.settings.SettingsStore,
) : ViewModel() {
    private val _ui = MutableStateFlow(ChatHomeUiState())
    val ui: StateFlow<ChatHomeUiState> = _ui.asStateFlow()

    /** 语音兜底：录音文件上传后端 /api/stt 转文字（设备无系统语音服务时用），结果回主线程。 */
    fun transcribeAudio(audio: java.io.File, onResult: (String?) -> Unit) {
        viewModelScope.launch {
            val text = runCatching { sttRepo.transcribe(audio) }.getOrNull()
            onResult(text)
        }
    }

    private var sendJob: Job? = null      // 当前进行中的流式请求，用于「停止生成」
    private var lastUserText: String = "" // 最后一条用户输入，用于重试/重新生成
    private var observedManagedStream = false

    /** 本地乐观消息的时间戳（UTC ISO，和 Supabase 一致，便于气泡统一格式化成本地 HH:mm）。 */
    private fun nowIso(): String =
        java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
            .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date())

    // App 内置文件查看器 URL（非空=全屏 WebView 查看文件，不跳浏览器）
    private val _viewerUrl = MutableStateFlow<String?>(null)
    val viewerUrl: StateFlow<String?> = _viewerUrl.asStateFlow()
    fun openFile(downloadUrl: String, filename: String) {
        viewModelScope.launch {
            _viewerUrl.value = liveRepo.fileViewUrl(downloadUrl, filename) ?: downloadUrl
        }
    }
    fun closeViewer() { _viewerUrl.value = null }

    /** 回到首页空态（新对话）。 */
    fun newChat() {
        msgSubJob?.cancel(); msgSubJob = null
        realtimeRefreshJob?.cancel(); realtimeRefreshJob = null
        openConversationJob?.cancel(); openConversationJob = null
        _ui.value = ChatHomeUiState()
    }

    // ── 无感同步（对标 Claude/Codex）：内联会话订阅 Supabase Realtime，
    //    桌面端/其它设备发的消息实时回流到首页，无需手动刷新。──
    private var msgSubJob: Job? = null
    private var realtimeRefreshJob: Job? = null
    private var openConversationJob: Job? = null
    private val realtimeRefreshMutex = Mutex()

    private fun subscribeInline(convId: String) {
        msgSubJob?.cancel()
        msgSubJob = viewModelScope.launch {
            runCatching {
                sync.subscribeMessages(convId) { scheduleRealtimeRefresh(convId) }
            }
        }
    }

    private fun scheduleRealtimeRefresh(convId: String) {
        realtimeRefreshJob?.cancel()
        realtimeRefreshJob = viewModelScope.launch {
            // A completed turn updates several columns and may emit a burst of
            // Realtime events. Collapse the burst into one incremental sync.
            delay(400L)
            refreshInline(convId)
        }
    }

    private suspend fun refreshInline(convId: String) {
        realtimeRefreshMutex.withLock {
            if (_ui.value.convId != convId) return@withLock
            if (_ui.value.messages.any { it.status == "streaming" }) return@withLock
            // Realtime already tells us Supabase changed. Pull only that
            // source; calling the backend here caused one HTTP request per
            // Realtime event and could overlap dozens of slow requests.
            val synced = runCatching { sync.syncMessages(convId) }.getOrDefault(emptyList())
            if (synced.isNotEmpty() && _ui.value.convId == convId) {
                _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(synced))
            }
        }
    }

    /** 载入一个历史会话到内联视图继续。 */
    fun openConversation(convId: String) {
        if (convId.isBlank()) return
        if (_ui.value.convId == convId && openConversationJob?.isActive == true) return
        openConversationJob?.cancel()
        _ui.value = ChatHomeUiState(convId = convId, loadingHistory = true)
        subscribeInline(convId)
        openConversationJob = viewModelScope.launch {
            val cached = runCatching { sync.cachedMessages(convId) }.getOrDefault(emptyList())
            if (cached.isNotEmpty() && _ui.value.convId == convId) {
                _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(cached))
            }
            val synced = runCatching { sync.syncMessages(convId) }.getOrDefault(cached)
            if (_ui.value.convId != convId) return@launch
            _ui.value = _ui.value.copy(loadingHistory = false, messages = ChatMessageOps.sortChronological(synced.ifEmpty { cached }))
            val fresh = liveRepo.getMessages(convId)
            if (!fresh.isNullOrEmpty() && _ui.value.convId == convId) {
                _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(fresh))
                var pollAttempt = 0
                while (isActive && _ui.value.messages.any { it.status == "streaming" }) {
                    delay(if (pollAttempt < 6) 3_000L else 5_000L)
                    pollAttempt += 1
                    if (_ui.value.convId != convId) break
                    val f = liveRepo.getMessages(convId) ?: break
                    _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(f))
                }
            }
        }
    }

    init {
        // V251：执行体为全局持久设置，与会话详情页共用同一值
        viewModelScope.launch {
            settings.execMode.collect { m -> _ui.value = _ui.value.copy(execMode = m) }
        }
        viewModelScope.launch {
            liveManager.streams.collect { streams ->
                val activeConv = _ui.value.convId
                val live = activeConv?.let { streams[it] }
                if (live != null) observedManagedStream = true
                val managedFinished = live == null && observedManagedStream
                _ui.value = _ui.value.copy(
                    sending = when {
                        live != null -> live.streaming
                        managedFinished -> false
                        else -> _ui.value.sending
                    },
                    taskContract = live?.taskContract.orEmpty(),
                    liveTodo = live?.todo.orEmpty(),
                    liveProgress = live?.progress.orEmpty(),
                    liveStepCount = live?.stepCount ?: 0,
                    activeTurnId = live?.turnId.orEmpty(),
                    turnSteerable = live?.turnSteerable == true,
                    steering = if (live == null) false else _ui.value.steering,
                    interrupting = if (live == null) false else _ui.value.interrupting,
                )
                if (managedFinished) observedManagedStream = false
            }
        }
    }

    fun setExecMode(mode: String) {
        if (mode !in listOf("auto", "backend", "direct")) return
        viewModelScope.launch { settings.setExecMode(mode) }
    }

    /** 手机直连回答（同一个 pending 气泡续流）。返回错误信息，空＝成功；成功轮次 upsert 入 Supabase。 */
    private suspend fun runDirect(msg: String, pendingId: String): String {
        val hist = _ui.value.messages
            .filter { it.id != pendingId && it.content.isNotBlank() }
            .map { it.role to it.content } + ("user" to msg)
        val acc2 = StringBuilder()
        val derr = direct.streamChat(hist, _ui.value.convId.orEmpty()) { token ->
            acc2.append(token)
            val cur = acc2.toString()
            _ui.value = _ui.value.copy(directMode = true, messages = _ui.value.messages.map {
                if (it.id == pendingId) it.copy(content = cur) else it
            })
        }
        if (derr.isBlank() && acc2.isNotBlank()) {
            _ui.value = _ui.value.copy(
                messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(status = "complete") else it
                },
                sending = false, directMode = true, directPersisted = false, error = null,
            )
            val delivered = runCatching {   // 只有后端返回写入回执才可声明已同步
                val iso = nowIso()
                val ms = _ui.value.messages
                val rows = listOfNotNull(
                    ms.lastOrNull { it.role == "user" && it.content == msg },
                    ms.lastOrNull { it.id == pendingId },
                ).map { it.copy(createdAt = it.createdAt.ifBlank { iso }, updatedAt = iso, status = "complete") }
                sync.upsertMessages(rows)
            }.getOrDefault(false)
            _ui.value = _ui.value.copy(directPersisted = delivered)
            return ""
        }
        return derr.ifBlank { "直连没有返回内容" }
    }

    /** 发送（首页内联）：无会话则新建，乐观插入 + SSE 逐字，完成再拉规范列表，绝不重复请求。 */
    fun send(text: String) {
        val msg = text.trim()
        if (msg.isBlank()) return
        if (_ui.value.sending) {
            steerCurrentTurn(msg)
            return
        }
        lastUserText = msg
        val convId = _ui.value.convId ?: UUID.randomUUID().toString()
        if (_ui.value.convId == null) subscribeInline(convId)   // 新会话：从第一条起就无感同步
        val now = System.currentTimeMillis()
        val ts = nowIso()
        val userMsg = ChatMessage(id = "u-$now", convId = convId, role = "user", content = msg, status = "complete", createdAt = ts)
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", status = "streaming", createdAt = ts)
        _ui.value = _ui.value.copy(
            convId = convId,
            messages = _ui.value.messages + userMsg + pending,
            sending = true, directPersisted = false, error = null,
        )
        streamResponse(convId, msg, pendingId)
    }

    private fun steerCurrentTurn(text: String) {
        val content = text.trim()
        val convId = _ui.value.convId ?: return
        if (content.isBlank() || _ui.value.steering) return
        if (!_ui.value.turnSteerable) {
            _ui.value = _ui.value.copy(error = "当前任务正在建立运行通道或已进入收尾，请稍后再试")
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(steering = true, error = null)
            val result = liveManager.steer(convId, content, UUID.randomUUID().toString())
            if (result?.accepted == true) {
                liveRepo.invalidateMessages(convId)
                liveRepo.getMessages(convId)?.let { fresh ->
                    _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(fresh))
                }
            } else {
                _ui.value = _ui.value.copy(error = "追加要求未被接受，任务可能已进入收尾")
            }
            _ui.value = _ui.value.copy(steering = false)
        }
    }

    fun decideToolApproval(requestId: String, approve: Boolean) {
        val convId = _ui.value.convId ?: return
        if (requestId.isBlank() || _ui.value.sending) return
        viewModelScope.launch {
            val status = liveRepo.decideToolApproval(convId, requestId, approve)
            if (status == null) {
                _ui.value = _ui.value.copy(error = "审批失败，请检查与桌面端的连接")
                return@launch
            }
            val fresh = liveRepo.getMessages(convId)
            if (!fresh.isNullOrEmpty()) {
                _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(fresh), error = null)
            }
            if (approve && status == "approved") {
                send("继续执行已批准的操作。只执行刚才批准的原始工具和参数。")
            }
        }
    }

    fun submitMessageFeedback(messageId: String, rating: String, reasonCode: String, comment: String) {
        val convId = _ui.value.convId ?: return
        if (messageId.isBlank()) return
        viewModelScope.launch {
            val ok = liveRepo.submitMessageFeedback(convId, messageId, rating, reasonCode, comment)
            if (!ok) {
                _ui.value = _ui.value.copy(error = "反馈提交失败，请检查服务器连接")
                return@launch
            }
            liveRepo.getMessages(convId)?.let { fresh ->
                _ui.value = _ui.value.copy(
                    messages = ChatMessageOps.sortChronological(fresh),
                    error = null,
                )
            }
        }
    }

    /** 优先通知后端协作式停止，等待服务端保存部分结果并广播到所有客户端。 */
    fun stop() {
        val convId = _ui.value.convId ?: return
        if (_ui.value.execMode != "direct" && _ui.value.activeTurnId.isNotBlank()) {
            viewModelScope.launch {
                _ui.value = _ui.value.copy(interrupting = true, turnSteerable = false, error = null)
                if (!liveManager.interrupt(convId)) hardCancelGeneration(convId)
            }
            return
        }
        hardCancelGeneration(convId)
    }

    /** 后端没有活动 turn 或手机直连时的传输级兜底。 */
    private fun hardCancelGeneration(convId: String) {
        _ui.value.convId?.let { liveManager.cancel(it) }
        direct.cancel(convId)
        sendJob?.cancel(); sendJob = null
        _ui.value = _ui.value.copy(
            messages = _ui.value.messages.mapNotNull {
                if (it.role == "assistant" && it.status == "streaming") {
                    if (it.content.isBlank()) null else it.copy(status = "interrupted")
                } else it
            },
            sending = false,
            taskContract = "",
            liveTodo = "",
            liveProgress = "",
            liveStepCount = 0,
            activeTurnId = "",
            turnSteerable = false,
            steering = false,
            interrupting = false,
        )
    }

    /** 出错后重试：对最后一条用户消息重新请求（不重复插入用户消息）。 */
    fun retryLast() {
        if (_ui.value.sending) return
        val convId = _ui.value.convId ?: return
        val lastUser = _ui.value.messages.lastOrNull { it.role == "user" } ?: return
        val now = System.currentTimeMillis()
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", status = "streaming")
        val cleaned = _ui.value.messages.filterNot { it.role == "assistant" && it.status == "error" }
        _ui.value = _ui.value.copy(messages = cleaned + pending, sending = true, error = null)
        streamResponse(convId, lastUser.content, pendingId)
    }

    /** 重新生成：移除最后一条助手回复，对最后一条用户消息重新请求（同一问题再答一次）。 */
    fun regenerate() {
        if (_ui.value.sending) return
        val convId = _ui.value.convId ?: return
        val msgs = _ui.value.messages
        val lastUser = msgs.lastOrNull { it.role == "user" } ?: return
        val lastAssistantIdx = msgs.indexOfLast { it.role == "assistant" }
        val trimmed = if (lastAssistantIdx >= 0) msgs.filterIndexed { i, _ -> i != lastAssistantIdx } else msgs
        val now = System.currentTimeMillis()
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", status = "streaming")
        _ui.value = _ui.value.copy(messages = trimmed + pending, sending = true, error = null)
        streamResponse(convId, lastUser.content, pendingId)
    }

    /** 共用的流式请求逻辑：SSE 逐字 → 完成拉规范列表；SSE 失败回退一次非流式请求。 */
    private fun streamResponse(convId: String, msg: String, pendingId: String) {
        sendJob = viewModelScope.launch {
            // V251 执行体=手机直连 → 跳过后端（Marvis「我的手机」模式）
            if (_ui.value.execMode == "direct") {
                val derr = runDirect(msg, pendingId)
                if (derr.isNotBlank()) {
                    _ui.value = _ui.value.copy(
                        messages = _ui.value.messages.filter { it.id != pendingId },
                        sending = false, error = "手机直连失败：$derr",
                    )
                }
                return@launch
            }
            val acc = StringBuilder()
            val ok = liveManager.stream(convId, msg) { cur ->
                acc.setLength(0)
                acc.append(cur)
                _ui.value = _ui.value.copy(messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(content = cur) else it
                })
            }
            if (ok) {
                _ui.value = _ui.value.copy(
                    messages = _ui.value.messages.map { if (it.id == pendingId) it.copy(status = "complete") else it },
                    sending = false, directMode = false, directPersisted = true,
                )
                val fresh = liveRepo.getMessages(convId)
                if (!fresh.isNullOrEmpty()) {
                    _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(fresh))
                } else if (acc.isEmpty()) {
                    _ui.value = _ui.value.copy(error = "回复为空，请重试")
                }
            } else {
                // 不把一次不确定的流式失败自动重投旧接口。服务端如果已经接收，重投会
                // 生成两个并发轮次。先读权威消息，只在确认未接收时才走手机直连。
                liveRepo.invalidateMessages(convId)
                val fresh = liveRepo.getMessages(convId)
                val acceptedByServer = fresh?.any { it.role == "user" && it.content.trim() == msg } == true
                if (acceptedByServer) {
                    _ui.value = _ui.value.copy(
                        messages = ChatMessageOps.sortChronological(fresh.orEmpty()),
                        sending = false,
                        error = "连接中断，服务端已接收任务；重新进入会话即可继续查看进度",
                    )
                } else {
                    // V251 直连兜底（Marvis 式）：后端两级不可达 → 自动切手机直连（配置经 Supabase 无感下发）
                    if (_ui.value.execMode != "backend" && direct.configured()) {
                        val derr = runDirect(msg, pendingId)
                        if (derr.isNotBlank()) {
                            _ui.value = _ui.value.copy(
                                messages = _ui.value.messages.filter { it.id != pendingId },
                                sending = false, error = "发送失败：后端未连接；$derr",
                            )
                        }
                    } else {
                        _ui.value = _ui.value.copy(
                            messages = _ui.value.messages.filter { it.id != pendingId },
                            sending = false,
                            error = if (_ui.value.execMode == "backend")
                                "发送失败：桌面端后端未连接（已按你的选择不启用手机直连）"
                            else "发送失败：后端未连接，且直连配置尚未从桌面端同步（先在桌面端配好默认模型并启动一次）",
                        )
                    }
                }
            }
            sendJob = null
        }
    }
}
