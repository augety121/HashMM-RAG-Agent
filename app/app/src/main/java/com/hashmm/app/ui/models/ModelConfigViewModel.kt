package com.hashmm.app.ui.models

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.ModelCreate
import com.hashmm.app.data.remote.ModelInfo
import com.hashmm.app.data.remote.ModelProviderInfo
import com.hashmm.app.data.remote.ModelRepository
import com.hashmm.app.data.settings.SettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

private val FALLBACK_PROVIDERS = listOf(
    ModelProviderInfo("deepseek", "DeepSeek", "https://api.deepseek.com", listOf("chat_completions"), "chat_completions", false, false, "", emptyList()),
    ModelProviderInfo("openai", "OpenAI", "https://api.openai.com/v1", listOf("chat_completions", "responses"), "chat_completions", false, false, "", emptyList()),
    ModelProviderInfo("anthropic", "Anthropic Claude", "https://api.anthropic.com", listOf("anthropic_messages"), "anthropic_messages", false, false, "", emptyList()),
    ModelProviderInfo("custom", "OpenAI 兼容服务", "", listOf("chat_completions"), "chat_completions", false, false, "填写服务商提供的 API 根地址。", emptyList()),
    ModelProviderInfo("sub2api", "Sub2API / 自有反代", "", listOf("chat_completions", "responses"), "chat_completions", false, false, "填写你有权使用的 Sub2API HTTPS 根地址；先测试，再设为当前模型。", emptyList()),
)

data class AddForm(
    val show: Boolean = false,
    val name: String = "",
    val provider: String = "deepseek",
    val providerName: String = "DeepSeek",
    val baseUrl: String = "https://api.deepseek.com",
    val apiKey: String = "",
    val modelName: String = "",
    val wireApi: String = "chat_completions",
    val wireApis: List<String> = listOf("chat_completions"),
    val apiKeyOptional: Boolean = false,
    val endpointNote: String = "",
    val modelHints: List<String> = emptyList(),
    val testing: Boolean = false,
    val testOk: Boolean? = null,
    val testMsg: String? = null,
    val saving: Boolean = false,
) {
    val canSubmit: Boolean get() = name.isNotBlank() && modelName.isNotBlank() && baseUrl.isNotBlank()
        && (apiKeyOptional || apiKey.isNotBlank())
}

data class ModelsUi(
    val loading: Boolean = true,
    val models: List<ModelInfo> = emptyList(),
    val providers: List<ModelProviderInfo> = FALLBACK_PROVIDERS,
    val providerError: String? = null,
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
            val (providers, providerError) = repo.listProviders()
            _ui.value = _ui.value.copy(
                loading = false,
                models = list,
                providers = providers.ifEmpty { FALLBACK_PROVIDERS },
                providerError = providerError,
                error = if (list.isEmpty()) err else null,
            )
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
    fun openAdd() {
        val provider = _ui.value.providers.firstOrNull() ?: FALLBACK_PROVIDERS.first()
        _ui.value = _ui.value.copy(addForm = AddForm(
            show = true,
            provider = provider.id,
            providerName = provider.name,
            baseUrl = provider.baseUrl,
            wireApi = provider.defaultWireApi,
            wireApis = provider.wireApis,
            apiKeyOptional = provider.authOptional,
            endpointNote = provider.endpointNote,
            modelHints = provider.modelHints,
            name = provider.name,
        ))
    }
    fun closeAdd() { _ui.value = _ui.value.copy(addForm = _ui.value.addForm.copy(show = false)) }

    private fun mutateForm(block: (AddForm) -> AddForm) {
        _ui.value = _ui.value.copy(addForm = block(_ui.value.addForm))
    }

    fun onName(v: String) = mutateForm { it.copy(name = v, testOk = null, testMsg = null) }
    fun onApiKey(v: String) = mutateForm { it.copy(apiKey = v, testOk = null, testMsg = null) }
    fun onBaseUrl(v: String) = mutateForm { it.copy(baseUrl = v, testOk = null, testMsg = null) }
    fun onModelName(v: String) = mutateForm { it.copy(modelName = v, testOk = null, testMsg = null) }
    fun onWireApi(v: String) = mutateForm { it.copy(wireApi = v, testOk = null, testMsg = null) }

    fun onProvider(p: String) {
        val provider = _ui.value.providers.firstOrNull { it.id == p } ?: return
        mutateForm {
            it.copy(
                provider = p,
                providerName = provider.name,
                baseUrl = provider.baseUrl,
                modelName = "",
                wireApi = provider.defaultWireApi,
                wireApis = provider.wireApis,
                apiKeyOptional = provider.authOptional,
                endpointNote = provider.endpointNote,
                modelHints = provider.modelHints,
                name = provider.name,
                testOk = null, testMsg = null,
            )
        }
    }

    fun testAdd() {
        val f = _ui.value.addForm
        if (f.testing || !f.canSubmit) return
        mutateForm { it.copy(testing = true, testOk = null, testMsg = null) }
        viewModelScope.launch {
            val r = repo.testModel(ModelCreate(f.name, f.provider, f.baseUrl, f.apiKey, f.modelName, wireApi = f.wireApi))
            mutateForm { it.copy(testing = false, testOk = r.ok, testMsg = r.message + if (r.latencyMs > 0) "（${r.latencyMs}ms）" else "") }
        }
    }

    fun saveAdd() {
        val f = _ui.value.addForm
        if (f.saving || !f.canSubmit) return
        mutateForm { it.copy(saving = true) }
        viewModelScope.launch {
            val (ok, msg) = repo.createModel(ModelCreate(f.name, f.provider, f.baseUrl, f.apiKey, f.modelName, wireApi = f.wireApi))
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
