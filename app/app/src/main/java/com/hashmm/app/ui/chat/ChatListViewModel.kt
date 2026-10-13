package com.hashmm.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.remote.ChatLiveRepository
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.ConversationSyncState
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import javax.inject.Inject

data class ChatListUiState(
    val loading: Boolean = true,
    val syncing: Boolean = false,
    val conversations: List<ChatConversation> = emptyList(),
    val error: String? = null,
    val syncState: ConversationSyncState = ConversationSyncState.ERROR,
    val synchronizedAt: Long? = null,
)

@HiltViewModel
class ChatListViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val auth: AuthRepository,
    private val live: ChatLiveRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(ChatListUiState())
    val ui: StateFlow<ChatListUiState> = _ui.asStateFlow()
    private val syncMutex = Mutex()
    private var syncRequested = false
    private var subscriptionJob: Job? = null
    private var realtimeDebounceJob: Job? = null

    // 本地隐私模式：(on, llmLocal)。null=未知/未加载。
    private val _privacy = MutableStateFlow<Triple<Boolean, Boolean, Boolean>?>(null)
    val privacy: StateFlow<Triple<Boolean, Boolean, Boolean>?> = _privacy.asStateFlow()
    fun loadPrivacy() { viewModelScope.launch { live.getPrivacyMode()?.let { _privacy.value = it } } }
    fun setPrivacy(on: Boolean) { viewModelScope.launch { (live.setPrivacyMode(on))?.let { _privacy.value = it } } }

    init {
        loadPrivacy()
        // 修复"刚进 app 没有历史、要退出重进才出现"：冷启动时 Supabase 会话还在从存储异步恢复，
        // 此刻 uid 为 null → 拉不到历史。监听登录态，会话一就绪就再拉一次，历史立刻出来。
        viewModelScope.launch {
            try {
                auth.isLoggedIn.collect { loggedIn ->
                    if (loggedIn) {
                        load()
                        ensureSubscription()
                    } else {
                        subscriptionJob?.cancel()
                        subscriptionJob = null
                    }
                }
            } catch (_: Exception) {
            }
        }
        // 实时联动：客户端改了会话 → 立刻增量同步刷新
        viewModelScope.launch {
            try {
                ensureSubscription()
            } catch (e: Exception) {
                // 实时通道不可用不致命，仍可手动刷新
            }
        }
    }

    fun load() {
        viewModelScope.launch {
            // 本地缓存秒显（离线可看）——V272 显式最新在前；V273 预计算排序键，会话多也不卡
            val cached = ChatMessageOps.sortConversationsByActivity(sync.cachedConversations())
            if (cached.isNotEmpty()) {
                _ui.value = _ui.value.copy(
                    loading = false,
                    conversations = cached,
                    syncState = ConversationSyncState.OFFLINE_CACHED,
                )
            }
            syncNow()
        }
    }

    private fun ensureSubscription() {
        if (subscriptionJob?.isActive == true) return
        subscriptionJob = viewModelScope.launch {
            sync.subscribeConversations {
                realtimeDebounceJob?.cancel()
                realtimeDebounceJob = viewModelScope.launch {
                    delay(750)
                    syncNow()
                }
            }
        }
    }

    private suspend fun syncNow() {
        if (!syncMutex.tryLock()) {
            syncRequested = true
            return
        }
        try {
            do {
                syncRequested = false
        _ui.value = _ui.value.copy(syncing = true, error = null)
        // V272 展示前统一最新在前；V273 预计算排序键（decorate-sort-undecorate），会话上千也跟手
        val snapshot = runCatching { sync.syncConversationSnapshot() }.getOrElse {
            com.hashmm.app.data.sync.ConversationSyncSnapshot(
                conversations = _ui.value.conversations,
                state = if (_ui.value.conversations.isEmpty()) ConversationSyncState.ERROR else ConversationSyncState.OFFLINE_CACHED,
                failureClass = "network_error",
            )
        }
        val display = if (snapshot.conversations.isNotEmpty() || snapshot.state == ConversationSyncState.VERIFIED_EMPTY) {
            ChatMessageOps.sortConversationsByActivity(snapshot.conversations)
        } else {
            _ui.value.conversations
        }
        _ui.value = _ui.value.copy(
            loading = false,
            syncing = false,
            conversations = display,
            syncState = snapshot.state,
            synchronizedAt = snapshot.synchronizedAt ?: _ui.value.synchronizedAt,
            error = failureMessage(snapshot.failureClass),
        )
            } while (syncRequested)
        } finally {
            syncMutex.unlock()
        }
    }

    fun refresh() = load()

    private fun failureMessage(failure: String?): String? = when (failure) {
        null -> null
        "authentication_required", "authentication_expired" -> "登录状态已过期，请重新登录"
        "identity_project_mismatch" -> "App 与服务器使用了不同的账号项目，请检查连接配置"
        "identity_subject_mismatch" -> "当前 App 与服务器识别到的账号不一致"
        "server_upgrade_required", "sync_protocol_mismatch" -> "服务器版本过旧，请先升级 HashMM 后端"
        "forbidden" -> "当前账号无权读取这些对话"
        "invalid_backend_url" -> "服务器地址无效，请在“我的”中恢复自动配置"
        else -> "暂时无法连接服务器，已保留上次同步记录"
    }

    // ── 会话管理：乐观更新（立刻改本地列表）→ 后端 PATCH/DELETE → 后端同步 Supabase；
    //    Supabase 变更经 subscribeConversations 实时回流，自然对齐（失败也会被下次同步纠正）。──
    fun renameConversation(convId: String, title: String) {
        val t = title.trim()
        if (t.isBlank() || convId.isBlank()) return
        _ui.value = _ui.value.copy(conversations = _ui.value.conversations.map { if (it.id == convId) it.copy(title = t) else it })
        viewModelScope.launch { live.renameConversation(convId, t) }
    }

    fun togglePin(convId: String) {
        val cur = _ui.value.conversations.firstOrNull { it.id == convId } ?: return
        val np = !cur.pinned
        _ui.value = _ui.value.copy(
            conversations = ChatMessageOps.sortConversationsByActivity(
                _ui.value.conversations.map { if (it.id == convId) it.copy(pinned = np) else it }
            )
        )
        viewModelScope.launch { live.setPinned(convId, np) }
    }

    fun deleteConversation(convId: String) {
        _ui.value = _ui.value.copy(conversations = _ui.value.conversations.filterNot { it.id == convId })
        viewModelScope.launch { live.deleteConversation(convId) }
    }
}
