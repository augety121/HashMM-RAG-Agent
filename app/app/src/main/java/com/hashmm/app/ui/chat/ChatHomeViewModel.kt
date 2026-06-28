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
import java.util.UUID
import javax.inject.Inject

data class ChatHomeUiState(
    val convId: String? = null,        // null = 首页空态（小哈），非空 = 正在某个对话里
    val messages: List<ChatMessage> = emptyList(),
    val sending: Boolean = false,
    val loadingHistory: Boolean = false,
    val error: String? = null,
)

/**
 * 首页内联对话：发消息直接在首页内展开会话，不跳独立页（对标 Marvis）。
 * 新会话首发时本地生成 convId，后端首条消息时建库；历史会话可载入内联继续。
 */
@HiltViewModel
class ChatHomeViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val liveRepo: ChatLiveRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(ChatHomeUiState())
    val ui: StateFlow<ChatHomeUiState> = _ui.asStateFlow()

    private var sendJob: Job? = null      // 当前进行中的流式请求，用于「停止生成」
    private var lastUserText: String = "" // 最后一条用户输入，用于重试/重新生成

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
        _ui.value = ChatHomeUiState()
    }

    /** 载入一个历史会话到内联视图继续。 */
    fun openConversation(convId: String) {
        if (convId.isBlank()) return
        _ui.value = ChatHomeUiState(convId = convId, loadingHistory = true)
        viewModelScope.launch {
            val cached = runCatching { sync.cachedMessages(convId) }.getOrDefault(emptyList())
            if (cached.isNotEmpty()) _ui.value = _ui.value.copy(messages = cached)
            val synced = runCatching { sync.syncMessages(convId) }.getOrDefault(cached)
            _ui.value = _ui.value.copy(loadingHistory = false, messages = synced.ifEmpty { cached })
            val fresh = liveRepo.getMessages(convId)
            if (!fresh.isNullOrEmpty()) {
                _ui.value = _ui.value.copy(messages = fresh)
                while (isActive && _ui.value.messages.any { it.status == "streaming" }) {
                    delay(1500)
                    val f = liveRepo.getMessages(convId) ?: break
                    _ui.value = _ui.value.copy(messages = f)
                }
            }
        }
    }

    /** 发送（首页内联）：无会话则新建，乐观插入 + SSE 逐字，完成再拉规范列表，绝不重复请求。 */
    fun send(text: String) {
        val msg = text.trim()
        if (msg.isBlank() || _ui.value.sending) return
        lastUserText = msg
        val convId = _ui.value.convId ?: UUID.randomUUID().toString()
        val now = System.currentTimeMillis()
        val ts = nowIso()
        val userMsg = ChatMessage(id = "u-$now", convId = convId, role = "user", content = msg, status = "complete", createdAt = ts)
        val pendingId = "a-$now"
        val pending = ChatMessage(id = pendingId, convId = convId, role = "assistant", content = "", status = "streaming", createdAt = ts)
        _ui.value = _ui.value.copy(
            convId = convId,
            messages = _ui.value.messages + userMsg + pending,
            sending = true, error = null,
        )
        streamResponse(convId, msg, pendingId)
    }

    /** 停止生成：取消进行中的请求，把已收到的部分定为完成（没收到任何内容则去掉占位）。 */
    fun stop() {
        sendJob?.cancel(); sendJob = null
        _ui.value = _ui.value.copy(
            messages = _ui.value.messages.mapNotNull {
                if (it.role == "assistant" && it.status == "streaming") {
                    if (it.content.isBlank()) null else it.copy(status = "complete")
                } else it
            },
            sending = false,
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
            val acc = StringBuilder()
            val ok = liveRepo.streamMessage(convId, msg) { token ->
                acc.append(token)
                val cur = acc.toString()
                _ui.value = _ui.value.copy(messages = _ui.value.messages.map {
                    if (it.id == pendingId) it.copy(content = cur) else it
                })
            }
            if (ok) {
                _ui.value = _ui.value.copy(
                    messages = _ui.value.messages.map { if (it.id == pendingId) it.copy(status = "complete") else it },
                    sending = false,
                )
                val fresh = liveRepo.getMessages(convId)
                if (!fresh.isNullOrEmpty()) {
                    _ui.value = _ui.value.copy(messages = fresh)
                } else if (acc.isEmpty()) {
                    _ui.value = _ui.value.copy(error = "回复为空，请重试")
                }
            } else {
                val answer = liveRepo.sendMessage(convId, msg)
                if (answer != null) {
                    val fresh = liveRepo.getMessages(convId)
                    if (!fresh.isNullOrEmpty()) {
                        _ui.value = _ui.value.copy(messages = fresh, sending = false)
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
            sendJob = null
        }
    }
}
