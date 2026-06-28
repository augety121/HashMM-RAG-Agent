package com.hashmm.app.ui.knowledge

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.CorpusStats
import com.hashmm.app.data.remote.KbDoc
import com.hashmm.app.data.remote.KnowledgeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class KnowledgeUi(
    val loading: Boolean = true,
    val stats: CorpusStats? = null,
    val documents: List<KbDoc> = emptyList(),
    val error: String? = null,
    val uploading: Boolean = false,
    val uploadMsg: String? = null,
)

@HiltViewModel
class KnowledgeViewModel @Inject constructor(
    private val repo: KnowledgeRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(KnowledgeUi())
    val ui: StateFlow<KnowledgeUi> = _ui.asStateFlow()

    init { load() }

    fun load() {
        _ui.value = _ui.value.copy(loading = true, error = null)
        viewModelScope.launch {
            val s = repo.stats()
            val docs = repo.documents()
            _ui.value = _ui.value.copy(
                loading = false,
                stats = s,
                documents = docs,
                error = if (s == null) "未连到客户端后端 —— 请到「我的 → 客户端连接」填写地址（如 ）" else null,
            )
        }
    }

    fun upload(filename: String, bytes: ByteArray, mime: String) {
        if (_ui.value.uploading) return
        _ui.value = _ui.value.copy(uploading = true, uploadMsg = null)
        viewModelScope.launch {
            val (ok, msg) = repo.uploadDocument(filename, bytes, mime)
            _ui.value = _ui.value.copy(uploading = false, uploadMsg = (if (ok) "✓ " else "✗ ") + msg)
            if (ok) load() // 上传成功刷新统计与文档列表
        }
    }

    fun clearUploadMsg() { _ui.value = _ui.value.copy(uploadMsg = null) }
}
