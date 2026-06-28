package com.hashmm.app.ui.relay

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.RelayRepository
import com.hashmm.app.data.remote.RemoteHost
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class RelayUi(
    val loading: Boolean = true,
    val conversations: List<ChatConversation> = emptyList(),
    val hosts: List<RemoteHost> = emptyList(),
    val handingOff: String? = null,
    val toast: String? = null,
)

@HiltViewModel
class RelayViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val relay: RelayRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(RelayUi())
    val ui: StateFlow<RelayUi> = _ui.asStateFlow()

    init { load() }

    fun load() {
        _ui.value = _ui.value.copy(loading = true)
        viewModelScope.launch {
            val cached = runCatching { sync.cachedConversations() }.getOrDefault(emptyList())
            if (cached.isNotEmpty()) _ui.value = _ui.value.copy(loading = false, conversations = cached)
            val fresh = runCatching { sync.syncConversations() }.getOrDefault(cached)
            val hosts = relay.onlineHosts()
            _ui.value = _ui.value.copy(loading = false, conversations = fresh.ifEmpty { cached }, hosts = hosts)
        }
    }

    fun refreshHosts() {
        viewModelScope.launch { _ui.value = _ui.value.copy(hosts = relay.onlineHosts()) }
    }

    /** 交接到（第一个）在线桌面主机。 */
    fun handoff(conv: ChatConversation) {
        if (_ui.value.handingOff != null) return
        val host = _ui.value.hosts.firstOrNull()
        if (host == null) {
            _ui.value = _ui.value.copy(toast = "没有在线的桌面主机，请先在电脑端登录同账号并开启远程")
            return
        }
        _ui.value = _ui.value.copy(handingOff = conv.id)
        viewModelScope.launch {
            val ok = relay.handoff(conv.id, conv.title, host.id)
            _ui.value = _ui.value.copy(
                handingOff = null,
                toast = if (ok) "已交接到「${host.name}」，桌面端将打开并接管此对话" else "交接失败，请重试",
            )
        }
    }

    fun clearToast() { _ui.value = _ui.value.copy(toast = null) }
}
