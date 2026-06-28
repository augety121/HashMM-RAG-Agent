package com.hashmm.app.ui.models

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ModelCreate
import com.hashmm.app.data.remote.ModelInfo
import com.hashmm.app.data.remote.ModelRepository
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

/** 厂商预设：provider -> (base_url, 推荐模型名)。与后端 PROVIDER_PRESETS 对齐。 */
val PROVIDER_PRESETS: List<Triple<String, String, String>> = listOf(
    Triple("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    Triple("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
    Triple("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    Triple("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    Triple("moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    Triple("ollama", "http://localhost:11434/v1", "qwen2.5:7b"),
    Triple("lmstudio", "http://localhost:1234/v1", "local-model"),
    Triple("vllm", "http://localhost:8000/v1", "served-model"),
)

data class AddForm(
    val show: Boolean = false,
    val name: String = "",
    val provider: String = "openai",
    val baseUrl: String = "https://api.openai.com/v1",
    val apiKey: String = "",
    val modelName: String = "gpt-4o-mini",
    val testing: Boolean = false,
    val testOk: Boolean? = null,
    val testMsg: String? = null,
    val saving: Boolean = false,
) {
    val canSubmit: Boolean get() = name.isNotBlank() && modelName.isNotBlank() && baseUrl.isNotBlank()
}

data class ModelsUi(
    val loading: Boolean = true,
    val models: List<ModelInfo> = emptyList(),
    val error: String? = null,
    val switching: String? = null,
    val deleting: String? = null,
    val backendUrl: String = "",
    val backendSaved: Boolean = false,
    val addForm: AddForm = AddForm(),
    val toast: String? = null,
)

@HiltViewModel
class ModelConfigViewModel @Inject constructor(
    private val repo: ModelRepository,
    private val settings: SettingsStore,
) : ViewModel() {
    private val _ui = MutableStateFlow(ModelsUi())
    val ui: StateFlow<ModelsUi> = _ui.asStateFlow()

    init {
        viewModelScope.launch {
            val url = runCatching { settings.clientUrl.first() }.getOrDefault("")
            _ui.value = _ui.value.copy(backendUrl = url)
        }
        load()
    }

    fun onBackendChange(v: String) { _ui.value = _ui.value.copy(backendUrl = v, backendSaved = false) }

    fun saveBackend() {
        val url = _ui.value.backendUrl.trim()
        viewModelScope.launch {
            settings.setClientUrl(url)
            _ui.value = _ui.value.copy(backendSaved = true)
            load()
        }
    }

    fun load() {
        _ui.value = _ui.value.copy(loading = true, error = null)
        viewModelScope.launch {
            val (list, err) = repo.listModels()
            _ui.value = _ui.value.copy(loading = false, models = list, error = if (list.isEmpty()) err else null)
        }
    }

    fun setDefault(id: String) {
        if (_ui.value.switching != null) return
        _ui.value = _ui.value.copy(switching = id)
        viewModelScope.launch {
            val ok = repo.setDefault(id)
            if (ok) _ui.value = _ui.value.copy(switching = null, models = _ui.value.models.map { it.copy(isDefault = it.id == id) })
            else _ui.value = _ui.value.copy(switching = null, toast = "切换失败，请重试")
        }
    }

    fun deleteModel(id: String) {
        if (_ui.value.deleting != null) return
        _ui.value = _ui.value.copy(deleting = id)
        viewModelScope.launch {
            val ok = repo.deleteModel(id)
            if (ok) _ui.value = _ui.value.copy(deleting = null, models = _ui.value.models.filterNot { it.id == id }, toast = "已删除")
            else _ui.value = _ui.value.copy(deleting = null, toast = "删除失败")
        }
    }

    // ---- 新增模型表单 ----
    fun openAdd() { _ui.value = _ui.value.copy(addForm = AddForm(show = true)) }
    fun closeAdd() { _ui.value = _ui.value.copy(addForm = _ui.value.addForm.copy(show = false)) }

    private fun mutateForm(block: (AddForm) -> AddForm) {
        _ui.value = _ui.value.copy(addForm = block(_ui.value.addForm))
    }

    fun onName(v: String) = mutateForm { it.copy(name = v, testOk = null, testMsg = null) }
    fun onApiKey(v: String) = mutateForm { it.copy(apiKey = v, testOk = null, testMsg = null) }
    fun onBaseUrl(v: String) = mutateForm { it.copy(baseUrl = v, testOk = null, testMsg = null) }
    fun onModelName(v: String) = mutateForm { it.copy(modelName = v, testOk = null, testMsg = null) }

    fun onProvider(p: String) {
        val preset = PROVIDER_PRESETS.firstOrNull { it.first == p }
        mutateForm {
            it.copy(
                provider = p,
                baseUrl = preset?.second ?: it.baseUrl,
                modelName = preset?.third ?: it.modelName,
                name = if (it.name.isBlank()) p.replaceFirstChar { c -> c.uppercase() } else it.name,
                testOk = null, testMsg = null,
            )
        }
    }

    fun testAdd() {
        val f = _ui.value.addForm
        if (f.testing || !f.canSubmit) return
        mutateForm { it.copy(testing = true, testOk = null, testMsg = null) }
        viewModelScope.launch {
            val r = repo.testModel(ModelCreate(f.name, f.provider, f.baseUrl, f.apiKey, f.modelName))
            mutateForm { it.copy(testing = false, testOk = r.ok, testMsg = r.message + if (r.latencyMs > 0) "（${r.latencyMs}ms）" else "") }
        }
    }

    fun saveAdd() {
        val f = _ui.value.addForm
        if (f.saving || !f.canSubmit) return
        mutateForm { it.copy(saving = true) }
        viewModelScope.launch {
            val (ok, msg) = repo.createModel(ModelCreate(f.name, f.provider, f.baseUrl, f.apiKey, f.modelName))
            if (ok) {
                _ui.value = _ui.value.copy(addForm = AddForm(show = false), toast = msg)
                load()
            } else {
                mutateForm { it.copy(saving = false) }
                _ui.value = _ui.value.copy(toast = msg)
            }
        }
    }

    fun clearToast() { _ui.value = _ui.value.copy(toast = null) }
}
