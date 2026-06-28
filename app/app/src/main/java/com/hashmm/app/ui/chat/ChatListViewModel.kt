package com.hashmm.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.auth.AuthRepository
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ChatListUiState(
    val loading: Boolean = true,
    val syncing: Boolean = false,
    val conversations: List<ChatConversation> = emptyList(),
    val error: String? = null,
)

@HiltViewModel
class ChatListViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val auth: AuthRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(ChatListUiState())
    val ui: StateFlow<ChatListUiState> = _ui.asStateFlow()

    init {
        load()
        // 修复"刚进 app 没有历史、要退出重进才出现"：冷启动时 Supabase 会话还在从存储异步恢复，
        // 此刻 uid 为 null → 拉不到历史。监听登录态，会话一就绪就再拉一次，历史立刻出来。
        viewModelScope.launch {
            try {
                auth.isLoggedIn.collect { loggedIn -> if (loggedIn) load() }
            } catch (_: Exception) {
            }
        }
        // 实时联动：客户端改了会话 → 立刻增量同步刷新
        viewModelScope.launch {
            try {
                sync.subscribeConversations { viewModelScope.launch { syncNow() } }
            } catch (e: Exception) {
                // 实时通道不可用不致命，仍可手动刷新
            }
        }
    }

    fun load() {
        viewModelScope.launch {
            // 本地缓存秒显（离线可看）
            val cached = sync.cachedConversations()
            if (cached.isNotEmpty()) {
                _ui.value = _ui.value.copy(loading = false, conversations = cached)
            }
            syncNow()
        }
    }

    private suspend fun syncNow() {
        _ui.value = _ui.value.copy(syncing = true, error = null)
        val fresh = sync.syncConversations()
        _ui.value = _ui.value.copy(loading = false, syncing = false, conversations = fresh)
    }
}
