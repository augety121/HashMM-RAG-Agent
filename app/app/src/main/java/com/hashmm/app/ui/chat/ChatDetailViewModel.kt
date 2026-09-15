package com.hashmm.app.ui.chat

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ChatLiveRepository
import com.hashmm.app.data.remote.FileDispatchRepository
import com.hashmm.app.data.remote.SttRepository
import com.hashmm.app.data.remote.StreamingSttClient
import com.hashmm.app.data.sync.ChatMessage
import kotlinx.serialization.json.contentOrNull
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject

data class ChatDetailUiState(
    val loading: Boolean = true,
    val messages: List<ChatMessage> = emptyList(),
    val live: Boolean = false,          // true=正显示客户端实时内容
    val sending: Boolean = false,       // true=正在等 Agent 回复
    val error: String? = null,
    // V1700 直连模式：先保留账号隔离本地副本，写回成功后再提供持久化回执。
    val directMode: Boolean = false,
    val directPersisted: Boolean = false,
    // V250 执行体（Marvis 式顶栏切换）：auto=后端优先直连兜底 / backend=仅桌面端 / direct=仅手机直连
    val execMode: String = "auto",
    // V262 后端未启动状态卡：替代裸报错——可一键切手机直连重答 / 重试后端
    val backendDown: Boolean = false,
    val lastFailedMsg: String = "",
    val directReady: Boolean = false,   // 手机直连配置是否已就绪（决定卡片主按钮是否可用）
    // V346: 与桌面端共享同一条语义任务流，避免 App 只能看到正文、看不到任务如何完成。
    val taskContract: String = "",
    val liveTodo: String = "",
    val liveProgress: String = "",
    val liveStepCount: Int = 0,
    val activeTurnId: String = "",
    val turnSteerable: Boolean = false,
    val steering: Boolean = false,
    val interrupting: Boolean = false,
)

/** V174「从电脑取文件」一条下发记录（轻量历史）。delivered=对话里已收到文件卡片；fileUrl/fileName 用于点开直达。 */
data class DispatchRecord(
    val query: String, val ok: Boolean, val time: Long,
    val delivered: Boolean = false, val fileUrl: String? = null, val fileName: String? = null,
    val requestId: String = "", val status: String = if (ok) "pending" else "error",
)

