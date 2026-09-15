package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ClientStatusRepository
import com.hashmm.app.data.remote.RuntimeCapability
import com.hashmm.app.data.remote.MobileWorkRun
import com.hashmm.app.data.remote.WorkRuntimeRepository
import com.hashmm.app.data.remote.WorkspaceRuntimeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import javax.inject.Inject

data class HubUi(
    val backendOnline: Boolean = false,
    val desktopOnline: Boolean = false,
    val desktopCount: Int = 0,
    val cloudWorkspaceConfigured: Boolean = false,
    val cloudWorkspaceReady: Boolean = false,
    val checking: Boolean = true,
    val capabilities: List<RuntimeCapability> = emptyList(),
    val capabilityReady: Int = 0,
    val capabilityTotal: Int = 0,
    val capabilityNotice: String? = null,
    val capabilityError: String? = null,
    val capabilityFreshness: String = "unknown",
    val capabilityVerifiedAtEpochMs: Long = 0L,
    val workRuns: List<MobileWorkRun> = emptyList(),
    val workNotice: String? = null,
    val workError: String? = null,
)

@HiltViewModel
class WorkbenchHubViewModel @Inject constructor(
    private val status: ClientStatusRepository,
    private val workRuntimeRepository: WorkRuntimeRepository,
    private val workspaceRuntimeRepository: WorkspaceRuntimeRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(HubUi())
    val ui: StateFlow<HubUi> = _ui.asStateFlow()
    private var refreshJob: Job? = null

    init {
        refresh()
        viewModelScope.launch {
            while (true) {
                delay(8_000L)
                val s = status.fetch()
                val deviceCount = maxOf(s.desktops, s.remoteHosts)
                _ui.value = _ui.value.copy(
                    backendOnline = s.online,
                    desktopOnline = deviceCount > 0,
                    desktopCount = deviceCount,
                )
            }
        }
        viewModelScope.launch {
            while (true) {
                delay(60_000L)
                refresh(force = false)
            }
        }
        viewModelScope.launch {
            while (isActive) {
                workRuntimeRepository.streamChanges { feed ->
                    val merged = _ui.value.workRuns.associateBy { it.id }.toMutableMap()
                    feed.items.forEach { merged[it.id] = it }
                    _ui.value = _ui.value.copy(
                        workRuns = merged.values.sortedByDescending { it.updatedAt },
                        workNotice = null,
                        workError = null,
                    )
                }
                delay(3_000L)
            }
        }
    }

    fun refresh(force: Boolean = false) {
        if (!force && refreshJob?.isActive == true) return
        if (force) refreshJob?.cancel()
        if (force || _ui.value.capabilities.isEmpty()) {
            _ui.value = _ui.value.copy(checking = true)
        }
        refreshJob = viewModelScope.launch {
            val (s, work, cloud) = coroutineScope {
                val statusCall = async { status.fetch() }
                val workCall = async { workRuntimeRepository.fetch(force) }
                val cloudCall = async { workspaceRuntimeRepository.cloudWorkspace() }
                Triple(statusCall.await(), workCall.await(), cloudCall.await())
            }
            val deviceCount = maxOf(s.desktops, s.remoteHosts)
            _ui.value = HubUi(
                backendOnline = s.online,
                desktopOnline = deviceCount > 0,
                desktopCount = deviceCount,
                cloudWorkspaceConfigured = cloud.configured,
                cloudWorkspaceReady = cloud.ready,
                checking = false,
                workRuns = work.runs,
                workNotice = work.notice,
                workError = work.error,
            )
        }
    }
}
