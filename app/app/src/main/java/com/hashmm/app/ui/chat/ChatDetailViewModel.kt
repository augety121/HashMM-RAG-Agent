package com.hashmm.app.ui.chat

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ChatLiveRepository
import com.hashmm.app.data.sync.ChatMessage
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ChatDetailUiState(
    val loading: Boolean = true,
    val messages: List<ChatMessage> = emptyList(),
    val live: Boolean = false,          // true=正显示客户端实时内容
    val sending: Boolean = false,       // true=正在等 Agent 回复
    val error: String? = null,
)

@HiltViewModel
class ChatDetailViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val liveRepo: ChatLiveRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val convId: String = savedStateHandle.get<String>("convId").orEmpty()
    private val initial: String = run {
        val raw = savedStateHandle.get<String>("initial").orEmpty()
        if (raw.isBlank()) "" else runCatching { java.net.URLDecoder.decode(raw, "UTF-8") }.getOrDefault(raw)
    }

    private val _ui = MutableStateFlow(ChatDetailUiState())
    val ui: StateFlow<ChatDetailUiState> = _ui.asStateFlow()

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

    init {
        if (initial.isNotBlank()) {
            // 首页带来的新会话：无历史可加载，直接发首条
            _ui.value = ChatDetailUiState(loading = false)
            send(initial)
        } else {
            start()
        }
        // 实时联动：该会话有新消息变更 → 刷新（流式生成中由轮询/发送负责）
        if (convId.isNotBlank()) {
            viewModelScope.launch {
                try {
                    sync.subscribeMessages(convId) { viewModelScope.launch { refreshFromRealtime() } }
                } catch (e: Exception) {
                    // 实时不可用不致命
                }
            }
        }
    }

    fun load() = start()

    /** 原生继续对话（流式）：乐观插入用户消息 + 占位，SSE 逐字追加，结束再拉规范列表。 */
    fun send(text: String) {
        val msg = text.trim()
        if (msg.isBlank() || convId.isBlank() || _ui.value.sending) return
        val now = System.currentTimeMillis()
        val userMsg = ChatMessage(id = "u-$now", convId = convId, role = "user", content = msg, thinking = "", status = "complete", createdAt = "")
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", thinking = "", status = "streaming", createdAt = "")
        _ui.value = _ui.value.copy(messages = _ui.value.messages + userMsg + pending, sending = true, error = null)
        viewModelScope.launch {
            val acc = StringBuilder()
            val ok = liveRepo.streamMessage(convId, msg) { token ->
                acc.append(token)
                val cur = acc.toString()
                _ui.value = _ui.value.copy(messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(content = cur) else it
                })
            }
            if (ok) {
                // 流式已连上（后端处理并持久化了消息）→ 标记完成 + 拉规范列表，绝不重复请求
                _ui.value = _ui.value.copy(
                    messages = _ui.value.messages.map { if (it.id == pendingId) it.copy(status = "complete") else it },
                    sending = false,
                )
                val fresh = liveRepo.getMessages(convId)
                if (!fresh.isNullOrEmpty()) {
                    _ui.value = _ui.value.copy(messages = fresh, live = true)
                } else if (acc.isEmpty()) {
                    _ui.value = _ui.value.copy(error = "回复为空，请重试")
                }
                // 兜底自动刷新：桌面投送的文件是被控端轮询后才异步推来的（晚于本次拉取），
                // 发完后再短轮询一阵，新消息一到就自动显示，不必用户再发一条触发。
                pollForIncoming()
            } else {
                // 连接失败 → 回退非流式一次性请求
                val answer = liveRepo.sendMessage(convId, msg)
                if (answer != null) {
                    val fresh = liveRepo.getMessages(convId)
                    if (!fresh.isNullOrEmpty()) {
                        _ui.value = _ui.value.copy(messages = fresh, sending = false, live = true)
                    } else {
                        _ui.value = _ui.value.copy(
                            messages = _ui.value.messages.map { if (it.id == pendingId) it.copy(content = answer, status = "complete") else it },
                            sending = false,
                        )
                    }
                } else {
                    _ui.value = _ui.value.copy(
                        messages = _ui.value.messages.filter { it.id != pendingId },
                        sending = false, error = "发送失败：客户端后端未连接或超时",
                    )
                }
            }
        }
    }

    private suspend fun refreshFromRealtime() {
        if (_ui.value.messages.any { it.status == "streaming" }) return  // 流式时交给轮询
        val synced = sync.syncMessages(convId)
        if (synced.isNotEmpty()) _ui.value = _ui.value.copy(messages = synced)
        val fresh = liveRepo.getMessages(convId)
        if (fresh != null && fresh.isNotEmpty()) _ui.value = _ui.value.copy(messages = fresh, live = true)
    }

    private var incomingPollJob: Job? = null
    /** 发完消息后短轮询一阵：捕获桌面投送等"异步晚到"的消息（如文件卡片），到了就自动刷新。约 2 分钟。 */
    private fun pollForIncoming() {
        incomingPollJob?.cancel()
        incomingPollJob = viewModelScope.launch {
            val baseline = _ui.value.messages.size
            repeat(40) {                       // 40 × 3s ≈ 2 分钟
                delay(3000)
                if (_ui.value.sending) return@launch                       // 用户又发了，交给新流程
                if (_ui.value.messages.any { it.status == "streaming" }) return@repeat
                val fresh = liveRepo.getMessages(convId) ?: return@repeat
                val changed = fresh.size > _ui.value.messages.size ||
                    fresh.lastOrNull()?.content != _ui.value.messages.lastOrNull()?.content ||
                    fresh.lastOrNull()?.files != _ui.value.messages.lastOrNull()?.files
                if (changed) _ui.value = _ui.value.copy(messages = fresh, live = true)
                if (fresh.size > baseline) return@launch                   // 新消息（文件）已到，收工
            }
        }
    }

    private fun start() {
        if (convId.isBlank()) {
            _ui.value = ChatDetailUiState(loading = false, error = "会话不存在")
            return
        }
        viewModelScope.launch {
            // 第一步：本地加密缓存秒显（离线也能看历史）
            val cached = sync.cachedMessages(convId)
            if (cached.isNotEmpty()) {
                _ui.value = ChatDetailUiState(loading = false, messages = cached)
            }
            // 第二步：Supabase 增量同步 → 更新缓存 + 显示（时间戳一致）
            val synced = sync.syncMessages(convId)
            _ui.value = ChatDetailUiState(loading = false, messages = synced)
            // 第三步：客户端直连实时（仅覆盖显示，含流式逐字；不写缓存避免时间戳格式不一致）
            val fresh = liveRepo.getMessages(convId)
            if (fresh != null && fresh.isNotEmpty()) {
                _ui.value = ChatDetailUiState(loading = false, messages = fresh, live = true)
                while (isActive && _ui.value.messages.any { it.status == "streaming" }) {
                    delay(1500)
                    val f = liveRepo.getMessages(convId) ?: break
                    _ui.value = _ui.value.copy(messages = f)
                }
            }
        }
    }
}