@HiltViewModel
class ChatDetailViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val direct: com.hashmm.app.data.remote.DirectLlmRepository,
    private val appSettings: com.hashmm.app.data.settings.SettingsStore,
    private val liveRepo: ChatLiveRepository,
    private val liveManager: com.hashmm.app.data.remote.LiveChatManager,
    private val fileDispatch: FileDispatchRepository,
    private val sttRepo: SttRepository,
    private val streamStt: StreamingSttClient,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val convId: String = savedStateHandle.get<String>("convId").orEmpty()
    private val initial: String = run {
        val raw = savedStateHandle.get<String>("initial").orEmpty()
        if (raw.isBlank()) "" else runCatching { java.net.URLDecoder.decode(raw, "UTF-8") }.getOrDefault(raw)
    }
    // 非空表示这次会话由"电脑任务"快捷指令发起：首条要下发到电脑客户端执行（computer-task），而非发给云端 Agent。
    private val dispatchKind: String = savedStateHandle.get<String>("dispatch").orEmpty()
    /** V244：给 UI 用——本会话是否为"电脑任务"落地页（顶栏标题与任务横幅按此切换）。 */
    val isDispatchSession: Boolean get() = dispatchKind.isNotBlank()
    /** 当前会话的服务端 ID，供文件/画布查看器保存同一会话产物。 */
    val conversationId: String get() = convId

    private val _ui = MutableStateFlow(ChatDetailUiState())
    val ui: StateFlow<ChatDetailUiState> = _ui.asStateFlow()
    private var observedManagedStream = false

    // App 内置文件查看器要打开的 URL（非空=显示全屏 WebView 查看文件，不跳浏览器）
    private val _viewerUrl = MutableStateFlow<String?>(null)
    val viewerUrl: StateFlow<String?> = _viewerUrl.asStateFlow()
    fun openFile(downloadUrl: String, filename: String) {
        viewModelScope.launch {
            val url = liveRepo.fileViewUrl(downloadUrl, filename)
            if (url != null) _viewerUrl.value = url
            else _viewerUrl.value = downloadUrl   // 兜底：拿不到内置 URL 时退回原下载地址
        }
    }
    fun closeViewer() { _viewerUrl.value = null }

    // V174「从电脑取文件」：把输入当文件名/描述，一键下发给电脑端（桌面轮询器接走并上传回本会话）。
    private val _toast = MutableStateFlow<String?>(null)
    val toast: StateFlow<String?> = _toast.asStateFlow()
    fun clearToast() { _toast.value = null }
    // 轻量历史：最近几条下发记录（query + 成功否 + 时间）
    private val _dispatches = MutableStateFlow<List<DispatchRecord>>(emptyList())
    val dispatches: StateFlow<List<DispatchRecord>> = _dispatches.asStateFlow()
    fun clearDispatches() { _dispatches.value = emptyList() }

    private fun displayDispatchQuery(raw: String): String {
        val match = Regex("^\\[\\[([A-Z_!]+)]]\\s*").find(raw)
        val kind = match?.groupValues?.getOrNull(1).orEmpty()
        val task = raw.removeRange(0, match?.range?.last?.plus(1) ?: 0).trim()
        val prefix = when (kind) {
            "AGENT", "AGENT!", "AGENT_RESUME" -> "浏览器·"
            "SEQ", "SEQ_RESUME" -> "多步·"
            "CMD" -> "只读·"
            "EXEC", "EXEC!" -> "执行·"
            "CU", "AUTO" -> "电脑·"
            else -> "取文件·"
        }
        return prefix + task.ifBlank { raw }.take(32)
    }

    private suspend fun refreshDispatches() {
        val remote = fileDispatch.requests(convId)
        if (remote.isEmpty()) return
        val old = _dispatches.value.associateBy { it.requestId }
        _dispatches.value = remote.map { state ->
            val previous = old[state.id]
            DispatchRecord(
                query = displayDispatchQuery(state.query),
                ok = state.status != "error",
                time = state.createdAt * 1000L,
                delivered = previous?.delivered == true,
                fileUrl = previous?.fileUrl,
                fileName = previous?.fileName,
                requestId = state.id,
                status = state.status,
            )
        }
    }
    // 语音转文字：上传录音到后端 STT，结果回主线程填进输入框（设备无系统语音服务时用）。
    fun transcribeAudio(audio: java.io.File, onResult: (String?) -> Unit) {
        viewModelScope.launch {
            val text = runCatching { sttRepo.transcribe(audio) }.getOrNull()
            onResult(text)
        }
    }
    // 边录边转（流式）：回调都在主线程。起不来 onFail。
    fun startVoiceStream(onPartial: (String) -> Unit, onAmp: (Float) -> Unit, onFinal: (String?) -> Unit, onFail: () -> Unit) {
        viewModelScope.launch {
            val ok = runCatching { streamStt.start(onPartial, onAmp, onFinal) }.getOrDefault(false)
            if (!ok) onFail()
        }
    }
    fun stopVoiceStream() = streamStt.stop()
    fun cancelVoiceStream() = streamStt.cancel()
    // 监听对话里新到的文件卡片 → 把对应"已下发未送达"的取文件标成已送达（优先按文件名精确匹配）。
    private var lastFileMsgCount = 0
    private fun hasFiles(m: ChatMessage): Boolean =
        m.files?.toString()?.let { it != "null" && it != "[]" && it.isNotBlank() } == true
    private fun fileMsgCount(messages: List<ChatMessage>): Int = messages.count { hasFiles(it) }
    private fun newestFilesStr(messages: List<ChatMessage>): String =
        messages.lastOrNull { hasFiles(it) }?.files?.toString()?.lowercase().orEmpty()
    /** 取刚到文件卡片里的第一个文件 (文件名, 下载URL)，用于"点开直达"。 */
    private fun newestFile(messages: List<ChatMessage>): Pair<String, String>? {
        val el = messages.lastOrNull { hasFiles(it) }?.files ?: return null
        val arr = el as? kotlinx.serialization.json.JsonArray ?: return null
        val o = arr.firstOrNull() as? kotlinx.serialization.json.JsonObject ?: return null
        fun str(k: String) = (o[k] as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull
        val name = str("filename") ?: str("name") ?: return null
        val url = str("url") ?: str("download_url") ?: ""
        return name to url
    }
    private fun noteIncomingFiles(messages: List<ChatMessage>) {
        val c = fileMsgCount(messages)
        if (c > lastFileMsgCount) {
            val filesStr = newestFilesStr(messages)
            val f = newestFile(messages)
            val list = _dispatches.value
            // 优先：刚到的文件名里包含某条下发的关键词（按文件名精确匹配）
            var idx = list.indexOfFirst { it.ok && !it.delivered && it.query.isNotBlank() && filesStr.contains(it.query.lowercase()) }
            // 兜底：没匹配到名字 → 标最近一条未送达
            if (idx < 0) idx = list.indexOfFirst { it.ok && !it.delivered }
            if (idx >= 0) _dispatches.value = list.toMutableList().also {
                it[idx] = it[idx].copy(delivered = true, fileName = f?.first ?: it[idx].fileName, fileUrl = f?.second ?: it[idx].fileUrl)
            }
        }
        lastFileMsgCount = c
    }
    fun requestFileFromDesktop(query: String) {
        val q = query.trim()
        if (q.isBlank() || convId.isBlank()) return
        viewModelScope.launch {
            val result = fileDispatch.requestFile(convId, q)
            _toast.value = result.message
            _dispatches.value = (listOf(DispatchRecord(
                query = "取文件·$q", ok = result.ok, time = System.currentTimeMillis(),
                requestId = result.requestId, status = result.status.ifBlank { if (result.ok) "pending" else "error" },
            )) + _dispatches.value).take(8)
            if (result.ok) pollForIncoming()                       // 结果回来自动显示，不用手动刷新
        }
    }
    /** 让电脑执行任务（结果回到本对话）。公开类型与桌面端统一。 */
    fun requestComputerTask(task: String, kind: String = "computer_use") {
        val t = task.trim()
        if (t.isBlank() || convId.isBlank()) return
        viewModelScope.launch {
            val result = fileDispatch.requestComputerTask(convId, t, kind)
            _toast.value = result.message
            val tag = when (kind) {
                "browser_use" -> "浏览器·"
                "seq" -> "多步·"
                "cmd" -> "只读·"
                "exec" -> "执行·"
                else -> "电脑·"
            }
            _dispatches.value = (listOf(DispatchRecord(
                query = tag + t.take(24), ok = result.ok, time = System.currentTimeMillis(),
                requestId = result.requestId, status = result.status.ifBlank { if (result.ok) "pending" else "error" },
            )) + _dispatches.value).take(8)
            if (result.ok) pollForIncoming(expectEdits = kind in setOf("agent", "auto", "seq"))
        }
    }

    // ───── 路线图阶段 D：意图编排发送（V202 澄清气泡版）─────
    /** 待回答的澄清上下文：非空=上一轮编排要求澄清，值为累积到目前的完整意图文本。 */
    private var clarifyOriginal: String? = null
    private var clarifyCount = 0
    private fun resetClarify() { clarifyOriginal = null; clarifyCount = 0 }

    /**
     * 编排发送：语音入口始终走这里；打字入口在有待回答澄清时也走这里（答案接得上问题）。
     *   dispatch → 任务用「原话＋补充」的完整意图派给桌面端，用户气泡持久化（桌面端历史也完整）；
     *   clarify  → 本轮说的话与追问都写成真实消息——Siri 式气泡出现在对话里、跨端同步，最多追问 2 轮防循环；
     *   local / 编排不可用 → 交给 Agent（带累积意图，上下文不丢）。
     */
    private fun sendOrchestrated(text: String) {
        val t = text.trim()
        if (t.isBlank() || convId.isBlank() || _ui.value.sending) return
        val combined = clarifyOriginal?.let { "$it（补充：$t）" } ?: t
        viewModelScope.launch {
            val o = fileDispatch.orchestrate(combined, convId)
            when {
                o == null -> { resetClarify(); sendRaw(combined) }
                o.action == "dispatch" -> {
                    resetClarify()
                    val label = "【电脑任务】$combined"
                    liveRepo.postUserMessage(convId, label)   // 持久化用户气泡；失败不阻断，下面本地先落屏
                    _ui.value = _ui.value.copy(messages = _ui.value.messages + ChatMessage(
                        id = "task-${System.currentTimeMillis()}", convId = convId, role = "user",
                        content = label, thinking = "", status = "complete", createdAt = "",
                    ))
                    when (o.dispatchKind) {
                        "browser_use" -> requestComputerTask(combined, "browser_use")
                        "computer_use" -> requestComputerTask(combined, "computer_use")
                        "seq" -> requestComputerTask(combined, "seq")
                        "file" -> requestFileFromDesktop(combined)
                        else -> requestComputerTask(combined, "auto")
                    }
                }
                o.action == "clarify" && o.question.isNotBlank() -> {
                    if (clarifyCount >= 2) {   // 两轮还没澄清清楚 → 交给 Agent 自己追问，不困住用户
                        resetClarify(); sendRaw(combined)
                    } else {
                        clarifyOriginal = combined
                        clarifyCount += 1
                        val marked = o.question + "⟦CLARIFY⟧"   // 渲染层据此加左侧色条与「等你回答」
                        val ok = liveRepo.postClarifyExchange(convId, t, marked)
                        if (ok) {
                            refreshAfterLocalWrite()
                        } else {   // 后端不可达 → 本地气泡兜底，体验不断
                            val now = System.currentTimeMillis()
                            _ui.value = _ui.value.copy(messages = _ui.value.messages +
                                ChatMessage(id = "cu-$now", convId = convId, role = "user", content = t, thinking = "", status = "complete", createdAt = "") +
                                ChatMessage(id = "ca-$now", convId = convId, role = "assistant", content = marked, thinking = "", status = "complete", createdAt = ""))
                        }
                    }
                }
                else -> { resetClarify(); sendRaw(combined) }
            }
        }
    }

    /** 语音发送：始终先过意图编排。 */
    fun sendVoice(text: String) = sendOrchestrated(text)

    init {
        // V251：执行体为全局持久设置（与首页共用同一值）
        viewModelScope.launch {
            appSettings.execMode.collect { m -> _ui.value = _ui.value.copy(execMode = m) }
        }
        viewModelScope.launch {   // V262：状态卡按钮的可用性
            _ui.value = _ui.value.copy(directReady = direct.configured())
        }
        viewModelScope.launch {
            liveManager.streams.collect { streams ->
                val live = streams[convId]
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
                    interrupting = live?.turnStatus == "interrupting",
                )
                if (managedFinished) observedManagedStream = false
            }
        }
        // V217: initial 一次性消费——用完立刻清空 SavedStateHandle，进程恢复也不会重发
        if (initial.isNotBlank()) savedStateHandle["initial"] = ""
        if (initial.isNotBlank()) {
            _ui.value = ChatDetailUiState(loading = false)
            if (dispatchKind.isNotBlank()) {
                // 电脑任务：把首条作为"电脑操作/取文件"下发到桌面客户端（桌面端 computer-use 执行、结果回本会话）。
                // 这条链路走的是电脑客户端，不是云端后端——桌面才能看见你的文件、才能操作你的电脑。
                val label = if (dispatchKind == "file") "【取电脑文件】$initial" else "【电脑任务】$initial"
                _ui.value = _ui.value.copy(
                    messages = listOf(
                        ChatMessage(
                            id = "task-${System.currentTimeMillis()}", convId = convId, role = "user",
                            content = label, thinking = "", status = "complete", createdAt = "",
                        )
                    ),
                )
                if (dispatchKind == "file") requestFileFromDesktop(initial)
                else requestComputerTask(initial, dispatchKind)
            } else {
                // 普通问答：首条发给 Agent
                send(initial)
            }
        } else {
            start()
        }
        // 实时联动：该会话有新消息变更 → 刷新（流式生成中由轮询/发送负责）
        if (convId.isNotBlank()) {
            viewModelScope.launch {
                try {
                    sync.subscribeMessages(convId) { scheduleRealtimeRefresh() }
                } catch (e: Exception) {
                    // 实时不可用不致命
                }
            }
        }
    }

    fun load() = start()

    /** 发送入口：有待回答的澄清时，打字的答案也接上问题（否则答案脱离上下文直达 Agent）。 */
    fun send(text: String) {
        if (_ui.value.sending) { steerCurrentTurn(text); return }
        if (clarifyOriginal != null) { sendOrchestrated(text); return }
        sendRaw(text)
    }

    private fun steerCurrentTurn(text: String) {
        val content = text.trim()
        if (content.isBlank() || convId.isBlank() || _ui.value.steering) return
        if (!_ui.value.turnSteerable) {
            _toast.value = "当前任务正在建立运行通道或已进入收尾，请稍后再试"
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(steering = true)
            val result = liveManager.steer(convId, content, java.util.UUID.randomUUID().toString())
            if (result?.accepted == true) {
                liveRepo.invalidateMessages(convId)
                liveRepo.getMessages(convId)?.let { fresh ->
                    _ui.value = _ui.value.copy(
                        messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)),
                        live = true,
                    )
                }
                _toast.value = "已加入当前任务，Agent 将按新要求继续"
            } else {
                _toast.value = "追加要求未被接受，任务可能已进入收尾"
            }
            _ui.value = _ui.value.copy(steering = false)
        }
    }

    /** Cross-device human-in-the-loop decision. Approval is followed by a
     * normal Chat turn so execution still passes through the backend guard. */
    fun decideToolApproval(requestId: String, approve: Boolean) {
        if (requestId.isBlank() || convId.isBlank() || _ui.value.sending) return
        viewModelScope.launch {
            val status = liveRepo.decideToolApproval(convId, requestId, approve)
            if (status == null) {
                _toast.value = "审批失败，请检查与桌面端的连接"
                return@launch
            }
            val fresh = liveRepo.getMessages(convId)
            if (!fresh.isNullOrEmpty()) {
                _ui.value = _ui.value.copy(
                    messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)),
                    live = true,
                )
            }
            if (approve && status == "approved") {
                sendRaw("继续执行已批准的操作。只执行刚才批准的原始工具和参数。")
            } else if (!approve) {
                _toast.value = "已拒绝该工具操作"
            }
        }
    }

    fun submitMessageFeedback(messageId: String, rating: String, reasonCode: String, comment: String) {
        if (convId.isBlank() || messageId.isBlank()) return
        viewModelScope.launch {
            val ok = liveRepo.submitMessageFeedback(convId, messageId, rating, reasonCode, comment)
            if (!ok) {
                _toast.value = "反馈提交失败，请检查服务器连接"
                return@launch
            }
            liveRepo.getMessages(convId)?.let { fresh ->
                _ui.value = _ui.value.copy(
                    messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)),
                    live = true,
                )
            }
            _toast.value = if (rating == "down") "已进入质量复核队列" else "感谢你的反馈"
        }
    }

    /** 原生继续对话（流式）：乐观插入用户消息 + 占位，SSE 逐字追加，结束再拉规范列表。 */
    private fun sendRaw(text: String) {
        val msg = text.trim()
        if (msg.isBlank() || convId.isBlank() || _ui.value.sending) return
        val now = System.currentTimeMillis()
        val userMsg = ChatMessage(id = "u-$now", convId = convId, role = "user", content = msg, thinking = "", status = "complete", createdAt = "")
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", thinking = "", status = "streaming", createdAt = "")
        _ui.value = _ui.value.copy(messages = _ui.value.messages + userMsg + pending, sending = true, error = null)
        streamResponse(convId, msg, pendingId)
    }

    private var sendJob: Job? = null
    /** 共用的流式请求：SSE 逐字 → 完成拉规范列表；失败回退一次非流式。停止/重新生成都走它。 */
    /** V251：顶栏执行体切换（auto / backend / direct）——全局持久（SettingsStore），与首页共用。 */
    fun setExecMode(mode: String) {
        if (mode !in listOf("auto", "backend", "direct")) return
        viewModelScope.launch { appSettings.setExecMode(mode) }
    }

    /** V262「后端未启动」状态卡：重试后端——原文重发（用户消息已在列表，只补占位气泡）。 */
    fun retryAfterBackendDown() {
        val msgText = _ui.value.lastFailedMsg
        if (msgText.isBlank() || _ui.value.sending) return
        val now = System.currentTimeMillis()
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", thinking = "", status = "streaming", createdAt = "")
        _ui.value = _ui.value.copy(messages = _ui.value.messages + pending, sending = true, backendDown = false, error = null)
        streamResponse(convId, msgText, pendingId)
    }

    /** V262 状态卡「切到我的手机直连」：持久切换执行体并立刻用直连重答同一条。 */
    fun switchToDirectAndRetry() {
        if (_ui.value.sending) return
        viewModelScope.launch { appSettings.setExecMode("direct") }
        _ui.value = _ui.value.copy(execMode = "direct")
        retryAfterBackendDown()
    }

    fun dismissBackendDown() { _ui.value = _ui.value.copy(backendDown = false, error = null) }

    /** 手机直连回答（同一个 pending 气泡续流）。返回错误信息，空＝成功。 */
    private suspend fun runDirect(msg: String, pendingId: String): String {
        val hist = _ui.value.messages
            .filter { it.id != pendingId && it.content.isNotBlank() }
            .map { it.role to it.content } + ("user" to msg)
        val acc2 = StringBuilder()
        val derr = direct.streamChat(hist, convId) { token ->
            acc2.append(token)
            val cur = acc2.toString()
            _ui.value = _ui.value.copy(directMode = true, messages = _ui.value.messages.map {
                if (it.id == pendingId) it.copy(content = cur) else it
            })
        }
        return if (derr.isBlank() && acc2.isNotBlank()) {
            _ui.value = _ui.value.copy(
                messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(status = "complete") else it
                },
                sending = false, directMode = true, directPersisted = false, error = null,
            )
            // V250 消息互通：直连轮次（用户消息 + 手机回答）upsert 进 Supabase chat_messages——
            // 桌面端（配置 service_role 同步后）拉到同一会话可继续接着聊；失败静默不拦对话。
            val delivered = runCatching {
                val iso = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
                    .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }
                    .format(java.util.Date())
                val ms = _ui.value.messages
                val userMsg = ms.lastOrNull { it.role == "user" && it.content == msg }
                val botMsg = ms.lastOrNull { it.id == pendingId }
                val rows = listOfNotNull(userMsg, botMsg).map {
                    it.copy(
                        createdAt = it.createdAt.ifBlank { iso },
                        updatedAt = iso,
                        status = "complete",
                    )
                }
                sync.upsertMessages(rows)
            }.getOrDefault(false)
            _ui.value = _ui.value.copy(directPersisted = delivered)
            ""
        } else derr.ifBlank { "直连没有返回内容" }
    }

    private fun streamResponse(convId: String, msg: String, pendingId: String) {
        sendJob = viewModelScope.launch {
            // V250：执行体=手机直连 → 跳过后端，直接直连（Marvis「我的手机」模式）
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
            // V269 后台化：经 LiveChatManager 在**应用级作用域**跑流式——离开会话页 ViewModel 被清
            // 也不取消，后端继续生成并每 1.5s 落 partial；用户回到本会话时 start() 的重连轮询就能续看
            // 到增长中的内容，不再"点进去先空白"。回调给的是累计内容，直接替换气泡；acc 同步维护以
            // 兼容下方 acc.isEmpty() 的空回复判断。
            val ok = liveManager.stream(convId, msg) { cur ->
                acc.setLength(0); acc.append(cur)
                _ui.value = _ui.value.copy(messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(content = cur) else it
                })
            }
            if (ok) {
                // 流式已连上（后端处理并持久化了消息）→ 标记完成 + 拉规范列表，绝不重复请求
                _ui.value = _ui.value.copy(
                    messages = _ui.value.messages.map { if (it.id == pendingId) it.copy(status = "complete") else it },
                    sending = false, directMode = false, directPersisted = true,
                )
                val fresh = liveRepo.getMessages(convId)
                if (!fresh.isNullOrEmpty()) {
                    _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)), live = true)
                } else if (acc.isEmpty()) {
                    _ui.value = _ui.value.copy(error = "回复为空，请重试")
                }
                // 兜底自动刷新：桌面投送的文件是被控端轮询后才异步推来的（晚于本次拉取），
                // 发完后再短轮询一阵，新消息一到就自动显示，不必用户再发一条触发。
                pollForIncoming()
            } else {
                // 传输失败后不能把同一条用户消息自动改投旧 /api/chat：服务端可能已
                // 持久化并仍在执行，那会制造重复轮次和重复扣费。先拉权威历史；只有
                // 服务端明确没有接收本条消息时，才允许走手机直连兜底。
                liveRepo.invalidateMessages(convId)
                val fresh = liveRepo.getMessages(convId)
                val acceptedByServer = fresh?.any { it.role == "user" && it.content.trim() == msg } == true
                if (acceptedByServer) {
                    _ui.value = _ui.value.copy(
                        messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh.orEmpty())),
                        sending = false,
                        live = true,
                        error = "连接中断，服务端已接收任务；重新进入会话即可继续查看进度",
                    )
                } else {
                    // ── V250 直连兜底（Marvis 式）：后端流式+非流两级都不可达 → 自动切手机直连
                    //    （配置由桌面端经 Supabase 无感下发，无需用户手填）。仅桌面端模式不兜底。
                    // V262：后端不可达不再抛裸报错——弹「后端未启动」状态卡
                    //（一键切手机直连重答 / 重试后端；执行体也可在标题下切换）。
                    if (_ui.value.execMode != "backend" && direct.configured()) {
                        val derr = runDirect(msg, pendingId)
                        if (derr.isNotBlank()) {
                            _ui.value = _ui.value.copy(
                                messages = _ui.value.messages.filter { it.id != pendingId },
                                sending = false, backendDown = true, lastFailedMsg = msg,
                                error = "手机直连也失败：$derr",
                            )
                        }
                    } else {
                        _ui.value = _ui.value.copy(
                            messages = _ui.value.messages.filter { it.id != pendingId },
                            sending = false, backendDown = true, lastFailedMsg = msg,
                            error = null,
                        )
                    }
                }
            }
        }
    }

    /** 重新生成：删掉最后一条 Agent 回复，用最后一条用户消息重新跑。 */
    fun regenerate() {
        if (_ui.value.sending || convId.isBlank()) return
        val msgs = _ui.value.messages
        val lastUser = msgs.lastOrNull { it.role == "user" } ?: return
        val lastAssistantIdx = msgs.indexOfLast { it.role == "assistant" }
        val trimmed = if (lastAssistantIdx >= 0) msgs.filterIndexed { i, _ -> i != lastAssistantIdx } else msgs
        val now = System.currentTimeMillis()
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", thinking = "", status = "streaming", createdAt = "")
        _ui.value = _ui.value.copy(messages = trimmed + pending, sending = true, error = null)
        streamResponse(convId, lastUser.content, pendingId)
    }

    /** 停止生成：同时取消应用级任务和真实 HTTP 传输，不能只让按钮停止转动。 */
    fun stopGenerating() {
        if (_ui.value.execMode == "direct" || _ui.value.activeTurnId.isBlank()) {
            hardCancelGeneration()
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(interrupting = true, turnSteerable = false)
            if (!liveManager.interrupt(convId)) hardCancelGeneration()
        }
    }

    private fun hardCancelGeneration() {
        liveManager.cancel(convId)
        direct.cancel(convId)
        sendJob?.cancel()
        sendJob = null
        _ui.value = _ui.value.copy(
            messages = _ui.value.messages.mapNotNull {
                if (it.status != "streaming") it
                else if (it.content.isBlank()) null
                else it.copy(status = "interrupted")
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

    /** V217 防御：服务器侧（如快照恢复）可能把同一条消息重复入库——相邻同角色同内容折叠为一条。
     *  窗口极窄（相邻），不误伤用户刻意重发（中间隔了助手回复就不相邻）。 */
    private fun dedupeAdjacent(list: List<ChatMessage>): List<ChatMessage> =
        ChatMessageOps.dedupeAdjacent(list)   // V270：逻辑抽到 ChatMessageOps（纯函数，可 ./gradlew test 单测）

    private var realtimeRefreshJob: Job? = null
    private val realtimeRefreshMutex = Mutex()

    private fun scheduleRealtimeRefresh() {
        realtimeRefreshJob?.cancel()
        realtimeRefreshJob = viewModelScope.launch {
            delay(400L)
            refreshFromRealtime()
        }
    }

    private suspend fun refreshFromRealtime() {
        realtimeRefreshMutex.withLock {
            if (_ui.value.messages.any { it.status == "streaming" }) return@withLock
            // The event came from Supabase, so one incremental Supabase read is
            // sufficient. A second backend GET per event was the request-storm
            // feedback loop seen in the production logs.
            val synced = sync.syncMessages(convId)
            if (synced.isNotEmpty()) {
                _ui.value = _ui.value.copy(
                    messages = ChatMessageOps.sortChronological(dedupeAdjacent(synced))
                )
            }
        }
    }

    private suspend fun refreshAfterLocalWrite() {
        liveRepo.invalidateMessages(convId)
        val fresh = liveRepo.getMessages(convId)
        if (!fresh.isNullOrEmpty()) {
            _ui.value = _ui.value.copy(
                messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)),
                live = true,
            )
        }
    }

    private var incomingPollJob: Job? = null
    /** 发完消息后短轮询一阵：捕获桌面投送等"异步晚到"的消息（如文件卡片），到了就自动刷新。约 2 分钟。 */
    private fun pollForIncoming(expectEdits: Boolean = false) {
        incomingPollJob?.cancel()
        incomingPollJob = viewModelScope.launch {
            val baseline = _ui.value.messages.size
            lastFileMsgCount = fileMsgCount(_ui.value.messages)   // 基线：开始观察前已有的文件数
            val attempts = if (expectEdits) 15 else 12
            repeat(attempts) { attempt ->
                val waitMs = if (expectEdits) {
                    when {
                        attempt < 3 -> 3_000L
                        attempt < 6 -> 5_000L
                        attempt < 10 -> 8_000L
                        else -> 12_000L
                    }
                } else {
                    when {
                        attempt < 2 -> 5_000L
                        attempt < 5 -> 8_000L
                        else -> 12_000L
                    }
                }
                delay(waitMs)
                refreshDispatches()
                if (_ui.value.sending) return@launch                       // 用户又发了，交给新流程
                if (_ui.value.messages.any { it.status == "streaming" }) return@repeat
                val fresh = liveRepo.getMessages(convId) ?: return@repeat
                noteIncomingFiles(fresh)                                   // 文件卡片到了 → 标记取文件"已送达"
                val changed = fresh.size > _ui.value.messages.size ||
                    fresh.lastOrNull()?.content != _ui.value.messages.lastOrNull()?.content ||
                    fresh.lastOrNull()?.files != _ui.value.messages.lastOrNull()?.files
                if (changed) _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(dedupeAdjacent(fresh)), live = true)
                if (!expectEdits && fresh.size > baseline) return@launch   // 取文件类：到一条结果就收工；agent 边做边改，继续追
            }
        }
    }

    private var startJob: Job? = null

    private fun start() {
        if (convId.isBlank()) {
            _ui.value = ChatDetailUiState(loading = false, error = "会话不存在")
            return
        }
        if (startJob?.isActive == true) return
        startJob = viewModelScope.launch {
            // 第一步：本地加密缓存秒显（离线也能看历史）
            val cached = sync.cachedMessages(convId)
            if (cached.isNotEmpty()) {
                _ui.value = ChatDetailUiState(loading = false, messages = ChatMessageOps.sortChronological(cached))
            }
            // 第二步：Supabase 增量同步 → 更新缓存 + 显示（时间戳一致）
            val synced = sync.syncMessages(convId)
            _ui.value = ChatDetailUiState(
                loading = false,
                messages = ChatMessageOps.sortChronological(synced.ifEmpty { cached }),
            )
            // 第三步：客户端直连实时（仅覆盖显示，含流式逐字；不写缓存避免时间戳格式不一致）
            val fresh = liveRepo.getMessages(convId)
            if (fresh != null && fresh.isNotEmpty()) {
                _ui.value = ChatDetailUiState(loading = false, messages = ChatMessageOps.sortChronological(fresh), live = true)
                var pollAttempt = 0
                while (isActive && _ui.value.messages.any { it.status == "streaming" }) {
                    delay(if (pollAttempt < 6) 3_000L else 5_000L)
                    pollAttempt += 1
                    val f = liveRepo.getMessages(convId) ?: break
                    _ui.value = _ui.value.copy(messages = ChatMessageOps.sortChronological(f))
                }
            }
            refreshDispatches()
            startJob = null
        }
    }
}
