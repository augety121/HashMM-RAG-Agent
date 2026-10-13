package com.hashmm.app.ui.usage

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.hashmm.app.data.remote.UsageRepository
import com.hashmm.app.data.remote.UsageStat
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class UsageUi(
    val loading: Boolean = true,
    val days: Int = 30,
    val stat: UsageStat? = null,
)

@HiltViewModel
class UsageViewModel @Inject constructor(
    private val repo: UsageRepository,
) : ViewModel() {
    private val _ui = MutableStateFlow(UsageUi())
    val ui: StateFlow<UsageUi> = _ui.asStateFlow()

    init { load(30) }

    fun load(days: Int) {
        _ui.value = _ui.value.copy(loading = true, days = days)
        viewModelScope.launch {
            val s = repo.usage(days)
            _ui.value = UsageUi(loading = false, days = days, stat = s)
        }
    }
}
