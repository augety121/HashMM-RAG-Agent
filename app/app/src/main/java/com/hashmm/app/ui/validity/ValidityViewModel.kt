package com.hashmm.app.ui.validity

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.DocValidity
import com.hashmm.app.data.remote.KnowledgeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 失效区视图状态。filter: ""=全部 / expired / archived / active。 */
data class ValidityUi(
    val loading: Boolean = true,
    val filter: String = "expired",
    val items: List<DocValidity> = emptyList(),
    val error: String? = null,
    val busy: Boolean = false,
    val msg: String? = null,
)

@HiltViewModel
class ValidityViewModel @Inject constructor(
    private val repo: KnowledgeRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(ValidityUi())
    val ui: StateFlow<ValidityUi> = _ui.asStateFlow()

    init { load("expired") }

    fun load(filter: String = _ui.value.filter) {
        _ui.value = _ui.value.copy(loading = true, filter = filter, error = null)
        viewModelScope.launch {
            val items = repo.validityList(filter)
            _ui.value = _ui.value.copy(loading = false, items = items)
        }
    }

    fun setValidity(filename: String, effective: String?, expiry: String?, note: String) {
        if (_ui.value.busy) return
        _ui.value = _ui.value.copy(busy = true, msg = null)
        viewModelScope.launch {
            val (ok, m) = repo.setValidity(filename, effective, expiry, note)
            _ui.value = _ui.value.copy(busy = false, msg = (if (ok) "✓ " else "✗ ") + m)
            if (ok) load()
        }
    }

    fun archive(filename: String) = mutate(filename) { repo.archiveDoc(filename) }
    fun restore(filename: String) = mutate(filename) { repo.restoreDoc(filename) }

    fun sweep() {
        if (_ui.value.busy) return
        _ui.value = _ui.value.copy(busy = true, msg = null)
        viewModelScope.launch {
            val n = repo.sweepExpired()
            _ui.value = _ui.value.copy(busy = false, msg = "扫描完成，新增失效 $n 篇")
            load()
        }
    }

    private fun mutate(filename: String, action: suspend () -> Boolean) {
        if (_ui.value.busy) return
        _ui.value = _ui.value.copy(busy = true, msg = null)
        viewModelScope.launch {
            val ok = action()
            _ui.value = _ui.value.copy(busy = false, msg = if (ok) "已更新「$filename」" else "操作失败（需管理员）")
            if (ok) load()
        }
    }

    fun clearMsg() { _ui.value = _ui.value.copy(msg = null) }
}
