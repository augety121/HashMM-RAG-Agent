package com.hashmm.app.ui.relay

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.RelayRepository
import com.hashmm.app.data.remote.RemoteHost
import com.hashmm.app.data.sync.ChatConversation
import com.hashmm.app.data.sync.SyncRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 接力生命周期：一条会话交接后的状态。 */
enum class RelayPhase { SENDING, SENT, ACKED, FAILED }

data class RelayStatus(
    val convId: String,
    val phase: RelayPhase,
    val hostName: String = "",
    val startedAt: Long = System.currentTimeMillis(),
    val ackedAt: Long = 0L,
)

data class RelayUi(
    val loading: Boolean = true,
    val conversations: List<ChatConversation> = emptyList(),
    val hosts: List<RemoteHost> = emptyList(),
    val handingOff: String? = null,
    val statuses: Map<String, RelayStatus> = emptyMap(),   // convId -> 接力状态（周期跟踪）
    val toast: String? = null,
)

@HiltViewModel
class RelayViewModel @Inject constructor(
    private val sync: SyncRepository,
    private val relay: RelayRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(RelayUi())
    val ui: StateFlow<RelayUi> = _ui.asStateFlow()

    private var hostPollJob: Job? = null
    private val ackJobs = mutableMapOf<String, Job>()

    init { load(); startHostPoll() }

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

    /** 在线主机每 5s 刷新一次（对标设备列表实时性），页面在时持续跟踪。 */
    private fun startHostPoll() {
        hostPollJob?.cancel()
        hostPollJob = viewModelScope.launch {
            while (isActive) {
                val hosts = runCatching { relay.onlineHosts() }.getOrDefault(_ui.value.hosts)
                _ui.value = _ui.value.copy(hosts = hosts)
                delay(5000)
            }
        }
    }

    fun refreshHosts() {
        viewModelScope.launch { _ui.value = _ui.value.copy(hosts = relay.onlineHosts()) }
    }

    /** 交接到（第一个）在线桌面主机，并开始周期性跟踪桌面端是否接管。 */
    fun handoff(conv: ChatConversation) {
        if (_ui.value.handingOff != null) return
        val host = _ui.value.hosts.firstOrNull()
        if (host == null) {
            _ui.value = _ui.value.copy(toast = "没有在线的桌面主机，请先在电脑端登录同账号并开启远程")
            return
        }
        val startedAt = System.currentTimeMillis()
        _ui.value = _ui.value.copy(
            handingOff = conv.id,
            statuses = _ui.value.statuses + (conv.id to RelayStatus(conv.id, RelayPhase.SENDING, host.name, startedAt)),
        )
        viewModelScope.launch {
            val ok = relay.handoff(conv.id, conv.title, host.id)
            if (!ok) {
                _ui.value = _ui.value.copy(
                    handingOff = null,
                    statuses = _ui.value.statuses + (conv.id to RelayStatus(conv.id, RelayPhase.FAILED, host.name, startedAt)),
                    toast = "交接失败，请重试",
                )
                return@launch
            }
            _ui.value = _ui.value.copy(
                handingOff = null,
                statuses = _ui.value.statuses + (conv.id to RelayStatus(conv.id, RelayPhase.SENT, host.name, startedAt)),
                toast = "已发送到「${host.name}」，等待桌面端接管…",
            )
            trackAck(conv.id, startedAt)
        }
    }

    /** 周期性查回执：桌面端接管后把状态推进到 ACKED；30s 无回执则停止跟踪（保留"已发送"）。 */
    private fun trackAck(convId: String, startedAt: Long) {
        ackJobs[convId]?.cancel()
        ackJobs[convId] = viewModelScope.launch {
            val deadline = startedAt + 30_000
            while (isActive && System.currentTimeMillis() < deadline) {
                val (acked, hostName, ackedAt) = runCatching { relay.handoffAck(convId, startedAt) }
                    .getOrDefault(Triple(false, "", 0L))
                if (acked) {
                    val prev = _ui.value.statuses[convId]
                    _ui.value = _ui.value.copy(
                        statuses = _ui.value.statuses + (convId to RelayStatus(
                            convId, RelayPhase.ACKED,
                            hostName.ifBlank { prev?.hostName.orEmpty() }, startedAt, ackedAt,
                        )),
                        toast = "桌面端已接管此对话，可在电脑上继续",
                    )
                    return@launch
                }
                delay(2500)
            }
        }
    }

    /** 收起某条状态卡（用户已知晓）。 */
    fun dismissStatus(convId: String) {
        ackJobs[convId]?.cancel(); ackJobs.remove(convId)
        _ui.value = _ui.value.copy(statuses = _ui.value.statuses - convId)
    }

    fun clearToast() { _ui.value = _ui.value.copy(toast = null) }

    override fun onCleared() {
        super.onCleared()
        hostPollJob?.cancel()
        ackJobs.values.forEach { it.cancel() }
    }
}
