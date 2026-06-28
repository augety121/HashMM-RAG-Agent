package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ClientStatusRepository
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

data class HubUi(
    val online: Boolean = false,
    val clientUrl: String = "",
    val checking: Boolean = true,
)

@HiltViewModel
class WorkbenchHubViewModel @Inject constructor(
    private val status: ClientStatusRepository,
    private val settings: SettingsStore,
) : ViewModel() {
    private val _ui = MutableStateFlow(HubUi())
    val ui: StateFlow<HubUi> = _ui.asStateFlow()

    init { refresh() }

    fun refresh() {
        _ui.value = _ui.value.copy(checking = true)
        viewModelScope.launch {
            val url = runCatching { settings.clientUrl.first() }.getOrDefault("")
            val s = status.fetch()
            _ui.value = HubUi(online = s.online, clientUrl = url, checking = false)
        }
    }
}
